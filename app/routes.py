import json

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from .models import db, Season, Survivor, Pick, User, SoleSurvivorPick, calculate_ss_streak
from .scoring import get_scoring_system, SCORING_SYSTEMS
from .scoring.classic import DEFAULT_CONFIG, CONFIG_LABELS
from .data import download_survivor_data, refresh_season
from .predictions import calculate_win_probabilities

main_bp = Blueprint('main', __name__)

PICK_TYPE_LABELS = {
    'draft': 'Draft',
    'wildcard': 'Wildcard',
    'pmr_w': 'Replacement (½)',
    'pmr_d': 'Replacement',
}


def _require_admin():
    """Return a redirect response if current user is not admin, else None."""
    if not current_user.is_authenticated or not current_user.is_admin:
        flash('Admin access required.', 'error')
        return redirect(url_for('main.index'))
    return None


def _build_leaderboard(season):
    """Build leaderboard data for a season. Reads current state of survivor objects."""
    scoring = get_scoring_system(season.scoring_system, season.get_scoring_config())

    users = (User.query.join(Pick).filter(Pick.season_id == season.id)
             .distinct().all())
    leaderboard_data = []

    for user in users:
        picks = Pick.query.filter_by(user_id=user.id, season_id=season.id).all()
        total = 0
        pick_details = []

        for pick in picks:
            survivor = pick.survivor
            breakdown = scoring.calculate_survivor_points(survivor, season)
            modified = scoring.apply_pick_modifier(
                breakdown.total, pick.pick_type,
                season.num_players, season.left_at_jury
            )
            total += modified
            pick_details.append({
                'survivor': survivor.name,
                'full_name': survivor.full_name,
                'survivor_image': survivor.image_url,
                'tribe_color': survivor.tribe_color,
                'tribe': survivor.tribe,
                'stats_url': survivor.stats_url,
                'result': survivor.result,
                'pick_type': PICK_TYPE_LABELS.get(pick.pick_type, pick.pick_type),
                'pick_type_raw': pick.pick_type,
                'base_points': breakdown.total,
                'modified_points': modified,
                'breakdown': breakdown.items,
                'eliminated': survivor.voted_out_order > 0,
            })

        # Sole Survivor streak bonus (separate from draft/wildcard picks)
        ss_picks = SoleSurvivorPick.query.filter_by(
            user_id=user.id, season_id=season.id).all()
        ss_streak = calculate_ss_streak(ss_picks, season)
        ss_bonus = scoring.calculate_sole_survivor_bonus(ss_streak)
        if ss_bonus > 0 or ss_picks:
            # Show the current SS pick in the pick list
            current_ss = max(ss_picks, key=lambda p: p.episode) if ss_picks else None
            if current_ss:
                survivor = current_ss.survivor
                pick_details.append({
                    'survivor': survivor.name,
                    'full_name': survivor.full_name,
                    'survivor_image': survivor.image_url,
                    'tribe_color': survivor.tribe_color,
                    'tribe': survivor.tribe,
                    'stats_url': survivor.stats_url,
                    'result': survivor.result,
                    'pick_type': f'SS Pick ({ss_streak} ep streak)',
                    'pick_type_raw': 'sole_survivor',
                    'base_points': ss_bonus,
                    'modified_points': ss_bonus,
                    'breakdown': {'sole_survivor_streak': ss_bonus},
                    'eliminated': survivor.voted_out_order > 0,
                })
        total += ss_bonus

        leaderboard_data.append({
            'user': user,
            'total_points': total,
            'picks': sorted(pick_details, key=lambda p: p['modified_points'], reverse=True),
        })

    leaderboard_data.sort(key=lambda x: x['total_points'], reverse=True)
    return leaderboard_data, scoring.name


# --- Public routes (no login required) ---

@main_bp.route('/')
def index():
    season = Season.query.filter_by(is_active=True).first()
    if season:
        return redirect(url_for('main.leaderboard', season_id=season.id))
    return render_template('no_season.html')


def _apply_as_of(season, as_of):
    """Temporarily set survivors to state as of N eliminations. Returns restore function."""
    survivors = Survivor.query.filter_by(season_id=season.id).all()
    originals = {s.id: (s.voted_out_order, s.made_jury, s.result) for s in survivors}
    jury_threshold = season.num_players - season.left_at_jury

    for s in survivors:
        if s.voted_out_order > as_of:
            s.voted_out_order = 0
            s.made_jury = False
            s.result = None
        elif s.voted_out_order > 0 and s.voted_out_order > jury_threshold:
            s.made_jury = True

    def restore():
        for s in survivors:
            if s.id in originals:
                s.voted_out_order, s.made_jury, s.result = originals[s.id]

    return restore


