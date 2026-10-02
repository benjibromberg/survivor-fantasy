import json
import logging
from datetime import date

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from . import schedule, wildcards
from .data import (
    download_survivor_data,
    export_all_picks,
    export_season_picks,
    generate_season_images,
    hand_entered_survivor_count,
    refresh_season,
    store_uploaded_headshot,
)
from .models import (
    TEAM_NAME_MAX_LENGTH,
    Pick,
    Season,
    SoleSurvivorPick,
    Survivor,
    TeamName,
    User,
    calculate_ss_streak,
    db,
    normalize_email,
)
from .predictions import calculate_win_probabilities, season_structure_known
from .scoring import SCORING_SYSTEMS, compute_stat_overrides, get_scoring_system
from .scoring.classic import CONFIG_LABELS, DEFAULT_CONFIG, LEGACY_CONFIG

logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)

BREAKDOWN_LABELS = {
    "pre_merge_tribal": "Pre-merge",
    "post_merge_tribal": "Post-merge",
    "finale_tribal": "Finale",
    "jury": "Jury",
    "merge": "Merge",
    "placement": "Placement",
    "final_tribal": "FTC",
    "individual_immunity": "Indiv. Imm.",
    "tribal_immunity": "Tribal Imm.",
    "idols_found": "Idols",
    "advantages_found": "Advantages",
    "idol_plays": "Idol Plays",
    "advantage_plays": "Adv. Plays",
    "fire_win": "Fire Win",
    "sole_survivor_streak": "SS Streak",
}

# Episode stats JSON key → model attribute (full mapping used by _apply_as_of)
_EP_STAT_MAP = {
    "ii": "individual_immunity_wins",
    "ti": "tribal_immunity_wins",
    "idol": "idols_found",
    "idol_play": "idols_played",
    "adv": "advantages_found",
    "adv_play": "advantages_played",
    "conf": "confessional_count",
    "conf_time": "confessional_time",
    "votes": "votes_received",
    "reward": "reward_wins",
    "tribals": "tribal_councils_attended",
    "votes_cast": "votes_cast",
    "correct_votes": "correct_votes",
    "nullified": "votes_nullified",
    "sit_outs": "sit_outs",
    "tribe": "tribe",
    "tribe_color": "tribe_color",
}


