# Design System — Survivor Fantasy

## Product Context
- **What this is:** Survivor (TV show) fantasy league webapp with public leaderboard and admin management
- **Who it's for:** Fantasy league players tracking their Survivor picks across seasons
- **Space/industry:** Fantasy sports / reality TV fan communities
- **Project type:** Web app (Flask + Jinja2 + Pico CSS v2), dark theme only

## Aesthetic Direction
- **Direction:** Thematic immersion. The Fiji Night / Tribal Council atmosphere is the design itself, not a skin over a generic dashboard.
- **Decoration level:** Intentional. Ember particle animations on finale pages, gradient text on winner names, torch iconography, glow effects on champion cards. Decoration is earned by in-game moments, not applied uniformly.
- **Mood:** Dramatic, warm, cinematic. Like watching Tribal Council from the jury bench. Dark backgrounds with fire-colored accents create depth and atmosphere.
- **No light mode.** The dark theme is the product identity.

## Typography

Four brand fonts, each with a distinct role, over Pico's system sans for body text. This is the core of the visual identity.

- **Logo/Branding:** `Survivant` (local @font-face, `fonts/survivant.ttf`) -- Official Survivor logo font. Used ONLY for site logo, finale labels, and logo motto. Uppercase, wide letter-spacing (0.08-0.2em). CSS var: `--font-logo`
- **Interface:** the system sans stack. CSS var: `--font-ui`. Headings below h1, player names, points, ranks, chips, badges and section labels. This is the default voice of the UI; reach for a decorative family only for the cases below.
- **Page title:** `Cinzel` (Google Fonts) -- Serif with classical authority. **h1 only.** It used to run through h1-h3, player names and leaderboard names; concentrating it on the page title keeps the ceremony where it is read once and out of the interface, where three decorative families at once was the main thing making the site look unpolished. CSS var: `--font-heading`
- **Micro-labels and stat values:** `Bebas Neue` (Google Fonts) -- Condensed caps for the small uppercase labels that sit above or beside data, and for stat values. **Not** for chips, badges or anything carrying a phrase: at 12px, condensed caps with tracking is hard to read, which is what made the journey badges difficult. CSS var: `--font-label`
- **Castaway Names:** `Palatino Linotype` > `Palatino` > `Book Antiqua` > `serif` (system) -- Warm serif for castaway names, team names and a few short labels. Not used for running text. CSS var: `--font-tribal`
- **Body text:** Pico's system sans-serif stack (no `font-family` set on `body`). Paragraphs, pick meta lines (points, stats, result), form text. Chosen over Palatino because sans reads better at the small sizes used on phones.
- **Loading:** Survivant is self-hosted (`/static/fonts/survivant.ttf`, `font-display: swap`). Cinzel and Bebas Neue via Google Fonts CDN.

### Type Scale
Sizes are as declared in `style.css` (desktop, then the phone override where one exists). Pico scales the root font with the viewport: 16px on phones, 18px from 768px, 20px from 1280px, 21px from 1536px, so `em` and `rem` sizes grow on larger screens. A `rem` figure here is therefore not a pixel figure: `1.05rem` is 16.8px on a phone and 21px at 1280px, and converting one against an assumed 16px root is how a reading of this table goes wrong.

Every row below was read out of a rendered page with `getComputedStyle` rather than from the stylesheet source, because the two disagreed. The font column was stale for six rows after #149 moved the interface on to one sans: it still claimed Cinzel for `h2`, `h3` and player names, and Bebas for ranks, points and badges. Anyone building a new page from the old table would have put Cinzel back on every heading, which is the thing #149 removed.