@main_bp.route('/scoring-analysis')
def scoring_analysis():
    return render_template('scoring_analysis.html')


@main_bp.route('/rules', defaults={'season_id': None})
@main_bp.route('/rules/<int:season_id>')
def rules(season_id):
    if season_id:
        season = Season.query.get_or_404(season_id)
    else:
        season = Season.query.filter_by(is_active=True).first()
        if not season:
            season = Season.query.first()

    config = {**DEFAULT_CONFIG, **(season.get_scoring_config() if season else {})}

    # Build list of active and inactive components
    active_rules = []
    inactive_rules = []
    for key, (label, desc) in CONFIG_LABELS.items():
        val = config.get(key, 0)
        entry = {'key': key, 'label': label, 'desc': desc, 'value': val}
        if val:
            active_rules.append(entry)
        else:
            inactive_rules.append(entry)

    return render_template('rules.html', season=season, config=config,
                           active_rules=active_rules, inactive_rules=inactive_rules,
                           PICK_TYPE_LABELS=PICK_TYPE_LABELS)


@main_bp.route('/leaderboard/<int:season_id>')
def leaderboard(season_id):
    season = Season.query.get_or_404(season_id)

    # Elimination slider
    max_eliminations = max(
        (s.voted_out_order for s in season.survivors if s.voted_out_order), default=0)
    as_of = request.args.get('as_of', type=int)
    if as_of is not None:
        as_of = max(0, min(as_of, max_eliminations))
    effective_as_of = as_of if as_of is not None else max_eliminations

    # Apply as_of filter (affects both leaderboard and predictions)
    restore = None
    if as_of is not None:
        restore = _apply_as_of(season, as_of)

    leaderboard_data, scoring_name = _build_leaderboard(season)

    active_count = sum(1 for s in season.survivors if s.voted_out_order == 0)

    # Win probabilities (skip for historical as_of views — too expensive)
    win_pcts = {}
    if leaderboard_data and active_count > 0 and as_of is None:
        results, total_scenarios, exhaustive = calculate_win_probabilities(season)
        for uid, data in results.items():
            win_pcts[uid] = data['win_pct']

    # Restore original state
    if restore:
        restore()

    return render_template(
        'leaderboard.html', season=season, leaderboard=leaderboard_data,
        scoring_system_name=scoring_name, active_count=active_count,
        max_eliminations=max_eliminations, as_of=as_of,
        effective_as_of=effective_as_of, win_pcts=win_pcts)


@main_bp.route('/stats/<int:season_id>')
def season_stats(season_id):
    season = Season.query.get_or_404(season_id)
    survivors = Survivor.query.filter_by(season_id=season.id).all()

    # Build stat leaderboards
    def top_by(attr, label, limit=10):
        ranked = sorted(survivors, key=lambda s: getattr(s, attr) or 0, reverse=True)
        return {
            'label': label,
            'rows': [
                {'survivor': s, 'value': getattr(s, attr)}
                for s in ranked[:limit] if getattr(s, attr)
            ],
        }

    stat_boards = [
        top_by('confessional_count', 'Confessionals'),
        top_by('individual_immunity_wins', 'Individual Immunity Wins'),
        top_by('votes_received', 'Votes Received at Tribal'),
        top_by('advantages_found', 'Advantages Found'),
        top_by('advantages_played', 'Advantages Played'),
        top_by('idols_found', 'Idols Found'),
        top_by('tribal_immunity_wins', 'Tribal Immunity Wins'),
    ]

    # Filter out empty boards
    stat_boards = [b for b in stat_boards if b['rows']]

    return render_template('stats.html', season=season, stat_boards=stat_boards)


@main_bp.route('/compare/<int:season_id>')
def scoring_compare(season_id):
    """Show leaderboard side-by-side with different scoring configs."""
    season = Season.query.get_or_404(season_id)

    # Define preset configs to compare
    presets = {
        'Current': season.get_scoring_config(),
        'Classic (original)': {
            'tribal_val': 1, 'jury_val': 1,
            'first_val': 7, 'second_val': 3, 'third_val': 1,
        },
        'High Stakes': {
            'tribal_val': 0.5, 'jury_val': 2, 'merge_val': 3,
            'first_val': 15, 'second_val': 8, 'third_val': 4,
            'final_tribal_val': 5,
        },
        'Performance': {
            'tribal_val': 0.5, 'jury_val': 1,
            'first_val': 10, 'second_val': 5, 'third_val': 3,
            'immunity_win_val': 3, 'idol_play_val': 4, 'advantage_play_val': 2,
            'merge_val': 2,
        },
        'Balanced': {
            'tribal_val': 0.75, 'jury_val': 1.5, 'merge_val': 2,
            'first_val': 10, 'second_val': 5, 'third_val': 2,
            'immunity_win_val': 2, 'idol_play_val': 3, 'advantage_play_val': 1,
            'final_tribal_val': 3,
        },
    }

    # Build leaderboard for each preset
    comparisons = []
    for preset_name, config in presets.items():
        scoring = get_scoring_system('Classic', config)
        users = (User.query.join(Pick).filter(Pick.season_id == season.id)
                 .distinct().all())
        entries = []
        for user in users:
            picks = Pick.query.filter_by(user_id=user.id, season_id=season.id).all()
            total = 0
            for pick in picks:
                survivor = pick.survivor
                breakdown = scoring.calculate_survivor_points(survivor, season)
                modified = scoring.apply_pick_modifier(
                    breakdown.total, pick.pick_type,
                    season.num_players, season.left_at_jury)
                total += modified
            entries.append({'user': user, 'total_points': total})
        entries.sort(key=lambda x: x['total_points'], reverse=True)
        comparisons.append({
            'name': preset_name,
            'config': config,
            'entries': entries,
        })

    return render_template('compare.html', season=season, comparisons=comparisons,
                           config_labels=CONFIG_LABELS)


