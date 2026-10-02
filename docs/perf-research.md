# Front-end and delivery performance: research and plan

Goal: find out why the leaderboard feels laggy even though the server renders it
in ~0.15 s, and rank the fixes by impact. Research only; no application code was
changed. Sources are official docs (Cloudflare, Flask, MDN, web.dev, project
docs) unless noted. Facts measured in this repo on 2026-10-01 are marked
**(measured)**.

> **Status, updated 2026-10-01.** The two highest-priority recommendations below
> have already shipped, within hours of this document being written. Read the
> table in "New tickets worth filing" as a record of what was proposed, not as a
> to-do list.
>
> - **Item 1, self-host resized headshots** (called the biggest win here):
>   shipped in #119, with lazy loading in #109. `app/headshots.py` exists and
>   headshots are served from the data volume.
> - **Item 2, cache headers and versioned static URLs**: shipped in #137.
> - **Items 3 to 8** are still open and unfiled: Cloudflare edge caching behind
>   Access, deferring and lazy-initialising Chart.js, Speculation Rules
>   prefetch, deferring the hidden journey and breakdown DOM, a Lighthouse
>   baseline, and evaluating htmx or Turbo. Item 7, the baseline, is the one
>   this document itself says to do first.
>
> The measurements are unchanged and still accurate as of the date above. What
> has moved is the state of the codebase they describe.

---

## Problem recap

- The backend is not the bottleneck. The page is slow because of what the
  browser has to fetch and build after the HTML arrives.
- **(measured)** One headshot (`.../images/50/biopics/coachBIO.jpg`) is a
  440x440 progressive JPEG of **86 KB**, but the app displays it at **80 px**
  (`.lb-pick-img`, 56 px on mobile) or **32 px** (`.stat-img`) in
  `app/static/style.css`. At that size a 160x160 WebP is roughly an order of
  magnitude smaller. With ~147 `<img>` tags that is on the order of 12 MB of
  source pixels if every image loads (estimate: 147 x 86 KB; not a measured page
  total).
- The images are hot-linked to `fantasysurvivorgame.com`, a third-party origin.
  Every visitor opens a new connection to it, and we have no control over its
  cache headers (currently `Cache-Control: max-age=604800, public`, **measured**),
  uptime, or willingness to be hot-linked.
- Flask's static route sends no `Cache-Control` by default, so the browser
  revalidates `style.css` on every navigation (see item 2).
- **(measured)** The leaderboard HTML is ~273 KB because each pick renders its
  point breakdown, stats detail, bio line and full journey timeline into the DOM
  even when the toggle that shows them is off (`leaderboard.html`, CSS classes
  `show-stats`, `show-bio`, `show-journey`).
- Three Chart.js charts are constructed at load time (`leaderboard.html` lines
  ~310, ~407, ~441) from a render-blocking `<script src=...jsdelivr...>` placed
  mid-body. **(measured)** Chart.js from jsDelivr is ~72 KB brotli.
- Every navigation is a full page load, which re-parses ~273 KB of HTML and
  re-runs all inline scripts.

---

## Recommendations, highest impact first

### 1. Self-host resized headshots (the image-caching work — the biggest win)

Do both parts together. Mirroring the 440 px originals alone fixes the third-party
dependency but not the byte count.

**What to build**

- On data refresh (`generate_season_images()` in `app/data.py`; note `seed.py`
  has a duplicate `generate_image_urls()`, which breaks the repo's own
  single-source-of-truth rule, so consolidate while there), download each image,
  resize to **160x160** (2x the largest display size), encode as WebP (JPEG
  fallback is unnecessary for current browsers), and write it to local storage.
  Pillow is not in `requirements.txt` today; it would be a new pinned dependency
  (run `snyk_sca_scan`).
- Store a **local relative path or filename** in `Survivor.image_url` instead of
  the remote URL, so templates and `routes.py` need no change beyond a
  `url_for` wrapper.
