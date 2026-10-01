# Private access via Cloudflare (email OTP) — research + plan

Goal: make the app reachable to ~50 league members **privately**, gated by a
**one-time code emailed to an allowlist**, so there's no Tailscale membership to
manage. Keep the Flask app where it is (on Proxmox). Prefer **$0** and a free
`*.workers.dev` URL.

Status when written: the app is **already live on the Tailnet** (season 51 is
running), so this is a calm swap of the *access method*, not a launch blocker.
That matters: there's a working fallback, so we don't have to take risks.

---

## The one hard constraint that shapes everything

**Cloudflare Access (email OTP) needs a hostname, and a normal Cloudflare Tunnel
public hostname must live in a domain you own on Cloudflare.** Publishing
`app.example.com` through a tunnel creates a DNS record in *your zone* (the
internal target is `<uuid>.cfargotunnel.com`); you cannot hang a public app
hostname off `*.workers.dev` this way.
([Tunnel routing](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/),
[tunnel prereqs: "a zone — a domain you control on Cloudflare DNS"](https://developers.cloudflare.com/sandbox/sdk/api/tunnels/))

So there are exactly two shapes:

- **A free `workers.dev` URL** — only a *Worker* can live there, so the front
  door must be a Worker, which reaches the Proxmox app over a tunnel via
  **Workers VPC**.
- **A `cloudflared` public hostname** — requires a **domain** (~$10/yr), but
  then it's a near-drop-in replacement for the current Tailscale sidecar with
  **zero app code**.

Both gate with the same thing: **Cloudflare Access One-Time PIN** — add emails
to an Allow policy, Cloudflare emails a 6-digit code, browser-only, no client.
([One-Time PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/))
Note OTP is **no longer added automatically** to new Zero Trust orgs (the
default is now "sign in with a Cloudflare account"); you must **add the One-Time
PIN identity provider explicitly**, which is what we want since league members
won't have Cloudflare accounts.
([Cloudflare as IdP](https://developers.cloudflare.com/changelog/post/2026-05-19-cloudflare-as-identity-provider/))

---

## Option A — Free: `workers.dev` + proxy Worker + Workers VPC

```
Member ──▶ survivor.bromberg-benji.workers.dev
             │  (Cloudflare Access OTP enforced on the workers.dev route)
             ▼
         Proxy Worker  ──(Workers VPC binding: env.APP.fetch)──▶
             │
         cloudflared (private tunnel, no public hostname) ──▶ Flask :5050 on Proxmox
```

- **Access on `workers.dev` is one click** (Workers & Pages → your Worker →
  Settings → Domains & Routes → *Enable Cloudflare Access*), then set the policy
  to OTP + your email allowlist.
  ([One-click Access for Workers](https://developers.cloudflare.com/changelog/post/2025-10-03-one-click-access-for-workers/))
- **Workers VPC** lets the Worker `fetch()` the Proxmox app over the tunnel.
  Create a tunnel in the Workers VPC dashboard, then a VPC Service:
  ```sh
  npx wrangler vpc service create survivor-app \
    --type http --tunnel-id <TUNNEL_ID> --hostname localhost --http-port 5050
  ```
  and bind it in `wrangler.jsonc`:
  ```jsonc
  { "vpc_services": [ { "binding": "APP", "service_id": "<SERVICE_ID>", "remote": true } ] }
  ```
  Beta, **free on all Workers plans**, needs the *Connectivity Directory Admin*
  role. ([Workers VPC](https://developers.cloudflare.com/workers-vpc/),
  [get-started](https://developers.cloudflare.com/workers-vpc/get-started/),
  [VPC Services](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/))
- **The Worker** is ~30 lines: forward method/path/headers/body to
  `env.APP.fetch(...)`, stream the response back, and **validate the
  `Cf-Access-Jwt-Assertion` JWT** (with the `jose` package) so nobody who finds
  the `workers.dev` URL can bypass Access. Validation is called out as required
  in the one-click-Access docs.
- **Proxmox side**: swap the Tailscale sidecar for a `cloudflared` one:
  ```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment: [ "TUNNEL_TOKEN=${CF_TUNNEL_TOKEN}" ]
    restart: unless-stopped
  ```
  (remotely-managed, token-based —
  [run params](https://developers.cloudflare.com/tunnel/reference/run-parameters/)).
  The app keeps sharing the connector's netns so the VPC Service host is
  `localhost:5050`, exactly as it reaches Tailscale today.

**Pros:** $0, the exact URL you wanted, app stays on Proxmox.
**Cons / where it breaks:**
- **Workers VPC is beta** ("APIs may change before GA"). Fine for a hobby app
  with a Tailnet fallback; not what you'd pick for something load-bearing.
- A proxy Worker is **real moving parts**: it must faithfully relay a
  server-rendered, cookie-based Flask app (Set-Cookie, redirects, static
  assets, Chart.js, fonts). Tractable, but it's code to own.
- Must get the **JWT validation** right or the origin is bypassable.

---

## Option B — Robust: ~$10/yr domain + `cloudflared` public hostname + Access

```
Member ──▶ survivor.yourdomain.com
             │  (Cloudflare Access OTP enforced on the hostname)
             ▼
         cloudflared public-hostname ingress ──▶ Flask :5050 on Proxmox
```

- Register a domain (Cloudflare Registrar sells at wholesale, ~$10/yr for
  `.com`; cheaper TLDs exist — **verify current price at purchase**), let
  Cloudflare manage its DNS.
- Replace the Tailscale sidecar with a `cloudflared` sidecar whose tunnel has a
  **public hostname** route `survivor.yourdomain.com → http://localhost:5050`.
  Cloudflare auto-creates the DNS record.
- Create an **Access self-hosted application** on that hostname, policy = OTP +
  email allowlist. Done.

**Pros:** **zero app code**, **no beta**, a true drop-in for the current
Tailscale pattern, durable. Publishing a tunnel app needs **no paid Access
plan**; Access seats are only for the policy layer, which is free at this scale.
([Tunnel routing note](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/))
**Cons:** costs ~$10/yr and means owning a domain.

---

## Admin login — recommendation: fold it into Access

Today admin is GitHub OAuth (`ADMIN_GITHUB_USERNAME` + a client id/secret).
Behind Access, every request already carries the member's **verified email** in
the `Cf-Access-Jwt-Assertion` JWT. Recommendation: **mark admin by email from
that JWT and retire the GitHub OAuth app.**

- Removes an OAuth app + two secrets, and (critically for Option A) removes the
  **OAuth redirect dance** — the single most annoying thing to proxy through a
  Worker. This is why "fold admin into Access" and "free path" pair well.
- Small change in `auth.py`: trust the Access-forwarded identity header (set by
  the Worker in Option A, or `Cf-Access-Authenticated-User-Email` from Access in
  Option B) and treat a configured email as admin. Keep the existing
  `DEV_LOGIN=1` path for local dev untouched.
- Only keep GitHub OAuth if you specifically want admin identity independent of
  the email allowlist.

---

## Recommendation

**If you'll spend ~$10/yr: Option B.** It's the better engineering choice by a
wide margin for this app — no code, no beta, a direct swap of the sidecar you
already run, and nothing new to maintain. For a thing your league relies on
each week, "boring and durable" wins.

**If the $0 / no-domain constraint is firm: Option A is genuinely viable** —
especially with admin folded into Access (no OAuth to proxy). Accept that you're
taking on a small Worker and a beta binding, with the live Tailnet as fallback
while you shake it out.

Either way: add the **OTP identity provider**, set the Allow policy to your
league's emails, and keep the Tailscale sidecar running until the new door is
verified, then remove it.

## Open items to verify before building
- Current **Zero Trust free-tier user cap** (historically 50) — confirm in the
  dashboard; the league must fit under it.
- Cloudflare **Registrar price** for the TLD you'd pick (Option B).
- Whether any league member's email provider filters the OTP mail; if so,
  allowlist `noreply@notify.cloudflare.com`.
