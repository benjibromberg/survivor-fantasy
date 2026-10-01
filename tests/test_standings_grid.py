"""The standings grid variants, and the cascade trap they fell into.

The header and the rows share `--lb-cols` and `--lb-areas`, so they cannot
disagree with each other. What they can do is disagree with the phone
override: media queries add no specificity, so a base variant written with
an extra class (or `:not()`) beats the breakpoint rule *inside* the
breakpoint. That shipped, and it left phones with a column the phone layout
does not use, which crushed the team name to a few characters per line.

These are text checks on the stylesheet rather than rendering checks,
because the defect is in the selectors, not in any one rule's values.
"""

import re
from pathlib import Path

STYLE = Path(__file__).resolve().parent.parent / "app" / "static" / "style.css"

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _grid_rules():
    """(selector, body) for every rule that DECLARES --lb-cols.

    Comments are stripped first: they sit between rules, so a naive selector
    capture swallows the comment above each one. Rules that merely reference
    var(--lb-cols), such as .lb-standings-head, are not grid variants.
    """
    css = re.sub(r"/\*.*?\*/", "", STYLE.read_text(), flags=re.S)
    return [
        (selector.strip(), body)
        for selector, body in RULE.findall(css)
        if "--lb-cols:" in body
    ]


def _columns(value):
    """Track widths, counting `minmax(0, 1fr)` as the one track it is."""
    flat = re.sub(
        r"\(([^)]*)\)", lambda m: "(" + m.group(1).replace(" ", "") + ")", value
    )
    return flat.split()


def _standings_parts(selector):
    for part in selector.split(","):
        part = part.strip()
        # Any .lb-standings variant, however it is written. Matching only
        # clean class chains would let a :not() variant slip past unrecorded,
        # which is exactly the selector that caused this.
        if part.startswith(".lb-standings") and not part.startswith(".lb-standings-"):
            yield part


def test_every_rule_declares_as_many_columns_as_its_areas_use():
    checked = 0
    for selector, body in _grid_rules():
        areas = re.search(r"--lb-areas:([^;]+);", body)
        if not areas:
            continue
        cols = _columns(re.search(r"--lb-cols:([^;]+);", body).group(1))
        first_row = areas.group(1).strip().strip('"').split('"')[0].split()
        assert len(cols) == len(first_row), f"{selector}: {cols} vs {first_row}"
        checked += 1
    assert checked >= 3, "expected the base variants and the phone override"


def test_no_standings_grid_rule_uses_not():
    """`:not()` carries its argument's specificity, which is how this broke."""
    for selector, _body in _grid_rules():
        assert ":not(" not in selector, selector


def test_every_base_variant_is_named_in_the_phone_override():
    base, phone = set(), set()
    for selector, body in _grid_rules():
        target = phone if "1.8rem" in body else base
        target.update(_standings_parts(selector))
    assert base and phone, "expected both base and phone standings rules"
    assert base <= phone, f"not overridden on phones: {base - phone}"


def test_the_finished_variant_is_declared_after_the_no_win_one():
    """A finished season carries both classes, so order decides the tie."""
    css = re.sub(r"/\*.*?\*/", "", STYLE.read_text(), flags=re.S)
    no_win = css.index(".lb-standings.no-win {")
    finished = css.index(".lb-standings.is-finished {")

    assert no_win < finished
