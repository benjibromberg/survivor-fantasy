"""The breakpoint structure DESIGN.md describes, asserted against the file.

Media queries add no specificity, so when two rules for the same element
declare the same property at the same specificity, source order alone decides
which one wins. On a 375px screen every `max-width` block from 768 down is
matching at once. A narrower block placed *before* a wider one therefore loses
to it on a phone, which is the opposite of what the breakpoint is for, and
nothing about the page announces it.

DESIGN.md described a structure the stylesheet did not have: one block at the
end, ordered wide to narrow, when there were nine blocks in the order
768, 768, 576, 768, 640, 576. The prose was corrected once before and drifted
again. These tests exist so the next drift fails a run instead of being
discovered by someone reading computed values off a page.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "app/static/style.css"
DESIGN = ROOT / "DESIGN.md"


def _blocks():
    """(start_line, condition) for every @media block, in source order.

    Comments are blanked rather than stripped so line numbers survive, and so a
    brace inside a comment cannot shift the nesting depth.
    """
    src = re.sub(
        r"/\*.*?\*/",
        lambda m: "\n" * m.group(0).count("\n"),
        CSS.read_text(),
        flags=re.S,
    )
    out, stack, depth = [], [], 0
    for i, line in enumerate(src.splitlines(), 1):
        found = re.search(r"@media([^{]*)\{", line)
        if found:
            stack.append((i, depth, found.group(1).strip()))
        depth += line.count("{") - line.count("}")
        while stack and depth <= stack[-1][1]:
            start, _, condition = stack.pop()
            out.append((start, condition))
    return sorted(out)


def _widths(kind):
    """Widths of every `{kind}-width` block, in source order."""
    return [
        (start, int(m.group(1)))
        for start, condition in _blocks()
        if (m := re.search(rf"{kind}-width:\s*(\d+)px", condition))
    ]


def test_the_sheet_still_has_breakpoints_to_check():
    """If this fails the checks below have quietly become vacuous."""
    assert len(_blocks()) >= 5


def test_max_width_blocks_run_wide_to_narrow():
    """A narrower block must never come before a wider one.

    Both match on a phone, neither adds specificity, so the later one wins. A
    768 block placed after a 576 block silently governs phones.
    """
    found = _widths("max")
    widths = [w for _, w in found]
    out_of_order = [
        (found[i], found[i + 1])
        for i in range(len(found) - 1)
        if found[i][1] < found[i + 1][1]
    ]
    assert not out_of_order, (
        "these max-width blocks are narrower than a block that follows them, so "
        "the wider rule wins on a phone: "
        + ", ".join(
            f"line {a[0]} ({a[1]}px) before line {b[0]} ({b[1]}px)"
            for a, b in out_of_order
        )
        + f"\nfull order: {widths}"
    )


def test_min_width_blocks_run_narrow_to_wide():
    """The mirror of the rule above, for the handful of min-width blocks."""
    found = _widths("min")
    out_of_order = [
        (found[i], found[i + 1])
        for i in range(len(found) - 1)
        if found[i][1] > found[i + 1][1]
    ]
    assert not out_of_order, f"min-width blocks out of order: {out_of_order}"


def test_design_md_lists_exactly_the_breakpoints_the_sheet_uses():
    """DESIGN.md's breakpoint table must name every width, and no others.

    This is the half that keeps the document honest. Correcting the prose once
    is worth less than a check that fails the next time the two diverge.
    """
    section = DESIGN.read_text().split("## Breakpoints", 1)
    assert len(section) == 2, "DESIGN.md has no Breakpoints section"
    documented = {
        int(w) for w in re.findall(r"`(\d+)px`", section[1].split("\n## ", 1)[0])
    }
    used = {w for _, w in _widths("max")} | {w for _, w in _widths("min")}
    assert documented == used, (
        f"DESIGN.md documents {sorted(documented)} but the stylesheet uses "
        f"{sorted(used)}"
    )
