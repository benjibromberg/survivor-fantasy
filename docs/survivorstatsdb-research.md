# survivorstatsdb research

Research into [survivorstatsdb.com](https://survivorstatsdb.com) for the Survivor Fantasy
webapp: its URL scheme, what each page shows, where its data comes from, whether it can be
used, and which parts fit the planned Sleeper-style features (a castaway player sheet with
tabs, a mobile bottom tab bar, collapsible teams, a live draft board). The trigger was a
broken link. The app builds `https://survivorstatsdb.com/castaway/{version_season}/{castaway_id}`
and the site now uses query-string URLs.

This document changes no app code.

- Date: 2026-10-01
- Baseline: `origin/main` at `fad94c0`. Every `file:line` reference into this repo is against
  that commit.
- Requests to survivorstatsdb.com: 20, at least 6 seconds apart, listed in Appendix A. None
  returned 429 or 503. The old castaway URL returned 500. The last request
  (`data/castawayCareer.json`) lost its connection after 5,764,000 of 6,238,816 bytes and was
  not retried.
- Every claim about the site comes from a raw response fetched with curl. No WebFetch summary
  is used as evidence.

## How to read the evidence labels

| Label | Meaning |
|---|---|
| **[fetched]** | Read in the raw response of a survivorstatsdb.com URL fetched for this document. Line numbers in the site's JavaScript (`js/*.js`) refer to the files as fetched on 2026-10-01. |
| **[read]** | Read in this repo, in the survivoR repo on GitHub, or in a downloaded survivoR.xlsx. |
| **[computed]** | Calculated by comparing data files. The method is in Appendix B. |
| **[inferred]** | My reading of the evidence. Plausible, not confirmed. |
| **[not verified]** | Could not be confirmed within the request budget. Treat as unknown. |

## Summary

1. **URL scheme.** The site uses query strings, not paths. A castaway has two kinds of page:
   - one season: `https://survivorstatsdb.com/castaway?id={version_season}{castaway_id}`,
     for example `castaway?id=US51US0771`;
   - career: `https://survivorstatsdb.com/castaway?id=career{castaway_id}`, for example
     `castaway?id=careerUS0009`.

   Both ids are built from two values the app already stores (`Survivor.version_season` and
   `Survivor.castaway_id`, `app/models.py:138-139`). Seasons are `season?vs=US51` with an
   optional `&tab=`. Challenges are `challenge?v=US&name=A%20Bit%20Tipsy`. There are no
   episode, tribal council, tribe or advantage pages. The path the app links to today returns
   HTTP 500. **[fetched]**
2. **Data.** A castaway page shows a profile, an overall score with six components, two threat
   levels, a challenge record and history, a vote record and history, alliances, jury votes,
   confessionals against an expected count, advantages and achievements. A season page has ten
   tabs. Everything is drawn in the browser from static JSON under `/data/`. For Season 51 the
   site has data through episode 1, the same as survivoR. Model outputs (scores, threat levels,
   win predictions) are missing for every castaway still in the game. **[fetched]**
3. **Source.** The site is built on survivoR by survivoR's author. Nearly everything a fantasy
   player would want (each vote, each challenge, each advantage, confessionals with expected
   counts, episode titles, bios) is already in survivoR.xlsx, which the app downloads every
   morning. Site-only items are few: inputs from user votes and from a podcast, the win
   predictions (finished seasons only), the challenge proficiency ratings, and the portraits.
   **[read, computed]**
4. **Use.** robots.txt allows everything. The site has no terms of use, no licence page and no
   API. survivoR is MIT-licensed. The site footer says "A fan project. All rights and content
   for Survivor belong to CBS." Linking to the site is fine. Build features from survivoR.xlsx,
   not from the site's JSON. **[fetched, read]**
5. **Recommendation.** Fix the link first (one line plus one test). Then build the player
   sheet's Episodes, Votes, Advantages, Challenges and Confessionals tabs from survivoR. Most of
   the sheets they need are already read by `refresh_season()` (`app/data.py:198-208`); only
   Episodes and Challenge Description are new, plus Advantage Timeline if wanted. Section 5
   ranks the work.

## 1. URL scheme

### 1.1 Page types

`vs` below is survivoR's `version_season` (`US51`). All 77 `vs` values in the site's
`data/seasons.json` are the version code plus a two-digit season number **[computed]**.

| Page | Pattern | Id format | Verified examples | Evidence |
|---|---|---|---|---|
| Castaway, one season | `/castaway?id={vs}{castaway_id}` | `version_season` and `castaway_id` joined with no separator. All 1,464 records in `data/castaways.json` follow this rule **[computed]**. | `castaway?id=US51US0771` (Rob Antonson, S51), `castaway?id=US50US0009`, `castaway?id=US08US0009`, `castaway?id=US01US0001` | `id` read from the query string at `js/castaway.js:7-8`, looked up in `castaways` at `:15`, season tabs link `castaway?id=${ps.id}` at `:181`. Example ids found in `data/castaways.json`. **[fetched]** |
| Castaway, career | `/castaway?id=career{castaway_id}` | `career` followed by `castaway_id`. All 1,122 complete records in my partial download follow this rule **[computed]**. | `castaway?id=careerUS0009` (Jenna Lewis Dougherty, three seasons), `castaway?id=careerUS0001`, `castaway?id=careerUS0477` (Aubry Bracco) | Career detected by the `career` prefix at `js/castaway.js:9`, looked up in `careers` at `:13`, built as `` `career${castaway.castaway_id}` `` at `:173`. Example ids found in `data/castawayCareer.json`. **[fetched]** `careerUS0771` is the owner's example; my download stopped at `careerUS0676`, so its presence in the data is **[not verified]**. |
| Season | `/season?vs={vs}`, optionally `&tab={tab id}` | `version_season` | `season?vs=US51` (fetched, 200), `season?vs=US50`, `season?vs=US51&tab=tab-vote-history` | `vs` read at `js/season.js:96-99`. Tab links built at `js/sitemap.js:41-46`. The active tab is written to `?tab=` and restored on load (`js/season.js:2242-2298`). **[fetched]** |
| Season list | `/seasons`, `/seasons?filter=inprogress` | none | | `js/app.js:493-505` **[fetched]** |
| Challenge (one recurring challenge in one version) | `/challenge?v={version}&name={challenge name, URL-encoded}` | version code and challenge name, matched without regard to case (`js/challenge-data.js:40-46`) | `challenge?v=US&name=A%20Bit%20Tipsy` (from the sitemap, fetched, 200), `challenge?v=US&name=Quest%20for%20Fire`, `challenge?v=US&name=A%20Crate%20Idea` | The three names are `recurring_name` values in survivoR's Challenge Description sheet (14, 7 and 6 US rows) and `challenge_name` values in the site's `seasons.json` **[computed]**. The page reads `data/challenge_summary.json`, which was not fetched, so whether each of these names renders a profile is **[not verified]**. |
| Lists | `/castaways`, `/castaways-map`, `/challenges` | none | | sitemap **[fetched]** |
| Leaderboards | `/leaderboards`, `/leaderboard/{name}` | `overview`, `scores`, `achievements`, `challenges`, `voting`, `jury`, `confessionals`, `advantages`, `first-boots`, `quits`, `medevacs`, `runner-ups`, `winners`, `oldest-players`, `resi`, `season-rank`, `episode-rank` | | `js/app.js:183-201`, sitemap **[fetched]** |
| Records | `/records` | none | | sitemap **[fetched]** |
| Analysis | `/article-list`, `/articles/{slug}` | `final-immunity-challenge`, `the-edit`, `viewers`, `winner-confessionals`, `right-side-of-the-vote` | | sitemap **[fetched]** |
| Fun pages | `/fun-stuff`, `/polls`, `/top10`, `/birthdays`, `/birthday`, `/personality-test`, `/trivia` | none | | sitemap, `js/app.js:208-215` **[fetched]** |
| Reference | `/about`, `/data-dictionary`, `/confessional-timing`, `/change-log`, `/download`, `/sitemap`, `/leaderboard-dir` | none | | sitemap **[fetched]** |

The ten season tab ids are `tab-overview` (the default, which removes `tab` from the URL),
`tab-castaways`, `tab-stats`, `tab-voted-with`, `tab-edit`, `tab-predictions`,
`tab-vote-history`, `tab-challenges`, `tab-confessionals` and `tab-advantages`
(`season.html` lines 21-30). A tab with no data for the season is hidden, and a deep link to a
hidden tab falls back to Overview (`js/season.js:2277-2298`) **[fetched]**.

### 1.2 Page types that do not exist

The sitemap calls itself "Every page on Survivor Stats DB" and lists no episode, tribal
council, tribe or advantage page **[fetched]**. Episode-level data appears inside other pages:

- the season Overview timeline, one point per episode with title, air date, length, viewers and
  IMDb rating in a tooltip (`js/season.js:240-320`);
- the season Vote History tab, one card per boot headed "Ep N, Boot M"
  (`js/vote-history-season.js:50-178`);
- the season Challenges tab, one card per challenge headed "Ep N: name"
  (`js/season.js:2142-2233`);
- the season Confessionals tab, an episode-by-episode table;
- `/leaderboard/episode-rank`, which ranks episodes (not fetched).

### 1.3 Castaway links: what to change in the app

- The app builds `f"https://survivorstatsdb.com/castaway/{self.version_season}/{self.castaway_id}"`
  (`app/models.py:191-196`) **[read]**. `https://survivorstatsdb.com/castaway/US51/US0771`
  returned "500 Internal Server Error" on 2026-10-01 **[fetched]**. Every "Full stats on
  survivorstatsdb" link (`app/templates/_pick_card.html:118-120`, and the summary-card link at
  `:37-38`) is therefore broken.