- **Fallback:** the template already renders a letter placeholder
  (`lb-pick-placeholder`) when `survivor_image` is empty. Keep that behavior for
  failed downloads: leave `image_url` null rather than storing a broken path.
  Also keep the existing `requests` timeout and log failures instead of
  swallowing them (the current `except Exception: pass` hides them, which
  contradicts the repo's engineering principles).

**Where to store them: the `/app/data` volume, not `app/static/`**

- `app/static/` is baked into the Docker image. Images downloaded at runtime
  there would be lost on every rebuild, and the container runs as `appuser`
  with the rest of `/app` read-only (see `CLAUDE.md`, Docker permissions).
- `/app/data` is the persistent volume already mounted in `docker-compose.yml`
  and already owned by UID 1000. Put images in `/app/data/headshots/`.
- Serve them with a small route using `send_from_directory`. Flask's
  `send_file` takes `max_age`, `conditional` and `etag` arguments, and
  `SEND_FILE_MAX_AGE_DEFAULT` defaults to `None`, which makes browsers use
  conditional requests instead of a timed cache
  ([Flask API](https://flask.palletsprojects.com/en/stable/api/),
  [Flask config](https://flask.palletsprojects.com/en/stable/config/)).
  That default is wrong for fingerprinted files, so pass `max_age` explicitly.

**Filename and caching strategy**

- Name files by content hash or include a version, e.g.
  `<season>-<slug>-<hash8>.webp`, and store that name in the DB. A changed image
  gets a new URL, so the old one can be cached forever.
- Send `Cache-Control: public, max-age=31536000, immutable` for those URLs. MDN
  documents this exact pattern: "include version/hashes in their URLs ... while
  never modifying the resources", then use a long `max-age` and add `immutable`
  to avoid revalidation
  ([MDN Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control)).
- Add `width`/`height` attributes to the `<img>` tags. MDN: explicit dimensions
  let the browser reserve space and prevent layout shift, and are "especially
  important" for lazy-loaded images
  ([MDN img](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/img)).
  The CSS already fixes the size, so this is a small safety gain, not the main
  win. Keep `loading="lazy" decoding="async"` (already added).
- Keep downloading and resizing out of the request path. Do it in the refresh
  job or `seed.py`, and skip files that already exist.

**Licensing note:** the headshots belong to the Survivor fantasy site's
operators, not to us. Mirroring them is a bigger step than hot-linking. Worth a
conscious decision, even for a private league site (the site is behind Access,
so exposure is small).

### 2. Make the browser cache work: long-lived caching for our own static files

This is the cheapest change and does not depend on Cloudflare at all.

- Flask's default is conditional requests with no timed cache
  ([Flask config](https://flask.palletsprojects.com/en/stable/config/)). With
  Access and a tunnel in the path, that means each repeat navigation sends a
  revalidation request per asset (CSS, font, JSON) through Access and
  `cloudflared` to gunicorn, just to get a 304.
- Version the URLs (`style.css?v=<content-hash>` via a small `url_for` helper
  or a build-time hash), then set a long `max-age` plus `immutable` as in item 1.
  MDN's cache-busting guidance applies directly.
- Pico CSS and Chart.js already come from jsDelivr with `max-age=604800`
  **(measured for Chart.js)**, so they cache in the browser after the first
  visit. Self-hosting them is optional (see items 4 and 2b).
- The `survivant.ttf` font (50 KB) is local. Add `font-display: swap` in its
  `@font-face` if not already set, and preload it only if it is used above the
  fold.

### 2b. Can Cloudflare cache static assets for an Access-gated app?

Short answer: **the browser cache (item 2) gets you most of the benefit; CDN
caching of Access-gated paths is possible but I could not confirm it from the
docs, so test before relying on it.**

What the docs establish:

- Published tunnel hostnames get Cloudflare's CDN features: "Cloudflare applies
  CDN caching, WAF, and DDoS protection before forwarding the request to your
  origin"
  ([Tunnel routing](https://developers.cloudflare.com/tunnel/concepts/routing/)).
- By default Cloudflare caches by **file extension**, not MIME type, and does not
  cache HTML or JSON. The default list includes `JPG`, `JPEG`, `PNG`, `WEBP`,
  `CSS`, `JS`, `TTF`, `WOFF2`, `SVG`
  ([default cache behavior](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)).
  So `.webp` headshots, `.css`, `.js` and `.ttf` are already cache-eligible.
  `scoring_analysis.json` is JSON and is not, unless a Cache Rule makes it so.
- Cloudflare skips the cache when the response has `Cache-Control: private`,
  `no-store`, `no-cache` or `max-age=0`, or any `Set-Cookie`; it caches when
  `Cache-Control` is `public` with `max-age` > 0 (same page). **Flask sessions
  and Flask-WTF CSRF may add `Set-Cookie`.** Make sure the image and static
  routes do not set cookies (the `send_from_directory` route normally does not
  touch the session), or the CDN will miss every time
  ([troubleshooting: MISS on every request](https://developers.cloudflare.com/speed/troubleshooting/slow-website/)).
- You can force eligibility with a Cache Rule (Eligible for cache = Yes, set Edge
  TTL). Cloudflare warns that "cache everything" caches all HTML regardless of
  dynamic content, and says to add conditions to exclude dynamic paths
  ([cache everything example](https://developers.cloudflare.com/cache/how-to/cache-rules/examples/cache-everything/)).
  **Never apply it to `/` or any HTML route here:** the leaderboard is per-viewer
  behind Access and carries CSRF tokens. Scope any rule to `/static/*` and
  `/media/*` only.
- Edge TTL minimum on the Free plan is 2 hours
  ([Edge and Browser Cache TTL](https://developers.cloudflare.com/cache/how-to/edge-browser-cache-ttl/)).

What the docs do **not** say (I searched the Access, Cache and Tunnel pages and
found nothing): whether a response to an Access-authorized request is stored in
and served from the edge cache. My reading is that Access evaluates each request
first and the CDN layer only sees requests that passed, so caching static files
should be both safe and possible. That is an inference. **Verify it:** request a
static URL twice with a valid session or service token and inspect
`cf-cache-status` (`HIT` vs `MISS`/`DYNAMIC`)
([cache status values](https://developers.cloudflare.com/cache/concepts/cache-responses/)).

**Bypass policy for static paths (optional, only if the test above shows
`DYNAMIC`/`MISS`, or if you want zero Access overhead on assets):**

- Access has a **Bypass** action that "disables Access enforcement for specific
  traffic", used for "applications that require specific endpoints to be
  public". Cost: "requests are not logged" and bypass cannot use identity
  selectors like email
  ([Access policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)).
- Cloudflare's own alternative when you want enforcement plus logs without a
  login is **Service Auth** with a service token (`CF-Access-Client-Id` /
  `CF-Access-Client-Secret` headers), but browsers do not send those headers for
  `<img>` loads, so a service token does not help end users
  ([Service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)).
  Use service tokens for measurement tooling (item 6), not for assets.
- Path scoping: "When multiple rules are set for a common root path, the more
  specific rule takes precedence"
  ([Application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/)).
  So a separate Access application for `www.<domain>/static/*` with a Bypass
  policy overrides the main application for just that path.
- **Risk judgment:** a public `/static/*` exposes CSS, fonts, Chart.js-adjacent
  files and `scoring_analysis.json` to anyone who guesses the URL. That is fine
  for CSS and fonts. Headshots are already public on the source site. Do not put
  anything per-user or per-season-private under the bypassed prefix. Prefer a
  dedicated prefix (`/static/` for code assets, `/media/headshots/` for images)
  over bypassing broad paths.
- **Recommendation:** do item 2 first, run the `cf-cache-status` test, and add
  the Bypass application only if measurement shows asset requests are still slow
  through Access. Serving assets from a separate cookieless, un-gated hostname is
  the cleaner long-term pattern, but it is a bigger change than a path bypass
  and is not needed at ~50 users.

### 3. Shrink the ~273 KB leaderboard HTML and DOM

The toggles (points, stats, bio, journey) hide content with CSS, so everything is
always in the DOM. Options, cheapest first:

1. **Confirm compression is on.** Cloudflare compresses `text/html` at the edge
   by default; the algorithm depends on plan (Zstandard on Free, Brotli on
   Pro/Business) and the `Accept-Encoding` the browser sends
   ([Content compression](https://developers.cloudflare.com/speed/optimization/content/compression/)).
   Check with `curl -sI -H 'Accept-Encoding: br' ...` through Access, or in
   DevTools. The Flask app itself has no compression middleware, and does not
   need one if the edge handles it. Compression shrinks transfer, not DOM size or
   parse cost.
2. **Do not render hidden-by-default detail until asked.** The journey timelines,
   point breakdowns, stats detail and bio lines are the repeated per-pick bulk.
   Two lighter approaches for this Jinja app:
   - Emit the detail as one JSON blob (or `<template>` elements) and build the DOM
     on first toggle. Smallest change to the Python side.
   - Add a fragment route (e.g. `/season/<n>/picks/<id>/journey`) and fetch it on
     demand with htmx (`hx-get`, `hx-trigger="click once"`). The server already
     computes `journey_events` in the route handler, so this moves the cost, not
     removes it.
3. **Cut template boilerplate.** The draft and special pick blocks are near
   duplicates, and per-pick inline `style="border: ... {{ tribe_color|contrast }}"`
   attributes repeat on both the card and the `<img>`. Use a CSS variable
   (`style="--tribe: #abc"`) set once per card and reference it from CSS. A Jinja
   macro for the pick card removes the duplication in source (not in output size).
4. **Treat DOM size as a measurement, not a guess.** Lighthouse flags excessive
   DOM size and reports the node count, which tells you whether this is worth the
   work after items 1 and 2 land.

I would defer item 3.2 until images and caching are fixed. A 273 KB document that
compresses well and parses in tens of milliseconds is rarely the dominant cost
next to 12 MB of images.

### 4. Chart.js: load it later and build charts only when seen

- Today: a parser-blocking `<script src="...chart.js@4">` in the middle of the
  page, then three immediate `new Chart(...)` calls.
- Add `defer` to the Chart.js tag. `defer` scripts "execute after the document
  has been parsed" and "in the order in which they appear", and inline scripts
  ignore `defer`
  ([MDN script](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/script)).
  Because the existing inline scripts call `new Chart` directly, they must be
  wrapped in a `DOMContentLoaded` handler (or converted to a `defer`red external
  file) so they run after Chart.js loads.
- **Lazy init:** wrap each chart's setup in an `IntersectionObserver` callback on
  its canvas. The base template already uses `IntersectionObserver` for the TOC,
  so the pattern is in the codebase. Charts below the fold then cost nothing
  until scrolled to. The progression chart is likely above or near the fold;
  measure before lazifying that one.
- **Pin the version.** `chart.js@4` is a floating range on a CDN, and the repo
  pins every dependency exactly (Snyk guidance in `CLAUDE.md`). Pin
  `chart.js@4.x.y` with an SRI `integrity` hash, or self-host the UMD file under
  `/static/` so it rides the same long-lived cache as everything else and
  removes a third-party round trip (connection setup to jsDelivr).
- **Bundling/tree-shaking is not worth it here.** Chart.js supports tree-shaking
  only through a bundler with explicit `Chart.register(...)`
  ([integration docs](https://www.chartjs.org/docs/latest/getting-started/integration.html)).
  This project has no JS build step, and the CDN file is ~72 KB brotli
  **(measured)**. Adding a bundler for a few KB saved is poor value.

### 5. Reduce full-page-reload cost on navigation

Ranked by effort and fit for a small server-rendered Flask app:

1. **Speculation Rules prefetch (near-zero code, Chromium only).** One
   `<script type="speculationrules">` with document rules (`where` /
   `href_matches`) prefetches likely next pages. Prefetch downloads the response
   body only (not subresources); prerender renders the page in a hidden tab so
   navigation is near-instant. Support is **Chromium only**, so it is a
   progressive enhancement, not a fix
   ([Speculation Rules API](https://developer.mozilla.org/en-US/docs/Web/API/Speculation_Rules_API)).
   Same-origin prefetch carries the user's cookies, so it works behind Access.
   MDN's caveat applies: do not prefetch unsafe URLs (logout, state-changing
   links); the page documents `Sec-Purpose: prefetch` for server-side handling.
   The admin routes must be excluded.
2. **htmx `hx-boost`.** One attribute on `<body>` turns links and forms into AJAX
   requests that swap the response into `<body>`, and "degrades gracefully if
   javascript is not enabled"
   ([htmx docs](https://htmx.org/docs/#boosting)). It is dependency-free and
   needs no build step. Tradeoff for this app: the leaderboard page is full of
   inline `<script>` blocks that build charts and attach toggle handlers, which
   must re-run correctly after each swap (use `htmx:afterSettle` / `htmx:load`
   hooks). It also does not reduce the 273 KB of HTML per navigation, only the
   asset and head re-parse. Useful mainly if you also move to fragment routes
   (item 3.2).
3. **Turbo Drive (Hotwire).** Replaces `<body>`, merges `<head>`, keeps already
   loaded JS/CSS, and prefetches links on hover by default since Turbo 8
   (documented speedup "500-800 ms per click")
   ([Turbo Drive](https://turbo.hotwired.dev/handbook/drive)). Same inline-script
   caveat, and the docs warn that scripts "disconnected from the document" do not
   unload their evaluated code, which matters for the chart setup. It is a
   heavier commitment than htmx for a few navigations.

**Recommendation:** fix images and caching first, then add Speculation Rules
(cheap, no regressions in other browsers). Adopt htmx or Turbo only if
navigation is still the complaint, and only after the inline scripts are moved
to external deferred files that can be re-initialized safely.

### 6. Measure real client-side performance behind Cloudflare Access

Targets (75th percentile of real loads, per web.dev): **LCP <= 2.5 s,
INP <= 200 ms, CLS <= 0.1**
([Web Vitals](https://web.dev/articles/vitals)).

Lighthouse cannot complete an email OTP login, so use one of these:

- **Chrome DevTools Lighthouse panel on your own logged-in browser.** Lighthouse
  docs: the panel "will never clear your cookies, so you can log in to the target
  site and then run Lighthouse"
  ([Lighthouse authenticated pages](https://github.com/GoogleChrome/lighthouse/blob/main/docs/authenticated-pages.md)).
  Simplest and good enough for this site. Use the real-browser result for
  user-perceived timing.
- **Lighthouse CLI with a header.** `--extra-headers` can pass the Access service
  token headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`), which Access
  accepts when a **Service Auth** policy includes the token
  ([Service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)).
  Caveat from the Lighthouse docs: custom headers apply to requests broadly, and
  Puppeteer scripting is the flexible alternative. This needs a second Access
  policy and a stored secret (use `keychain-secret`; never put the secret in the
  repo). Worth it only if you want a scheduled CI run.
- **Local unauthenticated instance.** Run `python run.py` locally with
  `DEV_LOGIN=1` and Lighthouse against `localhost:5050`. This isolates app and
  front-end weight from Access and tunnel overhead, but misses the CDN/tunnel
  latency and does not exercise the third-party image host the same way.
  Use it to compare before/after for items 1, 3 and 4 because it is stable and
  needs no secrets.
- **Real-user monitoring** with the `web-vitals` library: measures LCP, INP, CLS,
  FCP and TTFB in ~3 KB brotli, via `onLCP()`, `onINP()`, `onCLS()` callbacks
  ([web-vitals](https://github.com/GoogleChrome/web-vitals)). With ~50 users this
  is a small, honest sample. It would need a tiny same-origin `POST /metrics`
  endpoint (CSRF-exempt, rate-limited, logged only). Lab numbers are enough to
  drive these fixes; RUM is optional.
- Also useful, no setup: DevTools Network tab with "Disable cache" off to see the
  repeat-visit waterfall, and `curl -sI` on one asset to read `cf-cache-status`
  and `content-encoding`.

Take a baseline (LCP, total transfer, request count, DOM node count) **before**
the image change so the improvement is measurable.

---

## New tickets worth filing

Tickets were not created. Check the internal tracker and GitHub for duplicates
before filing (`tracker-dedupe`).

| # | Proposed ticket | Tie-in | Priority |
|---|----------------|--------|----------|
| 1 | Self-host headshots: download on refresh, resize to 160 px WebP, store in `/app/data/headshots/`, serve with immutable caching, keep letter placeholder fallback, consolidate the duplicate `generate_image_urls()` in `seed.py` with `generate_season_images()` | existing image-caching ticket (extend scope to include resizing and fingerprinted filenames) | High |
| 2 | Cache headers and versioned URLs for `/static/*` (`max-age` + `immutable`, content-hash query), ensure no `Set-Cookie` on static routes | New | High |
| 3 | Verify Cloudflare edge caching for static paths behind Access (`cf-cache-status` test); add a path-scoped Bypass application only if it shows `DYNAMIC`/`MISS` | New (investigation, then optional config; update `docs/cloudflare-private-access.md`) | Medium |
| 4 | Chart.js: pin version with SRI or self-host, `defer`, lazy-init charts with `IntersectionObserver` | New | Medium |
| 5 | Add Speculation Rules prefetch (exclude admin/logout routes) | New | Low |
| 6 | Defer rendering of hidden journey/breakdown/bio detail (on-demand fragment or `<template>`), and collapse inline per-pick styles into CSS variables | New, gated on item 1 and 2 results and DOM-size measurement | Low to Medium |
| 7 | Performance baseline and budget: Lighthouse (DevTools or local `DEV_LOGIN`) before/after, optional `web-vitals` RUM | New | Medium (do first, to measure the rest) |
| 8 | Evaluate htmx `hx-boost` or Turbo Drive | New, only if navigation is still slow after the above | Low |

## Open questions to settle before building

- Is `scoring_analysis.json` (74 KB, served from `/static/`) meant to be public
  if `/static/*` is ever bypassed? It is aggregate scoring analysis, but confirm.
- Does the Cloudflare plan include anything beyond Free? Polish and Images
  transformations are paid features; local resizing at refresh time works on
  Free and is what this plan assumes.
- Is mirroring the fantasysurvivorgame.com headshots acceptable to the site's
  owner? (See licensing note in item 1.)
