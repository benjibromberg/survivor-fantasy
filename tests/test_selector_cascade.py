"""Two selector defects that render a plausible page and raise nothing.

Both have shipped. Neither is visible in a screenshot, which is why they are
guarded here rather than left to review.

1. A positional selector that matches zero elements. `.lb-standings-head` is
   the first child of `.lb-standings`, so `.leaderboard-entry:first-child`
   matched nothing and the leader accent silently never applied. The same
   mistake was still sitting in the compare page months later:
   `.compare-row:nth-child(1)` counted the `<h3>` above the rows, so the
   brightest podium tint reached nobody and ranks 1 and 2 wore the shades
   meant for 2 and 3. A rule that matches nothing is indistinguishable from a
   rule that was never written.

2. A breakpoint override that cannot win. Media queries add no specificity, so
   a base rule carrying an extra class or a `:not()` beats the phone rule for
   the same component *inside* the phone block. That reached production once
   and left phones laying out against a column count the phone layout does not
   use. `test_standings_grid.py` guards the standings component specifically;
   this generalises it to any component whose phone override declares a
   property the base section also declares.
"""

import importlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

STYLE = Path(__file__).resolve().parent.parent / "app" / "static" / "style.css"

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
POSITIONAL = re.compile(r":(?:nth-|first-|last-|only-)child\b")


def _css_without_comments() -> str:
    return re.sub(r"/\*.*?\*/", "", STYLE.read_text(), flags=re.S)


def _selectors():
    """Every individual selector in the sheet, media blocks flattened.

    Media conditions are stripped rather than tracked, because both checks
    here care about the selector text and about which block a rule sits in,
    and the block is recovered separately from the raw text.
    """
    css = _css_without_comments()
    css = re.sub(r"@media[^{]*\{", "", css)
    for selector, body in RULE.findall(css):
        for part in selector.split(","):
            part = part.strip()
            if part and not part.startswith("@"):
                yield part, body


# --------------------------------------------------------------------------
# 1. positional selectors must match something
# --------------------------------------------------------------------------

# Components that only exist in a state these fixtures do not build. Each needs
# a reason, so that "it matched nothing" is never silently acceptable.
POSITIONAL_NOT_RENDERED = {
    ".ember-particle": "finale banner only, and only on a finished season",
}


PSEUDO_ELEMENT = re.compile(r"::[a-z-]+(?:\([^)]*\))?")


def _positional_selectors():
    """Class-qualified positional selectors, pseudo-elements stripped.

    `::before` cannot be queried and does not change which elements match, so
    `.tl-point:first-child::before` is checked as `.tl-point:first-child`.
    """
    seen = set()
    for selector, _body in _selectors():
        if POSITIONAL.search(selector) and ("." in selector or "#" in selector):
            seen.add(PSEUDO_ELEMENT.sub("", selector).strip())
    return sorted(seen)


def test_the_sheet_still_contains_positional_selectors_to_check():
    """If this fails the check below has quietly become vacuous."""
    assert len(_positional_selectors()) >= 3


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client on a temp DB. The scheduler is stubbed."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])

    monkeypatch.setattr("app.scheduler.init_scheduler", lambda _app: None)

    from app import create_app, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        yield application.test_client(), db
        db.session.remove()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


@pytest.fixture()
def league(client):
    """Active season after two boots, with two teams holding picks."""
    from app.models import Pick, Season, Survivor, User

    c, db = client
    season = Season(number=99, name="Season 99", is_active=True, num_players=6)
    db.session.add(season)
    db.session.flush()

    # elimination_episode on the booted two is what makes the season timeline
    # render at all: it needs more than one point. Without it the timeline's
    # positional rules have nothing to match and the check below goes vacuous.
    survivors = [
        Survivor(
            season_id=season.id,
            name=f"Castaway{i + 1}",
            voted_out_order=i + 1 if i < 2 else 0,
            elimination_episode=i + 1 if i < 2 else None,
            castaway_id=f"US9{i}",
            version_season="US99",
        )
        for i in range(6)
    ]
    pat = User(username="pat", display_name="Pat")
    sam = User(username="sam", display_name="Sam")
    db.session.add_all([*survivors, pat, sam])
    db.session.flush()

    db.session.add_all(
        [
            Pick(user_id=u.id, season_id=season.id, survivor_id=s.id, pick_type="draft")
            for u, s in ((pat, survivors[0]), (pat, survivors[2]), (sam, survivors[4]))
        ]
    )
    db.session.commit()
    return SimpleNamespace(c=c, season=season)


@pytest.fixture()
def rendered(league):
    """The pages that carry positional rules, parsed.

    A page that fails to render is reported as such rather than silently
    reducing the corpus, because a shrinking corpus makes the check pass.
    """
    pages = {}
    for name, url in (
        ("leaderboard", f"/leaderboard/{league.season.id}"),
        ("settings-hub", "/league-settings"),
        ("settings-detail", "/league-settings/scoring"),
        ("analysis", "/scoring-analysis"),
        ("compare", f"/compare/{league.season.id}"),
    ):
        response = league.c.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        pages[name] = BeautifulSoup(response.data, "html.parser")
    return pages