def _fmt_time(secs):
    """Format seconds as Xm Ys."""
    if not secs:
        return "0s"
    m, s = divmod(int(secs), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _fmt_occupation(value):
    """survivoR joins a returning player's occupations with ';' (Ozzy is
    "Waiter;Photographer"), which reads as a typo wherever it is shown."""
    return value.replace(";", ", ") if value else value


def _voting_accuracy(correct_votes, votes_cast):
    """Percent of votes cast that were for the person voted out, or None.

    The denominator is votes cast, not tribals attended: a castaway who played
    Shot in the Dark or lost their vote cast nothing, and revotes or extra
    votes add a ballot each. None when no vote has been cast.
    """
    if not votes_cast:
        return None
    return (correct_votes or 0) / votes_cast * 100


def _ensure_contrast(hex_color, min_luminance=0.15):
    """Lighten a hex color if it's too dark for the dark theme background.

    Uses relative luminance (W3C formula). Colors below min_luminance are
    lightened to ensure readability against the ~#0d1b2a background.
    """
    if not hex_color or not hex_color.startswith("#"):
        return hex_color
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return hex_color

    def _srgb(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)
    if lum >= min_luminance:
        return hex_color

    # Lighten by blending toward white until we hit min_luminance
    for pct in range(10, 100, 5):
        nr = r + (255 - r) * pct // 100
        ng = g + (255 - g) * pct // 100
        nb = b + (255 - b) * pct // 100
        nl = 0.2126 * _srgb(nr) + 0.7152 * _srgb(ng) + 0.0722 * _srgb(nb)
        if nl >= min_luminance:
            return f"#{nr:02x}{ng:02x}{nb:02x}"
    return hex_color


PICK_TYPE_LABELS = {
    "draft": "Draft",
    "wildcard": "Wildcard",
    "pmr_w": "Replacement (½)",
    "pmr_d": "Replacement",
}


def _require_admin():
    """Return a redirect response if current user is not admin, else None."""
    if not current_user.is_authenticated or not current_user.is_admin:
        flash("Admin access required.", "error")
        return redirect(url_for("main.index"))
    return None


EPISODE_COLUMNS = (
    ("conf", "Conf"),
    ("ii", "Ind"),
    ("ti", "Tribal"),
    ("idol", "Idols"),
    ("adv", "Advs"),
    ("votes", "Votes"),
)


def _air_date_label(iso_date):
    """An ISO air date as "Oct 2, 2024", or None if it cannot be read."""
    if not iso_date:
        return None
    try:
        day = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return None
    return f"{day:%b} {day.day}, {day.year}"


def _episode_rows(survivor, as_of_episode=None, episode_info=None):
    """Per-episode activity for one castaway, for the sheet's Episodes tab.

    Two things about episode_stats make the obvious reading wrong. Its totals
    are CUMULATIVE, so an episode's own activity is the difference from the
    episode before it. And it keeps repeating the final totals for every
    episode after the castaway is voted out, so rows have to stop at the
    elimination episode or the table shows a boot still playing.

    episode_info is the season's titles and air dates (Season.episode_info),
    passed in rather than read per castaway. Either can be missing: survivoR
    lists a scheduled episode before it has a title.
    """
    info = episode_info or {}
    raw = survivor.get_episode_stats()
    if not raw:
        return []

    episodes = sorted(int(k) for k in raw)
    if not episodes:
        return []
    last = survivor.elimination_episode or episodes[-1]
    if as_of_episode:
        last = min(last, as_of_episode)

    rows, prev = [], {}
    for ep in episodes:
        if ep > last:
            break
        cur = raw[str(ep)]
        values = [
            (label, (cur.get(key) or 0) - (prev.get(key) or 0))
            for key, label in EPISODE_COLUMNS
        ]
        episode = info.get(str(ep), {})
        rows.append(
            {
                "episode": ep,
                "title": episode.get("title"),
                "air_date": _air_date_label(episode.get("date")),
                "tribe": cur.get("tribe"),
                "tribe_color": cur.get("tribe_color"),
                # The end of this castaway's game, which is NOT the same as
                # being voted out: the winner, both runners-up and the fire
                # loser all share the finale's elimination_episode.
                "final": survivor.voted_out_order > 0
                and ep == survivor.elimination_episode,
                "values": values,
            }
        )
        prev = cur

    # Every column zero in every episode means the season has no per-episode
    # data for this castaway, which is a blank table rather than information.
    if not any(v for r in rows for _label, v in r["values"]):
        return []

    # Heat is scaled per column to this castaway's own best episode, so a
    # quiet player's table still reads rather than being uniformly cold.
    peaks = {}
    for r in rows:
        for label, v in r["values"]:
            peaks[label] = max(peaks.get(label, 0), v)
    for r in rows:
        r["cells"] = [
            {
                "label": label,
                "value": v,
                "heat": round(v / peaks[label], 3) if peaks.get(label) else 0,
            }
            for label, v in r["values"]
        ]
        del r["values"]
    return rows


def _build_leaderboard(season):
    """Build leaderboard data for a season. Reads current state of survivor objects."""
    scoring = get_scoring_system(season.scoring_system, season.get_scoring_config())

    users = User.query.join(Pick).filter(Pick.season_id == season.id).distinct().all()
    team_names = TeamName.for_season(season.id)
    wildcards_hidden = wildcards.are_hidden(season)
    leaderboard_data = []

    # Build display results for same-day eliminations (e.g. "T-5th voted out")
    _result_display = {}
    day_groups = {}
    for s in season.survivors:
        if s.voted_out_order and s.voted_out_order > 0 and s.day_voted_out:
            day_groups.setdefault(s.day_voted_out, []).append(s)
    for _day, survs in day_groups.items():
        if len(survs) > 1:
            # Exclude finalists/winner (placement is genuinely distinct)
            non_final = [
                s for s in survs if s.result and "voted out" in s.result.lower()
            ]
            if len(non_final) > 1:
                min_order = min(s.voted_out_order for s in non_final)
                # Find the ordinal from the lowest-order player's result
                base_result = next(
                    s.result for s in non_final if s.voted_out_order == min_order
                )
                tied_result = f"Tied {base_result}"
                for s in non_final:
                    _result_display[s.id] = tied_result

    # Wildcards are picked after episode 1; replacements after the merge
    current_elim_count = season.current_tribal_count
    merge_elim = season.merge_threshold  # None if merge data unknown

    # Find merge episode for post-merge-only replacement scoring
    merge_episode = None
    if merge_elim is not None:
        for s in season.survivors:
            if (
                s.voted_out_order
                and s.voted_out_order == merge_elim
                and s.elimination_episode
            ):
                merge_episode = s.elimination_episode
                break

    for user in users:
        picks = Pick.query.filter_by(user_id=user.id, season_id=season.id).all()
        # `picks` still feeds the reminders below; only scoring skips
        # wildcards that are hidden until every player has picked
        scored = wildcards.scored_picks(picks, wildcards_hidden)
        total = 0
        pick_details = []

        for pick in scored:
            # Wildcards: skip if no eliminations yet (picked after ep 1)
            if pick.pick_type == "wildcard" and current_elim_count == 0:
                continue
            # Replacements: skip if merge hasn't happened yet (or merge unknown)
            if pick.pick_type in ("pmr_w", "pmr_d") and (
                merge_elim is None or current_elim_count < merge_elim
            ):
                continue

            survivor = pick.survivor

            # Compute post-merge-only stat overrides for replacement picks
            stat_overrides = None
            if pick.pick_type in ("pmr_w", "pmr_d"):
                stat_overrides = compute_stat_overrides(survivor, merge_episode)

            modified, breakdown = scoring.score_pick(
                survivor, season, pick.pick_type, stat_overrides
            )
            total += modified
            # Compact stat line for display
            stats = []
            if survivor.confessional_count:
                stats.append(f"{survivor.confessional_count} confessionals")
            if survivor.individual_immunity_wins:
                stats.append(
                    f"{survivor.individual_immunity_wins} immunity win{'s' if survivor.individual_immunity_wins > 1 else ''}"
                )
            if survivor.idols_found:
                stats.append(
                    f"{survivor.idols_found} idol{'s' if survivor.idols_found > 1 else ''} found"
                )
            if survivor.advantages_found:
                stats.append(
                    f"{survivor.advantages_found} advantage{'s' if survivor.advantages_found > 1 else ''} found"
                )

            stats_detail = []
            if survivor.confessional_count:
                stats_detail.append(("Confessionals", survivor.confessional_count))
            ct = _fmt_time(survivor.confessional_time)
            if ct:
                stats_detail.append(("Screen time", ct))
            if survivor.individual_immunity_wins:
                stats_detail.append(
                    ("Individual immunity", survivor.individual_immunity_wins)
                )
            if survivor.tribal_immunity_wins:
                stats_detail.append(("Tribal immunity", survivor.tribal_immunity_wins))
            if survivor.reward_wins:
                stats_detail.append(("Challenge wins", survivor.reward_wins))
            if survivor.tribal_councils_attended:
                stats_detail.append(
                    ("Tribals attended", survivor.tribal_councils_attended)
                )
            pct = _voting_accuracy(survivor.correct_votes, survivor.votes_cast)
            if pct is not None and survivor.correct_votes:
                stats_detail.append(("Voting accuracy", f"{pct:.0f}%"))
            if survivor.votes_received:
                stats_detail.append(("Votes against", survivor.votes_received))
            if survivor.idols_found:
                stats_detail.append(("Idols found", survivor.idols_found))
            if getattr(survivor, "idols_played", 0):
                stats_detail.append(("Idols played", survivor.idols_played))
            if survivor.advantages_found:
                stats_detail.append(("Advantages found", survivor.advantages_found))
            if survivor.advantages_played:
                stats_detail.append(("Advantages played", survivor.advantages_played))
            if survivor.votes_nullified:
                stats_detail.append(("Votes nullified", survivor.votes_nullified))
            if survivor.sit_outs:
                stats_detail.append(("Sit-outs", survivor.sit_outs))

            # Bio info
            bio_parts = []
            if survivor.age:
                bio_parts.append(f"Age {survivor.age}")
            if survivor.city and survivor.state:
                bio_parts.append(f"{survivor.city}, {survivor.state}")
            elif survivor.city or survivor.state:
                bio_parts.append(survivor.city or survivor.state)
            if survivor.occupation:
                bio_parts.append(_fmt_occupation(survivor.occupation))
            if survivor.personality_type:
                bio_parts.append(survivor.personality_type)

            pick_details.append(
                {
                    "survivor": survivor.name,
                    "full_name": survivor.full_name,
                    "survivor_obj": survivor,
                    "survivor_image": survivor.image_url,
                    "tribe_color": survivor.tribe_color,
                    "tribe": survivor.tribe,
                    "stats_url": survivor.stats_url,
                    "result": _result_display.get(survivor.id, survivor.result),
                    "pick_type": PICK_TYPE_LABELS.get(pick.pick_type, pick.pick_type),
                    "pick_type_raw": pick.pick_type,
                    "base_points": breakdown.total,
                    "modified_points": modified,
                    "breakdown": breakdown.items,
                    "eliminated": survivor.voted_out_order > 0,
                    "stats_line": " · ".join(stats) if stats else None,
                    "stats_detail": stats_detail,
                    "bio_line": " · ".join(bio_parts) if bio_parts else None,
                    "age": survivor.age,
                    "occupation": _fmt_occupation(survivor.occupation),
                }
            )

        # Sole Survivor streak bonus (separate from draft/wildcard picks)
        ss_picks = SoleSurvivorPick.query.filter_by(
            user_id=user.id, season_id=season.id
        ).all()
        ss_streak = calculate_ss_streak(ss_picks, season)
        ss_bonus = scoring.calculate_sole_survivor_bonus(ss_streak)
        if ss_bonus > 0 or ss_picks:
            # Show the current SS pick in the pick list
            current_ss = max(ss_picks, key=lambda p: p.episode) if ss_picks else None
            if current_ss:
                survivor = current_ss.survivor
                pick_details.append(
                    {
                        "survivor": survivor.name,
                        "full_name": survivor.full_name,
                        "survivor_image": survivor.image_url,
                        "tribe_color": survivor.tribe_color,
                        "tribe": survivor.tribe,
                        "stats_url": survivor.stats_url,
                        "result": _result_display.get(survivor.id, survivor.result),
                        "pick_type": f"SS Pick ({ss_streak} ep streak)",
                        "pick_type_raw": "sole_survivor",
                        "base_points": ss_bonus,
                        "modified_points": ss_bonus,
                        "breakdown": {"sole_survivor_streak": ss_bonus},
                        "eliminated": survivor.voted_out_order > 0,
                    }
                )
        total += ss_bonus

        # Current sole survivor pick for header display
        current_ss_name = None
        current_ss_eliminated = False
        if ss_picks:
            latest = max(ss_picks, key=lambda p: p.episode)
            current_ss_name = latest.survivor.name
            current_ss_eliminated = latest.survivor.voted_out_order > 0

        # Aggregate team stats (only include picks that are active)
        def _pick_active(p):
            if p.pick_type == "wildcard" and current_elim_count == 0:
                return False
            return not (
                p.pick_type in ("pmr_w", "pmr_d")
                and (merge_elim is None or current_elim_count < merge_elim)
            )

        team_survivors = [p.survivor for p in scored if _pick_active(p)]
        team_stats = {}
        if team_survivors:
            total_conf = sum(s.confessional_count or 0 for s in team_survivors)
            total_imm = sum(s.individual_immunity_wins or 0 for s in team_survivors)
            total_idols = sum(s.idols_found or 0 for s in team_survivors)
            total_tribals = sum(s.tribal_councils_attended or 0 for s in team_survivors)
            total_cast = sum(s.votes_cast or 0 for s in team_survivors)
            total_correct = sum(s.correct_votes or 0 for s in team_survivors)
            total_votes_against = sum(s.votes_received or 0 for s in team_survivors)
            if total_conf:
                team_stats["Confessionals"] = total_conf
            if total_imm:
                team_stats["Immunity wins"] = total_imm
            if total_idols:
                team_stats["Idols found"] = total_idols
            if total_tribals:
                team_stats["Tribals attended"] = total_tribals
            pct = _voting_accuracy(total_correct, total_cast)
            if pct is not None and total_correct:
                team_stats["Voting accuracy"] = f"{pct:.0f}%"
            if total_votes_against:
                team_stats["Votes against"] = total_votes_against

        # Warnings for active season
        warnings = []
        if season.is_active:
            pick_types = {p.pick_type for p in picks}
            # Wildcard reminder
            if current_elim_count > 0 and "wildcard" not in pick_types:
                warnings.append("Wildcard not yet picked")
            # Replacement eligibility after merge
            if merge_elim is not None and current_elim_count >= merge_elim:
                has_replacement = "pmr_w" in pick_types or "pmr_d" in pick_types
                if not has_replacement:
                    draft_elim = any(
                        p.pick_type == "draft"
                        and p.survivor.voted_out_order > merge_elim
                        for p in picks
                    )
                    wc_elim = any(
                        p.pick_type == "wildcard"
                        and p.survivor.voted_out_order > merge_elim
                        for p in picks
                    )
                    if draft_elim:
                        warnings.append("Eligible for draft replacement")
                    elif wc_elim:
                        warnings.append("Eligible for wildcard replacement")

        leaderboard_data.append(
            {
                "user": user,
                "team_name": team_names.get(user.id),
                "total_points": total,
                "sole_survivor_pick": current_ss_name,
                "sole_survivor_eliminated": current_ss_eliminated,
                "picks": sorted(
                    pick_details, key=lambda p: p["modified_points"], reverse=True
                ),
                "team_stats": team_stats,
                "warnings": warnings,
            }
        )

    leaderboard_data.sort(key=lambda x: x["total_points"], reverse=True)
    return leaderboard_data, scoring.name


# --- Public routes (no login required) ---


@main_bp.route("/")
def index():
    season = Season.get_active()
    if season:
        return redirect(url_for("main.leaderboard", season_id=season.id))
    return render_template("no_season.html")


def _apply_as_of(season, as_of):
    """Temporarily set survivors to state as of N eliminations. Returns restore function.

    Uses per-episode cumulative stats when available. Falls back to zeroing
    stats for seasons without episode_stats data.
    """
    # All stat fields that get saved/restored
    stat_fields = [
        "individual_immunity_wins",
        "tribal_immunity_wins",
        "idols_found",
        "idols_played",
        "advantages_found",
        "advantages_played",
        "confessional_count",
        "confessional_time",
        "votes_received",
        "reward_wins",
        "tribal_councils_attended",
        "votes_cast",
        "correct_votes",
        "votes_nullified",
        "sit_outs",
        "tribe",
        "tribe_color",
    ]
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    originals = {}
    for s in survivors:
        originals[s.id] = {
            "voted_out_order": s.voted_out_order,
            "made_jury": s.made_jury,
            "result": s.result,
            "won_fire": s.won_fire,
            "day_voted_out": s.day_voted_out,
            **{f: getattr(s, f) for f in stat_fields},
        }

    # Build elimination_order -> episode mapping
    elim_to_episode = {}
    for s in survivors:
        if s.voted_out_order and s.voted_out_order > 0 and s.elimination_episode:
            elim_to_episode[s.voted_out_order] = s.elimination_episode

    target_episode = elim_to_episode.get(as_of, 0) if as_of > 0 else 0

    # For proration fallback when episode_stats is missing
    max_elim = max(
        (
            orig["voted_out_order"]
            for orig in originals.values()
            if orig["voted_out_order"]
        ),
        default=1,
    )
    fraction = as_of / max_elim if as_of > 0 and max_elim > 0 else 0

    jury_threshold = season.merge_threshold  # None if merge data unknown
    n_finalists = season.n_finalists  # None if in-progress
    # Fire loser is last eliminated before FTC (4th place with 3 finalists)
    fire_elim = (season.num_players - n_finalists) if n_finalists is not None else None
    # Finalists are the last n_finalists (not jury members)
    finalist_threshold = (
        (season.num_players - n_finalists) if n_finalists is not None else None
    )

    for s in survivors:
        if s.voted_out_order > as_of:
            s.voted_out_order = 0
            s.made_jury = False
            s.result = None
            s.day_voted_out = None
        elif (
            jury_threshold is not None
            and finalist_threshold is not None
            and s.voted_out_order > 0
            and s.voted_out_order > jury_threshold
            and s.voted_out_order <= finalist_threshold
        ):
            # Force jury for post-merge boots; exclude finalists and winner
            s.made_jury = True

        # Fire challenge hasn't happened yet at this point in the timeline
        if fire_elim is not None and as_of < fire_elim:
            s.won_fire = False

        # Apply episode-scoped stats
        ep_stats = s.get_episode_stats()
        if ep_stats and target_episode > 0:
            ep_data = ep_stats.get(str(target_episode), {})
            for ep_key, field in _EP_STAT_MAP.items():
                setattr(s, field, ep_data.get(ep_key, 0))
        elif as_of > 0:
            # No episode stats — prorate current stats by season progress
            for f in stat_fields:
                orig_val = originals[s.id][f]
                if f in ("tribe", "tribe_color"):
                    setattr(s, f, orig_val)
                elif isinstance(orig_val, (int, float)) and orig_val:
                    setattr(s, f, orig_val * fraction)
                else:
                    setattr(s, f, orig_val or 0)
        else:
            for f in stat_fields:
                val = 0
                if f in ("tribe", "tribe_color"):
                    val = ""
                elif f == "confessional_time":
                    val = 0.0
                setattr(s, f, val)

    def restore():
        for s in survivors:
            if s.id in originals:
                for key, val in originals[s.id].items():
                    setattr(s, key, val)

    return restore


@main_bp.route("/scoring-analysis")
def scoring_analysis():
    return render_template("scoring_analysis.html")


# The league-settings hub and its detail pages. Order here is the order the
# hub renders; each slug is also the detail route and the template name, so a
# section cannot exist in one place and not the other.
LEAGUE_SETTINGS_GROUPS = (
    (
        "Scoring",
        (
            ("scoring", "Scoring components", "What each action is worth this season"),
            (
                "pick-types",
                "Pick types",
                "How much each kind of pick scores, and when you make it",
            ),
            (
                "win-probability",
                "Win probabilities",
                "How the leaderboard estimates everyone's chances",
            ),
        ),
    ),
    (
        "Picks",
        (
            ("draft", "The draft", "Snake draft, four castaways, full points"),
            (
                "wildcard",
                "Wildcard",
                "One extra pick after the first boot, at reduced points",
            ),
            (
                "replacements",
                "Replacements",
                "What happens when a pick goes out before the merge",
            ),
            (
                "sole-survivor",
                "Sole Survivor",
                "Your winner prediction, scored by how long you held it",
            ),
        ),
    ),
    (
        "About",
        (
            (
                "how-it-works",
                "How it works",
                "The season in three phases, with a worked example",
            ),
            (
                "data-sources",
                "Data sources",
                "Where the game data comes from and how often it refreshes",
            ),
        ),
    ),
)

LEAGUE_SETTINGS_SECTIONS = {
    slug: {"slug": slug, "title": title, "blurb": blurb, "group": group}
    for group, entries in LEAGUE_SETTINGS_GROUPS
    for slug, title, blurb in entries
}


def _settings_season(season_id):
    """The season a settings page is describing."""
    if season_id:
        return Season.query.get_or_404(season_id)
    return Season.get_active() or Season.query.first()


def _settings_context(season):
    """Everything both the hub and every detail page may need.

    One builder rather than one per page: the detail pages are slices of the
    same configuration readout, and computing a slice per route is how two
    pages end up disagreeing about the same season.
    """
    config = {**DEFAULT_CONFIG, **(season.get_scoring_config() if season else {})}

    # Build list of active and inactive components
    active_rules = []
    inactive_rules = []
    progressive = config.get("tribal_base") is not None
    for key, (label, desc) in CONFIG_LABELS.items():
        # In progressive mode, hide flat tribal keys
        if progressive and key in ("tribal_val", "post_merge_tribal_val"):
            continue
        # In flat mode, hide progressive tribal keys
        if not progressive and key in (
            "tribal_base",
            "tribal_step",
            "post_merge_step",
            "finale_step",
            "finale_size",
        ):
            continue
        val = config.get(key, 0)
        entry = {"key": key, "label": label, "desc": desc, "value": val}
        if val:
            active_rules.append(entry)
        else:
            inactive_rules.append(entry)

    # Compute example values for the rules page (skip if merge data unknown)
    examples = {}
    if season and season.merge_threshold is not None:
        scoring = get_scoring_system(season.scoring_system, season.get_scoring_config())
        mt = season.merge_threshold

        if progressive:
            # Build a mock survivor/season to compute example tribal totals
            base = config["tribal_base"]
            step = config.get("tribal_step", 0)
            pm_step = config.get("post_merge_step", 0)
            finale_size = config.get("finale_size", 5)
            finale_threshold = max(mt, season.num_players - finale_size)

            # First tribal value and a few sample values
            examples["first_tribal"] = base
            examples["merge_tribal"] = base + (mt - 1) * step if mt > 0 else base
            last_pre = examples["merge_tribal"]
            pm_count = max(0, finale_threshold - mt)
            examples["last_post_merge"] = (
                last_pre + pm_count * pm_step if pm_count > 0 else last_pre
            )
            examples["finale_threshold"] = finale_threshold

            # Winner total tribals (survived all)
            winner_tribals = season.num_players - 1
            winner_tribal_items = scoring._compute_tribal_points(winner_tribals, season)
            examples["winner_tribal_pts"] = sum(winner_tribal_items.values())
            examples["winner_total"] = (
                examples["winner_tribal_pts"]
                + config.get("first_val", 0)
                + config.get("merge_val", 0)
                + config.get("final_tribal_val", 0)
            )

            # First juror
            juror_tribals = mt
            juror_tribal_items = scoring._compute_tribal_points(juror_tribals, season)
            examples["juror_tribal_pts"] = sum(juror_tribal_items.values())
            examples["juror_total"] = (
                examples["juror_tribal_pts"]
                + config.get("jury_val", 0)
                + config.get("merge_val", 0)
            )
        else:
            pre_merge_rate = config["tribal_val"]
            post_merge_rate = config.get("post_merge_tribal_val", pre_merge_rate)
            post_merge_count = season.left_at_jury - 1
            examples["winner_total"] = (
                mt * pre_merge_rate
                + post_merge_count * post_merge_rate
                + config.get("first_val", 0)
                + config.get("final_tribal_val", 0)
                + config.get("merge_val", 0)
            )
            examples["juror_total"] = (
                mt * pre_merge_rate
                + config.get("jury_val", 0)
                + config.get("merge_val", 0)
            )

    return {
        "season": season,
        "config": config,
        "active_rules": active_rules,
        "inactive_rules": inactive_rules,
        "progressive": progressive,
        "examples": examples,
        "PICK_TYPE_LABELS": PICK_TYPE_LABELS,
        "wildcard_self_service": bool(season) and wildcards.is_self_service(season),
    }


@main_bp.route("/league-settings", defaults={"season_id": None})
@main_bp.route("/league-settings/<int:season_id>")
def league_settings(season_id):
    season = _settings_season(season_id)
    return render_template(
        "league_settings/hub.html",
        groups=LEAGUE_SETTINGS_GROUPS,
        season_id=season_id,
        **_settings_context(season),
    )


@main_bp.route("/league-settings/<slug>", defaults={"season_id": None})
@main_bp.route("/league-settings/<int:season_id>/<slug>")
def league_settings_detail(slug, season_id):
    section = LEAGUE_SETTINGS_SECTIONS.get(slug)
    # Validated against the table before it reaches the template name below,
    # so the include cannot be steered by the URL.
    if section is None:
        abort(404)
    season = _settings_season(season_id)
    return render_template(
        "league_settings/detail.html",
        section=section,
        groups=LEAGUE_SETTINGS_GROUPS,
        season_id=season_id,
        **_settings_context(season),
    )


@main_bp.route("/leaderboard/<int:season_id>")
def leaderboard(season_id):
    season = Season.query.get_or_404(season_id)

    # Elimination timeline
    max_eliminations = max(
        (s.voted_out_order for s in season.survivors if s.voted_out_order), default=0
    )
    as_of = request.args.get("as_of", type=int)
    if as_of is not None:
        as_of = max(0, min(as_of, max_eliminations))
    effective_as_of = as_of if as_of is not None else max_eliminations

    # Build episode-based timeline (one dot per episode, not per elimination)
    # Each dot maps to the last elimination in that episode
    ep_to_last_elim = {}  # episode_number → highest voted_out_order in that episode
    for s in season.survivors:
        if s.voted_out_order and s.voted_out_order > 0 and s.elimination_episode:
            ep = s.elimination_episode
            ep_to_last_elim[ep] = max(ep_to_last_elim.get(ep, 0), s.voted_out_order)

    # timeline_points: ordered list of {ep, as_of, milestone}
    timeline_points = [{"ep": None, "as_of": 0, "milestone": "Pre-game"}]
    for ep_num in sorted(ep_to_last_elim):
        timeline_points.append(
            {
                "ep": ep_num,
                "as_of": ep_to_last_elim[ep_num],
                "milestone": None,
            }
        )

    # Merge episode from survivoR tribe_status data (set during refresh)
    merge_ep = season.merge_episode_num

    # Assign milestones to timeline points
    for pt in timeline_points:
        if pt["ep"] == 1:
            pt["milestone"] = "Premiere"
        elif merge_ep and pt["ep"] == merge_ep:
            pt["milestone"] = "Merge"
    # Last point milestone
    if len(timeline_points) > 1:
        is_finished = max_eliminations >= season.num_players
        last = timeline_points[-1]
        if not last["milestone"]:
            last["milestone"] = "Finale" if is_finished else "Latest"
        elif is_finished:
            last["milestone"] += " / Finale"

    # Determine which timeline point is active
    active_timeline_idx = 0
    for i, pt in enumerate(timeline_points):
        if pt["as_of"] <= effective_as_of:
            active_timeline_idx = i

    # Apply as_of filter (affects both leaderboard and predictions)
    restore = None
    if as_of is not None:
        restore = _apply_as_of(season, as_of)

    leaderboard_data, scoring_name = _build_leaderboard(season)

    active_count = sum(1 for s in season.survivors if s.voted_out_order == 0)

    # Win probabilities (skip for historical as_of views — too expensive)
    win_pcts = {}
    projected_win_pcts = {}
    # Tell players why there are no odds yet, rather than leaving a gap
    odds_pending = bool(
        leaderboard_data
        and active_count > 0
        and as_of is None
        and not season_structure_known(season)
    )
    if leaderboard_data and active_count > 0 and as_of is None:
        frozen, projected, total_scenarios, exhaustive, _rates = (
            calculate_win_probabilities(season)
        )
        for uid, data in frozen.items():
            win_pcts[uid] = data["win_pct"]
        for uid, data in projected.items():
            projected_win_pcts[uid] = data["win_pct"]

    # Build stat boards (affected by as_of — stats are zeroed in historical views)
    stat_boards = _build_stat_boards(season)

    # Restore original state
    if restore:
        restore()

    # Generate journey highlights for each pick (AFTER restore — reads episode_stats
    # JSON which is immutable, and gates terminal events on elimination_episode).
    # Do NOT move this into _build_leaderboard() — that function is also called in a
    # loop for past_winner_badges, which would waste O(N × survivors × episodes) work.
    from .highlights import generate_highlights

    # Build elim → episode mapping for as_of conversion
    elim_to_episode = {}
    for s in season.survivors:
        if s.voted_out_order and s.voted_out_order > 0 and s.elimination_episode:
            elim_to_episode[s.voted_out_order] = s.elimination_episode
    target_episode = (
        elim_to_episode.get(effective_as_of, 0) if as_of is not None else None
    )
    # One parse for the whole page: the titles are the season's, not each
    # castaway's, so this does not belong inside the loop.
    episode_info = season.get_episode_info()
    for entry in leaderboard_data:
        for pick in entry["picks"]:
            surv = pick.get("survivor_obj")
            if surv:
                events, badges = generate_highlights(
                    surv, season, merge_ep, as_of_episode=target_episode
                )
                pick["journey_events"] = events
                pick["journey_badges"] = badges
                pick["episode_rows"] = _episode_rows(surv, target_episode, episode_info)
            else:
                pick["journey_events"] = []
                pick["journey_badges"] = []
                pick["episode_rows"] = []

    # Season progression chart (show up to effective_as_of)
    progression_datasets = []
    progression_labels = []
    if leaderboard_data and effective_as_of > 0:
        scoring = get_scoring_system(season.scoring_system, season.get_scoring_config())
        users = [e["user"] for e in leaderboard_data]
        wildcards_hidden = wildcards.are_hidden(season)
        user_picks = {}
        for user in users:
            user_picks[user.id] = wildcards.scored_picks(
                Pick.query.filter_by(user_id=user.id, season_id=season.id).all(),
                wildcards_hidden,
            )

        prog_merge = season.merge_threshold  # None if merge data unknown
        # Find merge episode for replacement scoring
        prog_merge_ep = None
        if prog_merge is not None:
            for s in season.survivors:
                if (
                    s.voted_out_order
                    and s.voted_out_order == prog_merge
                    and s.elimination_episode
                ):
                    prog_merge_ep = s.elimination_episode
                    break

        # Pre-fetch sole survivor picks for all users
        user_ss_picks = {}
        for user in users:
            user_ss_picks[user.id] = SoleSurvivorPick.query.filter_by(
                user_id=user.id, season_id=season.id
            ).all()

        progression = {u.id: [] for u in users}
        for step in range(effective_as_of + 1):
            # Final step = current state — use raw DB values (same as leaderboard)
            r = _apply_as_of(season, step) if step < effective_as_of else lambda: None
            for user in users:
                total = 0
                for pick in user_picks[user.id]:
                    if pick.pick_type == "wildcard" and step == 0:
                        continue
                    if pick.pick_type in ("pmr_w", "pmr_d") and (
                        prog_merge is None or step < prog_merge
                    ):
                        continue

                    survivor = pick.survivor
                    stat_overrides = None
                    if pick.pick_type in ("pmr_w", "pmr_d"):
                        stat_overrides = compute_stat_overrides(survivor, prog_merge_ep)
                    modified, _ = scoring.score_pick(
                        survivor, season, pick.pick_type, stat_overrides
                    )
                    total += modified

                # Sole Survivor streak bonus
                ss_streak = calculate_ss_streak(user_ss_picks[user.id], season)
                total += scoring.calculate_sole_survivor_bonus(ss_streak)

                progression[user.id].append(round(total, 2))
            r()

        progression_labels = []
        for i in range(effective_as_of + 1):
            if i == 0:
                progression_labels.append("Start")
            elif i == season.num_players:
                progression_labels.append("Sole Survivor")
            else:
                progression_labels.append(f"Elim {i}")

        # Build elimination event data: which user lost which survivor at each step
        # Map voted_out_order → survivor for eliminated survivors (exclude winner)
        elim_at_step = {}
        for s in season.survivors:
            if s.voted_out_order and 0 < s.voted_out_order < season.num_players:
                elim_at_step[s.voted_out_order] = s

        # For each user, record which steps they lost a pick at
        # {user_id: {step: survivor_name}}
        user_eliminations = {u.id: {} for u in users}
        for user in users:
            pick_surv_ids = {p.survivor_id for p in user_picks[user.id]}
            for step, surv in elim_at_step.items():
                if surv.id in pick_surv_ids and step <= effective_as_of:
                    user_eliminations[user.id][step] = surv.name

        for entry in leaderboard_data:
            uid = entry["user"].id
            elims = user_eliminations[uid]
            progression_datasets.append(
                {
                    "name": entry["user"].display_name or entry["user"].username,
                    "points": progression[uid],
                    "eliminations": {str(k): v for k, v in elims.items()},
                }
            )

    breakdown_labels = BREAKDOWN_LABELS

    # Finale celebration data
    is_finished = max_eliminations >= season.num_players
    finale_data = None
    if is_finished and leaderboard_data:
        winner_entry = leaderboard_data[0]
        runner_up = leaderboard_data[1] if len(leaderboard_data) > 1 else None
        margin = (
            round(winner_entry["total_points"] - runner_up["total_points"], 2)
            if runner_up
            else 0
        )

        # Find the Sole Survivor castaway
        sole_survivor = None
        for s in season.survivors:
            if s.voted_out_order == season.num_players:
                sole_survivor = s
                break

        # Best single pick (highest modified_points across all entries)
        best_pick = None
        best_pick_owner = None
        for entry in leaderboard_data:
            for pick in entry["picks"]:
                if pick.get("pick_type_raw") == "sole_survivor":
                    continue
                if (
                    best_pick is None
                    or pick["modified_points"] > best_pick["modified_points"]
                ):
                    best_pick = pick
                    best_pick_owner = (
                        entry["user"].display_name or entry["user"].username
                    )

        finale_data = {
            "winner": winner_entry,
            "margin": margin,
            "sole_survivor": sole_survivor,
            "best_pick": best_pick,
            "best_pick_owner": best_pick_owner,
            "all_entries": leaderboard_data,
        }

    # Past season winner badges: user_id -> [season_number, ...]
    past_winner_badges = {}
    other_seasons = Season.query.filter(
        Season.id != season.id, Season.number < season.number
    ).all()
    for other in other_seasons:
        other_max = max(
            (s.voted_out_order for s in other.survivors if s.voted_out_order), default=0
        )
        if other_max >= other.num_players:
            other_lb, _ = _build_leaderboard(other)
            if other_lb:
                winner_uid = other_lb[0]["user"].id
                past_winner_badges.setdefault(winner_uid, []).append(other.number)
    # Sort badge lists by season number
    for uid in past_winner_badges:
        past_winner_badges[uid].sort()

    # Chart data: point breakdown per player (stacked bar) + confessional share (donut)
    breakdown_chart = []
    confessional_chart = []
    if leaderboard_data:
        # Aggregate breakdowns across all picks per player
        all_keys = list(breakdown_labels.keys())
        for entry in leaderboard_data:
            name = entry["user"].display_name or entry["user"].username
            agg = {k: 0 for k in all_keys}
            for pick in entry["picks"]:
                for k, v in pick.get("breakdown", {}).items():
                    if k in agg:
                        agg[k] += v
            breakdown_chart.append({"name": name, "breakdown": agg})

            conf = entry.get("team_stats", {}).get("Confessionals", 0)
            if conf:
                confessional_chart.append({"name": name, "value": conf})

    return render_template(
        "leaderboard.html",
        season=season,
        leaderboard=leaderboard_data,
        scoring_system_name=scoring_name,
        active_count=active_count,
        max_eliminations=max_eliminations,
        as_of=as_of,
        effective_as_of=effective_as_of,
        win_pcts=win_pcts,
        projected_win_pcts=projected_win_pcts,
        odds_pending=odds_pending,
        stat_boards=stat_boards,
        breakdown_labels=breakdown_labels,
        progression_datasets=progression_datasets,
        progression_labels=progression_labels,
        timeline_points=timeline_points,
        active_timeline_idx=active_timeline_idx,
        is_finished=is_finished,
        finale_data=finale_data,
        past_winner_badges=past_winner_badges,
        breakdown_chart=breakdown_chart,
        confessional_chart=confessional_chart,
    )


def _build_stat_boards(season):
    """Build stat leaderboards for a season's survivors (reads current object state)."""
    survivors = Survivor.query.filter_by(season_id=season.id).all()

    def top_by(attr, label, limit=10, fmt=None):
        ranked = sorted(survivors, key=lambda s: getattr(s, attr) or 0, reverse=True)
        return {
            "label": label,
            "rows": [
                {
                    "survivor": s,
                    "value": fmt(getattr(s, attr)) if fmt else getattr(s, attr),
                }
                for s in ranked[:limit]
                if getattr(s, attr)
            ],
        }

    def _voting_accuracy_board():
        """Build voting accuracy board (correct votes / votes cast)."""
        rows = []
        for s in survivors:
            pct = _voting_accuracy(s.correct_votes, s.votes_cast)
            if pct is not None:
                rows.append(
                    {
                        "survivor": s,
                        "value": f"{pct:.0f}% ({s.correct_votes}/{s.votes_cast})",
                        "_sort": pct,
                    }
                )
        rows.sort(key=lambda r: r["_sort"], reverse=True)
        return {"label": "Voting Accuracy", "rows": rows[:10]}

    stat_boards = [
        top_by("confessional_count", "Confessionals"),
        top_by("confessional_time", "Screen Time", fmt=_fmt_time),
        top_by("individual_immunity_wins", "Individual Immunity Wins"),
        top_by("tribal_immunity_wins", "Tribal Immunity Wins"),
        top_by("reward_wins", "Challenge Wins"),
        top_by("tribal_councils_attended", "Tribal Councils Attended"),
        _voting_accuracy_board(),
        top_by("correct_votes", "Correct Votes"),
        top_by("votes_received", "Votes Received at Tribal"),
        top_by("votes_nullified", "Votes Nullified (by Idol)"),
        top_by("idols_found", "Idols Found"),
        top_by("advantages_found", "Advantages Found"),
        top_by("advantages_played", "Advantages Played"),
        top_by("sit_outs", "Challenge Sit-Outs"),
    ]

    # Add jury votes board if any finalists have data
    jury_board = top_by("jury_votes_received", "Jury Votes Received")
    if jury_board["rows"]:
        stat_boards.append(jury_board)

    # Add performance score board
    perf_board = top_by(
        "performance_score",
        "survivoR Performance Score",
        fmt=lambda v: f"{v:.3f}" if v else "",
    )
    if perf_board["rows"]:
        stat_boards.append(perf_board)

    return [b for b in stat_boards if b["rows"]]


@main_bp.route("/stats/<int:season_id>")
def season_stats(season_id):
    season = Season.query.get_or_404(season_id)
    return render_template(
        "stats.html", season=season, stat_boards=_build_stat_boards(season)
    )


_compare_cache = {}  # {cache_key: (timestamp, data)}
_COMPARE_CACHE_TTL = 300  # 5 minutes


def _compare_cache_key(season):
    """Build cache key from season state + picks."""
    import hashlib

    state = "|".join(f"{s.id}:{s.voted_out_order}" for s in season.survivors)
    # Hidden wildcards are left out, so the key changes at the reveal
    picks = "|".join(
        f"{p.user_id}:{p.survivor_id}:{p.pick_type}"
        for p in wildcards.scored_picks(
            Pick.query.filter_by(season_id=season.id).order_by(Pick.id).all(),
            wildcards.are_hidden(season),
        )
    )
    config = season.scoring_config or "{}"
    raw = f"{season.id}:{state}:{picks}:{config}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _build_compare_data(season):
    """Build comparison data for all presets. Cached separately from the route."""
    import time as _time

    max_elim = max(
        (s.voted_out_order for s in season.survivors if s.voted_out_order), default=0
    )

    key = _compare_cache_key(season)
    if key in _compare_cache:
        ts, cached = _compare_cache[key]
        if _time.time() - ts < _COMPARE_CACHE_TTL:
            return cached

    presets = {
        "Current Settings": season.get_scoring_config(),
        "Default": DEFAULT_CONFIG,
        "Legacy (pre-site)": LEGACY_CONFIG,
    }

    users = User.query.join(Pick).filter(Pick.season_id == season.id).distinct().all()
    wildcards_hidden = wildcards.are_hidden(season)
    user_picks = {}
    for user in users:
        user_picks[user.id] = wildcards.scored_picks(
            Pick.query.filter_by(user_id=user.id, season_id=season.id).all(),
            wildcards_hidden,
        )

    # Find merge episode for accurate replacement scoring
    cmp_merge = season.merge_threshold  # None if merge data unknown
    current_elim_count = max_elim
    merge_episode = None
    if cmp_merge is not None:
        for s in season.survivors:
            if (
                s.voted_out_order
                and s.voted_out_order == cmp_merge
                and s.elimination_episode
            ):
                merge_episode = s.elimination_episode
                break

    comparisons = []
    for preset_name, config in presets.items():
        scoring = get_scoring_system("Classic", config)

        # Pre-fetch sole survivor picks for SS bonus
        user_ss_picks = {}
        for user in users:
            user_ss_picks[user.id] = SoleSurvivorPick.query.filter_by(
                user_id=user.id, season_id=season.id
            ).all()

        # Final standings
        entries = []
        for user in users:
            total = 0
            for pick in user_picks[user.id]:
                if pick.pick_type == "wildcard" and current_elim_count == 0:
                    continue
                if pick.pick_type in ("pmr_w", "pmr_d") and (
                    cmp_merge is None or current_elim_count < cmp_merge
                ):
                    continue
                stat_overrides = None
                if pick.pick_type in ("pmr_w", "pmr_d"):
                    stat_overrides = compute_stat_overrides(
                        pick.survivor, merge_episode
                    )
                modified, breakdown = scoring.score_pick(
                    pick.survivor, season, pick.pick_type, stat_overrides
                )
                total += modified
            ss_streak = calculate_ss_streak(user_ss_picks[user.id], season)
            total += scoring.calculate_sole_survivor_bonus(ss_streak)
            entries.append({"user": user, "total_points": total})
        entries.sort(key=lambda x: x["total_points"], reverse=True)

        # Build progression data (step through each elimination)
        progression = {u.id: [] for u in users}
        for step in range(max_elim + 1):
            # Final step = current state — use raw DB values
            restore = _apply_as_of(season, step) if step < max_elim else lambda: None
            for user in users:
                total = 0
                for pick in user_picks[user.id]:
                    if pick.pick_type == "wildcard" and step == 0:
                        continue
                    if pick.pick_type in ("pmr_w", "pmr_d") and (
                        cmp_merge is None or step < cmp_merge
                    ):
                        continue
                    stat_overrides = None
                    if pick.pick_type in ("pmr_w", "pmr_d"):
                        stat_overrides = compute_stat_overrides(
                            pick.survivor, merge_episode
                        )
                    modified, _ = scoring.score_pick(
                        pick.survivor, season, pick.pick_type, stat_overrides
                    )
                    total += modified
                ss_streak = calculate_ss_streak(user_ss_picks[user.id], season)
                total += scoring.calculate_sole_survivor_bonus(ss_streak)
                progression[user.id].append(round(total, 2))
            restore()

        # Build chart datasets (ordered by final rank)
        datasets = []
        for entry in entries:
            uid = entry["user"].id
            datasets.append(
                {
                    "name": entry["user"].display_name or entry["user"].username,
                    "points": progression[uid],
                }
            )

        # Build point breakdown per player (for stacked bar chart)
        breakdown_data = []
        for entry in entries:
            uid = entry["user"].id
            agg = {}
            for pick in user_picks[uid]:
                stat_overrides = None
                if pick.pick_type in ("pmr_w", "pmr_d"):
                    stat_overrides = compute_stat_overrides(
                        pick.survivor, merge_episode
                    )
                modified, bd = scoring.score_pick(
                    pick.survivor, season, pick.pick_type, stat_overrides
                )
                raw = bd.total
                modifier = modified / raw if raw else 1
                for k, v in bd.items.items():
                    agg[k] = round(agg.get(k, 0) + v * modifier, 2)
            # Add SS streak
            ss_streak = calculate_ss_streak(user_ss_picks[uid], season)
            ss_bonus = scoring.calculate_sole_survivor_bonus(ss_streak)
            if ss_bonus:
                agg["sole_survivor_streak"] = round(ss_bonus, 2)
            breakdown_data.append(
                {
                    "name": entry["user"].display_name or entry["user"].username,
                    "breakdown": agg,
                }
            )

        comparisons.append(
            {
                "name": preset_name,
                "config": config,
                "entries": entries,
                "datasets": datasets,
                "breakdown": breakdown_data,
            }
        )

    labels = []
    for i in range(max_elim + 1):
        if i == 0:
            labels.append("Start")
        elif i == season.num_players:
            labels.append("Sole Survivor")
        else:
            labels.append(f"Elim {i}")
    result = {"comparisons": comparisons, "labels": labels}
    _compare_cache[key] = (_time.time(), result)
    return result


@main_bp.route("/compare/<int:season_id>")
def scoring_compare(season_id):
    """Show leaderboard side-by-side with different scoring configs, including progression charts."""
    season = Season.query.get_or_404(season_id)
    data = _build_compare_data(season)
    return render_template(
        "compare.html",
        season=season,
        comparisons=data["comparisons"],
        labels=data["labels"],
        config_labels=CONFIG_LABELS,
        breakdown_labels=BREAKDOWN_LABELS,
    )


# --- Player: My Team ---


def _save_team_name(user, season, raw):
    """Validate and store a team name (blank clears it). Returns True if saved."""
    name = TeamName.clean(raw)
    if len(name) > TEAM_NAME_MAX_LENGTH:
        flash(f"Team name must be {TEAM_NAME_MAX_LENGTH} characters or fewer.", "error")
        return False
    try:
        TeamName.set_for(user.id, season.id, name)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Saving team name failed (user %s, season %s)", user.id, season.id
        )
        flash("Could not save the team name. Try again.", "error")
        return False
    return True