- The per-season replacement is `https://survivorstatsdb.com/castaway?id=US51US0771`. It uses
  the same two stored fields, so no new data is needed. The career equivalent is
  `castaway?id=careerUS0771`.
- Link the per-season page. The Challenge History and Voting History panels only appear on
  per-season pages (`js/castaway.js:1065-1071`, `:1109-1115`), and every per-season page has a
  "CR" tab that links to the career page (`:174-185`) **[fetched]**.
- `tests/test_standings_markup.py:223` asserts the old prefix
  `https://survivorstatsdb.com/castaway/US99/` and has to change in the same commit **[read]**.
- An HTTP 200 does not prove an id exists. The castaway HTML is one static 1,519-byte shell,
  the profile is drawn by `js/castaway.js`, and an unknown id renders "Castaway not found"
  (`js/castaway.js:18-21`) **[fetched]**. The server presumably answers 200 for any id
  **[inferred: I fetched one id only]**. The construction rule above holds for every record in
  the data, so building the id is safe without a lookup.
- The site's own code also links `castaway.html?id=...` in one place (`js/season.js:2431`)
  **[fetched]**. I did not request that form.

## 2. What each page shows

Every page is rendered in the browser. The HTML is a shell; `js/app.js` injects the navigation
and footer and routes on the file name (`js/app.js:44-108`), and each page's script fetches JSON
from `/data/` **[fetched]**. `fetchData()` loads `seasons.json`, `castaways.json`,
`castawaySeasonTbl.json`, `castawayCareer.json` and `confessionals.json`
(`js/app.js:338-380`). A comment there says each call used to re-parse "~16MB of JSON on the
main thread" (`js/app.js:322-325`). The two core files I fetched in full were 7.95 MB
(`castaways.json`) and 1.58 MB (`seasons.json`).

### 2.1 Castaway page (one season, and career)

From `js/castaway.js` **[fetched]**. Both modes use one layout. Career mode aggregates across
seasons and leaves out the per-event tables.