@main_bp.route('/predictions/<int:season_id>')
def predictions(season_id):
    season = Season.query.get_or_404(season_id)
    results, total_scenarios, exhaustive = calculate_win_probabilities(season)
    remaining = Survivor.query.filter_by(
        season_id=season.id, voted_out_order=0).count()

    return render_template(
        'predictions.html', season=season, results=results,
        total_scenarios=total_scenarios, exhaustive=exhaustive,
        remaining=remaining)


# --- Admin: Settings ---

@main_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip() or None
        current_user.display_name = display_name
        db.session.commit()
        flash('Settings saved!', 'success')
        return redirect(url_for('main.settings'))
    return render_template('settings.html')


# --- Admin: Picks ---

@main_bp.route('/admin/picks/<int:season_id>', methods=['GET', 'POST'])
@login_required
def admin_picks(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivors = Survivor.query.filter_by(
        season_id=season.id).order_by(Survivor.name).all()
    users = User.query.order_by(User.username).all()

    if request.method == 'POST':
        target_user_id = int(request.form['user_id'])

        # Clear existing picks for this user+season
        Pick.query.filter_by(
            user_id=target_user_id, season_id=season.id).delete()

        # Draft picks
        draft_ids = request.form.getlist('draft')
        for i, sid in enumerate(draft_ids):
            db.session.add(Pick(
                user_id=target_user_id, season_id=season.id,
                survivor_id=int(sid), pick_type='draft', pick_order=i + 1))

        # Wildcard
        wc = request.form.get('wildcard')
        if wc:
            db.session.add(Pick(
                user_id=target_user_id, season_id=season.id,
                survivor_id=int(wc), pick_type='wildcard'))

        # Post-jury replacement (wildcard pts)
        pmr_w = request.form.get('pmr_w')
        if pmr_w:
            db.session.add(Pick(
                user_id=target_user_id, season_id=season.id,
                survivor_id=int(pmr_w), pick_type='pmr_w'))

        # Post-jury replacement (draft pts)
        pmr_d = request.form.get('pmr_d')
        if pmr_d:
            db.session.add(Pick(
                user_id=target_user_id, season_id=season.id,
                survivor_id=int(pmr_d), pick_type='pmr_d'))

        db.session.commit()
        target_user = db.session.get(User, target_user_id)
        flash(f'Picks saved for {target_user.display_name or target_user.username}!', 'success')
        return redirect(url_for('main.admin_picks', season_id=season.id))

    # Load current picks per user
    picks_by_user = {}
    for user in users:
        user_picks = Pick.query.filter_by(user_id=user.id, season_id=season.id).all()
        picks_by_user[user.id] = {
            'draft': [p.survivor_id for p in user_picks if p.pick_type == 'draft'],
            'wildcard': next((p.survivor_id for p in user_picks if p.pick_type == 'wildcard'), None),
            'pmr_w': next((p.survivor_id for p in user_picks if p.pick_type == 'pmr_w'), None),
            'pmr_d': next((p.survivor_id for p in user_picks if p.pick_type == 'pmr_d'), None),
        }

    return render_template(
        'admin/picks.html', season=season, survivors=survivors,
        users=users, picks_by_user=picks_by_user)


# --- Admin: Players ---

@main_bp.route('/admin/players', methods=['GET', 'POST'])
@login_required
def admin_players():
    denied = _require_admin()
    if denied:
        return denied

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
        elif User.query.filter_by(username=name).first():
            flash(f'Player "{name}" already exists.', 'error')
        else:
            player = User(username=name, display_name=name)
            db.session.add(player)
            db.session.commit()
            flash(f'Player "{name}" added!', 'success')
        return redirect(url_for('main.admin_players'))

    players = User.query.order_by(User.username).all()
    return render_template('admin/players.html', players=players)


@main_bp.route('/admin/players/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_delete_player(user_id):
    denied = _require_admin()
    if denied:
        return denied

    player = db.session.get(User, user_id)
    if not player:
        flash('Player not found.', 'error')
    elif player.is_admin:
        flash('Cannot delete admin user.', 'error')
    else:
        Pick.query.filter_by(user_id=player.id).delete()
        db.session.delete(player)
        db.session.commit()
        flash(f'Player "{player.display_name or player.username}" deleted.', 'success')
    return redirect(url_for('main.admin_players'))


# --- Admin: Seasons ---

@main_bp.route('/admin/seasons', methods=['GET', 'POST'])
@login_required
def admin_seasons():
    denied = _require_admin()
    if denied:
        return denied

    if request.method == 'POST':
        number = int(request.form['number'])
        name = request.form.get('name', '').strip() or f'Season {number}'
        if Season.query.filter_by(number=number).first():
            flash(f'Season {number} already exists.', 'error')
        else:
            season = Season(number=number, name=name)
            db.session.add(season)
            db.session.commit()
            flash(f'Season {number} created!', 'success')
            return redirect(url_for('main.admin_season_detail', season_id=season.id))

    seasons = Season.query.order_by(Season.number.desc()).all()
    return render_template('admin/seasons.html', seasons=seasons)


@main_bp.route('/admin/season/<int:season_id>')
@login_required
def admin_season_detail(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivors = Survivor.query.filter_by(season_id=season.id).order_by(
        Survivor.voted_out_order.desc(), Survivor.name).all()
    current_config = {**DEFAULT_CONFIG, **season.get_scoring_config()}

    # Load recommended config from analysis JSON if available
    recommended_config = dict(DEFAULT_CONFIG)
    try:
        import os
        json_path = os.path.join(os.path.dirname(__file__), 'static', 'scoring_analysis.json')
        with open(json_path) as f:
            analysis = json.load(f)
        if 'recommended' in analysis:
            recommended_config = analysis['recommended']
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return render_template(
        'admin/season_detail.html', season=season, survivors=survivors,
        scoring_systems=SCORING_SYSTEMS, config_labels=CONFIG_LABELS,
        current_config=current_config, recommended_config=recommended_config,
        default_config=DEFAULT_CONFIG)


@main_bp.route('/admin/season/<int:season_id>/settings', methods=['POST'])
@login_required
def admin_season_settings(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    season.left_at_jury = int(request.form.get('left_at_jury', 11))
    season.num_players = int(request.form.get('num_players', season.num_players))

    # Build scoring config from individual toggle inputs
    scoring_config = {}
    for key in DEFAULT_CONFIG:
        form_val = request.form.get(f'scoring_{key}', '').strip()
        if form_val:
            scoring_config[key] = float(form_val)
        else:
            scoring_config[key] = DEFAULT_CONFIG[key]
    season.scoring_config = json.dumps(scoring_config)

    # Active toggle (deactivate others if setting this one active)
    if 'is_active' in request.form:
        Season.query.filter(Season.id != season.id).update({'is_active': False})
        season.is_active = True
    else:
        season.is_active = False

    db.session.commit()
    flash('Settings updated!', 'success')
    return redirect(url_for('main.admin_season_detail', season_id=season.id))


# --- Admin: Refresh Data ---

@main_bp.route('/admin/season/<int:season_id>/refresh', methods=['POST'])
@login_required
def admin_refresh(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    try:
        download_survivor_data()
        count = refresh_season(season)
        flash(f'Refreshed {count} survivors from survivoR dataset!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Refresh failed: {e}', 'error')

    return redirect(url_for('main.admin_season_detail', season_id=season.id))


# --- Admin: Update Survivors ---

@main_bp.route('/admin/season/<int:season_id>/survivors', methods=['POST'])
@login_required
def admin_update_survivors(season_id):
    denied = _require_admin()
    if denied:
        return denied

    season = Season.query.get_or_404(season_id)
    survivor_ids = request.form.getlist('survivor_id')

    for sid in survivor_ids:
        survivor = db.session.get(Survivor, int(sid))
        if survivor and survivor.season_id == season.id:
            survivor.tribe = request.form.get(f'tribe_{sid}', survivor.tribe)
            survivor.voted_out_order = int(request.form.get(f'voted_out_{sid}', 0))
            survivor.made_jury = f'jury_{sid}' in request.form
            placement = request.form.get(f'placement_{sid}', '').strip()
            survivor.placement = int(placement) if placement else None

    db.session.commit()
    flash('Survivors updated!', 'success')
    return redirect(url_for('main.admin_season_detail', season_id=season.id))
