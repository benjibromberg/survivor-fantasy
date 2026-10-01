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

Four fonts, each with a distinct role. This is the core of the visual identity.

- **Logo/Branding:** `Survivant` (local @font-face, `fonts/survivant.ttf`) -- Official Survivor logo font. Used ONLY for site logo, finale labels, and logo motto. Uppercase, wide letter-spacing (0.08-0.2em). CSS var: `--font-logo`
- **Headings/Player Names:** `Cinzel` (Google Fonts) -- Serif with classical authority. Used for h1-h3, player names, leaderboard names, TOC links. Letter-spacing 0.03-0.04em. CSS var: `--font-heading`
- **Labels/Stats/UI:** `Bebas Neue` (Google Fonts) -- Condensed sans for data-dense UI. Nav buttons, toggle buttons, rank numbers, stat values, badge text, point displays. Uppercase with letter-spacing 0.04-0.1em. CSS var: `--font-label`
- **Body/Castaway Names:** `Palatino Linotype` > `Palatino` > `Book Antiqua` > `serif` (system) -- Warm serif for longer text. Castaway names, pick meta text, body paragraphs. CSS var: `--font-tribal`
- **Loading:** Survivant is self-hosted (`/static/fonts/survivant.ttf`, `font-display: swap`). Cinzel and Bebas Neue via Google Fonts CDN.

### Type Scale
| Element | Font | Size | Weight | Spacing |
|---------|------|------|--------|---------|
| Site logo | Survivant | 1.5em (1.15em mobile) | normal | 0.08em (0.04em mobile) |
| h1, h2 | Cinzel | default | default | 0.04em |
| h3 | Cinzel | default | default | 0.03em |
| Nav buttons | Bebas Neue | 0.95em | 400 | 0.08em |
| Rank numbers | Bebas Neue | 1.6em | 400 | 0.04em |
| Points | Bebas Neue | 1.3em | 400 | 0.04em |
| Player name | Cinzel | 1.2em | 700 | 0.02em |
| Castaway name | Palatino | 0.9em | -- | -- |
| Pick meta | Palatino | 0.75em | -- | -- |
| Stat values | Bebas Neue | varies | -- | 0.02-0.04em |
| Badge text | Bebas Neue | 0.65-0.95em | -- | 0.04em |

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
| `--palm-green` | `#2d6a4f` | Success messages |
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
| Success | `#2d6a4f` | `--palm-green` |
| Error | `#e85d26` | `--fire-bright` |
| Warning | `#f4a261` | `--fire-glow` |
| Info | `#9aa5b1` | `--text-dim` |

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
- Rules page has Expand all / Collapse all

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

### Leaderboard Entry (`.leaderboard-entry`)
Card with left accent border (4px, fire-bright). Contains header (rank + name + points, plus `.lb-sub` holding Sole Survivor pick, win % and warnings: `display: contents` on desktop, its own second line on phones) and pick pills row. Champion variant gets gold border + glow. Snuffed variant dims to 0.85 opacity.

### Pick Pill (`.lb-pick`)
Compact card showing castaway headshot (80px, 48px on mobile), name, status, and optional stats/journey. On phones pills sit in a 2-up grid; with any detail toggle on they go full width. Eliminated picks dim to 0.55 opacity. Sole Survivor picks get gold border + subtle gradient background.

### Season Timeline (`.season-timeline`)
Horizontal scrollable row of episode dots. The connecting line is drawn per point (`.tl-point::before`) so it scrolls with the dots. Milestone dots (Premiere, Merge, Finale) are larger with labels. Active dot gets fire-bright color and is scrolled into view on load. On phones each point is at least 2.75rem wide and only milestone and active labels show.

### Stats Grid (`.stats-grid`)
3-column CSS Grid for stat items. Each item has a label (Bebas Neue, dim) and value. Drops to 1-column at 576px.

### Toolbar (`.lb-toolbar`)
Flex row with compare link left, toggle switches right (`margin-left: auto`). Toggles use Bebas Neue, uppercase. On phones the link goes full width and toggles become bordered chips (fire-bright border when on).

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
| 2026-10-01 | Responsive + readability pass (v2) | Consolidated all breakpoints at end of `style.css`; nav wraps instead of overflowing; header split via `.lb-sub`; 2-up pick grid; chips for toolbar toggles; scrollable timeline; collapsible `details.fold` sections with TOC integration for rules and analysis; `68ch` reading measure |
