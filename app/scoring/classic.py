from .base import PointBreakdown, ScoringSystem

# Legacy scoring (pre-site): flat tribals, merge bonus, placement only
LEGACY_CONFIG = {
    "tribal_base": None,  # flat mode
    "tribal_val": 1,
    "post_merge_tribal_val": 1,
    "jury_val": 0,
    "merge_val": 1,
    "final_tribal_val": 0,
    "first_val": 7,
    "second_val": 3,
    "third_val": 1,
    "individual_immunity_val": 0,
    "tribal_immunity_val": 0,
    "idol_found_val": 0,
    "advantage_found_val": 0,
    "idol_play_val": 0,
    "advantage_play_val": 0,
    "sole_survivor_val": 0,
    "wildcard_multiplier": 0.5,
    "replacement_multiplier": 0.5,
    "replacement_deduction": True,
}

# Default scoring config — optimized via analyze_scoring.py (run 6, 103k configs)
DEFAULT_CONFIG = {
    # Flat tribal rates (used when tribal_base is None)
    "tribal_val": 0.5,
    "post_merge_tribal_val": 1,
    # Progressive tribal values (overrides flat rates when tribal_base is set)
    "tribal_base": 0.5,  # starting value of first tribal (None = use flat system)
    "tribal_step": 0,  # per-tribal increase during pre-merge
    "post_merge_step": 0,  # per-tribal increase during post-merge
    "finale_step": 0,  # per-tribal increase during finale
    "finale_size": 5,  # players remaining when finale begins
    # Milestones
    "jury_val": 3,
    "merge_val": 0,
    "final_tribal_val": 2,
    # Placement
    "first_val": 5,
    "second_val": 0,
    "third_val": 0,
    # Individual performance
    "individual_immunity_val": 0,
    "tribal_immunity_val": 0,
    "idol_found_val": 0,
    "advantage_found_val": 0.5,
    "idol_play_val": 1,
    "advantage_play_val": 0,
    # Milestones
    "fire_win_val": 2,
    # Sole Survivor pick (points per tribal survived, only if your pick wins)
    "sole_survivor_val": 1,
    # Pick type modifiers
    "wildcard_multiplier": 0.5,
    "draft_replacement_multiplier": 1,  # for pmr_d
    "wc_replacement_multiplier": 0,  # for pmr_w
    "replacement_deduction": True,  # always deduct pre-merge tribals for replacements
}

# For the admin UI — descriptions for each config key
CONFIG_LABELS = {
    "tribal_val": (
        "Pre-Merge Tribal",
        "Points per pre-merge tribal (flat mode, ignored when Tribal Base is set)",
    ),
    "post_merge_tribal_val": (
        "Post-Merge Tribal",
        "Points per post-merge tribal (flat mode, ignored when Tribal Base is set)",
    ),
    "tribal_base": (
        "Tribal Base Value",
        "Starting point value for first tribal survived (enables progressive mode)",
    ),
    "tribal_step": (
        "Pre-Merge Step",
        "Point increase per additional pre-merge tribal survived",
    ),
    "post_merge_step": (
        "Post-Merge Step",
        "Point increase per additional post-merge tribal survived",
    ),
    "finale_step": (
        "Finale Step",
        "Point increase per additional finale tribal survived",
    ),
    "finale_size": (
        "Finale Size",
        "Number of players remaining when finale episode begins",
    ),
    "jury_val": ("Jury Bonus", "Bonus for making the jury"),
    "merge_val": ("Merge Bonus", "Bonus for making the merge"),
    "final_tribal_val": (
        "Final Tribal Bonus",
        "Bonus for reaching final tribal council",
    ),
    "first_val": ("1st Place", "Winner bonus"),
    "second_val": ("2nd Place", "Runner-up bonus"),
    "third_val": ("3rd Place", "3rd place bonus"),
    "individual_immunity_val": (
        "Individual Immunity Win",
        "Points per individual immunity win",
    ),
    "tribal_immunity_val": ("Tribal Immunity Win", "Points per tribal immunity win"),
    "idol_found_val": ("Idol Found", "Points per hidden immunity idol found"),
    "advantage_found_val": ("Advantage Found", "Points per advantage found (non-idol)"),
    "idol_play_val": ("Idol Played", "Points per idol played"),
    "advantage_play_val": ("Advantage Played", "Points per non-idol advantage played"),
    "fire_win_val": (
        "Fire Challenge Win",
        "Bonus for winning the final 4 fire-making challenge",
    ),
    "sole_survivor_val": (
        "Sole Survivor Pick",
        "Points per consecutive episode in winner streak (must include finale)",
    ),
    "wildcard_multiplier": (
        "Wildcard Multiplier",
        "Point multiplier for wildcard picks (e.g. 0.5 = half)",
    ),
    "draft_replacement_multiplier": (
        "Replacement (D) Multiplier",
        "Point multiplier for draft replacement picks",
    ),
    "wc_replacement_multiplier": (
        "Replacement (W) Multiplier",
        "Point multiplier for wildcard replacement picks",
    ),
    "replacement_deduction": (
        "Replacement Deduction",
        "Subtract pre-merge tribals from replacement picks (1=on, 0=off)",
    ),
}


