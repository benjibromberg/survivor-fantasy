---
name: survivor-frontend
description: >-
  What the survivor-fantasy templates and stylesheet contain: the four brand
  fonts, what each page renders, and the leaderboard's features. Use when
  changing a template, adding a page, or working out where an existing bit of
  UI lives. DESIGN.md is the design system itself and must be read before any
  visual decision; the CSS traps that cause bugs stay in CLAUDE.md.
---

# survivor-frontend

An index of the front end. Two things deliberately live elsewhere:

- **`DESIGN.md`** in the repo is the design system: fonts, palette, spacing,
  breakpoints, component patterns. Read it before any visual decision.
- **The CSS traps** (Pico container overflow, the breakpoint cascade, media
  queries adding no specificity, flex-column collapse, the nav dropdown) stay
  in `CLAUDE.md`, because they cause bugs and that file always loads.

## Fonts and styling

- **Fonts**: Survivant (official Survivor logo font, local `@font-face`), Bebas Neue (labels/stats via Google Fonts), Cinzel (headings/player names via Google Fonts), Palatino (castaway names, system font)
- **Styling**: Custom CSS variables layered on Pico CSS v2 dark theme. Tribal Council / Fiji Night color palette.

## What each template renders

- **Base template** (`base.html`): SVG torch logo, nav bar with Bebas Neue buttons, custom JS seasons dropdown, auto-generated sidebar TOC (from h2 headings, IntersectionObserver-based active highlighting, localStorage persistence), footer with credits/license
- **Leaderboard** (`leaderboard.html`): Standings are one `<details class="lb-team">` per team: the summary row holds rank, name/team name, a torch per pick, win % and points; opening it shows the roster. Each castaway card (macro `_pick_card.html`, the only place card markup lives) is also a `<details>` that opens its breakdown, stats, bio and journey. Toolbar: Compare scoring, Open all / Close all, and the Projected switch (the only global switch). Rosters start open from 768px; on phones only the logged-in player's team does; choices persist in `sessionStorage` key `lb-open-teams`. Also: episode timeline, Chart.js progression graph with mini standings sidebar, season stat boards. Finale celebration on finished seasons (hero banner, champion card, snuffed torches, Sole Survivor pick highlight). Past season winner badges on player names.
- **Rules** (`rules.html`): Sections for draft, wildcard, replacement (with eligibility/scoring subsections), sole survivor pick, scoring components table, example calculations, pick type summary

## Leaderboard features

- **Episode-based timeline**: Dots per episode (not per elimination), with milestone markers (Pre-game, Premiere, Merge, Finale)
- **Character Journey Cards**: Auto-generated narrative highlights per pick pill. Compact badges always visible (max 4, priority-ordered: immunity > idol > merge > votes > advantage > fire). Journey timeline (color-coded dots, episode labels, narrative text) shows when that castaway's card is opened. Tribal immunity aggregated per tribe phase. Fire losers show "Lost fire-making challenge". SS picks skip journey/badges. Events truncate to `as_of_episode` for timeline consistency.
- **Point breakdown and game stats**: shown per castaway when its card is opened (there are no global Points/Stats/Bio/Journey switches any more)
- **Team aggregate stats**: Combined stats across all picks per fantasy player
- **Season progression chart**: Chart.js line graph with mini standings sidebar (color-coded to match chart lines)
- **Win probabilities**: Two modes toggled via "Projected" switch:
  - **Frozen** (default): simulates elimination orders using current stats as-is
  - **Projected**: adds expected future immunity wins, idol finds, and advantage plays based on historical rates from new-era survivoR data. Uses per-player career immunity win rates (from new-era appearances), idol protection modeling (idol holders may survive one extra tribal)
- **Eligibility warnings**: Reminds players to pick wildcards and replacements when eligible
- **Finale celebration** (finished seasons only): "The Tribe Has Spoken" hero banner with ember particle animation, champion card with lit torch and gold glow, snuffed torch icons for non-winners, Sole Survivor pick pill highlighted across all teams
- **Winner badges**: Past season winners show gold `S46`, `S47` etc. badges next to their name on all leaderboards