| Block | Contents | Lines |
|---|---|---|
| Hero | Portrait, full name, age (career: from date of birth), city and state, occupation, "three words", place and result (or a Live marker), Merge, Jury and FTC tags (career: counts and wins), achievement icons, Share button | `:976-995` |
| Key stats | Overall Score with overall rank, Challenge Threat, Strategic Threat, Days Played with rounds survived, Challenge Wins with win rate, Successful Boots with percentage | `:950-958` |
| Timeline | Per season: one point per episode with tribe status, a boot marker, and a tooltip with title, date, length, viewers and IMDb rating. Career: one point per season with place. | `:192-345` |
| Scores & Threat | Overall score with a meter against the top score in that version; six component bars (Survivability, Read, Challenges, Influence, Advantage, Jury Management) on a -3 to 3 scale; challenge and strategic threat levels with up and down vote buttons | `:90-133`, `:765-824`, `:1003-1025` |
| Challenges | Record table by type (all, reward, immunity, tribal, individual, team, duels) with won, played and win %; proficiency on seven categories; per season, one row per challenge with overall number, episode, name, type, outcome, won and sat out | `:347-460`, `:826-880`, `:1027-1072` |
| Voting & Jury | Voting score, tribal councils, attended and %, votes cast, successful boots and %, votes in the majority and %, votes received, votes negated, main alliance, nemesis, exit quote; jury votes received and not received, or who a juror voted for; per season, one row per tribal with the vote, the boot, immunity and tribe status, wrong votes highlighted | `:462-572`, `:670-735`, `:1074-1116` |
| Screen Time | Edit verdict (for example "Under-edit"); confessional count and confessionals per hour, each as actual, expected, index and rank; game time, confessional time, episodes with zero, episodes with the most, most in one episode | `:1118-1166` |
| Game & Advantages | Days, rounds played, survived, not survived and in limbo, FTCs, RESI awards; auction purchases; advantages and idols found, held, played, played successfully, voted out with, tribals held; per season, a timeline for each advantage | `:574-668`, `:738-763`, `:1168-1215` |
| Achievements | Badges such as Riddler, Bullet Sponge, Trinket and Packin' Heat, each linked to its definition | `:893-926`, `:1217-1221` |

The in-page section links are Scores & Threat, Challenges, Voting & Jury, Screen Time, Game &
Advantages and Achievements (`:960-968`). That is close to the tab set planned for the app's
player sheet.

### 2.2 Season page

From `season.html`, `js/season.js` and `js/vote-history-season.js` **[fetched]**.

| Tab | Contents | Data file | Lines in `js/season.js` |
|---|---|---|---|
| Overview | Hero (number, name, location, aired and filmed dates, description); facts (castaways, days, tribes, finalists, jury, IMDb rating); tribe pills; episode timeline; tiles for winner with runners-up and final vote, average age, mean viewers, most immunities, total confessionals, most favourable edit, zero votes received, idols played and flushed, medevacs and quits, returnees, played future seasons and experience. Before a US premiere, a countdown replaces the tiles. | `seasons.json`, `castaways.json` | `:105-238`, `:860-1008` |
| Castaways | A card per castaway | `castaways.json` | |
| Player Stats | Sortable table: place, age, original tribe, merge, jury, FTC, days, rounds survived, overall score, both threat levels, tribals attended, votes received, successful boot %, confessional count, CPH and time, challenge wins by type | `castaways.json` | `:1764-1870` |
| Voted With | Pick a castaway, see who voted with them and how many times | `voted-with.json` | `:2308-2464` |
| The Edit | CPH and index by gender and by age group (18-39, 40+); CPH across the season; observed against expected CPH; a per-castaway edit index; residual CPH by episode | `edit-gender.json`, `edit-age.json`, `edit-line-chart.json`, `confessionals-dumbbell.json`, `res-cph-line-chart.json` | `:586-841` |
| Predictions | Favourite and least likely to win, most likely to win the final immunity, most votes with the jury; win probability with an interquartile band; win probability over time; threat scatter; jury voting alignment | `predictions.json`, `predictions-line-chart.json`, `threat-level-scatter-chart.json`, `jury-vote-chart.json` | `:333-584` |
| Vote History | A card per boot with the exit quote. Per vote: voter, voted for, vote order, immunity, nullified, vote event, event outcome, split vote, successful boot, majority vote. | `vote-history.json` | `js/vote-history-season.js:50-178` |
| Challenges | A card per challenge: episode, name, other names, type, outcome type, description, categories, winners | `seasons.json` (`challenge_history`) | `:2142-2240` |
| Confessionals | Episode-by-episode tables of count and of time | `confessionals.json`, `confessional-time.json` | `:1909-2140` |
| Advantage Movement | A chart of who held which advantage when | `advantage-movement-chart.json` | `:843-858` |

### 2.3 Challenge page

From `js/challenge.js` and `js/challenge-data.js` **[fetched]**. One page per recurring
challenge per version: times played, and how many of those were reward, immunity, tribal,
individual and duel; eight attribute flags (race, endurance, rounds, puzzle, balance,
precision, water, strength) counted by season; winners grouped by season with tribe colour;
links to the same challenge in other versions. It reads `data/challenge_summary.json` and
`data/challenge_winners.json` and joins them on version and name (`js/challenge-data.js:90-150`).

### 2.4 Pages not sampled

The home page, leaderboards, records, articles and fun pages were not fetched. Their URLs are
in Section 1.1. I make no claim about their contents.

### 2.5 How fresh it is for Season 51

As fetched on 2026-10-01 **[fetched, computed]**:

- `data/castaways.json`, `data/seasons.json` and `data/castawayCareer.json` were last modified
  2026-09-29 between 07:46 and 07:47 GMT, together with `js/app.js`, which sets
  `window.DATA_VERSION = '1.0.19'` (`js/app.js:2`). The JSON is requested with `?v=1.0.19` and
  served with `cache-control: public, max-age=31536000, immutable`.