@main_bp.route("/my-team")
@login_required
def my_team():
    season = Season.get_active()
    if season:
        return redirect(url_for("main.my_team_season", season_id=season.id))
    return render_template("my_team.html", season=None)


@main_bp.route("/my-team/<int:season_id>")
@login_required
def my_team_season(season_id):
    season = db.get_or_404(Season, season_id)

    leaderboard_data, _ = _build_leaderboard(season)
    entry = None
    rank = None
    for i, candidate in enumerate(leaderboard_data, start=1):
        if candidate["user"].id == current_user.id:
            entry, rank = candidate, i
            break

    my_seasons = (
        Season.query.join(Pick)
        .filter(Pick.user_id == current_user.id)
        .distinct()
        .order_by(Season.number.desc())
        .all()
    )

    return render_template(
        "my_team.html",
        season=season,
        entry=entry,
        rank=rank,
        team_count=len(leaderboard_data),
        team_name=TeamName.for_season(season.id).get(current_user.id),
        can_rename=season.is_active or current_user.is_admin,
        wildcard=wildcards.player_view(season, current_user),
        my_seasons=my_seasons,
        team_name_max_length=TEAM_NAME_MAX_LENGTH,
    )


@main_bp.route("/my-team/<int:season_id>/name", methods=["POST"])
@login_required
def my_team_name(season_id):
    season = db.get_or_404(Season, season_id)
    if not season.is_active and not current_user.is_admin:
        flash("Team names can only be changed for the active season.", "error")
    elif _save_team_name(current_user, season, request.form.get("team_name")):
        flash("Team name saved!", "success")
    return redirect(url_for("main.my_team_season", season_id=season.id))


