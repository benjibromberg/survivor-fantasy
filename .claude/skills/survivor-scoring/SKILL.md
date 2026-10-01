---
name: survivor-scoring
description: >-
  How survivor-fantasy scores a season: the ScoringSystem base class and how to add
  one, the configurable components, and the flat and progressive tribal modes with
  their closed-form arithmetic. Use when changing how points are calculated, adding a
  scoring component, or reading a season's scoring_config.
---

# survivor-scoring

The two rules that bite (tribal counting via `compute_tribals_survived`, and that
`PointBreakdown.total` is computed rather than assigned) stay in CLAUDE.md. This is
the arithmetic.

## Scoring System (`app/scoring/`)

Extensible via subclassing `ScoringSystem` in `base.py`:

1. Implement `name`, `description`, `calculate_survivor_points(survivor, season) -> PointBreakdown`
2. Optionally override `apply_pick_modifier()` for custom pick-type behavior
3. Register in `__init__.py`

Current: **Classic** (configurable components: tribal survival, jury, merge, placement, immunity, idols/advantages, sole survivor streak)

**Tribal counting:** `Season.compute_tribals_survived(survivor)` counts distinct elimination days before the player's `day_voted_out`. Same-day boots (double/triple tribals) get identical tribal counts. Falls back to `voted_out_order - 1` when `day_voted_out` is `None`. Never use `voted_out_order - 1` directly for tribal counting.

**Tribal scoring modes:**

- **Flat mode** (default): `tribal_val` for pre-merge, `post_merge_tribal_val` for post-merge. Used when `tribal_base` is `None`.
- **Progressive mode**: Three-phase piecewise linear growth. Enabled when `tribal_base` is set (not `None`).
  - Pre-merge: tribal N = `tribal_base + (N-1) * tribal_step`
  - Post-merge: continues from last pre-merge value, grows by `post_merge_step` per tribal
  - Finale: continues from last post-merge value, grows by `finale_step` per tribal. Finale starts when `finale_size` players remain.
  - All phases use closed-form arithmetic sums (no loops) for performance.
  - When `tribal_base` is set, `tribal_val`/`post_merge_tribal_val` are ignored.

**`PointBreakdown.total`** is a computed property (sum of `items` dict values) — never assign to it directly. Add/remove keys from `items` instead.