- `seasons.json` marks US51 as the only season `in_progress`. Its `timeline` is empty and its
  challenge history holds episode 1 only.
- `castaways.json` has 21 US51 records, all with episode 1 confessionals, votes and challenges.
  One castaway is out (place "21st", result "1st voted out", `boot_episode` 1). For the other 20,
  `overall_points`, the threat levels, `days` and `place` are empty, so their Scores & Threat
  panels have nothing to show.
- `data/predictions.json` (last modified 2026-07-12) has 310 rows: five castaways for each of 62
  finished seasons, and none for US51. The Predictions tab looks back at finished seasons; it is
  not a live forecast.
- Upstream, survivoR's xlsx gained US51 in a commit titled "ADD US51" at 2026-09-26 15:18 UTC,
  three days after episode 1 aired on 2026-09-23 (Episodes sheet, `episode_date`) **[read]**.
  During Season 50 the per-episode commits ("ADD US50E8" through "ADD complete US50") landed one
  to four days after each air date. For US51 the site's JSON followed the xlsx by about three
  days, which is one observation.
- The About page says "All data, tables, and analyses on this site are refreshed after each new
  season airs." The US51 data shows updates within a season in practice.
- The app downloads the same xlsx every morning (`app/data.py:18-19`, `:183-189`) **[read]**,
  so for episode-level stats the app is at least as current as the site.

## 3. Where the data comes from

### 3.1 It is survivoR, published by survivoR's author

