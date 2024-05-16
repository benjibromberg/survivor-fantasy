import pandas as pd
import argparse
import itertools
from itertools import permutations
import numpy as np


class SurvivorFantasy:
    def __init__(self, file_path, left_at_merge=11):
        self.survivors = self.read_excel_sheet(file_path)
        self.picks = {}
        self.num_tribals = max(self.survivors.voted_out)
        self.left_at_merge = left_at_merge
        self.merge_val = 1
        self.first_val = 3
        self.second_val = 2
        self.third_val = 1
        self.tribal_val = 1
        self.players_remaining = 18 - self.num_tribals

    def read_excel_sheet(self, file_path):
        df = pd.read_excel(file_path)
        df['player'].str.strip()
        return df

    def set_picks(self):
        # Get list of wildcard survivors
        picks_dict_w = self._get_picked_players('w')
        # Get list of drafted survivors
        picks_dict_d = self._get_picked_players('d')

        # Set picks for each fantasy player
        for player_name in list(picks_dict_w.keys()):
            self.set_draft_picks(player_name, picks_dict_d[player_name])
            self.set_wildcard_pick(player_name, picks_dict_w[player_name][0])

    def _get_picked_players(self, char):
        picks_dict = {}

        # Iterate through each column starting from index 3
        for x in range(3, len(self.survivors.columns)):
            player_name = self.survivors.columns.values[x]
            picked_players = []
            # Iterate through each row in the current column
            for index, value in self.survivors[player_name].items():
                # Check if the row value contains the character
                if char in str(value):
                    # add string in 'player' column of self.survivors for the same row
                    picked_players.append(self.survivors.at[index, 'player'])

            picks_dict[player_name] = picked_players

        return picks_dict

    def set_draft_picks(self, drafted_by, players):
        if drafted_by not in self.picks:
            self.picks[drafted_by] = {"draft": [], "wildcard": [], "points": 0}
        for player in players:
            if player in self.survivors['player'].tolist():
                self.picks[drafted_by]["draft"].append(player)
            else:
                raise ValueError(
                    f"{player} is not playing this season. Survivors are: \n{self.survivors['player']}")

    def set_wildcard_pick(self, drafted_by, wildcard):
        if drafted_by not in self.picks:
            raise ValueError(f"{drafted_by} has not drafted this season.")
        if wildcard in self.survivors['player'].tolist():
            self.picks[drafted_by]["wildcard"].append(wildcard)
        else:
            raise ValueError(
                f"{wildcard} is not playing this season. Survivors are: \n{self.survivors['player']}")

    def get_player_tribal_val(self, player):
        return self.survivors.loc[self.survivors['player'] == player].voted_out.values[0]

    def calculate_survivor_points(self, player, is_wildcard=False):
        points = 0

        # If player is already out of the game
        if self.get_player_tribal_val(player) != 0:
            # print(f"{player} out of game")
            points += self.get_player_tribal_val(player) - 1

            # Check if merge point should be awarded
            if self.left_at_merge > (18 - self.get_player_tribal_val(player)):
                points += self.merge_val

            if self.get_player_tribal_val(player) == 18:
                points += self.first_val

            if self.get_player_tribal_val(player) == 17:
                points += self.second_val

            if self.get_player_tribal_val(player) == 16:
                points += self.third_val

        # If player is still in the game
        else:
            # print(f"{player} in game")
            # print(f"player tribal val {self.get_player_tribal_val(player)}")

            # Check if merge point should be awarded
            if self.players_remaining <= self.left_at_merge:
                points += self.merge_val

            # Add points for tribal councils survived
            points += self.num_tribals * self.tribal_val

        if is_wildcard:
            points = points / 2
        # print(points)
        return points

    def calculate_player_points(self):
        for drafted_by in self.picks:
            # Reset points before recalculation
            self.picks[drafted_by]["points"] = 0
            for player in self.picks[drafted_by]["draft"]:
                self.picks[drafted_by]["points"] += self.calculate_survivor_points(
                    player)
            for player in self.picks[drafted_by]["wildcard"]:
                self.picks[drafted_by]["points"] += self.calculate_survivor_points(
                    player, is_wildcard=True)

    def print_standings(self):
        self.calculate_player_points()
        sorted_picks = sorted(
            self.picks.items(), key=lambda x: x[1]["points"], reverse=True)
        for i, (player, data) in enumerate(sorted_picks):
            print(f"{i+1}. {player} - {data['points']} points")

    def update_voted_out_order(self, new_order, num_finalists_set=0):
        for i, player in enumerate(new_order):
            self.survivors.loc[self.survivors['player']
                               == player, 'voted_out'] = i + 1 + (18 - len(new_order)) - num_finalists_set

    def define_stats(self, num_finalists_set=0):
        remaining_players = self.survivors[self.survivors['voted_out'] == 0]['player'].tolist(
        )
        permutations = list(itertools.permutations(remaining_players))

        print(permutations)

        player_wins = {player: 0 for player in self.picks.keys()}

        for perm in permutations:
            self.update_voted_out_order(perm, num_finalists_set)
            self.calculate_player_points()
            winner = max(self.picks.items(), key=lambda x: x[1]["points"])[0]
            player_wins[winner] += 1

        for player, win_count in player_wins.items():
            probability = win_count / len(permutations)
            print(f"{player} has a {probability:.2%} chance of winning.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run calculations for Survivor Fantasy")
    parser.add_argument(
        "file_path",
        help="""
        Path to manually created Excel file of season standings and player
        picks. See the example and README on GitHub for more information.
        """
    )
    parser.add_argument(
        "--left_at_merge", type=int, default=11,
        help="Number of remaining players left at merge"
    )
    parser.add_argument(
        "--stats",
        help="""
        Calculates likelihood of winning given all possible permutations of
        remaining players
        """,
        action="store_true"
    )
    parser.add_argument(
        "--num_finalists_set", type=int, default=0,
        help="""
        Allows stats to be run when finalists have been defined in the Excel
        file
        """
    )

    args = parser.parse_args()

    survivor_fantasy = SurvivorFantasy(args.file_path, args.left_at_merge)
    survivor_fantasy.set_picks()
    survivor_fantasy.print_standings()

    if args.stats:
        survivor_fantasy.define_stats(args.num_finalists_set)
