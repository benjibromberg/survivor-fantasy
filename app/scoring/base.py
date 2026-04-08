from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PointBreakdown:
    """Breakdown of points by scoring category."""

    items: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.items.values())


class ScoringSystem(ABC):
    """Base class for all scoring systems.

    To add a new scoring system:
    1. Create a new file in app/scoring/
    2. Subclass ScoringSystem and implement name, description, calculate_survivor_points
    3. Register it in app/scoring/__init__.py
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def calculate_survivor_points(self, survivor, season) -> PointBreakdown:
        """Calculate base points for a single survivor (before pick-type modifiers)."""
        pass

    def apply_pick_modifier(self, points, pick_type, num_survivors, left_at_jury):
        """Apply modifier based on pick type. Override for custom behavior."""
        if pick_type == "draft":
            return points
        elif pick_type == "wildcard":
            return points / 2
        elif pick_type == "pmr_w":
            pre_jury = num_survivors - left_at_jury
            return (points - pre_jury) / 2
        elif pick_type == "pmr_d":
            pre_jury = num_survivors - left_at_jury
            return points - pre_jury
        return points

    def score_pick(self, survivor, season, pick_type, stat_overrides=None):
        """Score a single pick. Returns (modified_points, breakdown).

        Args:
            survivor: Survivor object.
            season: Season object.
            pick_type: 'draft', 'wildcard', 'pmr_w', 'pmr_d'.
            stat_overrides: Optional dict of {attr: value} for replacement picks.
                When provided, temporarily applies post-merge-only stat values
                before scoring. When None, falls back to flat deduction.
        """
        breakdown = self.calculate_survivor_points(survivor, season)
        modified = self.apply_pick_modifier(
            breakdown.total, pick_type, season.num_players, season.left_at_jury
        )
        return modified, breakdown