- survivoR's README says "For those that don’t want to do the wrangling, check out the
  survivorstatsdb." and "If you are interested in counting/timing confessionals, I have a new
  app on the Survivor Stats Db." It is written in the first person by Daniel Oehm, the package
  author in `DESCRIPTION`
  ([README](https://github.com/doehm/survivoR/blob/master/README.md),
  [DESCRIPTION](https://github.com/doehm/survivoR/blob/master/DESCRIPTION)) **[read]**.
- The site's About page says "The data is freely available from Github or for download as an
  xlsx" and links to the same `dev/xlsx/survivoR.xlsx` that the app downloads. The footer links
  the survivoR repo, the same xlsx, and the Bluesky account that the README gives as the
  author's (`js/app.js:249-251`, `:296-297`). The Change Log page renders survivoR's `NEWS.md`
  (`js/app.js:571`). The Download page serves `data/survivoR.xlsx`. **[fetched]**
- survivoR's documentation points back to the site: the `castaway_scores` and
  `advantage_timeline` help pages both end with `\url{https://survivorstatsdb.com}`
  ([castaway_scores.Rd](https://github.com/doehm/survivoR/blob/master/man/castaway_scores.Rd),
  [advantage_timeline.Rd](https://github.com/doehm/survivoR/blob/master/man/advantage_timeline.Rd))
  **[read]**.
- The site's JSON uses survivoR's identifiers and column names (`castaway_id`,
  `version_season`, `challenge_id`, `r_score_*`, `threat_*`) **[fetched]**.

### 3.2 How closely the site matches the xlsx

The site's JSON against the current xlsx (survivoR `master`, commit `7336413`, 2026-09-26)
**[computed]**:

| Site field | xlsx source | Agreement |
|---|---|---|
| `conf_count` | Confessionals `confessional_count`, summed per castaway | US51 21 of 21, US50 24 of 24 |
| `conf_expected_count` | Confessionals `exp_count`, summed | US47 18 of 18, US50 24 of 24, US51 21 of 21 |
| `conf_time` | Confessionals `confessional_time`, summed | the same three seasons, every castaway |
| `total_minutes` ("game time") | Episodes `episode_length`, summed up to the boot episode | US50 24 of 24 |
| `challenge_wins_individual_immunity` | Challenge Results `won_individual_immunity`, summed | US50 24 of 24 |
| `n_votes_recieved` (sic) | Vote History rows grouped by `vote_id` | US50 19 of 21 |
| `score_overall` | Castaway Scores `score_overall` | 949 of 1,441 rows. 23 of the 26 non-US seasons agree on every row. No US season agrees on every row. |
| `threat_challenge` | Castaway Scores `threat_challenge` | 540 of 1,441 rows. All 26 non-US seasons agree. Nearly every US row differs. |

Counts match exactly. Model outputs match outside the US and differ for US seasons. The threat
panel says "User votes are used to refine the score" (`js/castaway.js:814`), the threat buttons
post to `/api/save_vote.php` (`:1448`), and the career file carries `r_score_top10_votes` and
`r_score_resi`, which have no column in the xlsx **[fetched]**. The US differences are
therefore probably site-only inputs **[inferred]**. The site also has scores for the one US51
castaway who is out, while the xlsx Castaway Scores sheet has no US51 rows yet **[computed]**.

### 3.3 Item by item

Verdicts: **in survivoR** (a column holds it), **derive** (computable from survivoR columns
with a stated rule), **site only**. "App today" says whether `refresh_season()`
(`app/data.py:192` onward) reads and stores the item **[read]**. Sheet coverage figures are
**[computed]** on the current xlsx.

| Item | Where on survivorstatsdb | survivoR sheet: columns | App today | Verdict |
|---|---|---|---|---|
| Boot order, place, result, jury, finalist, winner | hero, Player Stats | Castaways: `order`, `place`, `result`, `jury`, `finalist`, `winner`, `episode`, `day` | stored | in survivoR |
| Age, city, state, occupation, MBTI | hero | Castaways: `age`, `city`, `state`; Castaway Details: `occupation`, `personality_type` | stored | in survivoR |
| Three words, hobbies, pet peeves, date of birth | hero ("three words") | Castaway Details: `three_words`, `hobbies`, `pet_peeves`, `date_of_birth` (US51: 21, 19, 18 and 20 of 21 filled) | not read | in survivoR |
| Exit quote | Voting panel, Vote History cards | Castaways: `ack_quote` | not read | in survivoR |
| Tribe per episode, swaps, merge, exile | timeline, Player Stats | Tribe Mapping: `tribe`, `tribe_status`; Boot Mapping: `tribe`, `tribe_status`, `game_status`; Tribe Colours: `tribe_colour` | Tribe Mapping and Tribe Colours stored | in survivoR (see open question 4 for US51) |
| Episode title, air date, length, viewers, IMDb rating | timeline tooltips | Episodes: `episode_title`, `episode_date`, `episode_length`, `viewers`, `imdb_rating`, `episode_summary` (US seasons 41 and later: title and date 132 of 132 episodes, summary 117 of 132) | not read | in survivoR |
| Confessional count and time per episode | Screen Time, Confessionals tab | Confessionals: `confessional_count`, `confessional_time` | stored per episode | in survivoR |
| Expected confessionals and edit index | Screen Time, The Edit tab | Confessionals: `exp_count`, `exp_time`, `index_count`, `index_time` | not read | in survivoR |
| Edit verdict (Under, Balanced, Over) | Screen Time | The Data Dictionary sets the cut-offs at an index of 10% below and 10% above expected | | derive |
| Confessionals per hour, game time | Screen Time | Confessionals, plus Episodes `episode_length` summed up to the boot episode | | derive |
| Each vote: target, boot, immunity, nullified, vote event and outcome, split vote, vote order | Voting History, Vote History tab | Vote History: `castaway_id`, `vote_id`, `voted_out_id`, `immunity`, `nullified`, `vote_event`, `vote_event_outcome`, `split_vote`, `vote_order`, `tie`, `episode` | season totals and per-episode counts only | in survivoR |
| Successful boot, majority vote, who voted with whom | Voting panel, Voted With tab | Vote History: `vote_id` against `voted_out_id`, and voters sharing a `vote_id` at one tribal | `correct_votes` stored | derive |
| Main alliance, nemesis | Voting panel | Vote History, with the Data Dictionary rules: the ally is the player voted with successfully most often and at least 3 times; a nemesis was voted for at least twice and voted back at least once | | derive |
| Challenge results by type, sit-outs, order of finish | Challenges panel, Player Stats | Challenge Results: `challenge_type`, `outcome_type`, `won`, `won_*`, `sit_out`, `order_of_finish` (US51: 20 of 20 rows have `order_of_finish`) | immunity and reward wins, sit-outs | in survivoR |
| Challenge names, descriptions, categories | Challenge History, Challenges tab, challenge pages | Challenge Description: `name`, `recurring_name`, `description`, `reward`, and flags such as `race`, `puzzle`, `endurance`, `balance`, `precision`, `strength`, `water` | not read | in survivoR |
| Challenge proficiency (0 to 1 per category), challenge ranks | Proficiencies panel | Inputs are Challenge Results and Challenge Description; the rating method is not published | | site only (values) |
| Advantages: found, played, for whom, success, votes nullified | Advantages panel | Advantage Movement: `event`, `day`, `episode`, `played_for_id`, `success`, `votes_nullified`; Advantage Details: `advantage_type`, `clue_details`, `location_found`, `conditions` | found and played counts, current idol holdings | in survivoR |
| Who holds what at each stage of the game | advantage timeline, Advantage Movement tab | Advantage Timeline: `holding` by `sog_id` | not read | in survivoR |
| Journeys, lost votes, exile rewards | not seen on the pages fetched | Journeys: `reward`, `lost_vote`, `game_played`, `chose_to_play`, `event` | not read | in survivoR |
| Each juror's vote | Jury panel, jury alignment | Jury Votes: `castaway_id`, `finalist_id`, `vote` | finalists' totals | in survivoR |
| Auction purchases | Auction panel | Auction Details, Survivor Auction | not read | in survivoR |
| Rounds played, survived, in limbo | Game Stats | Boot Mapping: `final_n`, `game_status` | | derive |
| Overall score and its six components | Scores panel | Castaway Scores: `score_*`, `r_score_*`, `p_score_*` (no US51 rows yet) | `score_overall` only | in survivoR; site values differ for US seasons |
| Challenge and strategic threat levels | Threat panel | Castaway Scores: `threat_challenge`, `threat_strategic`, `threat_challenge_cat`, `threat_strategic_cat` | not read | in survivoR; site values differ for US seasons |
| Achievements | badges | Thresholds published in the Data Dictionary over survivoR fields, for example Packin' Heat for "holding an advantage or idol for 12 or more Tribal Councils" | | derive (most) |
| RESI awards | Game Stats, the ImpRESIve badge | `data/resi.json`. The Data Dictionary says the award is created by the hosts of the FUPASU podcast, who share the data with the site. | | site only |
| User threat votes, Your Top 10 | threat buttons, `/top10` | `/api/get_vote_counts.php`, `/api/save_vote.php` (`js/castaway.js:1419`, `:1448`); `r_score_top10_votes` in the career file | | site only |
| Win probability | Predictions tab | `data/predictions.json`, finished seasons only | the app computes its own (`app/predictions.py`) | site only |
| Portraits | hero, grids | Hosted by the site under `images/large-no-bg/{vs}/{castaway_id}.png` | the app uses fantasysurvivorgame.com | site only; the footer assigns Survivor content to CBS |
| Hometown coordinates | Castaway Map **[inferred]**, page not fetched | `hometown_lat_lon` in `castaways.json`; no such column in the xlsx | | site only |
| Season metadata | Overview | Season Summary | read | in survivoR |

## 4. Can it be used?

Each point quotes what the site or survivoR says. Where they say nothing, that is stated.

- **robots.txt** (fetched 2026-10-01, last modified 2026-08-24), complete text: `User-agent: *`
  and `Allow: /` **[fetched]**.
- **Terms of use, licence, privacy.** The site says nothing. The sitemap, which calls itself
  "Every page on Survivor Stats DB", lists no such page. The only statement about rights is the
  footer on every page: "A fan project. All rights and content for Survivor belong to CBS."
  (`js/app.js:301`) **[fetched]**.
- **What the site says about its data.** The About page: "The data is freely available from
  Github or for download as an xlsx. Please follow the link to Github for more options to access
  the data in R, Python, or JSON format." It also asks for support "to keep the site ad free"
  through a Buy Me a Coffee link **[fetched]**.
- **Who runs it.** Daniel Oehm, the survivoR author **[inferred]**. The site's text never names
  its author. The evidence: the footer's Bluesky link is the account the survivoR README names
  as the author's; the About page's Buy Me a Coffee link uses the same handle; and the README
  says "I have a new app on the Survivor Stats Db".
- **API.** None is documented. What exists:
  - static JSON files under `/data/` (named in Section 2). They are internal to the site, their
    shapes are undocumented, they carry misspelt field names that the site's own code works
    around (`n_votes_recieved`, and `chalenge_category` at `js/castaway.js:353-357`), and their
    URLs change with every `DATA_VERSION` bump **[fetched]**;
  - two PHP endpoints behind the threat vote buttons (`js/castaway.js:1419`, `:1448`). They are
    not an API for others.
- **Downloadable dataset.** Yes: survivoR.xlsx, from the Download page and the footer. survivoR's
  licence is MIT, "Copyright (c) 2021 Daniel Oehm"
  ([LICENSE.md](https://github.com/doehm/survivoR/blob/master/LICENSE.md)); `DESCRIPTION` says
  "License: MIT + file LICENSE". survivoR also publishes every sheet as JSON under
  [`dev/json/`](https://github.com/doehm/survivoR/tree/master/dev/json) **[read]**.
- **Contact.** Bluesky `@danoehm.bsky.social` (site footer and survivoR README); data problems go
  to [survivoR's GitHub issues](https://github.com/doehm/survivoR/issues) (`BugReports` in
  `DESCRIPTION`); an email address is given in the site footer and in the README and is not
  reproduced here **[fetched, read]**.

What this means for the app **[inferred]**:

- Linking to castaway pages is fine. It is what the site's own Share button is for.
- Showing survivoR data natively is covered by its MIT licence. The app already credits
  survivoR (`app/templates/rules.html:284-287`, `README.md:122`) **[read]**. Keep that credit, and
  keep the link to the site as "more on survivorstatsdb".
- Do not fetch the site's JSON at runtime or copy it. No licence covers those derived files, and
  they are not a stable interface. Every item worth showing, apart from the site-only ones, has
  a survivoR source.
- Do not use the portraits.
- For any site-only item (threat levels with user votes, RESI), ask the author first.

## 5. Recommendations, ranked by value and effort

Effort sizes follow `docs/design-pass-research.md`: S is one or two files and under about 150
changed lines, M is several files and 150 to 400 lines, L is above that. XS is a line or two
plus a test. Value is to fantasy players during a season.

| # | What | Planned feature | Value | Effort | Data |
|---|---|---|---|---|---|
| 1 | Fix the castaway link | every pick card | High: every link is broken | XS | none new |
| 2 | Episodes tab, and episode titles on the timeline | player sheet: Episodes; leaderboard timeline; episode page | High | S to M | Episodes (new); existing `episode_stats` |
| 3 | Votes tab | player sheet: Votes | High | M | Vote History (already read) |
| 4 | Advantages tab, and an idol-holder mark | player sheet: Advantages; leaderboard | High | M | Advantage Movement and Details (already read); Advantage Timeline (new, optional) |
| 5 | Challenges tab | player sheet: Challenges | Medium to high | M | Challenge Results (already read); Challenge Description (new) |
| 6 | Confessionals tab with expected count and edit verdict | player sheet: Confessionals | Medium | S | Confessionals `exp_count`, `index_count` (sheet already read) |
| 7 | Episode recap page | season or episode page | High once 2 to 5 exist | M | the above |
| 8 | Bio extras: three words, hobbies, pet peeves, exit quote | player sheet: Overview; draft board | Low to medium | S | Castaway Details, Castaways |
| 9 | Career tab | player sheet: Career | Low for Season 51, which has no returnees | S to M | Castaways and Castaway Scores by `castaway_id` |
| 10 | Score components and threat levels | player sheet: Overview | Low during a season: Castaway Scores has no US51 rows | S | Castaway Scores |
| Skip | Site predictions, RESI, user votes, portraits | | | | site only |

1. **Fix the link.** Change `app/models.py:195` to build
   `https://survivorstatsdb.com/castaway?id={version_season}{castaway_id}` and update
   `tests/test_standings_markup.py:223`. A second "Career" link
   (`castaway?id=career{castaway_id}`) is worth adding only for returnees.
2. **Episodes tab.** Points move episode by episode, and the app already keeps cumulative
   per-episode stats (`Survivor.episode_stats`) that `app/highlights.py` diffs into journey
   events. The tab is one row per episode: number and title, tribe, confessionals, challenge
   results, votes cast and received, advantages, and fantasy points earned that week. The only
   missing input is the title and air date, from the Episodes sheet, which `refresh_season()`
   does not read today (`app/data.py:198-208`). The same titles can label the leaderboard's
   timeline dots. Do not plan on `episode_summary`; it is empty for 15 of the 132 new-era
   episodes, including US50 and US51 episode 1.
3. **Votes tab.** Who a castaway voted for, and who voted for them, at each tribal is the
   clearest read on danger. Vote History is already loaded, but the app keeps only totals. Store
   one row per tribal per survivor (episode, target, boot, immunity, nullified, vote event), or
   build the rows on request from the cached sheet. The project notes on Vote History columns
   apply: `vote_id` is the target and `voted_out_id` repeats on every row of a tribal. Voted-with
   counts, and the alliance and nemesis rules from the Data Dictionary, come cheaply from the
   same rows.
4. **Advantages tab and idol mark.** Idols can score points under the Classic system
   (`idol_found_val` and `idol_play_val` in `app/scoring/classic.py`), they change who goes home,
   and `app/predictions.py` already models idol holders (`_get_season_idol_holdings`). The tab
   lists each advantage with its type, when and where it was found, its conditions and how it
   ended (played, for whom, whether it worked, votes nullified, or voted out holding it).
   The same data drives a small "holding an idol" mark on the leaderboard. In the current data,
   for example, Rob (US51) found a Hidden Immunity Idol on day 2 of episode 1 (Advantage
   Movement, Advantage Details) **[computed]**. Advantage Timeline's `holding` column records who
   held what at each stage of the game, but for US51 it has one row so far (Rob, stage 1,
   `holding` 0), so during a season the app's current method, finds minus plays in Advantage
   Movement (`_get_season_idol_holdings`), is the more current signal **[computed]**.
5. **Challenges tab.** Immunity decides who is safe each week, and immunity wins can also score
   points (`individual_immunity_val` and `tribal_immunity_val` in `app/scoring/classic.py`; both
   are 0 in the two built-in configs, so this depends on the season's settings). Show a record
   table by type, as the site does, and one row per challenge with the name (`recurring_name`),
   type, outcome type, won, sat out and order of finish.
6. **Confessionals tab.** The expected count and the index are survivoR columns, and the site's
   values match them exactly (Section 3.2). Show actual against expected per episode and the
   verdict at the 10% cut-offs. Many fans read the edit as a hint about who goes deep; label it
   as a hint, not a forecast.
7. **Episode recap page.** One page per episode: title and air date, who left and how the votes
   fell, challenge winners, idol finds and plays, confessional leaders, and each team's points
   for the week. The site has no episode pages, so this would be something the app offers that
   the site does not. It needs items 2 to 5.
8. **Bio extras.** Castaway Details already has `three_words`, `hobbies` and `pet_peeves` for the
   US51 cast. Exit quotes appear only after a boot.
9. **Career tab.** It matters in returnee seasons. Season 51 has none: `n_returnees` is 0 in the
   site's `seasons.json`, and none of the 21 castaway ids (`US0752` to `US0772`) appears in any
   other season of the Castaways sheet **[computed]**. Show past seasons with place and result,
   and link the site's career page.
10. **Scores and threat levels.** Castaway Scores has no US51 rows, and the site has values only
    for castaways already out. Low value while a season airs. If shown, use survivoR's values
    and say that they come from survivoR's model.

### 5.1 By planned feature

- **Castaway player sheet.** The tabs map onto the items above: Overview (8 and 10, plus the
  existing points breakdown and journey badges), Episodes (2), Votes (3), Challenges (5),
  Advantages (4), Confessionals (6), Career (9). The site's castaway page is a good reference
  for which numbers to show; its six sections line up with these tabs (Section 2.1).
- **Leaderboard.** Episode titles on the timeline dots (2), a mark on picks holding an idol (4),
  and votes received at the latest tribal as a danger hint (3). Keep the fixed external link (1).
- **Season or episode page.** The recap page (7). A season overview with tiles like the site's
  can be built from Season Summary and Castaways, but matters less to a league.
- **Mobile UX and collapsible teams.** Per-castaway data is small: tens of rows per tab. Render
  each tab on the server, or fetch it when the tab opens. Do not copy the site's approach of
  sending every castaway's data to the browser (`js/app.js:322-325`).
- **Live draft board.** survivoR cannot supply a cast before the premiere. The US51 cast first
  appeared in the xlsx in the 2026-09-26 "ADD US51" commit, after episode 1 had aired, and the
  2026-08-09 version had no US51 rows in Castaways, Season Summary, Castaway Details or Tribe
  Colours **[computed]**. The admin survivor route edits existing survivors and cannot add new
  ones (`app/routes.py:2277-2298`) **[read]**. A draft held before a premiere therefore needs a
  way to enter the cast by hand, or another source. Once survivoR has the season, the bio extras
  (8) fill in the draft cards.

## 6. Open questions

1. **Which link?** Per-season (recommended) or career?
2. **Does `careerUS0771` exist in the site's data?** The owner has seen the URL, but my download
   of `castawayCareer.json` stopped at `careerUS0676`. If career records only appear once a
   castaway's season is over, a career link for a Season 51 castaway would render "Castaway not
   found" **[not verified]**.
3. **Why do US scores and threat levels differ from the xlsx?** Probably user votes and RESI
   (Section 3.2) **[inferred]**. Ask the author before showing either.
4. **Season 51 tribes in the app.** The current xlsx has no US51 rows in Tribe Mapping, and
   Castaways `original_tribe` is empty for US51. Boot Mapping does have episode 1 tribes for all
   21 castaways (Savu, Toka, and one castaway on Exile Island with tribe "No Tribe")
   **[computed]**. `refresh_season()` takes tribe names, colours and the merge episode from Tribe
   Mapping (`app/data.py:305-312`, `:459-476`) **[read]**, so Season 51 picks may show no tribe
   until survivoR fills that sheet. I did not check the running app.
5. **Will survivoR publish future casts before their premieres?** It did not for Season 51. This
   decides how the draft board gets its cast.
6. **Does the owner want any site-only item** (threat levels with user votes, RESI)? If so, ask
   the author first.
7. The leaderboards, records and articles pages were not sampled.

## Appendix A. Sources

All fetched on 2026-10-01.

### survivorstatsdb.com, in request order

| # | Time (UTC) | Status | URL | Bytes | Last modified |
|---|---|---|---|---|---|
| 1 | 20:35:57 | 200 | `https://survivorstatsdb.com/robots.txt` | 22 | 2026-08-24 |
| 2 | 20:36:14 | 200 | `https://survivorstatsdb.com/sitemap` | 18,058 | 2026-09-23 |
| 3 | 20:36:45 | 200 | `https://survivorstatsdb.com/js/sitemap.js` | 3,748 | 2026-08-02 |
| 4 | 20:36:59 | 200 | `https://survivorstatsdb.com/js/app.js` | 31,281 | 2026-09-29 |
| 5 | 20:37:17 | 200 | `https://survivorstatsdb.com/castaway?id=careerUS0771` | 1,519 | 2026-09-13 |
| 6 | 20:37:30 | 200 | `https://survivorstatsdb.com/js/castaway.js` | 100,265 | 2026-09-13 |
| 7 | 20:38:12 | 200 | `https://survivorstatsdb.com/data/castaways.json?v=1.0.19` | 7,950,968 | 2026-09-29 |
| 8 | 20:39:37 | 500 | `https://survivorstatsdb.com/castaway/US51/US0771` | 713 | |
| 9 | 20:39:50 | 200 | `https://survivorstatsdb.com/season?vs=US51` | 7,559 | 2026-07-12 |
| 10 | 20:40:04 | 200 | `https://survivorstatsdb.com/js/season.js` | 157,195 | 2026-09-26 |
| 11 | 20:41:45 | 200 | `https://survivorstatsdb.com/about` | 5,213 | 2026-08-12 |
| 12 | 20:42:04 | 200 | `https://survivorstatsdb.com/download` | 3,744 | 2026-07-12 |
| 13 | 20:42:23 | 200 | `https://survivorstatsdb.com/data-dictionary` | 32,061 | 2026-09-05 |
| 14 | 20:42:46 | 200 | `https://survivorstatsdb.com/data/seasons.json?v=1.0.19` | 1,578,018 | 2026-09-29 |
| 15 | 20:43:06 | 200 | `https://survivorstatsdb.com/js/vote-history-season.js` | 9,628 | 2026-09-04 |
| 16 | 20:43:28 | 200 | `https://survivorstatsdb.com/data/predictions.json` | 49,610 | 2026-07-12 |
| 17 | 20:43:51 | 200 | `https://survivorstatsdb.com/challenge?v=US&name=A%20Bit%20Tipsy` | 1,565 | 2026-09-18 |
| 18 | 20:44:04 | 200 | `https://survivorstatsdb.com/js/challenge.js` | 17,411 | 2026-09-18 |
| 19 | 20:44:24 | 200 | `https://survivorstatsdb.com/js/challenge-data.js` | 6,943 | 2026-09-18 |
| 20 | 20:44:53 | 200, cut off | `https://survivorstatsdb.com/data/castawayCareer.json?v=1.0.19` | 5,764,000 of 6,238,816 | 2026-09-29 |

Not fetched: the home page, leaderboards, records, articles, fun pages, `castawaySeasonTbl.json`,
`confessionals.json`, `resi.json`, `vote-history.json`, `voted-with.json`, the chart files,
`challenge_summary.json` and `challenge_winners.json`.

### survivoR

- Repository: [doehm/survivoR](https://github.com/doehm/survivoR), public, default branch
  `master`, last pushed 2026-09-26 (GitHub API).
- At `master`: [README.md](https://github.com/doehm/survivoR/blob/master/README.md),
  [NEWS.md](https://github.com/doehm/survivoR/blob/master/NEWS.md),
  [LICENSE.md](https://github.com/doehm/survivoR/blob/master/LICENSE.md),
  [DESCRIPTION](https://github.com/doehm/survivoR/blob/master/DESCRIPTION),
  [man/castaway_scores.Rd](https://github.com/doehm/survivoR/blob/master/man/castaway_scores.Rd),
  [man/advantage_timeline.Rd](https://github.com/doehm/survivoR/blob/master/man/advantage_timeline.Rd).
- `dev/xlsx/survivoR.xlsx` at `master` (commit `7336413`, 2026-09-26) for every check in this
  document, and at commit `a6329b6` (2026-08-09) for the pre-season check in Section 5.1.
- The commit history of `dev/xlsx/survivoR.xlsx`, from the GitHub API.
- The repo's local copy of `survivoR.xlsx` predates Season 51 (US seasons to 50) and was not
  used for any check.

### This repo

`app/models.py`, `app/data.py`, `app/routes.py`, `app/highlights.py`, `app/predictions.py`,
`app/scoring/classic.py`, `app/templates/_pick_card.html`, `app/templates/rules.html`,
`tests/test_standings_markup.py`, `README.md`.

## Appendix B. How the checks were made

- **Id rules.** For every record in `castaways.json`, `id == vs + castaway_id` (1,464 of 1,464).
  For every complete record in the partial `castawayCareer.json`,
  `id == "career" + castaway_id` (1,122 of 1,122). For every season in `seasons.json`, `vs` is
  the version plus a two-digit season (77 of 77).
- **Partial career file.** The download ended mid-file. Complete objects were read in order with
  a streaming JSON decoder until the first incomplete one. Records are sorted by id; the last
  complete one is `careerUS0675`.
- **Agreement with the xlsx.** For each season, the xlsx column was summed per `castaway_id` and
  compared with the field on the site record whose id is `version_season + castaway_id`.
  Tolerances: 0.001 for scores, 0.05 for expected confessionals, one second for confessional
  time, one minute for game time. Votes received group Vote History rows on `vote_id` (the
  target), as the project notes require.
- **Update lag.** Commit times of `dev/xlsx/survivoR.xlsx` against Episodes `episode_date`. For
  Season 50: episode 8 aired 2026-04-15, committed 04-19; 9 aired 04-22, committed 04-23; 10
  aired 04-29, committed 05-01; 11 aired 05-06, committed 05-08; 12 aired 05-13, committed
  05-15; 13 aired 05-20, "ADD complete US50" committed 05-22.
- **Pre-season cast.** Counted US51 rows in Castaways, Season Summary and Tribe Colours, and
  castaway ids `US0752` to `US0772` in Castaway Details, in the 2026-08-09 xlsx: zero in each.