| Element | Font | Size | Weight | Spacing |
|---------|------|------|--------|---------|
| Site logo | Survivant | 1.5em (1.2em phone) | normal | 0.08em (0.05em phone) |
| h1 | Cinzel | Pico default (1.6rem phone) | 700 | 0.02em |
| h2 | system sans (`--font-ui`) | Pico default (1.3rem phone) | 650 | -0.01em |
| h3 | system sans (`--font-ui`) | Pico default | 600 | -0.005em |
| Nav buttons | Bebas Neue | 0.95em (1em phone) | 400 | 0.08em |
| Rank numbers | system sans (`--font-ui`) | 0.95em (1.5rem phone) | 600 | -- |
| Points | system sans (`--font-ui`) | 1.05em (1.25em phone) | 650 | -0.01em |
| Player name | system sans (`--font-ui`) | 1em (1.1em phone) | 650 | -0.005em |
| Castaway name | Palatino | inherits the pill (0.92em phone) | 600 | 0.02em |
| Pick meta | system sans | 0.78em, floor 12px | -- | -- |
| Stat values | Bebas Neue | 1.2em | 400 | -- |
| Badge text | system sans (`--font-ui`) | max(12px, 0.72em) | 600 | -- |

### Size Floor
- No text is smaller than `--fs-floor` (12px). The research for the design pass measured pill and badge text at 9-11px on phones, because small UI text is sized in `em` and the sizes nest (pill, then line, then badge).
- Small text is written `font-size: max(var(--fs-floor), 0.72em)`: the `em` keeps the intended proportion where there is room, the floor stops it shrinking past 12px. Use the same form for any new small text.
- The only text under the floor is the decorative season-menu caret, which is `aria-hidden`.
- Write font families as variables (`--font-logo`, `--font-heading`, `--font-label`, `--font-tribal`), never by name. The one exception is the `@font-face` rule that defines Survivant.

## Color

- **Approach:** Thematic. Every color is motivated by the Survivor visual language: ocean nights, fire, sand, jungle.
- **Dark theme only.** No light mode variant needed.

### Palette (CSS custom properties on `:root`)

| Variable | Hex | Role |
|----------|-----|------|
| `--night-sky` | `#0d1b2a` | Primary background, darkest surface |
| `--deep-ocean` | `#1b2d3e` | Secondary background, nav, cards elevated from bg |
| `--ocean-surface` | `#274060` | Hover states, elevated interactive surfaces |
| `--fire-bright` | `#e85d26` | Primary accent, CTAs, active states, nav border |
| `--fire-glow` | `#f4a261` | Secondary warm accent, h3 color, links, TOC active |
| `--ember` | `#d4602e` | Primary hover state (darker fire) |
| `--torch-gold` | `#fca311` | Champion/winner highlight, link hover, badges |
| `--sand-warm` | `#e8d5b7` | Primary text, h1/h2 color, nav text |
| `--sand-light` | `#f0e6d3` | Brightest text, gradient text start |
| `--palm-green` | `#2d6a4f` | Green fills and tints (merge badge background, journey dot). Too dark for text. |
| `--palm-light` | `#52b788` | Green text: success messages, merge badge label |
| `--text-light` | `#e8e0d6` | Body text |
| `--text-dim` | `#9aa5b1` | Muted text, secondary labels |
| `--card-bg` | `#162535` | Card/panel backgrounds |
| `--card-border` | `#2a4055` | Borders, dividers, subtle structure |

### Pico CSS Overrides
Pico's dark theme variables are mapped to our palette via `[data-theme="dark"]`:
- `--pico-background-color` -> `--night-sky`
- `--pico-color` -> `--text-light`
- `--pico-primary` -> `--fire-bright`
- `--pico-primary-hover` -> `--ember`
- `--pico-card-background-color` -> `--card-bg`
- `--pico-card-border-color` -> `--card-border`
- `--pico-h1-color`, `--pico-h2-color` -> `--sand-warm`
- `--pico-h3-color` -> `--fire-glow`

### Semantic Colors
| Semantic | Color | Variable |
|----------|-------|----------|
| Success | `#52b788` | `--palm-light` |
| Error | `#e85d26` | `--fire-bright` |
| Warning | `#f4a261` | `--fire-glow` |
| Info | `#9aa5b1` | `--text-dim` |