@main_bp.route("/my-team/<int:season_id>/wildcard", methods=["POST"])
@login_required
def my_team_wildcard(season_id):
    """A player sets, changes, or removes their own wildcard."""
    season = db.get_or_404(Season, season_id)
    choice = request.form.get("survivor_id", "")
    error = wildcards.set_pick(season, current_user, choice)
    if error:
        flash(error, "error")
    elif choice.strip():
        flash("Wildcard saved!", "success")
    else:
        flash("Wildcard removed.", "success")
    return redirect(url_for("main.my_team_season", season_id=season.id))


# --- Admin: Settings ---


@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    # Admin-only: display names key the pick export files, so a player taking
    # another player's name would merge their picks on re-import. Players
    # personalise through their team name instead.
    denied = _require_admin()
    if denied:
        return denied

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip() or None
        problem = display_name and _name_problem(display_name, current_user)
        if problem:
            flash(problem, "error")
            return redirect(url_for("main.settings"))
        current_user.display_name = display_name
        db.session.commit()
        flash("Settings saved!", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html")


# --- Admin: Picks ---


@main_bp.route("/admin/picks/<int:season_id>", methods=["GET", "POST"])
@login_required
def admin_picks(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivors = (
        Survivor.query.filter_by(season_id=season.id).order_by(Survivor.name).all()
    )
    users = User.query.order_by(User.username).all()

    if request.method == "POST":
        target_user_id = int(request.form["user_id"])

        # Clear existing picks for this user+season
        Pick.query.filter_by(user_id=target_user_id, season_id=season.id).delete()

        # Draft picks
        draft_ids = request.form.getlist("draft")
        for i, sid in enumerate(draft_ids):
            db.session.add(
                Pick(
                    user_id=target_user_id,
                    season_id=season.id,
                    survivor_id=int(sid),
                    pick_type="draft",
                    pick_order=i + 1,
                )
            )

        # Wildcard
        wc = request.form.get("wildcard")
        if wc:
            db.session.add(
                Pick(
                    user_id=target_user_id,
                    season_id=season.id,
                    survivor_id=int(wc),
                    pick_type="wildcard",
                )
            )

        # Post-jury replacement (wildcard pts)
        pmr_w = request.form.get("pmr_w")
        if pmr_w:
            db.session.add(
                Pick(
                    user_id=target_user_id,
                    season_id=season.id,
                    survivor_id=int(pmr_w),
                    pick_type="pmr_w",
                )
            )

        # Post-jury replacement (draft pts)
        pmr_d = request.form.get("pmr_d")
        if pmr_d:
            db.session.add(
                Pick(
                    user_id=target_user_id,
                    season_id=season.id,
                    survivor_id=int(pmr_d),
                    pick_type="pmr_d",
                )
            )

        # Sole Survivor pick with episode tracking
        ss_survivor = request.form.get("sole_survivor")
        ss_episode = request.form.get("ss_episode", "").strip()
        if ss_survivor and ss_episode:
            ss_episode_int = int(ss_episode)
            # Check if pick already exists for this user/season/episode
            existing = SoleSurvivorPick.query.filter_by(
                user_id=target_user_id, season_id=season.id, episode=ss_episode_int
            ).first()
            if existing:
                existing.survivor_id = int(ss_survivor)
            else:
                db.session.add(
                    SoleSurvivorPick(
                        user_id=target_user_id,
                        season_id=season.id,
                        survivor_id=int(ss_survivor),
                        episode=ss_episode_int,
                    )
                )

        db.session.commit()
        target_user = db.session.get(User, target_user_id)
        flash(
            f"Picks saved for {target_user.display_name or target_user.username}!",
            "success",
        )
        return redirect(url_for("main.admin_picks", season_id=season.id))

    # Load current picks per user
    picks_by_user = {}
    ss_picks_by_user = {}
    for user in users:
        user_picks = Pick.query.filter_by(user_id=user.id, season_id=season.id).all()
        picks_by_user[user.id] = {
            "draft": [p.survivor_id for p in user_picks if p.pick_type == "draft"],
            "wildcard": next(
                (p.survivor_id for p in user_picks if p.pick_type == "wildcard"), None
            ),
            "pmr_w": next(
                (p.survivor_id for p in user_picks if p.pick_type == "pmr_w"), None
            ),
            "pmr_d": next(
                (p.survivor_id for p in user_picks if p.pick_type == "pmr_d"), None
            ),
        }
        user_ss = (
            SoleSurvivorPick.query.filter_by(user_id=user.id, season_id=season.id)
            .order_by(SoleSurvivorPick.episode)
            .all()
        )
        ss_picks_by_user[user.id] = [
            {
                "id": p.id,
                "survivor_id": p.survivor_id,
                "episode": p.episode,
                "survivor_name": p.survivor.name,
            }
            for p in user_ss
        ]

    return render_template(
        "admin/picks.html",
        season=season,
        survivors=survivors,
        users=users,
        picks_by_user=picks_by_user,
        ss_picks_by_user=ss_picks_by_user,
    )


@main_bp.route("/admin/picks/<int:season_id>/ss/<int:pick_id>/delete", methods=["POST"])
@login_required
def admin_delete_ss_pick(season_id, pick_id):
    denied = _require_admin()
    if denied:
        return denied
    pick = db.session.get(SoleSurvivorPick, pick_id)
    if pick and pick.season_id == season_id:
        db.session.delete(pick)
        db.session.commit()
        flash("Sole Survivor pick deleted.", "success")
    return redirect(url_for("main.admin_picks", season_id=season_id))


# --- Admin: Players ---


def _player_label(player):
    return player.display_name or player.username


def _name_problem(name, player=None):
    """Why `name` cannot be a player's name, or None if it can.

    Names key the pick export files and seed.py finds players by the
    lowercased name, so two players may not share a name in any case.
    """
    if not name:
        return "Name is required."
    if len(name) > User.display_name.type.length:
        return f"Name must be {User.display_name.type.length} characters or fewer."
    lowered = name.lower()
    clash = User.query.filter(
        db.or_(
            db.func.lower(User.display_name) == lowered,
            db.func.lower(User.username) == lowered,
        )
    )
    if player is not None:
        clash = clash.filter(User.id != player.id)
    other = clash.first()
    if other:
        return f'"{name}" is already the name of {_player_label(other)}.'
    return None


def _is_plausible_email(email):
    """Loose shape check: one @, something on both sides, no whitespace."""
    local, sep, domain = email.partition("@")
    return bool(
        sep
        and local
        and domain
        and "@" not in domain
        and not any(ch.isspace() for ch in email)
        and len(email) <= User.email.type.length
    )


def _email_problem(player, email):
    """Why `player`'s login email cannot become `email`, or None if it can.

    `email` is already normalized; None unlinks. The address the admin logs
    in with stays where it is: unlinking it would make the next admin login
    create a fresh, empty admin row.
    """
    admin_email = current_app.config["ADMIN_EMAIL"]
    if admin_email and player.email == admin_email and email != admin_email:
        return (
            f"{admin_email} is the admin login. Merge this player instead of "
            "unlinking it."
        )
    if email and not _is_plausible_email(email):
        return f'"{email}" does not look like an email address.'
    if email:
        owner = User.query.filter(User.email == email, User.id != player.id).first()
        if owner:
            return f"{email} is already linked to {_player_label(owner)}."
    return None


def _seasons_by_player():
    """Return {user_id: [season numbers they have picks in]}, newest first."""
    rows = (
        db.session.query(Pick.user_id, Season.number)
        .join(Season, Season.id == Pick.season_id)
        .union(
            db.session.query(SoleSurvivorPick.user_id, Season.number).join(
                Season, Season.id == SoleSurvivorPick.season_id
            )
        )
        .all()
    )
    seasons = {}
    for user_id, number in rows:
        seasons.setdefault(user_id, set()).add(number)
    return {uid: sorted(nums, reverse=True) for uid, nums in seasons.items()}


@main_bp.route("/admin/players", defaults={"season_id": None}, methods=["GET", "POST"])
@main_bp.route("/admin/players/<int:season_id>", methods=["GET", "POST"])
@login_required
def admin_players(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = db.session.get(Season, season_id) if season_id else None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        problem = _name_problem(name)
        if problem:
            flash(problem, "error")
        else:
            try:
                db.session.add(User(username=name.lower(), display_name=name))
                db.session.commit()
                flash(f'Player "{name}" added!', "success")
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception("Adding player %r failed", name)
                flash(f'Could not add "{name}".', "error")
        return redirect(url_for("main.admin_players", season_id=season_id))

    players = User.query.order_by(db.func.lower(User.display_name)).all()
    return render_template(
        "admin/players.html",
        players=players,
        season=season,
        seasons_by_player=_seasons_by_player(),
        admin_email=current_app.config["ADMIN_EMAIL"],
        team_names=TeamName.for_season(season.id) if season else {},
        team_name_max_length=TEAM_NAME_MAX_LENGTH,
    )


@main_bp.route("/admin/player/<int:user_id>")
@login_required
def admin_player_detail(user_id):
    """Everything about one player across all seasons."""
    denied = _require_admin()
    if denied:
        return denied

    player = db.get_or_404(User, user_id)

    counts = {}  # {season_id: {pick_type: n}}
    for season_id, pick_type, n in (
        db.session.query(Pick.season_id, Pick.pick_type, db.func.count())
        .filter(Pick.user_id == player.id)
        .group_by(Pick.season_id, Pick.pick_type)
    ):
        counts.setdefault(season_id, {})[pick_type] = n
    ss_counts = dict(
        db.session.query(SoleSurvivorPick.season_id, db.func.count())
        .filter(SoleSurvivorPick.user_id == player.id)
        .group_by(SoleSurvivorPick.season_id)
        .all()
    )
    team_names = {
        row.season_id: row.name for row in TeamName.query.filter_by(user_id=player.id)
    }
    season_ids = set(counts) | set(ss_counts) | set(team_names)
    seasons = (
        Season.query.filter(Season.id.in_(season_ids))
        .order_by(Season.number.desc())
        .all()
        if season_ids
        else []
    )
    rows = [
        {
            "season": s,
            "draft": counts.get(s.id, {}).get("draft", 0),
            "wildcard": counts.get(s.id, {}).get("wildcard", 0),
            "replacement": counts.get(s.id, {}).get("pmr_w", 0)
            + counts.get(s.id, {}).get("pmr_d", 0),
            "sole_survivor": ss_counts.get(s.id, 0),
            "team_name": team_names.get(s.id, ""),
        }
        for s in seasons
    ]
    others = (
        User.query.filter(User.id != player.id)
        .order_by(db.func.lower(User.display_name))
        .all()
    )
    admin_email = current_app.config["ADMIN_EMAIL"]
    return render_template(
        "admin/player_detail.html",
        player=player,
        rows=rows,
        others=others,
        is_admin_login=bool(admin_email) and player.email == admin_email,
        is_you=player.id == current_user.id,
        team_name_max_length=TEAM_NAME_MAX_LENGTH,
    )


@main_bp.route("/admin/player/<int:user_id>/edit", methods=["POST"])
@login_required
def admin_player_edit(user_id):
    """Rename a player and set the email they log in with."""
    denied = _require_admin()
    if denied:
        return denied

    player = db.get_or_404(User, user_id)
    back = redirect(url_for("main.admin_player_detail", user_id=player.id))
    name = request.form.get("name", "").strip()
    email = normalize_email(request.form.get("email"))

    problem = _name_problem(name, player) or _email_problem(player, email)
    if problem:
        flash(problem, "error")
        return back

    try:
        player.display_name = name
        # The admin's own row can be keyed on the admin email (app/auth.py);
        # renaming that username would make the next admin login a new row
        if player.username != current_app.config["ADMIN_EMAIL"]:
            player.username = name.lower()
        player.email = email
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Editing player %s failed", player.id)
        flash(f"Could not save {name}.", "error")
        return back
    flash(f"Saved {name}.", "success")
    return back


@main_bp.route("/admin/players/<int:user_id>/email", methods=["POST"])
@login_required
def admin_player_email(user_id):
    """Link (or unlink) the Cloudflare Access email a player logs in with."""
    denied = _require_admin()
    if denied:
        return denied

    player = db.get_or_404(User, user_id)
    season_id = request.form.get("season_id", type=int)
    back = redirect(url_for("main.admin_players", season_id=season_id))
    name = _player_label(player)

    email = normalize_email(request.form.get("email"))
    problem = _email_problem(player, email)
    if problem:
        flash(problem, "error")
        return back

    try:
        player.email = email
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Linking an email to player %s failed", player.id)
        flash(f"Could not save the email for {name}.", "error")
        return back

    if email:
        flash(f"{name} can now log in as {email}.", "success")
    else:
        flash(f"Email unlinked from {name}.", "success")
    return back


@main_bp.route(
    "/admin/players/<int:user_id>/team-name/<int:season_id>", methods=["POST"]
)
@login_required
def admin_player_team_name(user_id, season_id):
    """Admin override of a player's team name for any season."""
    denied = _require_admin()
    if denied:
        return denied

    player = db.get_or_404(User, user_id)
    season = db.get_or_404(Season, season_id)
    if _save_team_name(player, season, request.form.get("team_name")):
        flash(f"Team name saved for {_player_label(player)}.", "success")
    if request.form.get("back") == "player":
        return redirect(url_for("main.admin_player_detail", user_id=player.id))
    return redirect(url_for("main.admin_players", season_id=season.id))


def _shared_seasons(source, target):
    """Season names both players have picks, Sole Survivor picks or a team name in."""

    def season_ids(user):
        ids = set()
        for model in (Pick, SoleSurvivorPick, TeamName):
            ids.update(
                sid
                for (sid,) in db.session.query(model.season_id)
                .filter(model.user_id == user.id)
                .distinct()
            )
        return ids

    shared = season_ids(source) & season_ids(target)
    if not shared:
        return []
    return [
        s.name or f"Season {s.number}"
        for s in Season.query.filter(Season.id.in_(shared)).order_by(Season.number)
    ]


@main_bp.route("/admin/player/<int:user_id>/merge", methods=["POST"])
@login_required
def admin_player_merge(user_id):
    """Move everything a player has onto another player, then delete them.

    For duplicates: the two rows must not share a season, so no team is
    combined with another, and at most one of them may have a login email.
    """
    denied = _require_admin()
    if denied:
        return denied

    source = db.get_or_404(User, user_id)
    back = redirect(url_for("main.admin_player_detail", user_id=source.id))
    target = db.session.get(User, request.form.get("into", type=int) or 0)
    if target is None:
        flash("Choose the player to merge into.", "error")
        return back
    if target.id == source.id:
        flash("A player cannot be merged into themselves.", "error")
        return back
    if source.id == current_user.id:
        flash(
            "You are logged in as this player. Merge the other player into "
            "this one instead.",
            "error",
        )
        return back
    shared = _shared_seasons(source, target)
    if shared:
        flash(
            f"{_player_label(source)} and {_player_label(target)} both play "
            f"{', '.join(shared)}. Move or delete those picks first.",
            "error",
        )
        return back
    if source.email and target.email:
        flash(
            "Both players have a login email. Unlink the one you do not want first.",
            "error",
        )
        return back

    source_name, target_name = _player_label(source), _player_label(target)
    try:
        moved = {
            model: model.query.filter_by(user_id=source.id).update(
                {"user_id": target.id}, synchronize_session=False
            )
            for model in (Pick, SoleSurvivorPick, TeamName)
        }
        email = source.email
        db.session.delete(source)
        db.session.flush()  # free the email before the target takes it
        if email:
            target.email = email
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Merging player %s into %s failed", user_id, target.id)
        flash(f"Could not merge {source_name} into {target_name}.", "error")
        return back

    flash(
        f"Merged {source_name} into {target_name}: {moved[Pick]} picks, "
        f"{moved[SoleSurvivorPick]} Sole Survivor picks and {moved[TeamName]} "
        "team names moved.",
        "success",
    )
    return redirect(url_for("main.admin_player_detail", user_id=target.id))


@main_bp.route("/admin/players/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_player(user_id):
    denied = _require_admin()
    if denied:
        return denied

    player = db.session.get(User, user_id)
    admin_email = current_app.config["ADMIN_EMAIL"]
    if not player:
        flash("Player not found.", "error")
    elif player.id == current_user.id:
        flash("You cannot delete the player you are logged in as.", "error")
    elif admin_email and player.email == admin_email:
        flash(f"This player holds the admin login ({admin_email}).", "error")
    else:
        name = _player_label(player)
        try:
            Pick.query.filter_by(user_id=player.id).delete()
            SoleSurvivorPick.query.filter_by(user_id=player.id).delete()
            TeamName.query.filter_by(user_id=player.id).delete()
            db.session.delete(player)
            db.session.commit()
            flash(f'Player "{name}" deleted.', "success")
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Deleting player %s failed", user_id)
            flash(f'Could not delete "{name}".', "error")
    return redirect(url_for("main.admin_players"))


# --- Admin: Seasons ---


@main_bp.route("/admin/seasons", methods=["GET", "POST"])
@login_required
def admin_seasons():
    denied = _require_admin()
    if denied:
        return denied

    if request.method == "POST":
        number = int(request.form["number"])
        if number < 41:
            flash("Only new-era seasons (41+) are supported.", "error")
        elif Season.query.filter_by(number=number).first():
            flash(f"Season {number} already exists.", "error")
        else:
            # Created inactive so the public homepage keeps showing the current
            # season; the admin activates it with the toggle when it is ready.
            season = Season(number=number, name=f"Season {number}", is_active=False)
            db.session.add(season)
            db.session.commit()

            # Auto-fetch from survivoR
            try:
                download_survivor_data()
                count, day_warnings = refresh_season(season)
                flash(
                    f"Season {number} created with {count} survivors! It is "
                    "inactive until you activate it from the seasons list.",
                    "success",
                )
                for w in day_warnings:
                    flash(f"Data warning: {w}", "error")

                # Generate images unless unchecked
                if "fetch_images" in request.form:
                    matched = generate_season_images(season)
                    flash(f"Found {matched} headshot images.", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Season created but data fetch failed: {e}", "error")

            return redirect(url_for("main.admin_season_detail", season_id=season.id))

    seasons = Season.query.order_by(Season.number.desc()).all()

    # Label each season's scoring config
    def _cfg_matches(cfg, ref):
        """Check if cfg matches ref on all shared keys (ignore extras)."""
        return all(cfg.get(key) == ref[key] for key in ref)

    scoring_labels = {}
    for season in seasons:
        cfg = season.get_scoring_config()
        if not cfg or _cfg_matches(cfg, DEFAULT_CONFIG):
            scoring_labels[season.id] = "Default"
        elif _cfg_matches(cfg, LEGACY_CONFIG):
            scoring_labels[season.id] = "Legacy"
        else:
            scoring_labels[season.id] = "Custom"

    return render_template(
        "admin/seasons.html", seasons=seasons, scoring_labels=scoring_labels
    )


@main_bp.route("/admin/season/<int:season_id>")
@login_required
def admin_season_detail(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivors = (
        Survivor.query.filter_by(season_id=season.id)
        .order_by(Survivor.voted_out_order.desc(), Survivor.name)
        .all()
    )
    current_config = {**DEFAULT_CONFIG, **season.get_scoring_config()}

    return render_template(
        "admin/season_detail.html",
        season=season,
        survivors=survivors,
        removable_ids=_removable_survivor_ids(survivors),
        scoring_systems=SCORING_SYSTEMS,
        config_labels=CONFIG_LABELS,
        current_config=current_config,
        default_config=DEFAULT_CONFIG,
        legacy_config=LEGACY_CONFIG,
        wildcard=wildcards.admin_view(season),
    )


@main_bp.route("/admin/season/<int:season_id>/wildcard-window", methods=["POST"])
@login_required
def admin_wildcard_window(season_id):
    """Switch wildcard self-service on or off, and override the Episode 2 time.

    The time is normally filled in automatically (app/schedule.py). A time
    that differs from the one shown becomes an override that the automatic
    lookup never replaces; a blank time goes back to automatic.
    """
    denied = _require_admin()
    if denied:
        return denied

    season = db.get_or_404(Season, season_id)
    back = redirect(url_for("main.admin_season_detail", season_id=season.id))
    try:
        starts_at = wildcards.parse_league_time(request.form.get("episode2_starts_at"))
    except ValueError:
        flash("Enter the Episode 2 start as a date and time.", "error")
        return back

    try:
        season.wildcard_self_service = "self_service" in request.form
        if starts_at is None:
            season.episode2_starts_at = None
            season.episode2_manual = False
        elif season.episode2_manual or starts_at != season.episode2_starts_at:
            # Saving the form unchanged keeps an automatic time automatic
            season.episode2_starts_at = starts_at
            season.episode2_manual = True
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Saving the Episode 2 time failed (season %s)", season.id)
        flash("Could not save the Episode 2 time.", "error")
        return back

    schedule.sync_episode2_time(season)

    if not season.wildcard_self_service:
        flash("Wildcard self-service is off for this season.", "success")
    elif wildcards.is_self_service(season):
        flash(
            "Wildcard picks lock "
            f"{wildcards.format_league_time(wildcards.lock_at(season))}.",
            "success",
        )
    else:
        flash(
            "Wildcard self-service starts once Episode 2's time is known. It "
            "is looked up daily for the active season.",
            "success",
        )
    return back


@main_bp.route("/admin/season/<int:season_id>/settings", methods=["POST"])
@login_required
def admin_season_settings(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)

    # Build scoring config from individual toggle inputs
    scoring_config = {}
    for key in DEFAULT_CONFIG:
        form_val = request.form.get(f"scoring_{key}", "").strip()
        if form_val:
            scoring_config[key] = float(form_val)
        else:
            scoring_config[key] = DEFAULT_CONFIG[key]
    season.scoring_config = json.dumps(scoring_config)

    db.session.commit()
    flash("Settings updated!", "success")
    return redirect(url_for("main.admin_season_detail", season_id=season.id))


# --- Admin: Refresh Data ---


@main_bp.route("/admin/season/<int:season_id>/refresh", methods=["POST"])
@login_required
def admin_refresh(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)

    held = hand_entered_survivor_count(season)
    if held:
        flash(
            f"Not refreshed. Season {season.number} has {held} hand-entered "
            "castaway(s), and a refresh would add survivoR's cast beside them "
            "instead of matching them, leaving every castaway in the season "
            "twice. Matching is not built yet.",
            "error",
        )
        return redirect(url_for("main.admin_season_detail", season_id=season.id))

    # Auto-export picks before refresh (so picks are preserved if refresh changes data)
    try:
        export_all_picks()
    except Exception as e:
        logger.warning("Pick export before refresh failed: %s", e)

    try:
        download_survivor_data()
        count, day_warnings = refresh_season(season)
        flash(f"Refreshed {count} survivors from survivoR dataset!", "success")
        for w in day_warnings:
            flash(f"Data warning: {w}", "error")
    except Exception as e:
        db.session.rollback()
        flash(f"Refresh failed: {e}", "error")

    schedule.sync_episode2_time(season)

    # Auto-export picks after refresh (capture any name/id updates)
    try:
        export_all_picks()
    except Exception as e:
        logger.warning("Pick export after refresh failed: %s", e)

    return redirect(url_for("main.admin_season_detail", season_id=season.id))


# --- Admin: Export Picks ---


@main_bp.route("/admin/season/<int:season_id>/export-picks", methods=["POST"])
@login_required
def admin_export_picks(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    try:
        path = export_season_picks(season)
    except OSError as e:
        logger.error("Pick export failed for season %d: %s", season.number, e)
        flash(f"Pick export failed: {e}", "error")
        return redirect(url_for("main.admin_season_detail", season_id=season.id))
    if path:
        flash(f"Exported picks to {path}", "success")
    else:
        flash("No picks to export for this season.", "warning")
    return redirect(url_for("main.admin_season_detail", season_id=season.id))


@main_bp.route("/admin/export-all-picks", methods=["POST"])
@login_required
def admin_export_all_picks():
    denied = _require_admin()
    if denied:
        return denied

    try:
        paths = export_all_picks()
    except OSError as e:
        logger.error("Pick export failed: %s", e)
        flash(f"Pick export failed: {e}", "error")
        return redirect(url_for("main.admin_seasons"))
    if paths:
        flash(f"Exported picks for {len(paths)} season(s).", "success")
    else:
        flash("No picks to export.", "warning")
    return redirect(url_for("main.admin_seasons"))


# --- Admin: Toggle Active / Delete Season ---


@main_bp.route("/admin/season/<int:season_id>/toggle-active", methods=["POST"])
@login_required
def admin_toggle_active(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    if season.is_active:
        season.is_active = False
    else:
        Season.query.filter(Season.id != season.id).update({"is_active": False})
        season.is_active = True
    db.session.commit()
    schedule.sync_episode2_time(season)
    flash(
        f"Season {season.number} {'activated' if season.is_active else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("main.admin_seasons"))


@main_bp.route("/admin/season/<int:season_id>/delete", methods=["POST"])
@login_required
def admin_delete_season(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    season_name = season.name or f"Season {season.number}"

    # Delete picks, sole survivor picks, team names, survivors, then season
    from .models import Pick, SoleSurvivorPick

    Pick.query.filter_by(season_id=season.id).delete()
    SoleSurvivorPick.query.filter_by(season_id=season.id).delete()
    TeamName.query.filter_by(season_id=season.id).delete()
    Survivor.query.filter_by(season_id=season.id).delete()
    db.session.delete(season)
    db.session.commit()
    flash(f"{season_name} deleted.", "success")
    return redirect(url_for("main.admin_seasons"))


# --- Admin: Survivors ---

# Survivor.name's column width. A castaway's name is also how picks are
# exported and how a hand-entered castaway will later be matched to survivoR,
# so names have to be unique within a season.
SURVIVOR_NAME_MAX_LENGTH = 100


def _picked_survivor_ids(survivor_ids):
    """Which of these castaways a player has picked, in two queries, never N+1.

    Matched on survivor_id alone rather than also on season_id, so a pick whose
    season_id disagrees with its castaway's still counts as a pick.
    """
    if not survivor_ids:
        return set()
    picked = set()
    for model in (Pick, SoleSurvivorPick):
        picked.update(
            sid
            for (sid,) in db.session.query(model.survivor_id)
            .filter(model.survivor_id.in_(survivor_ids))
            .distinct()
        )
    return picked


def _removable_survivor_ids(survivors):
    """Ids of hand-entered castaways nobody has picked, which Remove undoes."""
    hand_entered = [s.id for s in survivors if s.is_hand_entered]
    return set(hand_entered) - _picked_survivor_ids(hand_entered)


@main_bp.route("/admin/season/<int:season_id>/survivors/add", methods=["POST"])
@login_required
def admin_add_survivor(season_id):
    """Add one castaway to a season by hand, with an optional headshot.

    survivoR publishes a season's cast at its premiere, so before then this is
    the only way to have castaways to draft. The row is created with no
    castaway_id (see Survivor.is_hand_entered), which is what marks it as not
    yet matched to the dataset.
    """
    denied = _require_admin()
    if denied:
        return denied

    season = db.get_or_404(Season, season_id)
    back = redirect(url_for("main.admin_season_detail", season_id=season.id))

    name = " ".join((request.form.get("name") or "").split())
    if not name:
        flash("Enter the castaway's name.", "error")
        return back
    if len(name) > SURVIVOR_NAME_MAX_LENGTH:
        flash(
            f"A castaway's name can be at most {SURVIVOR_NAME_MAX_LENGTH} characters.",
            "error",
        )
        return back
    clash = Survivor.query.filter(
        Survivor.season_id == season.id,
        db.func.lower(Survivor.name) == name.lower(),
    ).first()
    if clash:
        flash(
            f"{clash.name} is already in {season.name or f'season {season.number}'}.",
            "error",
        )
        return back

    # Stored before the row is created, so a bad image adds nobody at all
    image_url = None
    upload = request.files.get("headshot")
    if upload and upload.filename:
        try:
            image_url = store_uploaded_headshot(season.number, upload.stream)
        except ValueError as e:
            flash(str(e), "error")
            return back

    try:
        db.session.add(
            Survivor(
                season_id=season.id,
                name=name,
                voted_out_order=0,  # still in the game
                image_url=image_url,
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Adding castaway %r to season %s failed", name, season.id)
        flash(f"Could not add {name}.", "error")
        return back

    flash(f"Added {name}.", "success")
    return back


@main_bp.route(
    "/admin/season/<int:season_id>/survivors/<int:survivor_id>/remove",
    methods=["POST"],
)
@login_required
def admin_remove_survivor(season_id, survivor_id):
    """Undo a hand entry: delete a castaway nobody has picked.

    Only ever an undo of admin_add_survivor. It refuses a castaway that came
    from survivoR, and refuses one that any player has picked, because a pick
    is the one thing that must survive: nothing in this feature deletes and
    recreates a Survivor row.
    """
    denied = _require_admin()
    if denied:
        return denied

    season = db.get_or_404(Season, season_id)
    survivor = db.session.get(Survivor, survivor_id)
    if survivor is None or survivor.season_id != season.id:
        abort(404)
    back = redirect(url_for("main.admin_season_detail", season_id=season.id))

    if not survivor.is_hand_entered:
        flash(
            f"{survivor.name} came from survivoR, so they cannot be removed here.",
            "error",
        )
        return back
    if _picked_survivor_ids([survivor.id]):
        flash(
            f"{survivor.name} is on someone's team. Change the picks first, "
            "under Manage Picks.",
            "error",
        )
        return back

    name = survivor.name
    try:
        db.session.delete(survivor)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Removing castaway %s failed", survivor_id)
        flash(f"Could not remove {name}.", "error")
        return back

    flash(f"Removed {name}.", "success")
    return back


@main_bp.route("/admin/season/<int:season_id>/survivors", methods=["POST"])
@login_required
def admin_update_survivors(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivor_ids = request.form.getlist("survivor_id")

    for sid in survivor_ids:
        survivor = db.session.get(Survivor, int(sid))
        if survivor and survivor.season_id == season.id:
            survivor.tribe = request.form.get(f"tribe_{sid}", survivor.tribe)
            survivor.voted_out_order = int(request.form.get(f"voted_out_{sid}", 0))
            survivor.made_jury = f"jury_{sid}" in request.form
            placement = request.form.get(f"placement_{sid}", "").strip()
            survivor.placement = int(placement) if placement else None

    db.session.commit()
    flash("Survivors updated!", "success")
    return redirect(url_for("main.admin_season_detail", season_id=season.id))
