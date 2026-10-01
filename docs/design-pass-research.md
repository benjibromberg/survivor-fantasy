# Design pass research

Research for a visual and readability pass on the Survivor Fantasy webapp (Flask, Jinja2,
Pico CSS v2). The goal set by the owner: reach the polish of a real fantasy-sports app such
as Sleeper (professional, clean, mobile-first) while keeping the Survivor theme: the Tribal
Council / Fiji Night dark palette, the torch motifs and the four-font system. A framework
rewrite was considered and declined. This is a design and CSS problem on the existing
templates.

This document changes no app code. It is meant to let the owner pick a direction and let an
implementer build it in slices.

- Date: 2026-10-01
- Baseline: `origin/main` at `5b31ba2`. Every `file:line` reference is against that commit
  unless a branch name is given.
- Section 7 lists the decisions only the owner can make.

## Status since this was written

- The `feat/mobile-responsive-v2` and `feat/player-self-login` branches have both merged to
  `main`. Where this document says either is "in flight", "open" or "local", read "merged".
  The `file:line` references still point at the baseline commit above, so line numbers have
  moved.
- The owner chose **Direction B** (Section 4). The slices common to every direction (S1 to
  S3) start once the wildcard self-service and pick-export work has merged, because those
  edit the same files.
- Question 1 in Section 7 (direction) is answered. The other questions are still open.

## How to read the evidence labels

Nothing here was rendered in a browser and the app was not run. The labels say how each
claim was established.

| Label | Meaning |
|---|---|
| **[read]** | Read directly in the repo file at the cited line. |
| **[computed]** | Calculated from values in the CSS or data files. The method is in Appendix B. |
| **[inferred]** | My reading of how the CSS will behave. Plausible, not rendered, needs a browser check. |
| **[verified]** | External claim that I read in the fetched text of the cited URL. |
| **[not verified]** | I could not confirm it from a source I fetched. Treat as unknown. |

## Summary

1. A mobile and readability branch, `feat/mobile-responsive-v2`, is in flight. It already
   delivers the phone layout work and the collapsible rules and scoring-analysis sections.
   This document treats it as the baseline and does not propose redoing it. Section 1 lists
   what it delivers and what it leaves open.
2. What stays open after that branch is mostly not about phones. It is the visual system
   (type scale, contrast, the eliminated state), the leaderboard's structure, the content
   order of the two text-heavy pages, the admin pages, and the gap between `DESIGN.md` and
   the stylesheet.
3. The patterns that could be verified for Sleeper, ESPN and Yahoo are structural: named
   league, team and matchup tabs, a summary that stays pinned while detail scrolls, condensed
   rows, tapping one item for its breakdown, a fixed status vocabulary with fixed colors. None
   of them is a paint job. Public sources for Sleeper's actual visual design are thin, and
   Section 3 says exactly what was and was not confirmed.
4. Recommendation: Direction B in Section 4. Do the system fixes first (they are needed under
   any direction), then restructure the leaderboard into standings rows with expandable
   roster cards and make My Team the player's home. Treat the mobile bottom tab bar as a
   separate, optional slice, because it is the least supported part of the plan.

## 1. What is in flight

Four branches touch the same files as a design pass. Merge results below are from
`git merge-tree`, so they are textual only. A clean textual merge can still be wrong in the
browser.

| Branch | State | Touches | Merge check |
|---|---|---|---|
| `feat/mobile-responsive-v2` | Open pull request | `DESIGN.md` (new), `app/static/style.css` (631 lines added, 102 removed), `base.html`, `leaderboard.html`, `rules.html`, `scoring_analysis.html` | Clean into `main`. Clean with `feat/player-self-login`. |
| `fix/mobile-responsive` | Older open draft pull request | `DESIGN.md` (new), `style.css`, `rules.html`, `scoring_analysis.html`, three admin templates | Clean into `main`. Conflicts with the v2 branch in `DESIGN.md`, `style.css` and `rules.html`. Conflicts with `feat/player-self-login` in `admin/players.html`. |
| `feat/player-self-login` | Local, not pushed | Adds `my_team.html`, a My Team nav link, a team-name label on leaderboard entries, about 150 lines of CSS, two inline forms in `admin/players.html` | Clean into `main`. |
| `docs/perf-research` | Open pull request, docs only | `docs/perf-research.md` | No file overlap. Its plan overlaps slices S4, S6 and S8 below. |

### 1.1 What `feat/mobile-responsive-v2` already delivers

All **[read]** from `git diff origin/main...origin/feat/mobile-responsive-v2`.

- `body { overflow-x: hidden }` and 1rem side padding on `main.container`.
- Phone nav at 640px and below: the logo gets its own row, the links wrap on a second row,
  each link is at least 2.5rem tall, and the seasons dropdown spans the nav width.
- Timeline: the connecting line is drawn per point so it scrolls with the dots, each point is
  at least 2.75rem wide with vertical padding on phones, and the active episode is scrolled
  into view on load.
- Leaderboard header on phones: rank, name and points on the first line, with the Sole
  Survivor pick, win percentage and warnings on a second line (new `.lb-sub` wrapper).
- Pick pills on phones: a two-column grid with 48px headshots, switching to one column when
  any detail toggle is on.
- Toolbar toggles on phones: bordered chips at least 2.5rem tall.
- A floor on secondary text sizes on phones (breakdown, bio, journey, badges and team stats
  move to 0.72rem to 0.8rem).
- Collapsible sections: every `<article>` on the rules and scoring-analysis pages becomes
  `<details class="fold">` with the `<h2>` inside the `<summary>`. Paragraphs and lists inside
  are capped at 68ch with line-height 1.6. The rules page gets Expand all and Collapse all.
  On phones, sections marked `data-mobile-collapsed` start closed. Deep links and contents
  links open the target section first.
- Tables: a `.table-scroll` wrapper with edge shadows, and tables directly inside `article`
  or `section` scroll sideways.
- Contents panel on phones: a full-width bottom sheet with larger links.
- Charts: explicit heights on tablet and phone, and a taller aspect ratio for the analysis
  charts on phones.
- `DESIGN.md`, including a rule that responsive overrides live in one block at the end of
  `style.css`.

### 1.2 What it leaves open

These are the subject of the rest of this document.

- Desktop and tablet layout above 768px is unchanged.
- Contrast, the eliminated state, motion preferences and keyboard focus (Section 2.1).
- The type scale. The branch adds sizes rather than consolidating them: 95 `font-size`
  declarations with 35 distinct values, up from 66 and 24 on `main` **[computed]**. Five
  elements inside a pick pill still compute below the 11 to 12px floor that the branch's own
  `DESIGN.md` states (table in Section 2.1).
- Leaderboard structure: one long page, global detail toggles, duplicated pick markup.
- The content of the rules and analysis pages: order, examples, stale copy. The branch wraps
  the existing sections; it does not reorder or rewrite them.
- Admin, compare, stats, settings, login and the empty states.
- The 69 inline `style=""` attributes in templates (none removed).
- `DESIGN.md` still disagrees with the stylesheet in several places (Section 2.3).
- Leftover cascade debris: three older media blocks remain mid-file on the branch
  (`style.css` lines 1678, 1779 and 1797 there) and the finale phone rules now exist twice
  **[read]**.

Four things on the branch are worth a browser check before or just after it lands. They are
notes for the design pass, not a request to redo the work.

