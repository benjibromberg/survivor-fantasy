from .base import ScoringSystem, PointBreakdown

# Default scoring config — everything has a non-zero value
DEFAULT_CONFIG = {
    # Base survival (separate rates for pre-merge and post-merge tribals)
    'tribal_val': 0.5,
    'post_merge_tribal_val': 1,
    # Milestones
    'jury_val': 2,
    'merge_val': 2,
    'final_tribal_val': 3,
    # Placement
    'first_val': 15,
    'second_val': 5,
    'third_val': 2,
    # Individual performance
    'individual_immunity_val': 3,
    'tribal_immunity_val': 1,
    'idol_found_val': 2,
    'advantage_found_val': 1,
    'idol_play_val': 3,
    'advantage_play_val': 2,
    # Sole Survivor pick (points per tribal survived, only if your pick wins)
    'sole_survivor_val': 1,
    # Pick type modifiers
    'wildcard_multiplier': 0.5,
    'replacement_multiplier': 0.5,  # for pmr_w
    'replacement_deduction': True,  # subtract pre-merge tribals for replacements
}

# For the admin UI — descriptions for each config key
CONFIG_LABELS = {
    'tribal_val': ('Pre-Merge Tribal', 'Points per pre-merge tribal council survived'),
    'post_merge_tribal_val': ('Post-Merge Tribal', 'Points per post-merge tribal council survived'),
    'jury_val': ('Jury Bonus', 'Bonus for making the jury'),
    'merge_val': ('Merge Bonus', 'Bonus for making the merge'),
    'final_tribal_val': ('Final Tribal Bonus', 'Bonus for reaching final tribal council'),
    'first_val': ('1st Place', 'Winner bonus'),
    'second_val': ('2nd Place', 'Runner-up bonus'),
    'third_val': ('3rd Place', '3rd place bonus'),
    'individual_immunity_val': ('Individual Immunity Win', 'Points per individual immunity win'),
    'tribal_immunity_val': ('Tribal Immunity Win', 'Points per tribal immunity win'),
    'idol_found_val': ('Idol Found', 'Points per hidden immunity idol found'),
    'advantage_found_val': ('Advantage Found', 'Points per advantage found (non-idol)'),
    'idol_play_val': ('Idol Played', 'Points per idol played'),
    'advantage_play_val': ('Advantage Played', 'Points per non-idol advantage played'),
    'sole_survivor_val': ('Sole Survivor Pick', 'Points per consecutive episode in winner streak (must include finale)'),
    'wildcard_multiplier': ('Wildcard Multiplier', 'Point multiplier for wildcard picks (e.g. 0.5 = half)'),
    'replacement_multiplier': ('Replacement (W) Multiplier', 'Point multiplier for wildcard replacement picks'),
    'replacement_deduction': ('Replacement Deduction', 'Subtract pre-merge tribals from replacement picks (1=on, 0=off)'),
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
        return 'Classic'

    @property
    def description(self):
        return 'Configurable scoring with toggleable components'

    def calculate_survivor_points(self, survivor, season):
        c = self.config
        breakdown = PointBreakdown()

        pre_merge_rate = c['tribal_val']
        post_merge_rate = c.get('post_merge_tribal_val', pre_merge_rate)
        merge_threshold = season.num_players - season.left_at_jury

        if survivor.voted_out_order and survivor.voted_out_order > 0:
            # Eliminated
            tribals = survivor.voted_out_order - 1
            pre_merge_tribals = min(tribals, merge_threshold)
            post_merge_tribals = max(0, tribals - merge_threshold)

            if pre_merge_rate and pre_merge_tribals:
                breakdown.items['pre_merge_tribal'] = pre_merge_tribals * pre_merge_rate
            if post_merge_rate and post_merge_tribals:
                breakdown.items['post_merge_tribal'] = post_merge_tribals * post_merge_rate

            if c['jury_val'] and survivor.made_jury:
                breakdown.items['jury'] = c['jury_val']

            if c['merge_val'] and survivor.voted_out_order > merge_threshold:
                breakdown.items['merge'] = c['merge_val']

            # Placement
            if survivor.voted_out_order == season.num_players:
                if c['first_val']:
                    breakdown.items['placement'] = c['first_val']
                if c['final_tribal_val']:
                    breakdown.items['final_tribal'] = c['final_tribal_val']
            elif survivor.voted_out_order == season.num_players - 1:
                if c['second_val']:
                    breakdown.items['placement'] = c['second_val']
                if c['final_tribal_val']:
                    breakdown.items['final_tribal'] = c['final_tribal_val']
            elif survivor.voted_out_order == season.num_players - 2:
                if c['third_val']:
                    breakdown.items['placement'] = c['third_val']
                if c['final_tribal_val']:
                    breakdown.items['final_tribal'] = c['final_tribal_val']
        else:
            # Still in the game
            current = season.current_tribal_count
            pre_merge_tribals = min(current, merge_threshold)
            post_merge_tribals = max(0, current - merge_threshold)

            if pre_merge_rate and pre_merge_tribals:
                breakdown.items['pre_merge_tribal'] = pre_merge_tribals * pre_merge_rate
            if post_merge_rate and post_merge_tribals:
                breakdown.items['post_merge_tribal'] = post_merge_tribals * post_merge_rate

            if c['jury_val'] and survivor.made_jury:
                breakdown.items['jury'] = c['jury_val']

            if c['merge_val'] and current > merge_threshold:
                breakdown.items['merge'] = c['merge_val']

        # Performance bonuses (work for both active and eliminated)
        if c['individual_immunity_val'] and survivor.individual_immunity_wins:
            breakdown.items['individual_immunity'] = survivor.individual_immunity_wins * c['individual_immunity_val']

        if c['tribal_immunity_val'] and survivor.tribal_immunity_wins:
            breakdown.items['tribal_immunity'] = survivor.tribal_immunity_wins * c['tribal_immunity_val']

        if c['idol_found_val'] and survivor.idols_found:
            breakdown.items['idols_found'] = survivor.idols_found * c['idol_found_val']

        if c['advantage_found_val'] and survivor.advantages_found:
            non_idol_found = survivor.advantages_found - survivor.idols_found
            if non_idol_found > 0:
                breakdown.items['advantages_found'] = non_idol_found * c['advantage_found_val']

        if c['idol_play_val'] and survivor.idols_found:
            # Use idols_found as proxy for idol plays (survivoR tracks "Played" events)
            breakdown.items['idol_plays'] = survivor.idols_found * c['idol_play_val']

        if c['advantage_play_val'] and survivor.advantages_played:
            non_idol_plays = max(0, survivor.advantages_played - survivor.idols_found)
            if non_idol_plays > 0:
                breakdown.items['advantage_plays'] = non_idol_plays * c['advantage_play_val']

        return breakdown

    def apply_pick_modifier(self, points, pick_type, num_survivors, left_at_jury):
        c = self.config
        pre_jury = num_survivors - left_at_jury

        if pick_type == 'draft':
            return points
        elif pick_type == 'wildcard':
            mult = c.get('wildcard_multiplier', 0.5)
            return points * mult
        elif pick_type == 'pmr_w':
            mult = c.get('replacement_multiplier', 0.5)
            deduct = pre_jury if c.get('replacement_deduction', True) else 0
            return (points - deduct) * mult
        elif pick_type == 'pmr_d':
            deduct = pre_jury if c.get('replacement_deduction', True) else 0
            return points - deduct
        return points

    def calculate_sole_survivor_bonus(self, streak_length):
        """Calculate sole survivor bonus from streak length.

        Points = sole_survivor_val × streak_length, where streak_length is the
        number of consecutive episodes ending at the finale where the player
        had the eventual winner picked.
        """
        return streak_length * self.config.get('sole_survivor_val', 0)
