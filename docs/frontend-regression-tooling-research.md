# Front-end regression checks: what existing tools cover, and what we keep hand-rolling

Goal: for the eight front-end checks this repo now needs, find out which ones an
existing tool performs well enough to replace or back up a hand-rolled test, and
which ones nothing off the shelf does. Every claim below was read out of the
tool's own source, its own docs, or the relevant W3C spec; nothing here comes
from a blog post or a comparison article. Versions and release dates were
checked on **2026-10-02** and are stated so a reader can tell when a claim has
aged.

The eight requirements come from defects that shipped in this codebase and were
fixed in #157, #158 and #159. They are referred to by number throughout:

1. CSS selectors that match zero elements
2. breakpoint overrides that cannot win on specificity
3. tap-target minimum of 2.5rem (40px at a 16px root)
4. computed text-size floor of 12px, including `em` nesting
5. colour contrast including composited `opacity`
6. horizontal page overflow
7. heading order and one `h1` per page
8. `DESIGN.md` claims matching the stylesheet

---

## Recommendation in short

Add **one browser-driven pytest file** using `playwright` plus
`pytest-playwright`, and inject **axe-core** into the page from
`axe-playwright-python`. That one dependency set covers requirements 3, 5 and 7
with real engine support, and gives the DOM and computed styles needed to
hand-roll 1, 4 and 6 in a few lines each. Threshold-wise axe is better than its
reputation: `target-size` reads a `minSize` option, so our 40px floor is
reachable through `axe.configure`, and `color-contrast` composites `opacity`
through the stacking context, which is exactly the defect class in #159.

Nothing off the shelf covers **requirement 2** (a breakpoint rule out-specified
by a base rule) or **requirement 6** (horizontal overflow), and nothing covers
**requirement 4** any more: Lighthouse deleted its 12px font-size audit in
13.0.0, and the version that still had it only required 60% of page text to pass.
**Requirement 8** has no tool that checks prose against code; the established
pattern is to invert the direction and generate the documented table from the
source with `cog --check`, which is a close cousin of what
`tests/test_breakpoint_order.py` already does by hand.

Do not adopt PurgeCSS for requirement 1. It matches words in template files, not
elements in a rendered DOM, so `.compare-row:nth-child(1)` is "used" to PurgeCSS
no matter what the first child is. UnCSS does do real DOM matching and would
catch it, but its last npm release was February 2020 and it runs on jsdom rather
than a browser.

---

## Requirement to tool map

Verdicts: **covered** means the tool checks the thing at our threshold with
configuration only; **partial** means it checks a weaker or differently scoped
version; **no** means it does not check it at all.