- `<h2>` inside `<summary>`. A `<summary>` behaves like a button. Whether screen readers
  still expose the heading inside it varies **[not verified]**. The GOV.UK accordion does the
  reverse and puts the button inside the `<h2>`
  ([source](https://design-system.service.gov.uk/components/accordion/)) **[verified]**.
- The phone collapse happens in a script that runs after the content is parsed
  (`base.html` on the branch, line 111), so sections may paint open and then close
  **[inferred]**.
- `article > table, section > table { display: block }` applies at every width. A table set
  to `display: block` usually stops stretching to the container width, so admin tables may
  shrink to their content on desktop **[inferred]**.
- On the analysis page the charts stay open on phones while Key Findings, Top Configurations
  and Recommended Configuration start closed **[read]**. That puts 31 charts ahead of the
  plain-language answer. Section 5 proposes the reverse.

### 1.3 Recommended landing order

1. `feat/mobile-responsive-v2` first. It is the largest stylesheet change and everything else
   rebases onto it more easily than the reverse.
2. Close `fix/mobile-responsive` as superseded. Two things exist only there: `.table-scroll`
   wrappers in three admin templates, and `flex-wrap` on two admin form rows. The v2 branch
   covers admin tables through the generic `section > table` rule, except the scoring table in
   `admin/season_detail.html:40`, which sits inside a `<form>` and is not matched **[read]**.
   The older branch also sets `main.container { max-width: 100% }`, which outranks Pico's
   `.container` max-widths and would let content run the full window width on desktop
   **[inferred]**. The v2 branch does not carry that rule.
3. `feat/player-self-login`, rebased onto v2, with three small follow-ups:
   - Its own `@media (max-width: 640px)` nav rule (line 145 of `style.css` on that branch)
     duplicates what v2 does at the end of the file. Remove it or move it into v2's block.
   - `.lb-team-name` sits in the leaderboard header between the name and the badges. The v2
     phone header was laid out without it, and team names can be 40 characters
     (`app/models.py:14` on that branch), so the first header line will wrap **[inferred]**.
   - My Team adds a nav link. A logged-in admin then has seven nav items (Season, Rules,
     Analysis, My Team, Admin, the username and Logout), which will wrap to more than one row
     on a phone under v2's layout **[inferred]**.
4. Design-pass slices (Section 6), all based on the result.

## 2. Current state audit

Findings are against `main`. Each carries a status for the v2 branch: **fixed by v2**,
**partly**, or **open**.

### 2.1 Cross-cutting

**Type sizes compound to 7 to 9px on phones. Status: partly.**
Pico v2.1.1 sets the root size to 100% below 576px and scales it up to 125% at 1280px and
131.25% at 1536px **[read in pico.css]**. Sizes in `style.css` are in `em` and nest, so the
pill content multiplies down. Apple lists 11pt as the minimum text size on iOS
([source](https://developer.apple.com/design/human-interface-guidelines/typography))
**[verified]**.

| Element at 576px and below | Rule on `main` | px on `main` | px on v2 |
|---|---|---|---|
| Pill base `.lb-pick` | `style.css:1253` (0.8em) | 12.8 | 13.6 |
| Castaway name | `style.css:1262` (0.85em) | 10.9 | 12.5 |
| Points line `.lb-pick-meta` | `style.css:1266` (0.7em) | 9.0 | 10.9 |
| Pick-type badge, nested in the points line | `style.css:849` (0.8em) | 7.2 | 8.7 |
| Stats line | `style.css:639` (0.7em) | 9.0 | 10.2 |
| Result or tribe | `style.css:812` (0.72em) | 9.2 | 10.6 |
| Breakdown item | `style.css:801` (0.68em) | 8.7 | 12.5 |
| Bio | `style.css:675` (0.68em) | 8.7 | 12.5 |
| Journey badge | `style.css:704` (0.6em) | 7.7 | 11.5 |
| Journey text | `style.css:791` (0.72em) | 9.2 | 12.8 |
| "Sole Survivor" tag, nested in the name | `style.css:1695` (0.65em) | 7.1 | 8.1 |
| Timeline episode label | `style.css:364` (0.65em) | 10.4 | 10.4 |

All px values **[computed]**. On desktop the same rules land between 10 and 17px because the
root is 20px, which suggests the sizes were tuned on a wide screen.

**Body text is not in the font `DESIGN.md` says. Status: open.**
`DESIGN.md` assigns Palatino (`--font-tribal`) to "body paragraphs". No rule sets a body
font. `--font-tribal` is used only for `.lb-pick-name` (`style.css:627`) and `.stat-name`
(`style.css:964`) **[read]**. Body copy therefore renders in Pico's `system-ui` stack
**[read in pico.css]**. Either the document or the stylesheet has to change (open question 4).

**Several text and state colors fail contrast. Status: open.**
WCAG 2.2 asks for 4.5:1 on normal text and 3:1 on large text
([source](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)) **[verified]**.
Ratios below are **[computed]**.

| Pairing | Where | Ratio |
|---|---|---|
| `--palm-green` text on `--night-sky` | `.flash-success`, `style.css:269` | 2.72:1 |
| `--palm-green` on its own 20% tint | `.lb-badge-merge`, `style.css:716` | 1.91:1 |
| `--fire-bright` at 0.6 opacity on the card | `.lb-rank`, `style.css:485-486` | 2.36:1 |
| `--fire-bright` at 0.5 opacity on the card | `.stat-rank`, `style.css:947-948` | 2.00:1 |
| Castaway name inside an eliminated pill (0.3 opacity) | `.lb-pick.eliminated`, `style.css:597-599` | 2.32:1 |
| `--fire-bright` on its own 15% tint | `.lb-badge-idol`, `style.css:715` | 3.47:1 |
| `#5b8fb9` on its own 15% tint | `.lb-badge-advantage`, `style.css:718` | 3.31:1 |
| `--text-dim` on the card (for reference, passes) | many | 6.21:1 |

**"Eliminated" has five different treatments. Status: open.**
`.lb-pick.eliminated` is 0.3 opacity (`style.css:597`). The generic `.eliminated` is 0.35 with
a strikethrough on `.survivor-name` (`style.css:1122-1128`). `.stat-row.eliminated` is 0.4
(`style.css:940`). `.lb-ss-pick.eliminated` is 0.5 with a strikethrough
(`style.css:505-508`). `admin/season_detail.html:89` sets 0.5 inline. `DESIGN.md` says
0.55. **[read]** This matters more than it looks: by the end of a season most castaways are
out, so most of the leaderboard is drawn at 30% opacity and reads at about 2.3:1.

**Small touch targets. Status: fixed by v2 on phones.**
On `main` a timeline point's link box is only its dot: the labels are absolutely positioned
(`style.css:349-371`) and hidden on phones for ordinary episodes (`style.css:1283-1289`). For
a 14-point timeline on a 375px screen that is about 22 by 10 CSS px **[computed]**. WCAG 2.2
sets a 24 by 24 CSS px minimum
([source](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)) and web.dev
recommends about 48px with about 8px between targets
([source](https://web.dev/articles/accessible-tap-targets)) **[verified]**. The v2 branch
brings timeline points to about 44 by 29px and nav links, toggles and the contents button to
about 40px **[computed]**. Between 577 and 768px (touch tablets) the old dot sizes still
apply.

**Breakpoints and cascade. Status: partly.**
`main` has two widths (576 and 768) spread over six blocks (`style.css:85`, `1223`, `1461`,
`1744`, `1845`, `1863`) plus one in `scoring_analysis.html:1003`. The phone override for the
contents panel (`style.css:1312-1315`) comes before the base rule (`style.css:1343-1347`), so
it never applies **[read]**. The v2 branch moves most overrides to the end of the file and
adds 640px, which is the right structure. See Section 1.2 for what is left over.

**69 inline `style=""` attributes. Status: open.**
Counts are **[computed]**: `admin/season_detail.html` 18, `admin/seasons.html` 17,
`leaderboard.html` 12, `scoring_analysis.html` 9, `admin/players.html` 6, and one or two each
in `admin/picks.html`, `stats.html`, `compare.html`, `settings.html` and `auth/login.html`.
They fall into three groups:

- The same button sizing repeated by hand, `padding:0.3rem 0.75rem;font-size:0.85rem;margin:0;`,
  for example `admin/season_detail.html:8`, `:9`, `:12`, `:16`, `:36-38`, `:115` and
  `admin/seasons.html:19`, `:30`, `:60`, `:61`, `:66`. This is a missing small-button class.
- Data-driven tribe colors, three attributes per pick (`leaderboard.html:132-133`,
  `:138-139`, `:152`, and again at `:203-204`, `:209-210`). One custom property per card
  (`style="--tribe: ..."`) would replace all three.
- One-off layout (`settings.html:4`, `auth/login.html:4`, `compare.html:36`).

`scoring_analysis.html` also carries a 120-line `<style>` block at the end of the page
(`:916-1037`) and injects inline styles from JavaScript (`:479`, `:565`, `:579`, `:587`).

**Tokens are bypassed. Status: open.**
- `--font-logo` is defined (`style.css:47`) and never used. The family name is written out at
  `style.css:112`, `:1451` and `:1493`. Bebas Neue is written out at `style.css:703` and
  `:782` **[read]**.
- 23 hex colors sit outside `:root` in `style.css`, for example `:717-718`, `:770-774`,
  `:1473` **[computed]**.
- The chart palette is pasted five times (`leaderboard.html:294`, `:306-309`, `:392-395`,
  `compare.html:55-58`, `scoring_analysis.html:208-211`). The mini standings at
  `leaderboard.html:294` and the chart at `:306` are matched to each other only by array
  index, which is fragile **[inferred]**. `#9aa5b1` (the value of `--text-dim`) is hard-coded
  through every chart config.
- 47 `!important` declarations, more than half of them in the nav (`style.css:94-198`)
  **[computed]**.
- There are no spacing or type-scale tokens. Border radius uses nine different fixed values,
  from 3px to 2rem **[computed]**.

**Motion and keyboard access. Status: open.**
- No `prefers-reduced-motion` query exists **[computed]**. The finale banner pulses forever
  (`style.css:1479`) and 30 ember particles loop forever (`style.css:1568-1620`). WCAG 2.2.2
  (Level A) covers moving content that "(1) starts automatically, (2) lasts more than five
  seconds, and (3) is presented in parallel with other content" and asks for "a mechanism for
  the user to pause, stop, or hide it"
  ([source](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html)) **[verified]**.
- The Season button removes its outline (`style.css:187`) and the dropdown items use
  `all: unset` (`style.css:226-227`), which removes their focus ring as well **[read]**.
- The Season button and the contents button have no `aria-expanded`, and the dropdown has no
  Escape handling (`base.html:35`, `:68`, `:86-98`) **[read]**.
- A finished season has two `<h1>` elements (`leaderboard.html:13` and `:44`) **[read]**.

### 2.2 Page by page

#### `base.html` (shell)

- The nav holds the logo plus three to six links in one unwrapped flex row. Pico's `nav` is
  `display: flex` with no wrap **[read in pico.css]**. Both the v2 branch and
  `feat/player-self-login` add their own fix for narrow screens, which indicates the overflow
  is real; I did not measure it. Status: fixed by v2.
- The contents panel is built from every `main h2` and shown whenever a page has two or more
  (`base.html:107-113`). That switches it on for admin pages (`admin/season_detail.html` has
  three `<h2>`, `admin/picks.html` has two) and for the compare page, where it adds little.
  It ignores `<h3>`, so rules subsections are unreachable from it. The trigger is an
  icon-only button, with no visible label, in the bottom-left corner (`base.html:68-70`, `style.css:1321-1335`). On
  screens narrower than 1200px it never reopens by itself (`base.html:130`) and the panel
  covers content when open. Status: partly (v2 restyles it for phones only).
- The footer is two centered lines (`base.html:156-161`). Fine as is.

#### `leaderboard.html`

- **One page carries everything.** Finale banner, season heading, timeline, toolbar, one card
  per player, a progression chart, two team charts and the season stat boards
  (`leaderboard.html:4-502`). The only wayfinding is the floating contents button. Status:
  open.
- **The header row has up to seven competing items** on one baseline: rank, name, winner
  badges, Sole Survivor pick, win percentage, points, warnings (`leaderboard.html:97-120`).
  Warnings come after the points in the markup (`:117-119`), and the `.lb-warnings` wrapper
  styled at `style.css:555-560` is never used. Status: partly (v2 splits the row on phones).
- **Pick pills are ragged.** `.lb-pick` is a content-sized pill in a wrapping flex row
  (`style.css:572-590`) with an 80px headshot (`style.css:602-603`). Widths differ per
  castaway, and with any toggle on, heights differ too (`style.css:686-692`). Status: fixed by
  v2 on phones (two-column grid), open on tablet and desktop.
- **Detail is global, not per item.** Five switches (`leaderboard.html:68-91`) each add a
  block of text to every pill on the page at once. With all five on, a pill can hold a name,
  points, a result, a point breakdown, a stats list, a bio, badges and a journey list. Status:
  open.
- **Near-duplicate markup.** The draft pick block (`leaderboard.html:128-193`) and the special
  pick block (`:199-265`) are about 65 lines each and differ in a handful of places.
  `my_team.html` on the self-login branch holds a third copy (`:66-93`). The toggle script
  repeats one block five times (`leaderboard.html:504-555`). The torch SVG is pasted inline
  twice (`base.html:18-26`, `leaderboard.html:98`). Status: open.
- **Rank carries little meaning.** The numeral is 2em Bebas at 0.6 opacity
  (`style.css:481-489`). Every card has the same left accent (`style.css:468`). Mid-season,
  first place looks like fourth. Status: open.
- **Nothing marks the viewer's own team**, even on the self-login branch, which adds only the
  team-name label. Status: open.
- **The empty state is one sentence** ("No picks submitted yet.", `leaderboard.html:279`).
  Status: open.
- **Scoring is named but not linked.** "Scoring: Classic" (`leaderboard.html:45`) is plain
  text. The rules route accepts a season id (`app/routes.py:554`) but nothing links to it per
  season. Status: open.
- Points are always printed with two decimals (`leaderboard.html:116`, `:145`). Status: open
  question 9.

#### `rules.html`

- Ten equal-weight cards, all open, in this order: How It Works, The Draft, Wildcard Pick,
  Replacement Picks, Sole Survivor Pick, Scoring Components, Example Calculation, Pick Type
  Summary, Win Probabilities, Data Sources (`rules.html:10-260`). The Pick Type Summary table,
  the best one-screen overview, is eighth (`:188-224`). The points table is buried in the
  sixth card, after the progressive-value explainer and the same-day tribal note (`:123-144`).
  Status: partly (v2 makes the cards collapsible and keeps the order).
- **Line length on desktop.** At 1280px the text column is about 1160px at a 20px root,
  roughly 115 to 130 characters per line **[computed]**. WCAG 1.4.8 (Level AAA) sets 80
  ([source](https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation.html))
  **[verified]**. Status: fixed by v2 (68ch).
- **The worked examples are the hardest part to read.** Four are hidden behind a bare
  "Example" label (`:27-30`, `:58-62`, `:72-76`, `:113-121`). The separate Example Calculation
  card puts the arithmetic in `<small>` run-on parentheses (`:166-184`). When a season has no
  merge data that card renders with a heading and no body, because the guard is inside the
  card (`:160-162`). Status: open.
- **Copy problems** (all open, all **[read]**):
  - The Sole Survivor example uses the cast size as the episode count: "Player A wins the
    season ({{ season.num_players if season else 18 }} episodes)" (`:74-75`).
    `season.num_episodes` exists and is used in `admin/season_detail.html:23`.
  - The win-probability text says rates come from "all 50 US seasons" (`:242`, `:246`). The
    code filters to seasons 41 and later (`app/predictions.py:81-119`).
  - Two consecutive paragraphs say the same thing about progressive tribal values (`:85` and
    `:88`).
  - "FTC" appears once, unexplained (`:168`).
- **A test pins one string.** `tests/test_active_season.py:168` asserts
  `Scoring for <strong>Season 61</strong>` in the response. Keep that markup or update the
  test in the same change.

#### `scoring_analysis.html`

- 1,038 lines: about 180 of markup, 730 of JavaScript and 120 of CSS. All numbers arrive from
  `static/scoring_analysis.json` after load, so the headline shows "..." first (`:6`, `:8`)
  and the summary card is hidden until then (`:13`), which shifts the layout **[inferred]**.
  Status: open.
- **The order answers the owner's questions before the player's.** Summary, then methodology
  with a 12-row metric table and weights (`:20-79`), a histogram, nine per-season cards with
  six bullets each, four closeness charts, eight comparison charts, 19 parameter charts, and
  only then Key Findings, Top Configurations and Recommended Configuration (`:158-180`). What
  a player wants to know (what changed and why) is in the last three. The page holds 32
  canvases once loaded **[computed]**. Status: partly (v2 makes the sections collapsible and
  keeps the order).
- **One chart slot is always blank.** `chart-placement_ratio` (`:140`) has no matching key in
  the JSON's `param_impact` **[computed]**. Status: open.
- **Stale or contradictory copy** (all open):
  - "stratified random sampling (25,000 configs)" (`:77`) next to a loaded total of 103,648
    **[computed from the JSON]**.
  - "The current default and legacy configs are marked" (`:84`), but `default_composite` is
    null in the JSON, so only Legacy and Recommended are drawn **[computed]**.
  - The season count and range (nine seasons, 41 to 49) are hard-coded in the intro (`:7`).
- **Statistical notation in the main flow**: "3σ" bands (`:238`), "pp" deltas (`:252`),
  "Spearman's rho" (`:761`). Status: open.
- **The recommended configuration is a `<pre>` dump** of `label: value` lines, zero-valued
  rows included (`:178`, `:883-890`). Status: open.
- On `main`, `.param-chart-grid` is two columns at every width (`:1008-1012`) and
  `.season-health-grid` has a 320px minimum (`:950`), wider than a phone's content box
  **[computed]**. Status: fixed by v2.

#### `compare.html`

- Each config lists every non-zero value as a tag whose meaning is only in a `title`
  attribute (`:16-22`), which touch screens do not show. Status: open.
- The first three rows are tinted gold whatever the league size (`style.css:1193-1195`).
- `.compare-grid`, `.compare-card` and `.compare-rankings` (`style.css:1148-1183`) are not
  used by any template **[computed]**.

#### `stats.html`

- Nothing links to it. The route exists (`app/routes.py:1046`) and no template calls it
  **[computed]**. The leaderboard renders the same boards itself (`leaderboard.html:474-502`).
- It has drifted from that copy: it uses the raw tribe color without the `contrast` filter
  (`stats.html:27`, `:31`) and adds a fifth child (`.stat-tribe`) to a row whose grid defines
  four columns (`style.css:918`), so the value probably wraps to a second line **[inferred]**.
- Decide: link it and remove the boards from the leaderboard, or delete it (open question 8).

#### `admin/*.html`

- 43 of the 69 inline styles are here.
- `admin/seasons.html`: an eight-column table with three unlabeled action columns
  (`:41-43`), a Delete button on every row next to Manage and Players (`:66`), a
  `prompt()`-based confirmation (`:64`), and an Active checkbox that submits on change with no
  label (`:56-57`).
- `admin/season_detail.html`: a row of four actions styled inline (`:7-18`) and the scoring
  form table (`:40-51`).
- `admin/picks.html`: four full-cast `<select>` lists (`:76-122`). The help text describes a
  rule the public rules page no longer states ("Earns half points minus pre-jury tribals" at
  `:86`, and the full-points version of the same sentence at `:97`).
- `admin/players.html` on the self-login branch gains two inline forms per row, each with a
  14rem minimum width. The table will scroll sideways on anything narrower than a laptop
  **[inferred]**.
- Status: open. The v2 branch only adds horizontal scrolling for tables.

#### `settings.html`, `auth/login.html`, `no_season.html`

- Two centered 400px cards with inline styles and a two-line empty state. Low traffic. Fold
  them into the admin tidy slice.

#### `my_team.html` (on `feat/player-self-login`)

- The stat tiles for rank, points and winner pick (`:27-43`) are the closest thing in the
  codebase to a fantasy-app team header and are worth promoting to a shared component.
- The Team Name form sits above the picks (`:45-59`), so a setting comes before the content.
- Heading order goes `h1`, `h3`, `h2` (`:10`, `:46`, `:61`).
- Picks use a third copy of the pill markup and show less than the leaderboard: no breakdown,
  no journey, no badges (`:66-93`).
- Empty states are single sentences (`:7`, `:95`).

### 2.3 `DESIGN.md` against the stylesheet

`DESIGN.md` arrives with the v2 branch. These disagreements exist on that branch as well.
All **[read]**.

| `DESIGN.md` says | The stylesheet does |
|---|---|
| Rank 1.6em, points 1.3em, player name 1.2em | 2em, 1.4em, 1.25em (`style.css:483`, `:522`, `:493`) |
| Leaderboard entry has a fire-bright left accent | `--fire-glow` (`style.css:468`) |
| Eliminated picks dim to 0.55 | 0.3 (`style.css:598`) |
| Palatino for body paragraphs | No body font rule; Pico's system sans |
| Custom properties live on `:root` | Font variables are on `[data-theme="dark"]` (`style.css:46-50`) |
| `--font-logo` is the logo font variable | Defined, never referenced |
| Pick pill gap 0.4rem | 0.5rem (`style.css:575`) |
| Stats grid items have "a label (Bebas Neue, dim) and value" | The grid holds stat boards of ranked rows |
| All responsive overrides live in one block at the end of the file | Three earlier blocks remain on the branch |
| Minimum text size on phones about 11 to 12px | Five pill elements compute to 8.1 to 10.9px |

## 3. What "Sleeper-style polish" means in practice

### 3.1 Source quality

Sleeper's interface lives in its apps. Sleeper's public material is a help center, an App
Store listing and a marketing site. I found no official design-system or design-blog post.
The help-center articles name screens and controls but show the interface in images, which I
did not see and do not describe. So:

- What is **verified** for Sleeper is vocabulary, structure and stated priorities.
- What is **not verified** for Sleeper: the position and labels of its main navigation (I
  cannot confirm it uses a bottom tab bar), its color palette and whether dark is the default
  theme, its typography, its row and card layout, its empty states, and how rules are shown
  inside the app.

ESPN and Yahoo published primary-source descriptions of their 2025 and 2024 redesigns, which
fill some of the gap. General guidance from NN/g, GOV.UK, Apple, Android and the W3C covers
the rest and is labelled as general guidance.

### 3.2 Verified patterns and how they map

**P1. A league is a small set of named tabs: League, Team, Matchup.**
Sleeper's help text refers to "the matchup tab", "the League tab" and "the TEAM tab"
([points breakdown](https://support.sleeper.com/en/articles/4126744-how-can-i-see-my-player-s-points-breakdown),
[team name](https://support.sleeper.com/en/articles/4427480-how-do-i-change-my-team-name-and-photo))
**[verified]**. Yahoo describes "a new global navigation with three updated tabs", named
Home, News and Scores
([source](https://www.yahooinc.com/press/award-winning-yahoo-fantasy-app-unveils-new-design-and-1-million-giveaway-for-2024-season))
**[verified]**.
*Mapping:* maps well. This app's equivalents are Standings (league), My Team (team) and Rules.
There is no head-to-head matchup, so that tab has no analogue. It argues for splitting the
single long leaderboard page into named sections.

**P2. Tap one player's score to see how it was earned.**
"you can press or click on the actual score for each player and get a better idea of how they
earned those points", and "You can also view the breakdown for any player by viewing their
player card. Under the Summary tab, tap a tile"
([source](https://support.sleeper.com/en/articles/4126744-how-can-i-see-my-player-s-points-breakdown))
**[verified]**.
*Mapping:* maps directly, and it is the opposite of what the leaderboard does today. The
Points, Stats, Bio and Journey switches expand every pill at once. The data is already in the
page; the change is where the disclosure control lives.

**P3. Compact by default, more on request.**
Yahoo: "We've condensed the player rows on the team screen in version 11.2 or later so you
can see more players at one time", after user feedback
([source](https://help.yahoo.com/kb/SLN36755.html)) **[verified]**. A Sleeper App Store
reviewer asks for the same thing, "a compressed view" with a toggle for more matchup info
([source](https://apps.apple.com/us/app/sleeper-fantasy-football/id987367543)) **[verified
as a user review, which is one person's opinion]**.
*Mapping:* maps well. A league here has four to ten players. All standings should fit on one
phone screen before any roster detail.

**P4. The summary stays pinned while the detail scrolls.**
ESPN's updated matchup view lets fans "scroll through the individual player scores in a
specific matchup, while the score for that game remains pinned at the top"
([source](https://espnpressroom.com/us/press-releases/2025/08/espn-fantasy-football-30th-anniversary-new-design-new-features-all-new-fantasy-app-for-2025/))
**[verified]**.
*Mapping:* partial. A sticky team header (rank, name, points) while an expanded roster
scrolls is cheap with `position: sticky`. It only matters when a roster is taller than the
screen, which is the case on phones with detail open.

**P5. Standings in an elimination format are grouped into zones.**
Sleeper's Chopped leagues have a "Chopping Block" that "shows your live projected standings
throughout the week", inverted so that "the team with the lowest projected score appears at
the top", with named sections: Chop Zone, "Danger: The teams closest to the Chop Zone" and
"Safe: Exactly where you want to be"
([source](https://support.sleeper.com/en/articles/12005468-introduction-to-chopped-leagues))
**[verified]**.
*Mapping:* partial, and a matter of taste. Fantasy players here are not eliminated; castaways
are. The transferable idea is a team-health signal on each standings row. My suggestion, not
a verified pattern: a row of small torches, lit for each pick still in the game and snuffed
for each one out, using the two torch icons that already exist. It is on theme and replaces
reading eight faded pills to learn how alive a team is.

**P6. Status has a fixed vocabulary and fixed colors.**
Sleeper's legend lists player statuses (Probable, Questionable, Doubtful, Out, Suspended, IR,
PUP) and a four-step color scale for matchup difficulty: green, light green, orange, red
([source](https://support.sleeper.com/en/articles/4584482-league-legend)) **[verified]**.
Yahoo fixed a bug so that "Favorable matchups should now show green and non-favorable
matchups should now show red" ([source](https://help.yahoo.com/kb/SLN36755.html))
**[verified]**.
*Mapping:* maps, with a constraint. This palette uses fire and gold for the brand, for links,
for points and for badges all at once, so color cannot carry status alone. Define a small
status set (in the game, voted out, jury, finalist, Sole Survivor) with one treatment each.
Keep tribe color as a separate channel. Sleeper does the same with position dots: "These are
color-coding the positions" (same source).

**P7. The champion carries a permanent mark.**
"you will automatically see a gold medal icon attached to the champion", and it "cannot be
removed or hidden, even by the commissioner" (same legend source) **[verified]**.
*Mapping:* already present as the gold season badges next to a name. Keep them.

**P8. A team has its own name and image per league.**
"Please note the team name character limit is 25", and "The avatar that you select from this
screen will only change for that specific league"
([source](https://support.sleeper.com/en/articles/4427480-how-do-i-change-my-team-name-and-photo))
**[verified]**.
*Mapping:* the self-login branch adds per-season team names with a 40-character limit. A
shorter limit, or truncation in the standings row, would protect the phone layout (open
question 10). Team avatars are out of scope unless the owner wants them.

**P9. Scoring settings are grouped into named categories, with plain notes on interactions.**
Sleeper's scoring article is organised as Passing, Rushing, Receiving, Kicking, Team Defense,
Special Teams (defense and player), Miscellaneous, Bonus and IDP, with sub-groups written as a label plus a gloss
("Negative Plays - Mistakes & sacks", "Bonuses - Big play rewards") and one-line notes with an
example where rules interact ("So if your QB throws a Pick 6, they would lose points for all
three of those categories")
([source](https://support.sleeper.com/en/articles/3998131-what-scoring-options-are-available))
**[verified]**. Sleeper also lists "easy-to-understand scoring and roster settings" among its
improvements
([source](https://support.sleeper.com/en/articles/1876010-intro-to-sleeper-fantasy-football))
**[verified]**.
*Mapping:* maps directly onto the rules page. The analysis page already groups the same
parameters into Survival, Milestones, Placement and pick-type modifiers, and Performance
(`scoring_analysis.html:124-146`). The rules table should use those groups.

**P10. Past seasons sit behind a switcher, not in the main navigation.**
On mobile: "open the league settings, scroll to the bottom, and tap Previous Leagues". On the
web: "click the Settings icon at the top of your league page"
([source](https://support.sleeper.com/en/articles/3941297-how-do-i-view-previous-seasons-of-my-leagues))
**[verified]**. Yahoo's Team Switcher opens when you "tap the name of your league at the top"
([source](https://help.yahoo.com/kb/SLN36755.html)) **[verified]**.
*Mapping:* maps. The Season dropdown already does this. In any restructured header, the
season name becomes the switcher and stops being a nav item.

**P11. The home screen surfaces actions that are due.**
ESPN's "Dynamic Roster Dashboard" keeps players "updated on potential action items based on
the day of the week and activity within a league, including reminders"
([source](https://espnpressroom.com/us/press-releases/2025/08/espn-fantasy-football-30th-anniversary-new-design-new-features-all-new-fantasy-app-for-2025/))
**[verified]**.
*Mapping:* maps. The eligibility warnings ("pick your wildcard") are exactly this. They
belong at the top of My Team as a prompt, not at the end of a header row.

**P12. Phone, portrait, first, with the web as the same product.**
"Our mobile app is optimized for portrait view only"
([source, dated June 16, 2020](https://support.sleeper.com/en/articles/4167768-can-i-change-the-view-to-landscape))
**[verified]**. A FantasyPros review of the 2021 web redesign says Sleeper "opted to create a
web app with the same feel as their successful app"
([source, third party, Aug 16, 2021](https://www.fantasypros.com/2021/08/sleeper-new-look-new-options/))
**[verified as a third-party description]**.
*Mapping:* supports designing at 375px first and letting wide screens add columns.

**P13. Design is the stated priority.**
Asked for the biggest reason to pick it over ESPN and Yahoo, Sleeper answers "Design." and
calls itself "widely considered to be the best designed fantasy football product in the
space"
([source](https://support.sleeper.com/en/articles/1876048-why-you-should-switch-to-sleeper))
**[verified, and it is self-description]**. Its App Store text says "Clean, modern design
built for speed"
([source](https://apps.apple.com/us/app/sleeper-fantasy-football/id987367543)) **[verified]**.
*Mapping:* none directly. It confirms the target and says nothing about how they do it.

**P14. League history is a destination.**
Sleeper's League History holds "League Champs", "All-Time Standings", "All-Time Weekly High
Scores" and "All-Time Player High Scores"
([source](https://support.sleeper.com/en/articles/3204499-league-history-and-weekly-reports))
**[verified]**.
*Mapping:* a possible later feature. It needs new queries, so it is outside a design pass.

### 3.3 General guidance used in this document

Labelled as general guidance, not as anything Sleeper does.

- **Bottom or tab navigation suits few destinations.** Android: use a navigation bar for
  "Three to five destinations of equal importance"
  ([source](https://developer.android.com/develop/ui/compose/components/navigation-bar)).
  NN/g: "If your site has more than 5 options, it's hard to fit them in a tab or navigation
  bar", a top bar "takes up valuable real estate above the fold", tab bars "are persistent",
  and labelled icons are "a recommended best practice in most cases"
  ([source, 2015](https://www.nngroup.com/articles/mobile-navigation-patterns/)). Apple: "Use
  a tab bar to support navigation, not to provide actions", "Don't disable or hide tab bar
  buttons, even when their content is unavailable", and "If a section is empty, explain why
  its content is unavailable"
  ([source](https://developer.apple.com/design/human-interface-guidelines/tab-bars)).
  All **[verified]**.
- **Accordions work on phones, with caveats.** NN/g: "on mobile, accordions are one of the
  most useful design elements" because they "allow users to get the big picture before
  focusing on details". It also reports a page where the first accordion was expanded by
  default and "prevented users from getting a quick glance (without scrolling) at the
  structure of the page", and recommends "a persistent accordion header" for long sections
  ([source, 2015](https://www.nngroup.com/articles/mobile-accordions/)) **[verified]**.
- **Do not hide what everyone needs.** GOV.UK: "Do not use an accordion for content that all
  users need to see", "Do not put accordions within accordions", and for the lighter details
  component, use it "when it contains information that only some users will need"
  ([accordion](https://design-system.service.gov.uk/components/accordion/),
  [details](https://design-system.service.gov.uk/components/details/)) **[verified]**.
- **Progressive disclosure.** "Initially, show users only a few of the most important
  options. Offer a larger set of specialized options upon request."
  ([source, 2006](https://www.nngroup.com/articles/progressive-disclosure/)) **[verified]**.
- **In-page contents.** NN/g: "In-page links are most valuable for long-form content", label
  the list "Table of Contents or On This Page", readers "clicked on in-page navigation links
  when they had a specific information need", and "Accordions and tabs are alternatives to an
  in-page table of contents"
  ([source, 2023](https://www.nngroup.com/articles/in-page-links-content-navigation/))
  **[verified]**.
- **Typefaces.** Apple: "Minimize the number of typefaces you use, even in a highly customized
  interface" ([source](https://developer.apple.com/design/human-interface-guidelines/typography))
  **[verified]**. The four-font system stays, by the owner's decision. The practical reading
  is that each font keeps one job and none is used for running text below about 12px.

## 4. Candidate directions

All three keep the palette, the four fonts, the torch motifs, the finale celebration, Pico
CSS, the Jinja templates and every route. All three assume the v2 branch has landed. Sizes
are my estimates: S is one or two files and under about 150 changed lines, M is several files
and 150 to 400 lines, L is structural template work above that.

### Direction A: finish the system

Keep every page's structure. Make what exists consistent, legible and accessible, and
restructure the content of the two text pages.

- **Changes:** a type scale in `rem` with a floor, spacing and radius tokens, one eliminated
  treatment, contrast fixes, reduced motion, focus styles, macros and utility classes in place
  of duplicated markup and inline styles, `DESIGN.md` brought into agreement, the rules and
  analysis pages reordered (Section 5), and an admin tidy.
- **Stays:** the long single-page leaderboard, the pills, the five global toggles, the top
  nav.
- **Risk:** low. Mostly CSS and copy. The one template-heavy slice (macros) is meant to
  produce identical HTML.
- **Size:** eight slices (S1 to S7 and S12), each S or M.
- **Result:** a clean, readable themed site. It will not feel like a fantasy app, because
  none of the verified patterns in Section 3.2 is cosmetic.

### Direction B: league app structure

Everything in A, then restructure the leaderboard and the player's entry point.

- **Changes on top of A:**
  - Standings become compact rows: rank, player and team name, a torch row for picks still
    in, win percentage, points. All players fit on one phone screen (P3, P5).
  - Each row expands with a native `<details>` into a roster of uniform castaway cards. Each
    card opens its own breakdown and journey (P2). The Points, Stats, Bio and Journey switches
    go away or shrink to a single density control.
  - The team header sticks while its roster scrolls (P4).
  - The page gets a short sticky section bar: Standings, Progress, Stats (P1).
  - My Team becomes the logged-in player's home: stat tiles, action prompts first (P11), the
    same roster cards, team name setting last. The viewer's own row is marked in the
    standings.
  - Optional and separate: a bottom tab bar on phones.
- **Stays:** routes, data, scoring, chart types, the finale banner, the theme.
- **Risk:** medium. `leaderboard.html` is 556 lines with inline scripts, and three other
  efforts touch it (v2, self-login, the performance plan). The two-level disclosure (row,
  then card) needs care to stay usable with a keyboard.
- **Size:** A plus four slices (S8 to S11), one of them L. S13 (wide-screen layout) is
  optional.
- **Result:** the structure the comparison apps share, in this theme.

### Direction C: multi-page app

Everything in B, then split the leaderboard into separate URLs.

- **Changes on top of B:** separate pages for standings, each team, castaway stats (the
  orphaned `/stats/<id>` route already exists) and charts, plus a castaway detail page in
  place of the external link.
- **Risk:** higher. It needs new routes and tests in `app/routes.py`, changes the caching
  described in the performance research, and is no longer the design and CSS job the owner
  described.
- **Size:** B plus route and test work, L.
- **Result:** the most app-like, with shareable team links and smaller pages. Little of that
  matters to a private league of four to ten people.

### Recommendation

**Direction B, built in the order of Section 6, with A's slices first.**

- A's slices are prerequisites for B anyway. They ship value alone, so the owner can stop
  after them and still have a better site.
- The request was polish comparable to Sleeper. What could be verified about Sleeper, ESPN
  and Yahoo is structural. A alone cannot deliver that.
- C adds backend scope for benefits that a small private league will barely notice. One piece
  of it is nearly free and worth taking: decide the fate of the existing stats page (S9).
- The bottom tab bar is the weakest part of B, and I have kept it as its own slice (S11) for
  three reasons. I could not verify that Sleeper uses one. A logged-out visitor has only two
  or three destinations. And Apple's guidance against hiding tabs conflicts with a My Team
  tab that only linked players can use. The v2 branch's wrapped top nav is an acceptable
  fallback if the owner declines it.

## 5. Text-heavy pages plan

### 5.1 Rules page

The v2 branch supplies the component: `details.fold`, the 68ch measure, Expand all and
Collapse all, deep links that open their section. This plan changes the order, takes three
blocks out of the folds, restructures the examples and fixes the copy. It does not replace
the component.

Principle, from the guidance in Section 3.3: what every player needs is always visible, and
what only some need goes in a fold.

| # | Block | Source today | Visibility |
|---|---|---|---|
| 0 | Title and the season line. Keep the `Scoring for <strong>...</strong>` markup (test-pinned). Make "Scoring: Classic" on the leaderboard link here with the season id. | `rules.html:4-8` | Always |
| 1 | **At a glance.** One sentence on how the game works, then the pick-type table: type, when you pick, what it is worth. | `:13`, `:188-224` | Always |
| 2 | **What happens when.** A four-step strip: before the premiere (draft four, name a Sole Survivor), after episode 1 (wildcard), after the merge (replacement, if eligible), finale. New framing, existing facts. | `:19`, `:25`, `:36`, `:69` | Always |
| 3 | **Points.** The active components table, grouped as Survival, Milestones, Placement, Performance (the same groups as `scoring_analysis.html:124-146`). On phones, show component and points with the description under the name, so it does not need sideways scrolling. Inactive components stay in their existing nested disclosure. | `:123-156` | Always |
| 4 | The Draft: sub-draft detail. | `:19` | Fold |
| 5 | Wildcard, with its example. | `:22-31` | Fold |
| 6 | Replacement picks. Turn the four eligibility bullets into a three-row table (what you lost before the merge, what you may pick, what it earns). | `:33-64` | Fold |
| 7 | Sole Survivor pick, with the streak example. Fix the episode count. | `:66-80` | Fold |
| 8 | How tribal points grow: the progressive phases table and same-day tribals, moved out of the points block. Render only when the season uses progressive values. | `:87-121` | Fold |
| 9 | Worked examples, one fold. | `:159-186` | Fold |
| 10 | Win probabilities. Fix the "all 50 US seasons" text. | `:226-250` | Fold, closed at every width |
| 11 | Data sources. | `:252-260` | Fold, closed at every width |

Worked examples:

- A small example stays next to its rule, inside that rule's fold, as today. Its label says
  what it shows ("Example: a wildcard who earns 12 base points"), not just "Example".
- The four full-season cases (winner as a draft pick, first juror, first boot, winner as a
  wildcard) become one fold, each as a short table: component, calculation, points, with a
  total row. The route already computes every number (`app/routes.py:589-647`). This replaces
  the `<small>` run-ons.
- Render that fold only when `examples` is non-empty.

Phone defaults: the v2 branch leaves the first section open and closes the rest. With blocks
1 to 3 always visible, every fold can start closed on phones. That gives the at-a-glance view
of the page structure that NN/g found was lost when a first section was open.

### 5.2 Scoring analysis page

Readers: players who want to know whether the scoring is fair and what changed, and the owner
checking the numbers.

| # | Block | Source today | Visibility |
|---|---|---|---|
| 0 | Title and one-sentence claim. Reserve space for the two loaded numbers so the line does not jump. | `scoring_analysis.html:4-10` | Always |
| 1 | **The bottom line.** The four stat tiles and the generated sentence. | `:13-17`, `:281-343` | Always |
| 2 | **What we found.** Key Findings as a list of short cards (a title and one or two sentences), not a two-column table. | `:158-165`, `:715-848` | Always |
| 3 | **Recommended settings.** A grouped table in place of the `<pre>` dump, using the rules page groups, legacy value beside the recommended value, linked to Rules. | `:175-180`, `:883-912` | Always |
| 4 | How close are games? One season at a time behind a season selector, in place of four stacked charts. | `:98-104` | Fold, open on desktop |
| 5 | Legacy against recommended. The same selector: two charts visible, not eight. | `:107-113` | Fold, open on desktop |
| 6 | Game health by season. One table (rows are seasons, columns are the six measures) in place of nine cards of prose. Uncertainty bands move to a footnote. | `:89-95`, `:424-471` | Fold |
| 7 | How we tested: the metric and weight table. | `:20-79` | Fold, closed |
| 8 | Score distribution histogram. | `:82-86` | Fold, closed |
| 9 | Parameter impact: 19 charts in four groups under `<h3>` headings (no nested folds). | `:116-155` | Fold, closed |
| 10 | Top configurations table. | `:168-172` | Fold, closed |

Notes:

- **Charts inside closed folds.** The v2 branch keeps chart sections open on phones "so
  Chart.js sizes them at load" (its `DESIGN.md`). Closing blocks 8 and 9 by default therefore
  needs those charts created on the fold's first `toggle` event. The performance research
  already proposes building charts only when seen, so do the two together.
- **Copy fixes in the same slice:** remove or feed the blank `placement_ratio` slot (`:140`),
  take the config count from the JSON and drop the fixed "25,000" (`:77`), only claim the
  default is marked when it is (`:84`), and stop hard-coding the season range (`:7`).
- **Notation:** say "typical range" in the main flow, and keep "3σ", "pp" and "Spearman's rho"
  for a footnote or the method fold.
- **The page's `<style>` block.** The v2 branch's `DESIGN.md` records a convention that
  page-specific responsive rules stay in that inline block because it comes after `style.css`
  in the cascade. Either keep the convention, or move the block into the end-of-file layer of
  `style.css` in one step. Do not half-move it.
- **Where it lives.** The page reads as "why the rules are what they are". It could move from
  the top-level nav to a link from the Rules page (open question 3).

### 5.3 The contents panel (TOC)

**Keep the generator, change the presentation, make it opt-in.** The script in
`base.html:101-154` is sound, and the v2 branch already teaches it to open folds.

- **Opt in per page.** Today any page with two `<h2>` elements gets the floating button,
  which includes admin pages. Let a template ask for it (a block or a `data-` attribute on
  `<main>`).
- **Rules and analysis on wide screens (1200px and up):** a sticky "On this page" list in the
  margin beside the 68ch reading column, always visible and labelled as NN/g recommends. The
  space is already there: the container is 950px or wider at those widths and the text column
  is capped at 68ch.
- **Rules and analysis on phones:** the closed fold headers are the contents list, which is
  the accordion-as-table-of-contents idea in NN/g's article. Drop the floating button on these
  pages. If the owner prefers to keep it, v2's bottom sheet is fine, but it cannot share the
  bottom-left corner with a bottom tab bar.
- **Leaderboard:** replace it with the sticky section bar from Direction B (Standings,
  Progress, Stats). Under Direction A, keep the floating button there.
- **Accessibility:** `aria-expanded` and `aria-controls` on the button, a visible focus ring,
  and Escape to close.
- **Optional:** include `<h3>` entries on the rules page. The v2 helper already opens ancestor
  folds.

## 6. Proposed slices

Ordered. Each ships alone. All assume the landing order in Section 1.3. "Verify" always
includes `python -m pytest` and `ruff`. The tests do not assert on class names; one test pins
a rules-page string, as noted. There are no visual tests, so each slice needs a manual pass
at 375, 768 and 1280px on an in-progress season, a finished season, and a season with no
picks, both logged out and as admin (and as a player once self-login lands).

**S1. Tokens, type scale and `DESIGN.md` agreement.** Size S to M.
- Scope: add `rem`-based type steps with a 0.75rem floor, plus spacing and radius tokens.
  Replace nested `em` sizes inside the pick pill with steps. Use `--font-logo` and
  `--font-label` where the family is written out. Settle each row of the table in Section 2.3
  and record the decisions in `DESIGN.md`.
- Files: `app/static/style.css`, `DESIGN.md`.
- Verify: no computed text size under 12px at 375px in browser dev tools. The count of
  distinct `font-size` values drops (35 on v2 today). `grep` finds no written-out family names
  outside the variable definitions and `@font-face`.
- Conflicts: `style.css` with v2 (land after it) and lightly with self-login's new rules.

**S2. Status and contrast.** Size S.
- Scope: one eliminated treatment used everywhere (for example a desaturated headshot and
  dimmed text that still meets 4.5:1, with no whole-element opacity). A lighter green for
  success text and the merge badge. Rank numerals in a solid color. Badge tints rechecked. One
  status vocabulary for castaways.
- Files: `style.css`, `DESIGN.md`, and `admin/season_detail.html` (one inline opacity).
- Verify: rerun the contrast calculation in Appendix B. Look at a finished season, where most
  picks are out.
- Conflicts: none beyond `style.css` ordering.

**S3. Motion, focus and control semantics.** Size S.
- Scope: a `prefers-reduced-motion` block that stops the finale glow and embers. Visible
  focus styles for nav links, the Season dropdown items, toggles, pills and the contents
  button. `aria-expanded` on the two disclosure buttons and Escape to close. Demote the second
  `<h1>` on finale pages.
- Files: `style.css`, `base.html`, `leaderboard.html` (one tag).
- Verify: a keyboard-only walk through each page. The operating system's reduce-motion setting
  stops the finale animation.
- Conflicts: `base.html` with v2 (adds a script) and self-login (nav link). Small.

**S4. Components without visual change.** Size M.
- Scope: Jinja macros for the pick card (the two leaderboard blocks and My Team), the stat
  board (leaderboard and stats page) and the torch icon. One tribe-color custom property per
  card in place of three inline styles. A small-button class and form-row classes in place of
  the repeated admin inline styles. One chart palette defined once. Remove the four unused
  rules (`.compare-grid`, `.compare-card`, `.compare-rankings`, `.lb-warnings`).
- Files: a new macros template, `leaderboard.html`, `stats.html`, `compare.html`,
  `admin/*.html`, `settings.html`, `auth/login.html`, `my_team.html` (after self-login),
  `style.css`.
- Verify: save each page's HTML before and after and diff it. Only class and attribute
  changes should appear. Inline `style=""` count falls from 69 to the data-driven few.
- Conflicts: `leaderboard.html` with v2 and self-login, and `admin/players.html` with
  self-login. Land after both. It overlaps the performance plan's item on cutting template
  boilerplate. Do them as one change or agree an order.

**S5. Rules page content.** Size M.
- Scope: Section 5.1.
- Files: `rules.html`, `style.css`, and `leaderboard.html` for the link from "Scoring:".
  Template only. No route change is needed, because `season.num_episodes` and the `examples`
  values are already available.
- Verify: read the page top to bottom at 375px. Blocks 1 to 3 are visible with every fold
  closed. Each worked example adds up to its total. The pinned test string is still present.
- Conflicts: `rules.html` with v2 (land after it). None with self-login.

**S6. Scoring analysis content.** Size M to L.
- Scope: Section 5.2, including chart creation on first open.
- Files: `scoring_analysis.html`, `style.css`.
- Verify: the first screen at 375px shows the bottom line and the findings with no chart
  above them. Opening a closed fold draws its charts at the right size. No blank chart slot.
  The headline numbers match the JSON.
- Conflicts: `scoring_analysis.html` with v2 (land after it). It overlaps the performance
  plan's Chart.js item.

**S7. Contents panel.** Size S.
- Scope: Section 5.3.
- Files: `base.html`, `style.css`, `rules.html`, `scoring_analysis.html`.
- Verify: admin pages no longer show the button. At 1280px the list is visible and tracks the
  current section. At 375px the rules page has no floating button, or has the bottom sheet if
  the owner keeps it.
- Conflicts: `base.html` with v2. S11 depends on this slice.

**S8. Standings rows and roster cards.** Size L. Direction B starts here.
- Scope: a compact standings list with a torch row for team health. A `<details>` per team
  that opens a grid of uniform castaway cards. A per-card disclosure for breakdown, stats and
  journey. A sticky team header inside an open roster. The global toggles reduced to what the
  owner keeps (open question 6). The team name placed under the player name.
- Files: `leaderboard.html`, the macros template, `style.css`, `DESIGN.md`.
- Verify: at 375px every player's rank, name and points fit on one screen with rosters
  closed. One castaway's breakdown opens without changing any other card. `localStorage` keys
  for removed toggles are ignored cleanly. The finale page still shows its champion and
  snuffed treatments.
- Conflicts: the highest of any slice. It rewrites the region that v2 and self-login both
  touch and that the performance plan wants to shrink. Land after S4, and coordinate with the
  performance work, since deferring hidden detail and per-card disclosure are the same
  change seen from two sides.

**S9. Leaderboard section bar and the stats page.** Size S to M.
- Scope: a sticky bar with Standings, Progress and Stats as in-page links. Decide the stats
  page: link to it and drop the boards from the leaderboard, or delete the route's template.
- Files: `leaderboard.html`, `stats.html`, `style.css`. Deleting the route is a small change
  in `app/routes.py` and is optional.
- Verify: each section is reachable in one tap at 375px. Charts still draw.
- Conflicts: `leaderboard.html`. Land after S8.

**S10. My Team as home, and "you" in the standings.** Size M.
- Scope: action prompts first, then stat tiles, then roster cards with the same per-card
  detail as the leaderboard, then the team name setting. Mark the viewer's own row in the
  standings (a template comparison against `current_user`, no route change). Better empty
  states with a reason and a next step.
- Files: `my_team.html`, `leaderboard.html`, `style.css`.
- Verify: as a linked player, My Team shows the same numbers as the leaderboard row. As an
  unlinked visitor, nothing breaks.
- Conflicts: depends on `feat/player-self-login` being merged. Land after S8.

**S11. Phone navigation shell.** Size M. Optional.
- Scope: either a bottom tab bar on phones with three to five labelled destinations, with
  the season switcher in the header, or a single-row top bar. Needs safe-area padding at the
  bottom and enough bottom padding on `<main>` that content is not covered.
- Files: `base.html`, `style.css`, `DESIGN.md`.
- Verify: on a real phone, not only an emulator. Nothing is hidden behind the bar. The
  contents button no longer overlaps it. The active destination is marked.
- Conflicts: v2's nav block, self-login's nav link, S7. Land after all three. Needs open
  questions 2 and 3 answered first.

**S12. Admin, compare and small pages.** Size M.
- Scope: label the action columns. Move Delete off the list rows and into the season page's
  danger zone only. Give the Active toggle a label. Wrap the scoring form table so it scrolls.
  Put the config tag explanations on the compare page in visible text. Update the stale help
  text in `admin/picks.html`. Tidy settings, login and the no-season page.
- Files: `admin/*.html`, `compare.html`, `settings.html`, `auth/login.html`, `no_season.html`,
  `style.css`.
- Verify: each admin task (create a season, set picks, edit scoring, link a player) completes
  at 375px without sideways scrolling outside tables.
- Conflicts: `admin/players.html` with self-login. Can land any time after S4.

**S13. Wide-screen layout.** Size M. Optional.
- Scope: use the width above 1024px. Standings beside the progression chart, two-column
  roster grids, a narrower reading column with the contents list in the margin.
- Files: `style.css`, small template wrappers.
- Verify: at 1280 and 1536px no text line runs past about 80 characters and no card stretches
  to the full container width without reason.
- Conflicts: none beyond `style.css` ordering. Land last.

Direction A is S1 to S7 plus S12. Direction B adds S8 to S11, and S13 if wanted.

## 7. Open questions for the owner

Each can be answered in one line.

1. **Direction:** A (finish the system), B (league app structure, recommended) or C
   (multi-page)?
2. **Phone navigation:** bottom tab bar, or keep the wrapped top nav from the v2 branch?
3. **Top-level destinations:** is Analysis a main destination, or a link from the Rules page?
4. **Body font:** system sans (what the stylesheet does now) or Palatino (what `DESIGN.md`
   says)?
5. **Eliminated castaways:** how should they look? Options: dim the whole pill (and to what
   opacity), or a greyscale headshot with readable text and a snuffed-torch mark.
6. **Detail toggles:** keep the five global switches, or move breakdown, stats, bio and
   journey to a tap on each castaway and keep only Projected?
7. **Rosters on phones:** all teams expanded by default, or only your own with the rest
   closed?
8. **Stats page:** link the existing page and remove the boards from the leaderboard, keep
   the boards and delete the page, or keep both?
9. **Point precision:** always two decimals ("12.50 pts"), or trim trailing zeros ("12.5")?
10. **Team names:** keep the 40-character limit and truncate in the standings, or shorten the
    limit?
11. **My Team for unlinked visitors:** hide the link, or show a page that explains how to get
    linked?
12. **Rules on phones:** are the pick-type table, the season steps and the points table
    always visible, with everything else closed?
13. **Who is the scoring-analysis page for:** league players first (plain-language summary on
    top) or you (method on top)?
14. **Admin pages:** full visual treatment, or a functional tidy only?
15. **The older draft branch `fix/mobile-responsive`:** close it as superseded by v2?
16. **Wide screens:** is a desktop layout pass (S13) wanted, or is the phone the only target
    that matters?
17. **Torch row:** do you want lit and snuffed torches per team as the team-health signal in
    standings (my suggestion in P5), or something plainer such as "3 of 5 in"?

## Appendix A. Sources

All fetched on 2026-10-01. For pages marked "text", I read the page's visible text after
stripping markup, not a summary of it.

Sleeper (primary)

- Help center, text: [points breakdown](https://support.sleeper.com/en/articles/4126744-how-can-i-see-my-player-s-points-breakdown),
  [team name and photo](https://support.sleeper.com/en/articles/4427480-how-do-i-change-my-team-name-and-photo),
  [league legend](https://support.sleeper.com/en/articles/4584482-league-legend),
  [Chopped leagues](https://support.sleeper.com/en/articles/12005468-introduction-to-chopped-leagues),
  [scoring options](https://support.sleeper.com/en/articles/3998131-what-scoring-options-are-available),
  [previous seasons](https://support.sleeper.com/en/articles/3941297-how-do-i-view-previous-seasons-of-my-leagues),
  [league history](https://support.sleeper.com/en/articles/3204499-league-history-and-weekly-reports),
  [landscape view](https://support.sleeper.com/en/articles/4167768-can-i-change-the-view-to-landscape),
  [intro](https://support.sleeper.com/en/articles/1876010-intro-to-sleeper-fantasy-football),
  [why switch](https://support.sleeper.com/en/articles/1876048-why-you-should-switch-to-sleeper),
  [unique features](https://support.sleeper.com/en/articles/1951583-what-are-sleeper-s-unique-features).
- [App Store listing](https://apps.apple.com/us/app/sleeper-fantasy-football/id987367543),
  text: developer description and user reviews.

Other fantasy apps (primary)

- [ESPN Press Room, Aug 7, 2025](https://espnpressroom.com/us/press-releases/2025/08/espn-fantasy-football-30th-anniversary-new-design-new-features-all-new-fantasy-app-for-2025/), text.
- [Yahoo press release, Aug 5, 2024](https://www.yahooinc.com/press/award-winning-yahoo-fantasy-app-unveils-new-design-and-1-million-giveaway-for-2024-season), text.
- [Yahoo Help: Welcome to the redesigned Yahoo Fantasy app](https://help.yahoo.com/kb/SLN36755.html), text.

Third party

- [FantasyPros, "Sleeper: New Look, New Options", Raju Byfield, Aug 16, 2021](https://www.fantasypros.com/2021/08/sleeper-new-look-new-options/), text.

General guidance

- NN/g, text: [Accordions on Mobile (2015)](https://www.nngroup.com/articles/mobile-accordions/),
  [Basic Patterns for Mobile Navigation (2015)](https://www.nngroup.com/articles/mobile-navigation-patterns/),
  [In-Page Links for Content Navigation (2023)](https://www.nngroup.com/articles/in-page-links-content-navigation/),
  [Progressive Disclosure (2006)](https://www.nngroup.com/articles/progressive-disclosure/).
- GOV.UK Design System, text: [accordion](https://design-system.service.gov.uk/components/accordion/),
  [details](https://design-system.service.gov.uk/components/details/).
- Apple Human Interface Guidelines: [tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars),
  [typography](https://developer.apple.com/design/human-interface-guidelines/typography). These
  pages are script-rendered. I read the text from the JSON document each page loads.
- [Android Developers: Navigation bar](https://developer.android.com/develop/ui/compose/components/navigation-bar), text.
- [web.dev: Accessible tap targets (last updated 2020-03-31)](https://web.dev/articles/accessible-tap-targets), text.
- W3C, Understanding WCAG 2.2, text: [1.4.3 Contrast (Minimum), AA](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
  [1.4.8 Visual Presentation, AAA](https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation.html),
  [2.2.2 Pause, Stop, Hide, A](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html),
  [2.5.8 Target Size (Minimum), AA](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- Pico CSS v2.1.1 stylesheet, as served from the CDN URL in `base.html:7`: root font sizes,
  container widths, nav and card rules.

Fetched and not used as evidence: the sleeper.com home page (I only had a machine summary of
it, not its text).

## Appendix B. How the computed numbers were made

- **Effective font sizes.** Root size from Pico (16px below 576px; 20px from 1280px), times
  each nested `em` factor along the element's ancestors, as written in `style.css` on `main`
  and on the v2 branch. Example on `main`: pick-type badge = 16 x 0.8 (`.lb-pick`) x 0.7
  (`.lb-pick-meta`) x 0.8 (`.lb-pick-badge`) = 7.2px.
- **Contrast ratios.** The WCAG relative-luminance formula on the hex values in `:root`. For
  elements with opacity, the foreground and the pill background were each alpha-blended over
  `--card-bg` first. For tinted badges, the tint was blended over `--deep-ocean`.
- **Timeline target size.** 375px viewport, minus 1rem of container padding each side
  (Pico), minus 1rem of timeline padding each side (`style.css:1276`), divided by 14 points.
  The height is the dot (`style.css:317-318`). The episode count varies by season.
- **Line length.** Container width (1200px at 1280px) minus 1rem of card padding each side,
  divided by an assumed 0.45 to 0.5em average character width. An estimate, not a
  measurement.
- **Counts** (inline styles, `font-size` values, `!important`, hex literals, unused classes,
  canvases) come from regular-expression counts over the files. Classes built from template
  variables (`flash-{{ category }}`, `lb-badge-{{ ... }}`, `journey-dot-{{ ... }}`,
  `lb-pick-{{ ... }}`) were excluded from the unused list by hand.
- **Merge checks.** `git merge-tree --write-tree` between each pair of branches.