### Contrast
- Small text needs 4.5:1 against the surface it sits on, large text (24px and up) 3:1.
- Do not dim text with `opacity`. It lowers contrast against the page and stacks with any dimmed parent. Use `--text-dim`, which is 5.6:1 or better on every surface except the hover surface (`--ocean-surface`, 4.2:1), where rows switch to `--sand-warm`.
- Journey badges are sentence case in `--font-ui`, not condensed caps, and their labels are short ("Merged", "Immunity", "Idol found"). The long caps versions were legible in isolation but wrapped to two lines each at readable sizes, which made every roster row taller. Contrast was never the problem: every badge measures 6:1 or better on its own tint.
- Badge labels use a lighter tone than the badge's tint (`#f58a5e` idol and fire, `--palm-light` merge, `#e88484` votes, `#8ab8dc` advantage). The saturated hue on its own tint reads at 1.9 to 4.2:1.
- Rank numerals are `--fire-bright` at 0.85 opacity: about 3.3:1, which passes only because they are large.

### Eliminated Castaways
One treatment everywhere a castaway appears (pick pills, stat rows, admin cards and tables):
- Headshot in greyscale (`grayscale(1) brightness(0.8)`) with a `--card-border` ring instead of the tribe color.
- Text in `--text-dim`. No whole-element opacity.
- Pick pills also lose the tribe-color border and fill, and show a small snuffed torch after the name.
- A crossed-out name is kept only where there is no photo or result line to carry it (the header's Sole Survivor pick, admin castaway cards).

### Gradient Treatments
- **Logo text:** `linear-gradient(180deg, --sand-light 0%, --fire-glow 100%)` with `background-clip: text`
- **Logo hover:** `linear-gradient(180deg, --torch-gold 0%, --fire-bright 100%)`
- **Winner name:** `linear-gradient(180deg, --sand-light 0%, --torch-gold 50%, --fire-bright 100%)`
- **Champion name:** `linear-gradient(90deg, --sand-light, --torch-gold)`
- **Finale banner bg:** `linear-gradient(135deg, #1a0a00 0%, #2d1200 30%, #0d1b2a 100%)`

### Tribe Colors
Survivor tribe colors come from the survivoR dataset (`tribe_colour`). These can be any color. Dark tribe colors are lightened via `_ensure_contrast()` using W3C relative luminance formula to maintain readability on dark backgrounds.

## Spacing
- **Base unit:** 0.25rem increments (Pico CSS default)
- **Density:** Comfortable. Cards have generous padding (1rem-1.5rem), but data-dense areas (pick pills, stats grids) are tighter.
- **Card padding:** 1rem default, 0.75rem on mobile
- **Section gaps:** 1-1.5rem between major sections
- **Pick pill gaps:** 0.4rem between pills, 0.5rem internal padding

## Layout
- **Approach:** Single-column document flow with Pico CSS container, enhanced with flexbox/grid for specific components
- **Framework:** Pico CSS v2 provides the base layout, form styling, and responsive container
- **Max content width:** Pico's default container (1200px approx)
- **Grid usage:** CSS Grid for stats grids (`repeat(3, 1fr)`, drops to `1fr` at 576px), survivor admin grids
- **Flexbox usage:** Leaderboard entries, pick pills, toolbar, nav, progression layout, charts row
- **Grid items carry an implicit minimum.** A grid item defaults to `min-width: auto`, so a `1fr` track cannot shrink below that item's min-content width. Anything with an intrinsic width in the cell sets that floor: a chart canvas, an image, a long unbroken string. The card then renders wider than its own column and hangs past it, which reads as a misaligned border rather than as an overflow. Measured on the analysis page: a card came out 326px inside a 313.8px column. Put `min-width: 0` on the items of any grid holding a canvas or an image.

### Border Radius Scale
| Context | Radius |
|---------|--------|
| Small elements (badges, tags) | 3-4px |
| Interactive (buttons, inputs) | 4px |
| Cards, panels | 8px |
| Dropdowns, TOC, timeline | 8-10px |
| Elevated overlays | 10-12px |
| Finale banner | 12px |

## Breakpoints

All responsive overrides live in one block at the END of `style.css`. Several base rules (progression, finale) are defined late in the file, so a breakpoint placed earlier loses the cascade. Order inside that block is wide to narrow: 768, 640, 576.

| Breakpoint | Target | Usage |
|------------|--------|-------|
| `768px` | Tablet | Stats grid 2-col, progression and team charts stack (`flex: none` + explicit height; `flex: 1` would collapse the absolutely positioned canvas), mini standings 2-up |
| `640px` | Large phone | Nav wraps: logo row, then links row (tap-sized, wraps); seasons dropdown spans the nav width, anchored to `<nav>` |
| `576px` | Phone | Stats grid 1-col, header split (rank/name/points, then extras line), pick pills become a 2-up grid (1-col when a detail toggle is on), toolbar toggles are tap-sized chips, timeline scrolls sideways, fold sections tighten, tables get `min-width` and scroll, TOC becomes a bottom sheet |

### Global Mobile Patterns
- `body { overflow-x: hidden }` prevents cascade overflow from wide children
- `main.container` gets `padding-left/right: 1rem`
- Pico pulls the first/last nav `<ul>` out by the `li` padding; reset on phones
- `.table-scroll` wraps tables that can exceed the viewport (edge shadows hint at more). Tables placed directly in `article`/`section` scroll too.
- Minimum text size on phones is about 11-12px (`0.75rem`); interactive targets are at least 2.5rem tall
- Inline `<style>` blocks in templates come AFTER `style.css` in the cascade, so responsive rules for page-specific components (scoring analysis) live in that inline block

### Collapsible Sections (`details.fold`)
Used by the rules and scoring analysis pages. `<details class="fold" open><summary><h2>..</h2></summary><div class="fold-body">..</div></details>`.
- `data-mobile-collapsed` starts a section closed at <= 576px (rules: all but the first; analysis: the text-heavy ones, charts stay open so Chart.js sizes them at load)
- The sidebar contents (TOC) opens the target section before jumping; deep links (`#scoring`) open their section
- `.fold-body` caps paragraphs and lists at `68ch`, line-height 1.6
- Rules page has Expand all / Collapse all. They are quiet right-aligned links
  rather than a button pair, and they set their own focus ring because
  `all: unset` drops it.
- The summary heading is sentence case in `--font-ui`, like every other section
  heading. Ten uppercase wide-tracked phrases down a page is the case the
  Typography section rules out by name. Note a phone override at 1.1rem has the
  same specificity as the base rule at 1.05rem and comes later, so below 576px
  the phone value wins; the two are close enough that this is not worth
  resolving, but it is worth knowing before editing either.

## Motion
- **Approach:** Intentional. Motion is tied to narrative moments (finale reveals, champion cards), not applied generically.
- **Easing:** Standard CSS ease-out for entrances, ease-in-out for state changes
- **Transitions:** 0.12-0.2s for hover states (buttons, links, cards)

### Named Animations
| Animation | Duration | Purpose |
|-----------|----------|---------|
| `finale-glow` | 3s infinite alternate | Pulsing box-shadow on finale banner |
| `torch-light` | 2s ease-out forwards | Winner name reveal (blur -> sharp, scale) |
| `champion-reveal` | 1s ease-out | Staggered card entrance (translateY, opacity) |
| `ember-rise` | 2.4-3.6s linear infinite | Particle float-up effect on finale banner |

### Motion Restraint
- No animations on the main leaderboard (performance, respect for data)
- Ember particles only on finale pages (earned spectacle)
- Champion reveal uses `--reveal-delay` custom property for staggered entrance
- `font-display: swap` on Survivant to prevent layout shift
- `prefers-reduced-motion: reduce` turns off all four named animations. The finale banner keeps a steady glow and the embers are not shown.

## Keyboard and Semantics
- **Focus ring:** `outline: 2px solid var(--fire-glow)` on `:focus-visible`, offset 2px (inset -2px inside the seasons dropdown, where an outer ring would be clipped). Most links and form controls get Pico's focus ring; the custom ring is for controls whose own `box-shadow` or `all: unset` would otherwise hide it (season button and its items, contents buttons, pick pills, collapsible summaries). A new control that sets its own `box-shadow` needs this ring.
- **Disclosure buttons** (season menu, contents panel) carry `aria-expanded` and `aria-controls`, close on Escape, and return focus to the button.
- **One `<h1>` per page.** The finale banner's winner name is styled text, not a heading, so it also stays out of the contents list.

## Component Patterns

### Standings Row (`details.lb-team`)
The standings are one card holding a column header (`.lb-standings-head`) and a row per team, divided by hairlines rather than drawn as separate cards. Each team is a `<details>`. Its `<summary class="lb-row">` is the row: rank, player name with past-winner badges, a "You" chip on your own row, team name, a torch per pick (lit while the castaway is in the game, snuffed once out, with an `n/m` count; hidden on finished seasons), win %, and points. On desktop that is one line; on phones torches and win % drop to a second line under the name.

The standings are hairline-separated rows on the page background, not a bordered card, with a faint alternating fill (`:nth-of-type(even)`) for scanning. Rank is a quiet tabular ordinal; only the leader's carries the accent (`:first-of-type`). **Use `:nth-of-type` and `:first-of-type`, never the `-child` forms**: `.lb-standings-head` is the first child, so a child-counting selector silently matches nothing.

The header and the rows share `--lb-cols` and `--lb-areas`, so they cannot disagree with each other. Three variants set those: the default, `.no-win` when there are no win percentages (every timeline `as_of` view of a season still in progress), and `.is-finished`. **Keep all three at one class of specificity and list every one in the phone block.** Media queries add no specificity, so a base variant written with `:not()` out-specifies the phone override and wins inside it; that shipped once and left phones with a torch column the phone layout does not use. `tests/test_castaway_sheet.py::TestStandingsGridCascade` guards it.
- Opening the row shows the roster (`.lb-roster`): the Sole Survivor pick and any warnings, then the castaway cards, then team stats.
- An open team's row is `position: sticky` so it stays in view while its roster scrolls.
- Rosters start open from 768px up. Below that only the logged-in player's own team starts open, so every row fits on one screen. Choices are remembered for the visit (`sessionStorage`, key `lb-open-teams`); Open all and Close all sit in the toolbar.
- Champion variant gets gold border + glow. Snuffed variant dims to 0.85 opacity.

### Castaway Card (`.lb-pick`, macro `app/templates/_pick_card.html`)
One macro renders every card. On the leaderboard a card is a `<details>` whose summary is a **list row**: a pick-type chip (`DR`, `WC`, `RP`, `RP½`, `SS`), the headshot ringed in tribe colour, the name with its result or tribe, journey badges, and points right-aligned. Rows group under "Draft picks" and "Extra picks". There are no global detail switches; detail opens one castaway at a time. On My Team a card is a plain link with no detail.
- The chip shows an abbreviation, with the full pick type in a `title` and a `visually-hidden` span, so the abbreviation is never the only label.
- The summary holds spans only (`<summary>` allows phrasing content).
- Eliminated picks use the eliminated treatment (see Color: Eliminated Castaways). Sole Survivor picks get gold border + subtle gradient background.

### Castaway Sheet (`.lb-sheet`, inside an open card)
Opening a card shows the sheet. A hero header tinted with the tribe colour (a left border at full strength, the colour only as a low-alpha gradient behind the text, because a saturated tribe colour behind text fails contrast on half the palette) carries the headshot, full name, age, occupation and tribe. Below it are three panels: **Summary** (point breakdown, stats, stats-site link), **Journey** (the narrative events) and **Episodes**.

- The tab strip is rendered with `hidden` and revealed by script, so without JavaScript every panel stays visible under its own heading, which is what the card used to look like. Tabs never appear without something behind them.
- Tabs follow the ARIA pattern: `role="tablist"`, roving `tabindex`, arrow keys plus Home and End, `aria-selected` and `aria-controls`. The ids are built from a `uid` passed in by the caller, because two players can hold the same castaway and the name alone is not unique.
- `.lb-sheet-tab` uses `all: unset`, which drops the focus ring, so it sets its own (see Keyboard and Semantics).

**Episodes table.** One row per episode, stopping at the elimination episode. Two things about `episode_stats` make the obvious reading wrong: its totals are **cumulative**, so an episode's own activity is the difference from the episode before it; and it **keeps repeating the final totals** for every episode after the castaway is voted out, so an untruncated table shows a boot still playing. Cells are heat-tinted on `--heat`, scaled per column to that castaway's own best episode so a quiet player's table still reads. The table sizes to its content rather than stretching, because early in a season most columns are empty.

The episode title and air date ride on the episode number as a `title` attribute plus a `visually-hidden` span: available on hover and to a screen reader, and costing the table no width. **They do not get a column.** The table is numeric and narrow by design and already overflows a phone inside `.table-scroll`; a multi-word text column pushes the numbers, which is what people scan, out of reach.

### Phone Tab Bar (`.tabbar`)
Below 640px the top nav's links are hidden and the primary destinations live in a fixed, pill-shaped bar at the bottom: Standings, My Team (when signed in), Rules, Analysis, and More. Icon above a small uppercase label; the active item is `--fire-glow`, the rest `--text-dim`. More opens a sheet above the bar with the season list and the account links.

- The header is one row on phones as a result. It used to wrap to three, with Logout orphaned on its own line.
- `body` gets bottom padding so content clears the bar, and the contents panel is hidden on phones: the bar owns that corner, and a floating button competing with it is worse than no button.
- `.tabbar` is a `<nav>`, so the `nav a { color: ... !important }` rule had to be scoped to `nav:not(.tabbar)`. A new component placed inside a `<nav>` will hit the same thing.

### Season Timeline (`.season-timeline`)
Horizontal scrollable row of episode dots. Point widths on phones are tuned so a typical in-progress season fits **without** scrolling, because the script scrolls the active dot into view on load: when the row overflows by even a little, that pushes the first milestone label off the left edge, where it reads as a truncated word rather than as something scrolled. A long season still scrolls, which is fine. The connecting line is drawn per point (`.tl-point::before`) so it scrolls with the dots. Milestone dots (Premiere, Merge, Finale) are larger with labels. Active dot gets fire-bright color and is scrolled into view on load. On phones each point is at least 2.75rem wide and only milestone and active labels show.

### Section Headings (`.section-title`, `.subsection-title`)
A page's section headings carry a phrase, so they are sentence case in
`--font-ui`: condensed caps are for labels that sit above data and carry a word
or two. `.section-title` is 1.3rem (1.15rem on phones) in `--sand-warm`;
`.subsection-title` is 1.05rem and sits one step below it.

Both are classes rather than bare `h2` / `h3` rules, and deliberately so. The
element rules are shared with every page and are being changed in more than one
branch at a time; a class is (0,1,0) and wins over an element rule regardless of
source order or media query, so a page's headings cannot be quietly restyled by
work happening elsewhere. Use these on any new page rather than relying on what
the bare `h2` rule happens to be.

Heading levels follow document order with no skipped levels: an `h1` is followed
by `h2`, not by `h3`. Several pages used `h3` directly under the page title,
which both skipped a level and painted a section heading in `--fire-glow`.

### Admin Controls (`.admin-*`)
The admin templates kept their styling in inline `style` attributes. They are
components now, and the reason is mechanical rather than tidiness: an inline
`padding` cannot be overridden by the phone block without `!important`, so the
tap-target floor could not be met while the inline styles remained, and a broad
`!important` is what captured the tab bar in #149.

- `.admin-form-row` -- a row of controls above a table. Wraps rather than
  squeezing its first field. Its label rule is written `:not(.admin-field)`,
  because at (0,1,1) it would otherwise out-specify a stacked `.admin-field`
  and silently flatten it back into a row.
- `.admin-field` -- label above value, with `.admin-field-label` as the micro
  label. Note it is a **column** flex container, so a `flex-basis` set on a
  child is a basis on the vertical axis; size the wrapper, never the input.
- `.admin-btn-sm` -- the compact admin button. Dense on desktop, which is what
  draft night wants; at least 2.5rem tall on phones.
- `.admin-check` -- hit area for a bare checkbox. The glyph stays its natural
  size and the wrapping label carries the 2.5rem target, because a 2.5rem
  checkbox reads as a button. The label is also what gives the control an
  accessible name.
- `.admin-page-nav`, `.admin-page-meta` -- the back link and the dim meta line
  under an admin `h1`.

Admin tables are wrapped in `.table-scroll`. They already scrolled without it,
via `article > table, section > table`, but only `.table-scroll` paints the
edge-shadow gradients, so nothing told you the content continued. The wrapper
also returns the table to `display: table` rather than the `display: block`
the fallback needs.

### Stats Grid (`.stats-grid`)
3-column CSS Grid for stat items. Each item has a label (Bebas Neue, dim) and value. Drops to 1-column at 576px.

### Toolbar (`.lb-toolbar`)
Flex row with compare link left; Open all, Close all and the Projected switch right (`margin-left: auto`). Bebas Neue, uppercase. Projected is the only global switch, because it changes everyone's win %. On phones the link goes full width and the controls become bordered chips (fire-bright border when on).

### Sidebar TOC (`.page-toc`)
Fixed-position overlay, bottom-left (full-width bottom sheet on phones, 0.95rem links). Toggle button always visible. Panel has Cinzel links with active border-left indicator (fire-bright). Uses IntersectionObserver + localStorage for persistence.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2024 | Pico CSS v2 dark theme as base | Lightweight, semantic HTML-first, good dark mode support |
| 2024 | Survivant font for logo only | Thematic authenticity, but too decorative for body text |
| 2024 | Cinzel for headings | Classical authority matches Survivor's dramatic tone |
| 2024 | Bebas Neue for data/labels | Condensed form fits dense stat displays, uppercase matches show aesthetic |
| 2024 | No light mode | Dark theme IS the product identity (Tribal Council at night) |
| 2024 | Ember particles finale-only | Earned spectacle, not ambient decoration |
| 2026-04-07 | DESIGN.md created | Documented existing system via /design-consultation for mobile responsive work |
| 2026-04-07 | Mobile responsive overhaul | Added 640px nav breakpoint, 577-768px tablet breakpoint, overflow-x fixes, table scroll wrappers, container padding |
| 2026-10-01 | Phone tab bar; one interface font | Navigation moved to a bottom bar so the phone header stops wrapping to three rows. Cinzel narrowed to h1 and Bebas to micro-labels, with a system sans carrying the interface, because three decorative families at once read as unpolished. Survivant, Palatino on castaway names, the torches and the fire accent stay: they carry the theme without being in the way of the data. |
| 2026-10-01 | Castaway sheet | Tribe-tinted hero plus Summary / Journey / Episodes tabs, replacing the stacked detail block. Tabs are progressive: the strip is hidden until script reveals it. |
| 2026-10-01 | Rosters as list rows | Replaced the 2-up pill grid. Denser, and it gives points a consistent right-aligned column. |
| 2026-10-01 | Episode titles on hover, not in a column | The Episodes table is numeric and already scrolls on a phone; a text column costs the numbers more than the title gains |
| 2026-10-01 | Responsive + readability pass (v2) | Consolidated all breakpoints at end of `style.css`; nav wraps instead of overflowing; header split via `.lb-sub`; 2-up pick grid; chips for toolbar toggles; scrollable timeline; collapsible `details.fold` sections with TOC integration for rules and analysis; `68ch` reading measure |
| 2026-10-02 | Accent means state, applied beyond the leaderboard | My Team and compare painted rank and points in the champion colour unconditionally, so rank 1 of 4 and rank 4 of 4 looked identical and the colour carried no information. The accent now marks the leader and live state only, which is what #149 established on the leaderboard. |
| 2026-10-02 | Type Scale table corrected against the rendered page | Six rows still named Cinzel or Bebas for things #149 had moved to the interface sans, and the tracking figures for h1-h3 were from before that pass. The table is the first thing a new page is built from, so a stale row re-introduces exactly what the pass removed. |
| 2026-10-02 | Page CSS lives in `style.css`, not in the template | The scoring analysis page kept 128 lines in an inline `<style>` block, which came after the stylesheet and so won every equal-specificity tie. It is in `style.css` now, base rules at the end of the base section and its media rules appended to the matching breakpoint blocks, which was only safe because none of its selectors existed elsewhere in the file. A second stylesheet in a template is a second place to look and a silent tie-breaker. |