def test_every_positional_selector_matches_something(rendered):
    """A class-qualified positional selector that matches nothing is a bug.

    Checked against rendered pages rather than against the stylesheet, because
    whether `:nth-child(1)` finds the first `.compare-row` depends entirely on
    what else is in that container, which only the DOM knows.
    """
    unmatched = []
    for selector in _positional_selectors():
        if any(selector.startswith(prefix) for prefix in POSITIONAL_NOT_RENDERED):
            continue
        if not any(page.select(selector) for page in rendered.values()):
            unmatched.append(selector)
    assert not unmatched, (
        "these selectors matched zero elements on every page that could carry "
        f"them, so the rules behind them do nothing: {unmatched}"
    )


def test_skipped_positional_selectors_are_all_still_in_the_sheet():
    """Stops the allowlist outliving the rule it excuses."""
    css = _css_without_comments()
    for prefix, reason in POSITIONAL_NOT_RENDERED.items():
        assert prefix in css, f"{prefix} is allowlisted ({reason}) but is gone"


# --------------------------------------------------------------------------
# 2. a phone override must be able to beat its own base rule
# --------------------------------------------------------------------------


def _specificity(selector: str) -> tuple:
    """(ids, classes, elements), with :not() contributing its argument.

    Only needs to be accurate enough to compare two selectors aimed at the
    same component, which is what the defect involves.
    """
    ids = len(re.findall(r"#[A-Za-z0-9_-]+", selector))
    inner = ""
    for match in re.finditer(r":not\(([^)]*)\)", selector):
        inner += " " + match.group(1)
    body = re.sub(r":not\([^)]*\)", " ", selector) + inner
    classes = len(re.findall(r"\.[A-Za-z0-9_-]+", body))
    classes += len(re.findall(r"\[[^\]]+\]", body))
    classes += len(re.findall(r":(?!:)[a-z-]+", body))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-z][a-z0-9]*)", body))
    return (ids, classes, elements)


COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")


def _compounds(selector: str) -> list:
    """Selector split on combinators, each compound as a token set.

    `.a .b.c` -> [{'.a'}, {'.b', '.c'}]. Comparing whole selectors this way is
    what keeps `.chart-card` and `.chart-card .chart-wrap canvas` apart: they
    are different lengths, so they target different elements and one being
    more specific than the other means nothing.
    """
    out = []
    for part in COMBINATOR.split(selector.strip()):
        if not part:
            continue
        tokens = set(
            re.findall(
                r"\.[A-Za-z0-9_-]+|#[A-Za-z0-9_-]+|\[[^\]]+\]|:not\([^)]*\)|:[a-z-]+|^[a-z][a-z0-9]*",
                part,
            )
        )
        out.append(tokens)
    return out


def _targets_same_element_more_specifically(base: str, override: str) -> bool:
    """True when `base` is `override` plus extra tokens on the same element."""
    b, o = _compounds(base), _compounds(override)
    if len(b) != len(o) or not b:
        return False
    if b[:-1] != o[:-1]:
        return False
    return o[-1] < b[-1]  # strict superset on the final compound


def _declared_properties(body: str) -> set:
    return {
        declaration.split(":", 1)[0].strip()
        for declaration in body.split(";")
        if ":" in declaration
    }


def _rules_by_block():
    """(selector, body) split into base rules and max-width override rules."""
    css = _css_without_comments()
    base, override = [], []
    index, depth = 0, 0
    while index < len(css):
        media = re.match(r"@media\s*\(max-width[^{]*\{", css[index:])
        if media and depth == 0:
            start = index + media.end()
            level, cursor = 1, start
            while level:
                if css[cursor] == "{":
                    level += 1
                elif css[cursor] == "}":
                    level -= 1
                cursor += 1
            for selector, body in RULE.findall(css[start : cursor - 1]):
                for part in selector.split(","):
                    if part.strip():
                        override.append((part.strip(), body))
            index = cursor
            continue
        index += 1
    stripped = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    for selector, body in RULE.findall(stripped):
        for part in selector.split(","):
            if part.strip():
                base.append((part.strip(), body))
    return base, override


def test_no_breakpoint_override_is_outspecified_by_its_own_base_rule():
    """A breakpoint rule must be able to beat the base rule it is overriding.

    Only compares rules aimed at the *same element*: a base selector that is
    the override's selector plus extra classes or states on its final
    compound. A base rule that is more specific because it targets a
    descendant is not in competition and is ignored.

    An override is also satisfied if some *other* rule in a breakpoint block
    covers the more specific variant with enough specificity, which is how the
    standings handles its `.no-win` and `.is-finished` variants.
    """
    base, override = _rules_by_block()
    assert base and override, "expected both base rules and max-width overrides"

    losers = []
    for o_sel, o_body in override:
        o_props = _declared_properties(o_body)
        if not o_props:
            continue
        o_spec = _specificity(o_sel)
        for b_sel, b_body in base:
            if not _targets_same_element_more_specifically(b_sel, o_sel):
                continue
            clash = o_props & _declared_properties(b_body)
            if not clash:
                continue
            if _specificity(b_sel) <= o_spec:
                continue
            covered = any(
                _specificity(other_sel) >= _specificity(b_sel)
                and _compounds(other_sel) == _compounds(b_sel)
                and (clash & _declared_properties(other_body))
                for other_sel, other_body in override
            )
            if not covered:
                losers.append(f"{o_sel} cannot override {b_sel} for {sorted(clash)}")

    assert not losers, (
        "these breakpoint rules are out-specified by a base rule setting the "
        "same property on the same element, so they do not apply inside their "
        f"own media query: {losers}"
    )