class ClassicScoring(ScoringSystem):
    """Modular scoring system with toggleable components.

    Each component's point value can be set to 0 to disable it.
    Configure via season scoring_config JSON.
    """

    def __init__(self, **kwargs):
        self.config = {**DEFAULT_CONFIG, **kwargs}

    @property
    def name(self):
        return "Classic"

    @property
    def description(self):
        return "Configurable scoring with toggleable components"

    def _compute_tribal_points(self, tribals_survived, season):
        """Compute tribal survival points using flat or progressive mode.

        Returns dict of breakdown items.
        """
        c = self.config
        # None means merge hasn't happened yet — treat all tribals as pre-merge
        merge_threshold = (
            season.merge_threshold
            if season.merge_threshold is not None
            else season.num_players
        )

        if c.get("tribal_base") is not None:
            return self._progressive_tribal_points(tribals_survived, season)

        # Flat mode (backward compatible)
        items = {}
        pre_merge_rate = c["tribal_val"]
        post_merge_rate = c.get("post_merge_tribal_val", pre_merge_rate)
        pre_merge_count = min(tribals_survived, merge_threshold)
        post_merge_count = max(0, tribals_survived - merge_threshold)

        if pre_merge_rate and pre_merge_count:
            items["pre_merge_tribal"] = pre_merge_count * pre_merge_rate
        if post_merge_rate and post_merge_count:
            items["post_merge_tribal"] = post_merge_count * post_merge_rate
        return items

    def _progressive_tribal_points(self, tribals_survived, season):
        """Compute progressive tribal points with per-phase growth slopes.

        Pre-merge: tribal N = base + (N-1) * step
        Post-merge: continues from last pre-merge value, grows by post_merge_step
        Finale: continues from last post-merge value, grows by finale_step
        """
        c = self.config
        base = c["tribal_base"]
        step = c.get("tribal_step", 0)
        pm_step = c.get("post_merge_step", 0)
        f_step = c.get("finale_step", 0)

        # None means merge hasn't happened yet — treat all tribals as pre-merge
        merge_threshold = (
            season.merge_threshold
            if season.merge_threshold is not None
            else season.num_players
        )
        finale_size = c.get("finale_size", 5)
        finale_threshold = max(merge_threshold, season.num_players - finale_size)

        pre_count = min(tribals_survived, merge_threshold)
        post_count = min(
            max(0, tribals_survived - merge_threshold),
            max(0, finale_threshold - merge_threshold),
        )
        finale_count = max(0, tribals_survived - finale_threshold)

        items = {}

        # Pre-merge: sum of base + (n-1)*step for n in 1..pre_count
        # = pre_count * base + step * pre_count * (pre_count - 1) / 2
        if pre_count > 0:
            total = pre_count * base + step * pre_count * (pre_count - 1) / 2
            if total:
                items["pre_merge_tribal"] = total

        # Post-merge: each tribal = last_pre_merge_value + n * pm_step (n=1,2,...)
        # last_pre_merge = base + (merge_threshold - 1) * step
        if post_count > 0:
            last_pre = (
                base + (merge_threshold - 1) * step if merge_threshold > 0 else base
            )
            total = post_count * last_pre + pm_step * post_count * (post_count + 1) / 2
            if total:
                items["post_merge_tribal"] = total

        # Finale: each tribal = last_post_merge_value + n * f_step (n=1,2,...)
        if finale_count > 0:
            last_pre = (
                base + (merge_threshold - 1) * step if merge_threshold > 0 else base
            )
            pm_count_full = max(0, finale_threshold - merge_threshold)
            last_post = last_pre + pm_count_full * pm_step
            total = (
                finale_count * last_post
                + f_step * finale_count * (finale_count + 1) / 2
            )
            if total:
                items["finale_tribal"] = total

        return items

    def calculate_survivor_points(self, survivor, season):
        c = self.config
        breakdown = PointBreakdown()

        # None means merge hasn't happened yet — treat all tribals as pre-merge
        merge_threshold = (
            season.merge_threshold
            if season.merge_threshold is not None
            else season.num_players
        )

        tribals = season.compute_tribals_survived(survivor)

        # Tribal survival points (flat or progressive)
        breakdown.items.update(self._compute_tribal_points(tribals, season))

        # Milestones based on how far they got
        elim_order = survivor.voted_out_order or 0
        past_merge = (
            elim_order > merge_threshold if elim_order else tribals > merge_threshold
        )

        if c["jury_val"] and survivor.made_jury:
            breakdown.items["jury"] = c["jury_val"]

        if c["merge_val"] and past_merge:
            breakdown.items["merge"] = c["merge_val"]

        # Placement (only for eliminated survivors with final positions)
        if elim_order > 0:
            if elim_order == season.num_players:
                if c["first_val"]:
                    breakdown.items["placement"] = c["first_val"]
                if c["final_tribal_val"]:
                    breakdown.items["final_tribal"] = c["final_tribal_val"]
            elif elim_order == season.num_players - 1:
                if c["second_val"]:
                    breakdown.items["placement"] = c["second_val"]
                if c["final_tribal_val"]:
                    breakdown.items["final_tribal"] = c["final_tribal_val"]
            elif elim_order == season.num_players - 2:
                if c["third_val"]:
                    breakdown.items["placement"] = c["third_val"]
                if c["final_tribal_val"]:
                    breakdown.items["final_tribal"] = c["final_tribal_val"]

        # Performance bonuses (work for both active and eliminated)
        if c["individual_immunity_val"] and survivor.individual_immunity_wins:
            breakdown.items["individual_immunity"] = (
                survivor.individual_immunity_wins * c["individual_immunity_val"]
            )

        if c["tribal_immunity_val"] and survivor.tribal_immunity_wins:
            breakdown.items["tribal_immunity"] = (
                survivor.tribal_immunity_wins * c["tribal_immunity_val"]
            )

        if c["idol_found_val"] and survivor.idols_found:
            breakdown.items["idols_found"] = survivor.idols_found * c["idol_found_val"]

        if c["advantage_found_val"] and survivor.advantages_found:
            breakdown.items["advantages_found"] = (
                survivor.advantages_found * c["advantage_found_val"]
            )

        if c["idol_play_val"] and getattr(survivor, "idols_played", 0):
            breakdown.items["idol_plays"] = survivor.idols_played * c["idol_play_val"]

        if c["advantage_play_val"] and survivor.advantages_played:
            breakdown.items["advantage_plays"] = (
                survivor.advantages_played * c["advantage_play_val"]
            )

        if c.get("fire_win_val") and getattr(survivor, "won_fire", False):
            breakdown.items["fire_win"] = c["fire_win_val"]

        return breakdown

    def score_pick(self, survivor, season, pick_type, stat_overrides=None):
        """Score a single pick with accurate replacement handling.

        For replacement picks (pmr_w, pmr_d) with stat_overrides, temporarily
        applies post-merge-only stat values, recalculates, and removes pre-merge
        tribal and merge bonuses from the breakdown. Falls back to flat deduction
        when stat_overrides is not available.

        Returns (modified_points, breakdown).
        """
        c = self.config

        if pick_type in ("pmr_w", "pmr_d") and stat_overrides:
            # Accurate replacement scoring: use post-merge-only stats
            saved = {}
            for attr, val in stat_overrides.items():
                saved[attr] = getattr(survivor, attr)
                setattr(survivor, attr, val)

            breakdown = self.calculate_survivor_points(survivor, season)

            # Replacement didn't earn pre-merge tribals or merge bonus
            breakdown.items.pop("pre_merge_tribal", None)
            breakdown.items.pop("merge", None)

            mult = (
                c.get("wc_replacement_multiplier", c.get("replacement_multiplier", 0.5))
                if pick_type == "pmr_w"
                else c.get("draft_replacement_multiplier", 1.0)
            )
            modified = breakdown.total * mult

            for attr, val in saved.items():
                setattr(survivor, attr, val)

            return modified, breakdown

        # Standard path: draft, wildcard, or replacement without stat_overrides
        breakdown = self.calculate_survivor_points(survivor, season)

        if pick_type == "draft":
            return breakdown.total, breakdown
        elif pick_type == "wildcard":
            mult = c.get("wildcard_multiplier", 0.5)
            return breakdown.total * mult, breakdown
        else:
            # Replacement fallback (flat deduction)
            modified = self.apply_pick_modifier(
                breakdown.total, pick_type, season.num_players, season.left_at_jury
            )
            return modified, breakdown

    def apply_pick_modifier(self, points, pick_type, num_survivors, left_at_jury):
        c = self.config
        pre_jury = (num_survivors - left_at_jury) if left_at_jury is not None else 0

        if pick_type == "draft":
            return points
        elif pick_type == "wildcard":
            mult = c.get("wildcard_multiplier", 0.5)
            return points * mult
        elif pick_type == "pmr_w":
            mult = c.get(
                "wc_replacement_multiplier", c.get("replacement_multiplier", 0.5)
            )
            deduct = pre_jury if c.get("replacement_deduction", True) else 0
            return (points - deduct) * mult
        elif pick_type == "pmr_d":
            mult = c.get("draft_replacement_multiplier", 1.0)
            deduct = pre_jury if c.get("replacement_deduction", True) else 0
            return (points - deduct) * mult
        return points

    def calculate_sole_survivor_bonus(self, streak_length):
        """Calculate sole survivor bonus from streak length.

        Points = sole_survivor_val × streak_length, where streak_length is the
        number of consecutive episodes ending at the finale where the player
        had the eventual winner picked.
        """
        return streak_length * self.config.get("sole_survivor_val", 0)