| # | Requirement | Tool | Verdict | Why |
|---|---|---|---|---|
| 1 | Selector matches zero elements | PurgeCSS | no | Default extractor "considers every word of a file as a selector" ([docs](https://github.com/FullHuman/purgecss/blob/main/docs/extractors.md)); never evaluates a selector against a DOM |
| 1 | | UnCSS | partial | Real `document.querySelector` matching in jsdom, and it does **not** strip `:nth-child`, so it would catch our defect. Node, jsdom not Chrome, last npm release 2020-02-11 |
| 1 | | CDP `CSS.startRuleUsageTracking` | partial | Reports per-rule `used` booleans for the whole sheet, by byte offset, not by selector. This is the mechanism, not a tool |
| 1 | | stylelint | no | No rule for dead selectors; `stylelint-no-unused-selectors` last published 2021-08-08 |
| 1 | | hand-rolled (`tests/test_selector_cascade.py`) | covered | Already in the repo; soupsieve does the matching |
| 2 | Breakpoint override out-specified | stylelint `no-descending-specificity` | no | "This rule only compares rules that are within the same media context", and the base-then-media-query shape is given as an explicit non-problem ([rule README](https://github.com/stylelint/stylelint/blob/main/lib/rules/no-descending-specificity/README.md)) |
| 2 | | stylelint `selector-max-specificity` | no | Caps absolute specificity; cannot express "this override must beat that base rule" |
| 2 | | Playwright `to_have_css` at 375px | covered (different layer) | Asserts the computed value the cascade actually produced, instead of re-deriving the arithmetic |
| 2 | | hand-rolled (`test_selector_cascade.py`, `test_breakpoint_order.py`) | covered | Already in the repo |
| 3 | Tap target 40px | axe-core `target-size` | covered | `minSize` option, overridable via `axe.configure`; four caveats below |
| 3 | | Lighthouse `target-size` | partial | Same axe rule at the stock 24px, no option passthrough |
| 3 | | pa11y (axe runner) | partial | Can enable the rule, cannot set check options |
| 3 | | IBM Equal Access `target_spacing_sufficient` | partial | 24 hardcoded in the rule source |
| 4 | 12px computed text floor | axe-core | no | No font-size rule exists in the rule list |
| 4 | | Lighthouse `font-size` | no | Removed in 13.0.0; in 12.6.1 it passed at "≥60% of page text ≥12px", so one 11px badge never failed it |
| 4 | | hand-rolled `getComputedStyle` sweep | covered | Nothing else does this |
| 5 | Contrast with `opacity` | axe-core `color-contrast` | covered | Composites element and ancestor opacity through the stacking context since 4.7.0; `contrastRatio` thresholds configurable |
| 5 | | Lighthouse `color-contrast` | partial | Same rule, stock thresholds, and its score reads violations only |
| 5 | | pa11y / IBM Equal Access | partial | Both run an engine with fixed thresholds; both soften gradients to "needs review" |
| 6 | Horizontal overflow | axe-core | no | No reflow or overflow rule |
| 6 | | Lighthouse | no | No such audit in the 13.5.0 or 12.6.1 audit lists |
| 6 | | hand-rolled `scrollLeft` probe | covered | Nothing else does this |
| 7 | Heading order, one h1 | axe-core `heading-order`, `page-has-heading-one` | covered | Both exist, both tagged `best-practice`, so a `wcag2aa`-only run misses them |
| 7 | | Lighthouse | partial | Enables `heading-order` and `empty-heading`, not `page-has-heading-one` |
| 7 | | pa11y (axe runner) | covered | Its tag list includes `best-practice` |
| 7 | | hand-rolled (`tests/test_accessibility_markup.py`) | covered | Server-rendered HTML is enough for this one; no browser needed |
| 8 | Doc matches stylesheet | cog `--check` | covered (inverted) | Generate the table from the source, fail CI when the file would change |
| 8 | | Sybil, pytest-markdown-docs | no | They execute code blocks in docs; our claims are prose and markdown tables |
| 8 | | hand-rolled (`tests/test_breakpoint_order.py`) | covered | Parses the doc section and the sheet, asserts set equality |
| all | BackstopJS, Percy, Chromatic, Applitools | no | Pixel diffing catches unintended visual change; it cannot express a 40px floor or a 4.5:1 ratio, and it needs a human to approve baselines |
| all | Galen Framework | no | Layout-spec DSL, Java plus Selenium, last release `galen-2.4.4` on 2019-03-15 |

---

## The CI constraint, confirmed rather than assumed

`ubuntu-latest` resolves to Ubuntu 24.04:
"Ubuntu 24.04 | x64 | `ubuntu-latest` or `ubuntu-24.04`"
([actions/runner-images README](https://github.com/actions/runner-images/blob/main/README.md)).

That image ships, under "Browsers and Drivers"
([Ubuntu2404-Readme.md](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)):

- Google Chrome 153.0.8010.52 and ChromeDriver 153.0.8010.52
- Chromium 153.0.8010.0
- Microsoft Edge 153.0.4234.48 with its WebDriver
- Mozilla Firefox 156.0, Geckodriver 0.37.1, Selenium server 4.49.0
- `CHROMEWEBDRIVER=/usr/local/share/chromedriver-linux64`

and separately Node.js 22.23.2 with npm 10.9.8. So a browser is free, and a Node
*runtime* is free. What is not free is a Node *toolchain* in this repo: a
`package.json`, a lockfile, Dependabot and Snyk coverage for a second ecosystem,
and a second place where versions drift. That cost is the reason to prefer the
Python options below where they are equal, not a claim that Node tools cannot
run here.

Playwright does not use the preinstalled Chrome by default; it downloads its own
Chromium. The documented Python CI step is `python -m playwright install
--with-deps` ([Playwright CI docs](https://github.com/microsoft/playwright/blob/main/docs/src/ci.md)).
If we would rather use the runner's Chrome, Playwright supports
`channel="chrome"`, with the caveat in its own docs that branded Chrome uses the
new headless mode while Playwright defaults to the chromium headless shell, "so
expect different behavior in some cases"
([browsers.md](https://github.com/microsoft/playwright/blob/main/docs/src/browsers.md)).
For stable screenshots there is also an official image,
`mcr.microsoft.com/playwright/python:v<version>-noble`.

Version compatibility with what we pin today (Python 3.11, `pytest==9.1.1`):

| Package | Version | Requires | Fits |
|---|---|---|---|
| `playwright` | 1.63.0 (2026-09-15) | Python >=3.10 | yes |
| `pytest-playwright` | 0.9.0 (2026-08-10) | Python >=3.10, `pytest<10,>=6.2.4` | yes |
| `axe-playwright-python` | 0.1.8 (2026-07-24) | Python >=3.8, `playwright>=1.36.0` | yes |
| `cogapp` | 3.6.0 (2025-09-21) | Python >=3.9 | yes |
| `axe-selenium-python` | 3.0.0 (2026-04-19) | **Python >=3.12** | no |

---

## Candidates in detail

### axe-core 4.13.0, the engine everything else wraps

Released 2026-08-05. It is a single browser-side JavaScript file; running it
needs a browser, not Node.

**`target-size` (requirement 3).** The rule is `"enabled": false` in its own
definition, and the rule-descriptions doc says of the WCAG 2.2 section: "These
rules are disabled by default, until WCAG 2.2 is more widely adopted and
required"
([rule JSON](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/rules/target-size.json),
[rule-descriptions.md](https://github.com/dequelabs/axe-core/blob/v4.13.0/doc/rule-descriptions.md)).
The check carries `"options": {"minSize": 24}`
([check JSON](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/checks/mobile/target-size.json))
and the evaluator reads it: `const minSize = options?.minSize || 24;`
([target-size-evaluate.js](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/checks/mobile/target-size-evaluate.js)).
`axe.configure` documents `options` as "the options structure that is passed to
the evaluate function ... the most common property that is intended to be
overridden for existing checks"
([API.md](https://github.com/dequelabs/axe-core/blob/v4.13.0/doc/API.md)), so
`minSize: 40` is a supported configuration, not a fork.

Four caveats, all read out of the source:

- The rule is `"any": ["target-size", "target-offset"]`, and `target-offset`
  carries `{"minOffset": 24}`
  ([target-offset.json](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/checks/mobile/target-offset.json)).
  Passing *either* check passes the rule, so a 37px control with generous
  spacing still passes. To enforce a hard floor, either raise `minOffset` to
  match or narrow the rule to `any: ['target-size']` through
  `axe.configure({rules: [...]})`.
- Scope is narrow by design: `widget-not-inline-matches` requires the element to
  have a widget role type, be focusable, not be an `area`, not be in the SVG
  namespace, and not be inside a text block
  ([matcher](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/rules/widget-not-inline-matches.js)).
  The inline-text exclusion is WCAG 2.5.8's own "Inline" exception, so a short
  link inside a sentence is not measured.
- Our `details.fold` and `details.lb-team` disclosure controls *are* in scope:
  axe maps `summary: 'button'`
  ([implicit-html-roles.js](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/commons/standards/implicit-html-roles.js)),
  which is a widget role.
- The threshold is in CSS pixels, but ours is written `2.5rem`, and `DESIGN.md`
  records that Pico scales the root font with the viewport (16px on phones, 18px
  from 768px, and up). 2.5rem is 40px only at a 16px root, so the run has to pin
  the viewport to the breakpoint being claimed. The defects in #158 and #159
  were all measured at 375px; the check should be too.

**`color-contrast` (requirement 5).** Thresholds are configurable:
`"contrastRatio": {"normal": {"expected": 4.5}, "large": {"expected": 3}}`, as
are `largeTextPt: 18` and `boldTextPt: 14`
([check JSON](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/checks/color/color-contrast.json)).

Opacity compositing is real and is the part worth knowing. `getForegroundColor`
calls `calculateBlendedForegroundColor`, which walks the node's stacking
contexts, multiplies `fgColor.alpha *= context.opacity`, and flattens against
the stack behind it
([get-foreground-color.js](https://github.com/dequelabs/axe-core/blob/v4.13.0/lib/commons/color/get-foreground-color.js)).
Two fixes in 4.7.0 (2023-04-17) put it there: "correcly apply opacity to
foreground color" (#3973) and "correctly compute background color for elements
with opacity" (#3944)
([CHANGELOG](https://github.com/dequelabs/axe-core/blob/v4.13.0/CHANGELOG.md)).
This is the same arithmetic that made `.stat-tribe` measure 2.80:1 and 3.57:1 in
#159 while its declared colour passed.

The trap: when axe cannot determine the backdrop it returns **incomplete**, not
a violation. The check's own incomplete messages include `bgImage`
("could not be determined due to a background image"), `bgGradient`,
`bgOverlap`, `fgAlpha` and `outsideViewport`. `DESIGN.md` has a whole
"Gradient Treatments" section, so some of our text sits on gradients and will
land in `incomplete`. A gate written `assert results.violations_count == 0`
passes those silently. Gate on `incomplete` as well, or keep a short explicit
list of colour pairs computed in Python for the gradient cases.

**Heading rules (requirement 7).** `heading-order` and `page-has-heading-one`
both exist, both carry `cat.semantics, best-practice`, and `empty-heading` is
`best-practice` too. `axe.run` with no `runOnly` runs every enabled rule
including best practices; a run scoped to `runOnly: ['wcag2a','wcag2aa']` skips
all three. Rules can be enabled individually through
`rules: {'page-has-heading-one': {enabled: true}}`, and the docs describe
combining `runOnly` tags with `rules` to "include rules with unspecified tags".

**No rule exists** for computed font size (requirement 4) or for horizontal
overflow (requirement 6). The only font-size-adjacent rule is `p-as-heading`,
which is about styling a `<p>` to look like a heading.

### axe-playwright-python 0.1.8, the Python runner

PyPI 0.1.8, uploaded 2026-07-24, by pamelafox;
[repo](https://github.com/pamelafox/axe-playwright-python). Depends only on
`playwright>=1.36.0`. It vendors `axe_playwright_python/axe.min.js` (572 KB) and
its CHANGELOG records "Upgraded axe-core JS version to v4.12.1" for 0.1.8, with
a GitHub Action that repacks the npm tarball to update it
([install-axe.sh](https://github.com/pamelafox/axe-playwright-python/blob/main/.github/scripts/install-axe.sh)).
So there is no Node dependency at test time: the engine is a vendored browser
script.

What matters for us is how thin the wrapper is. `Axe.run(page, context, options)`
does exactly this
([sync_playwright.py](https://github.com/pamelafox/axe-playwright-python/blob/main/axe_playwright_python/sync_playwright.py)):

```python
page.evaluate(self.axe_script)  # re-injects axe on every call
command = "axe.run(%s).then(results => {return results;})" % args_str
response = page.evaluate(command)
```

Consequences:

- `options` here are **`axe.run` options**, so enabling `target-size` works, but
  check options such as `minSize` need `axe.configure`, which the wrapper does
  not expose. Because `run()` re-evaluates the whole engine bundle on each call,
  configuration applied before a `run()` does not survive it. The reliable shape
  is to do the two evaluates ourselves, in order: inject
  `axe_playwright_python.base.AXE_SCRIPT` (or our own vendored copy), then
  `axe.configure({...})`, then `axe.run(...)`.
- Default options are `{"resultTypes": ["violations"]}`, and `AxeResults`
  exposes `violations_count`, `generate_report()` and `generate_snapshot()`
  ([base.py](https://github.com/pamelafox/axe-playwright-python/blob/main/axe_playwright_python/base.py)).
  `generate_snapshot` is built for snapshot testing, which collides with this
  repo's rule against blind snapshot updates. Prefer asserting counts and
  printing `generate_report()` on failure.

Other Python runners, for the record:

- `axe-selenium-python` 3.0.0 (2026-04-19) is maintained but declares
  `requires_python >=3.12`. CI pins 3.11, so it is out until that moves.
- `pytest-axe` 1.1.6 last shipped 2018-11-12 and pins
  `axe-selenium-python==2.1.5`. Dead.
- `axe-core-python` 0.1.0 last shipped 2022-09-01. Dead.
- No other Python accessibility runner exists on PyPI under the obvious names
  (`pytest-a11y`, `pytest-accessibility`, `accessibility-checker`,
  `playwright-axe` all 404).

### @axe-core/cli and @axe-core/playwright 4.13.0 (Node)

`@axe-core/cli` takes `--rules`, `--tags`, `--disable` and `--exit` ("exit with
a failure code 1 when any rule fails to pass"), and drives Chrome through
ChromeDriver
([README](https://github.com/dequelabs/axe-core-npm/blob/develop/packages/cli/README.md)).
There is no flag for check options, so the 24px default stands and requirement 3
is out of reach. `@axe-core/playwright` can reach `axe.configure` through its
builder API, but it buys nothing over the Python route except a Node toolchain.

### Lighthouse 13.5.0 and Lighthouse CI 0.15.1

Lighthouse runs axe for its accessibility category, with the configuration
hard-coded in the gatherer: `runOnly: {type: 'tag', values: ['wcag2a',
'wcag2aa']}` plus an explicit rule list that includes `'heading-order':
{enabled: true}`, `'empty-heading': {enabled: true}` and `'target-size':
{enabled: true}`, and does **not** include `page-has-heading-one`
([accessibility.js](https://github.com/GoogleChrome/lighthouse/blob/v13.5.0/core/gather/gatherers/accessibility.js)).
There is no passthrough for check options, so `target-size` runs at 24px.

Two removals matter and are easy to get wrong from memory:

- The old SEO `tap-targets` audit (48px) is gone: "The `tap-targets` audit is no
  longer a priority for SEO and has been replaced with the `target-size` audit
  in accessibility" (#15906), in the 12.0.0 section of the
  [changelog](https://github.com/GoogleChrome/lighthouse/blob/v13.5.0/changelog.md).
- The `font-size` audit is gone entirely: "remove font-size audit" (#16701), in
  the 13.0.0 section. It is absent from the 13.5.0 audit tree.

Even where it existed it would not have met requirement 4. In 12.6.1 its
description reads "Font sizes less than 12px are too small to be legible ...
Strive to have >60% of page text ≥12px", and the audit scores on
`percentageOfPassingText >= MINIMAL_PERCENTAGE_OF_LEGIBLE_TEXT` with that
constant set to 60
([font-size.js](https://github.com/GoogleChrome/lighthouse/blob/v12.6.1/core/audits/seo/font-size.js)).
A single 11px badge never fails that. The gatherer is still worth reading as
precedent for how to do it properly: it calls
`DOMSnapshot.captureSnapshot({computedStyles: ['font-size', 'visibility']})`
([gatherer](https://github.com/GoogleChrome/lighthouse/blob/v12.6.1/core/gather/gatherers/seo/font-size.js)).

Lighthouse CI is the part that fails a build. `lhci assert` exits non-zero on
`error`-level assertions, keyed by audit id, with `minScore`, `maxLength` and
`maxNumericValue` as the comparators, and `collect.startServerCommand` plus
`startServerReadyPattern` to boot a Flask dev server first
([configuration.md](https://github.com/GoogleChrome/lighthouse-ci/blob/v0.15.1/docs/configuration.md)).
Note the version skew: `@lhci/cli` 0.15.1 was published 2025-06-25 and pins
`"lighthouse": "12.6.1"`
([package.json](https://github.com/GoogleChrome/lighthouse-ci/blob/v0.15.1/packages/cli/package.json)).
So LHCI today still has the `font-size` audit that upstream Lighthouse deleted.
Building a gate on an audit the owner has removed is building on sand.

Lighthouse does carry one thing relevant to requirement 1: `unused-css-rules`
("Reduce unused CSS"), fed by a gatherer that is a thin wrapper over the
protocol calls below
([css-usage.js](https://github.com/GoogleChrome/lighthouse/blob/v13.5.0/core/gather/gatherers/css-usage.js)).
It reports unused **bytes** per stylesheet, not selectors, so it can tell us
"most of style.css is unused on this page" and never name a dead rule.

### Chrome DevTools Protocol CSS rule usage

The mechanism behind every "unused CSS" feature, and directly usable from
Python:

- `CSS.startRuleUsageTracking`: "Enables the selector recording."
- `CSS.stopRuleUsageTracking` returns `ruleUsage`.
- `CSS.RuleUsage` is `{styleSheetId, startOffset, endOffset, used}`, where
  `used` means "whether the rule was actually used by some element in the page".

([protocol reference](https://chromedevtools.github.io/devtools-protocol/tot/CSS/),
read from
[browser_protocol.json](https://github.com/ChromeDevTools/devtools-protocol/blob/master/json/browser_protocol.json);
the CSS domain is marked experimental.)

Reachable from Playwright Python through
`browser_context.new_cdp_session(page)`, which exists in
`playwright/sync_api/_generated.py` at 1.63.0. Playwright's convenience
`Coverage` class (`page.coverage.startCSSCoverage`) is **JavaScript only**:
there is no `Coverage` class and no `coverage` attribute anywhere in the Python
generated API.

Two honest limits before using this for requirement 1. Offsets are byte offsets,
so mapping a result back to a selector means slicing the stylesheet text
ourselves. And "used" is observational: a rule that only matches in a state the
run never enters (hover, an open `details`, a media query that does not match at
the test viewport) reports unused. That is the same false-positive class as
PurgeCSS arriving from the opposite direction, and it is why the existing
hand-rolled check limits itself to positional selectors with a documented
allowlist.

### Playwright's own assertions

`expect(locator).to_have_css(name, value)` "Ensures the Locator resolves to an
element with the given computed CSS style"
([class-locatorassertions.md](https://github.com/microsoft/playwright/blob/main/docs/src/api/class-locatorassertions.md)),
and it is available in Python. This is the right instrument for requirement 2:
instead of re-deriving specificity arithmetic from the stylesheet text, set the
viewport to 375px and assert the computed `grid-template-columns` on
`.lb-standings`. It tests the outcome the cascade produced rather than a model
of the cascade, which is the layer the claim is about.

Also present in Python: `to_match_aria_snapshot`, whose output includes heading
levels, which is an alternative route to requirement 7 if we ever want structure
locked rather than ordering checked. Not present in Python:
`to_have_screenshot`. The `PageAssertions` class at 1.63.0 exposes only title,
URL, aria-snapshot, text, attribute and class assertions, so Playwright's
built-in visual comparison is a Node-only feature.

### pa11y 10.0.0 and pa11y-ci 4.1.1 (Node)

pa11y drives Puppeteer and supports two runners, `htmlcs` (default) and `axe`,
with `--threshold <number>` to "permit this number of errors, warnings, or
notices, otherwise fail with exit code 2"
([README](https://github.com/pa11y/pa11y/blob/main/README.md)). Its axe runner
builds `axe.run` options from pa11y config: the tag list is
`wcag2a, wcag21a, wcag2aa, wcag21aa, best-practice`, and pa11y's `rules` option
maps to per-rule `{enabled: true}`
([lib/runners/axe.js](https://github.com/pa11y/pa11y/blob/main/lib/runners/axe.js)).

So pa11y covers requirement 7 out of the box (its tag list includes
`best-practice`, unlike Lighthouse's), and can enable `target-size` by id, but
it never calls `axe.configure`, so the 24px threshold is fixed. Note also that
`wcag22aa` is absent from its tag list, so the rule has to be named explicitly.

### IBM Equal Access `accessibility-checker` 4.0.34 (Node)

A different engine with its own ruleset, actively released (2026-09-08). Its
`target_spacing_sufficient` rule hardcodes 24 in several places
([rule source](https://github.com/IBMa/equal-access/blob/master/accessibility-checker-engine/src/v4/rules/target_spacing_sufficient.ts)),
and its `text_contrast_sufficient` rule returns `RulePotential` rather than a
failure when `colorCombo.hasBGImage || colorCombo.hasGradient`
([rule source](https://github.com/IBMa/equal-access/blob/master/accessibility-checker-engine/src/v4/rules/text_contrast_sufficient.ts)),
which is the same gradient softening axe does. No advantage for our thresholds,
plus a Node toolchain.

### PurgeCSS 8.0.0 and UnCSS 0.17.3

PurgeCSS "analyzes your content and your CSS files. Then it matches the
selectors used in your css files with the ones in your content files", and the
default extractor "considers every word of a file as a selector" with the noted
limitation that it "does not consider special characters such as `@`, `:`, `/`"
([introduction](https://github.com/FullHuman/purgecss/blob/main/docs/introduction.md),
[extractors](https://github.com/FullHuman/purgecss/blob/main/docs/extractors.md)).
It answers "does this class name appear as a word in a template", which is a
different question from "does this rule match an element". For our defect, the
word `compare-row` is in the template, so PurgeCSS is satisfied whether or not
the first child is the `<h3>`. Its false-positive story for JS-added classes is
managed by [safelisting](https://purgecss.com/safelisting.html), the same escape
hatch our own allowlist uses, and it is mandatory with PurgeCSS because the tool
is static.

UnCSS is the opposite trade. Its documented process is: "The HTML files are
loaded by jsdom and JavaScript is executed ... `document.querySelector` filters
out selectors that are not found in the HTML files"
([README](https://github.com/uncss/uncss/blob/master/README.md)). It accepts
URLs, so it can point at a running Flask server, and its `report` object exposes
`selectors: {all, used, unused}`
([src/lib.js](https://github.com/uncss/uncss/blob/master/src/lib.js)). Crucially
for us, its `dePseudify` ignore list covers only interaction and pseudo-element
pseudos (`:hover`, `:focus`, `:active`, `::before`, `::after` and friends);
`:nth-child` and `:first-child` are not in it, so they survive into the
`querySelector` call and a zero-match positional selector is detected.

Against that: last npm publish 2020-02-11, last repo push 2024-06-18; jsdom is
not Chrome; it "only runs the Javascript that is run on page load"; and the
report's `unused` list is computed as `difference(all, used)` where `all` holds
whole comma-separated selector strings (`rule.selector`) and `used` holds
individual de-pseudified ones, so grouped selectors appear unused even when they
are not. The trustworthy signal is which rules it removed from the output CSS,
not the report list. Reading the mechanism was worth it; adopting the tool is
not.

### stylelint 17.16.0

Worth having eventually for general CSS hygiene, but it does not solve
requirement 2, and its own docs say why. `no-descending-specificity` compares
"the last compound selector in every full selector" against others ending the
same way, and "only compares rules that are within the same media context". The
non-problem examples include precisely our shape:

```css
a { top: 10px; }
@media print {
  #baz a { top: 10px; }
}
```

([rule README](https://github.com/stylelint/stylelint/blob/main/lib/rules/no-descending-specificity/README.md)).
Our defect was further out of reach in a second way: the base rule
(`.lb-standings:not(.is-finished).no-win`) and the phone rule (`.lb-standings`)
have different last compound selectors, so they are not comparable under the
rule's own model either. Two independent reasons it cannot fire.
`selector-max-specificity` caps absolute specificity, which is a style policy,
not a statement about one rule beating another.

`stylelint-no-unused-selectors` exists but last published 2021-08-08.

### Visual regression

BackstopJS 6.3.25 (npm 2024-09-07, repo active) drives Puppeteer or Playwright,
supports a `--docker` mode "to eliminate cross-platform rendering shenanigans",
and gates on `misMatchThreshold`
([README](https://github.com/garris/BackstopJS/blob/master/README.md)). For
Python, `pytest-playwright-visual-snapshot` 0.5.1 (2026-02-05, Python >=3.11,
pillow plus pixelmatch) fills Playwright Python's missing screenshot assertion
and deliberately "fail[s] on `--update-snapshots` to make users manually review
images"
([PyPI](https://pypi.org/project/pytest-playwright-visual-snapshot/)); it is a
single-maintainer MIT project.

Either way, pixel diffing is a different net from the eight requirements. It
notices that something changed; it cannot say "this target is 37px and the floor
is 40px". It also needs baseline approval, which is the snapshot-review burden
this repo already treats with suspicion. Useful later as a safety net for the
design system, not as an answer here.

Paid SaaS, flagged and not assumed acceptable: [Percy](https://percy.io/pricing)
(BrowserStack), [Chromatic](https://www.chromatic.com/pricing),
[Applitools](https://applitools.com/pricing/). All three are hosted visual
testing billed per snapshot; they inherit the same "catches change, not
thresholds" limitation, and they add a third-party service to a site that is
otherwise entirely behind Cloudflare Access.

### Galen Framework

A layout-assertion DSL over Selenium, which on paper is the closest thing to
"assert this element is at least this big at this viewport". Last release
`galen-2.4.4` on 2019-03-15, last repository push 2022-07-15 (GitHub API). Java,
Selenium, and a bespoke spec language. Not a reasonable dependency to take on in
2026 for checks we can write in ten lines of Python.

### Doc and code correspondence

There is no tool that reads a prose claim and checks it against code. The
established patterns are both inversions of the problem:

1. **Generate the documented artefact.** `cog` embeds a Python generator in the
   file itself, and `--check` "run[s] cog just to check that the files would not
   change if run again. This is useful in continuous integration", with `--diff`
   to show what drifted and `--check-fail-msg` to tell the developer how to
   regenerate
   ([docs/running.rst](https://github.com/nedbat/cog/blob/master/docs/running.rst),
   `cogapp` 3.6.0 on PyPI). Applied here: `DESIGN.md`'s breakpoint table and
   parts of its type scale are derivable from `style.css`, so they could be
   generated rather than described.
2. **Make the claims executable.** `sybil` 10.1.0 "check[s] examples in your
   code and documentation by parsing them from their source and evaluating the
   parsed examples", and `pytest-markdown-docs` 0.9.2 runs markdown code fences
   as tests. Both only help for claims written as code blocks. `DESIGN.md`'s
   claims are prose and markdown tables, so neither applies without rewriting
   the document.

The repo has already picked a third route, and it works:
`tests/test_breakpoint_order.py::test_design_md_lists_exactly_the_breakpoints_the_sheet_uses`
parses the `## Breakpoints` section for `` `NNNpx` `` tokens, parses the sheet
for media-query widths, and asserts the two sets are equal. That is the pattern
to extend, with cog as the alternative where a table is fully machine-derivable.

---

## What to do per requirement

### 1. Selectors that match zero elements

Keep `tests/test_selector_cascade.py`. It is the right design: it parses the
sheet for positional selectors, renders the real pages, and matches with
soupsieve, with a reason required for every allowlisted exception and a guard
test that fails if the corpus goes empty.

One limit to record: soupsieve parses interaction pseudo-classes but documents
that they "will not match anything ... because they cannot be implemented
outside a live, browser environment"
([non-applicable pseudo classes](https://github.com/facelessuser/soupsieve/blob/main/docs/src/markdown/selectors/unsupported.md)).
So generalising the check beyond positional selectors in Python alone would
report every `:hover` rule as dead. If we want the general sweep, do the
matching in the browser once a Playwright fixture exists: iterate the sheet's
selectors and evaluate `document.querySelectorAll(s).length` in Chromium, which
handles every selector the browser does. Use CDP rule-usage tracking only if we
want whole-sheet coverage numbers, and expect state and viewport false
positives.

### 2. Breakpoint overrides that cannot win

No tool. Keep both hand-rolled checks, and add the outcome-level assertion: a
Playwright test at 375px that reads the computed `grid-template-columns` (and
the other breakpoint-critical properties) off the real components. The static
tests catch the mistake at the point it is made; the computed-value test is the
one that cannot be fooled by a gap in our specificity model.

### 3. Tap targets at 40px

axe-core, configured:

- enable the rule: `axe.run(..., {rules: {'target-size': {enabled: true}}})`
- set the threshold:
  `axe.configure({checks: [{id: 'target-size', options: {minSize: 40}}]})`
- decide about spacing: either `{id: 'target-offset', options: {minOffset: 40}}`
  or narrow the rule to `any: ['target-size']`
- pin the viewport to 375px so `2.5rem` is 40px
- remember the scope: widget roles, focusable, not inline in text.
  Non-focusable tappable things are not measured, so a hand-rolled
  bounding-box sweep stays useful as a complement.

### 4. 12px computed text floor

Nothing covers it. Hand-roll in the same Playwright run: walk elements with
non-empty text, read `getComputedStyle(el).fontSize`, fail under 12px, and skip
`aria-hidden` decoration (`DESIGN.md` records the season-menu caret as the one
deliberate exception). This is computed by construction, so the `em` nesting
problem (pill, then line, then badge) is handled for free. Run it at 375px and
at one wide viewport, because the root font size changes and the floor is
expressed with `max(var(--fs-floor), 0.72em)`.

### 5. Contrast with opacity

axe-core, with two adjustments:

- fail on `incomplete` as well as `violations`, or enumerate the gradient cases
  explicitly. Text on our gradient treatments will be `incomplete`, and the
  obvious assertion ignores it.
- keep thresholds explicit in the config (`contrastRatio.normal.expected: 4.5`)
  rather than relying on defaults, so the number in the test matches the number
  in `DESIGN.md`.

### 6. Horizontal overflow

Nothing covers it. There is no axe rule and no Lighthouse audit; the criterion
it maps to is WCAG 1.4.10 Reflow, "without requiring scrolling in two dimensions
for ... vertical scrolling content at a width equivalent to 320 CSS pixels"
([WCAG 2.2](https://www.w3.org/TR/WCAG22/#reflow)). Keep the probe that found
the 302px scroll in #158: set `documentElement.scrollLeft` and read it back,
which is more honest than comparing `scrollWidth` to `clientWidth`.

### 7. Heading order and one h1

Covered by axe, as long as the run is not scoped to WCAG tags only: enable
`heading-order`, `page-has-heading-one` and `empty-heading` explicitly. Note
that this requirement needs no browser at all, since heading structure is in the
server-rendered HTML; `tests/test_accessibility_markup.py` already works at that
layer and is faster. Adding the axe rules buys consistency with the rest of the
accessibility gate, not new coverage.

### 8. DESIGN.md against the stylesheet

No tool asserts the prose. Extend the pattern in
`tests/test_breakpoint_order.py` to the rest of the document's machine-checkable
claims, and consider `cog --check` for the tables that are fully derivable. Two
of the document's claims are only checkable from a rendered page, not from the
sheet: the Type Scale table says every row "was read out of a rendered page with
`getComputedStyle` rather than from the stylesheet source, because the two
disagreed". Those rows belong in the browser test, not in a text parse of
`style.css`.

---

## Shape of the change, if we take the recommendation

Dependencies, dev only (`requirements-dev.txt`), all Python:

```
playwright==1.63.0
pytest-playwright==0.9.0
axe-playwright-python==0.1.8
```

CI, one added step in the pytest job before `python -m pytest`:

```yaml
- name: Install browser
  run: python -m playwright install --with-deps chromium
```

Test design notes specific to this app:

- The pages need a server, not the Flask test client, because every one of these
  checks reads computed styles. A session-scoped fixture that boots the app on a
  free port against a temp SQLite DB is enough; `pytest-playwright` provides the
  browser fixtures and a `--base-url` option.
- Admin pages are where #158's defects were, and they need a login. `DEV_LOGIN=1`
  plus `/dev-login` is already the documented local route in, so the fixture can
  authenticate without touching Cloudflare Access.
- Run at 375px by default. Every recorded measurement in #158 and #159 is at
  375px, and the rem thresholds only equal their px values at a 16px root.
- Keep the browser suite in its own file and marker so `python -m pytest` stays
  usable without a browser installed, and so a missing browser is legible rather
  than appearing as a wall of unrelated failures.

### Prove each check against a defect that already shipped

The three recent fixes are a ready-made corpus with exact numbers, which is what
this repo asks for before a test is trusted. Each new check should be run against
the parent commit of its fix and shown to fail:

| Check | Commit to fail against | Expected measurement |
|---|---|---|
| tap target floor | before #159 | Expand all / Collapse all at 37px, survivorstatsdb link at 18px |
| tap target floor | before #158 | nine targets under the floor on the admin season page |
| contrast with opacity | before #159 | `.stat-tribe` at 3.57:1 and 2.80:1 against 4.5:1 |
| horizontal overflow | before #158 | admin season page 677px wide in a 375px viewport, 302px of scroll |
| zero-match selector | before #157 | `.compare-row:nth-child(1)` matching nothing |
| breakpoint override | before the standings fix | phone override out-specified by `.lb-standings:not(.is-finished).no-win` |
| doc correspondence | before b449921 | `DESIGN.md` naming four root-font steps where the sheet has six |

A check that cannot be made to fail on the right commit should not be merged,
and a check whose corpus can shrink to nothing needs the same vacuity guard the
existing selector tests carry.

---

## Honest negatives, collected

- **Requirement 2 has no tool.** stylelint's nearest rule documents our exact
  case as a non-problem, twice over.
- **Requirement 4 has no tool.** The one audit that ever used a 12px threshold
  was a 60%-of-text heuristic, and it has been deleted upstream.
- **Requirement 6 has no tool.** No accessibility engine implements WCAG 1.4.10
  Reflow, and no Lighthouse audit covers page overflow.
- **Requirement 8 has no tool** in the sense asked. Only inversions work:
  generate the doc, or make its claims executable.
- **Requirement 1 has a tool that would work and should not be adopted.** UnCSS
  does the right matching and is five years stale, jsdom-based, and its report's
  unused list is unreliable for grouped selectors.
- **Thresholds differ from ours almost everywhere.** axe `target-size` 24px
  (configurable), Lighthouse's removed `tap-targets` 48px, IBM 24px hardcoded,
  WCAG 2.5.8 24px, WCAG 2.5.5 44px, ours 40px. Only axe can be told our number.
- **Every engine softens gradients and background images to "needs review".**
  That is correct behaviour and a silent pass if the gate only reads violations.

## Specifications quoted

- [WCAG 2.2 SC 2.5.8 Target Size (Minimum)](https://www.w3.org/TR/WCAG22/#target-size-minimum),
  Level AA, "at least 24 by 24 CSS pixels", with Spacing, Equivalent, Inline,
  User Agent Control and Essential exceptions
- [WCAG 2.2 SC 2.5.5 Target Size (Enhanced)](https://www.w3.org/TR/WCAG22/#target-size-enhanced),
  Level AAA, "at least 44 by 44 CSS pixels"
- [WCAG 2.2 SC 1.4.3 Contrast (Minimum)](https://www.w3.org/TR/WCAG22/#contrast-minimum),
  "at least 4.5:1", large text 3:1
- [WCAG 2.2 SC 1.4.10 Reflow](https://www.w3.org/TR/WCAG22/#reflow), no
  two-dimensional scrolling at 320 CSS pixels wide
