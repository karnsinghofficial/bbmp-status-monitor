# BBMP / GBA Service Status Monitor

An independent, free, always-on status dashboard for Bengaluru's civic
e-governance services (BBMP / Greater Bengaluru Authority) — Property Tax,
e-Khata, Trade License, Building Permission, and more. It checks each
service roughly every 5 minutes, shows live status on a public dashboard,
tracks uptime/incident history, and sends you a Telegram (and/or email)
message the moment a service goes **DOWN** and — most importantly — the
moment it comes **back ONLINE**.

**Cost: ₹0/month.** Runs entirely on GitHub's free tier (Actions + Pages).
No server, no credit card, no computer of yours needs to stay on.

---

## 1. Architecture

```
┌─────────────────────┐      every ~5 min       ┌──────────────────────┐
│  GitHub Actions      │ ───────────────────────▶│  BBMP / GBA websites │
│  (free cron runner)  │   safe, read-only GET    │  (Property Tax,      │
│                       │◀───────────────────────  │   e-Khata, etc.)     │
│  scripts/monitor.py  │                          └──────────────────────┘
└──────────┬───────────┘
           │ writes + git-commits
           ▼
   docs/data/*.json  ───────────────▶  GitHub Pages  ───────────────▶  You,
   (status, history,                  (free static                    on your
    incidents, state)                  hosting)                       phone/PC
           │
           ▼
   Telegram Bot API / SMTP email  ───────────────▶  You get notified
   (only on state changes)                          instantly
```

* **Monitoring & scheduling:** GitHub Actions, triggered by a cron schedule
  (`*/5 * * * *`). A Python script performs one safe GET request per
  service, applies failure-confirmation logic, updates JSON data files, and
  commits them back to the repo.
* **Storage:** plain JSON / JSONL files inside the repo itself
  (`docs/data/`). No database needed — Git *is* the database, and it gives
  you full history for free.
* **Hosting / dashboard:** GitHub Pages, serving a static `docs/` folder
  (HTML + CSS + vanilla JS, no build step, no framework). It reads
  `docs/data/status.json` directly.
* **Notifications:** Telegram Bot API (primary) and/or SMTP email, both
  optional, both configured purely via **GitHub Secrets** — never exposed
  to the frontend or committed to the repo.

Nothing here requires your own computer, a paid server, or a credit card.

---

## 2. Services monitored (verified current URLs)

BBMP was formally dissolved on **2 September 2025** and its functions
transferred to the new **Greater Bengaluru Authority (GBA)**. Many old
tutorials/articles still reference stale or redirected BBMP URLs — the URLs
below were verified directly against the live sites during this project's
research phase.

| # | Service | URL | Check type |
|---|---------|-----|-----------|
| 1 | BBMP / GBA Main Website | `https://site.bbmp.gov.in/indexenglish.html` | HTTP reachability |
| 2 | Property Tax (SAS) | `https://bbmptax.karnataka.gov.in/Default.aspx` | HTTP + keyword |
| 3 | e-Aasthi / e-Khata | `https://bbmpeaasthi.karnataka.gov.in/` | HTTP reachability |
| 4 | Trade License (New) | `https://trade.bbmpgov.in/Forms/frmTradeLicenceRegistration.aspx` | HTTP reachability |
| 5 | Trade License Renewal | `https://trade.bbmpgov.in/Forms/frmRenewalTradeRegistration.aspx` | HTTP + keyword |
| 6 | Building Permission (OBPAS) | `https://bpas.bbmpgov.in/BPAMSClient4/Default.aspx` | HTTP reachability |
| 7 | BCCMS (Court Case Monitoring / eNyaya) | `https://bbmpenyaya.karnataka.gov.in/` | HTTP reachability |
| 8 | Citizen Grievance (Sahaaya 2.0 / Namma Bengaluru) | `https://bbmp.sahaaya.in/` | HTTP reachability |

### Excluded / duplicate / legacy links (found but not monitored)

| Link found | Why excluded |
|---|---|
| `http://www.bbmp.gov.in/` | Legacy domain with stale content; `site.bbmp.gov.in` is the actively maintained main site. Feel free to add it back in `config/services.yaml` if you'd like a second data point. |
| `gba.karnataka.gov.in` | The official GBA apex-body domain. Blocked automated research tooling via `robots.txt` during verification, so it was left out of the default config to avoid false negatives from research-time uncertainty — `curl`/`requests` used by the actual monitor do **not** obey `robots.txt`, so you can safely add it back (see "Adding a service" below) if you want it monitored too. |
| `sahaaya2.bbmpgov.in`, `bbmp.gov.in` grievance mirrors | Older mirrors of the same Sahaaya 2.0 grievance system; `bbmp.sahaaya.in` is the current citizen-facing one. |

### Reliable vs. basic-only checks — read this before trusting a green tick

| Confidence | Services | What is actually verified |
|---|---|---|
| **Higher** (HTTP + keyword) | Property Tax, Trade License Renewal | The page loads **and** contains expected text, a real signal the application server itself is rendering correctly, not just a generic error/holding page. |
| **Basic only** (HTTP reachability) | Main Website, e-Khata, Trade License (new), Building Permission, BCCMS, Grievance | Confirms the **server responds**. Several of these (e-Khata, Building Permission, Grievance) are JavaScript single-page apps — the check cannot verify that the app actually finishes loading and works inside a real browser, only that the front door is open. |

**No service's login, OTP, form-submission, or payment flow is ever tested.**
That would require personal data and real transactions, which this project
deliberately never does (see [Safety & scope](#8-safety--scope-what-this-project-will-never-do)).
Where a check can only prove "reachable", the dashboard says so explicitly
via the note under each service card.

---

## 3. Dashboard features

- Overall banner: 🟢 ALL SYSTEMS OPERATIONAL / 🟡 PARTIAL OUTAGE / 🔴 MAJOR OUTAGE / ⚪ CHECKING
- Per-service: status, last checked, response time, last success/failure,
  24h/7d/30d uptime %, current outage duration, a 24-hour timeline strip
  (🟢🟢🔴🔴🟢…), and an honest one-line note on what was actually checked
- Recent incidents list with start/recovery time and duration
- **"Check all services now"** button — runs an instant, approximate,
  *browser-side* reachability probe as a second opinion between the
  authoritative ~5-minute server-side checks (see
  [Manual checks](#manual-check-now-button-how-it-really-works) below for
  why it's approximate)
- Fully responsive — works on both desktop and mobile
- No login required to view

---

## 4. Complete project structure

```
bbmp-status-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml          # the cron job — runs the checks every ~5 min
├── config/
│   └── services.yaml            # <-- add/remove monitored services HERE
├── scripts/
│   ├── checker.py                # safe HTTP GET + failure classification
│   ├── monitor.py                # state machine, uptime calc, incidents, notifications
│   ├── notify.py                  # Telegram + email senders (secrets via env vars only)
│   ├── utils.py                   # small JSON/JSONL/time helpers
│   └── tests/
│       ├── test_checker.py        # success/failure/timeout/DNS/connection tests
│       ├── test_monitor_state.py  # consecutive-failure & recovery threshold tests
│       ├── test_notify.py         # notification logic + secret-safety tests
│       └── test_e2e.py            # full simulated down→recover cycle
├── docs/                          # <-- this is what GitHub Pages serves
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── data/
│       ├── status.json            # current snapshot (what the dashboard reads)
│       ├── state.json             # internal consecutive-counter bookkeeping
│       ├── incidents.json         # incident history
│       └── history.jsonl          # one line per check, per service (for uptime %)
├── requirements.txt
├── README.md                      # you are here
├── LICENSE
└── .gitignore
```

---

## 5. Setup instructions (for a non-technical person)

You will need: a free [GitHub](https://github.com) account, and (optional
but recommended) a free [Telegram](https://telegram.org) account.

### Step 1 — Create your own copy of this project on GitHub

1. Go to GitHub and click **"New repository"**.
2. Name it anything, e.g. `bbmp-status-monitor`. Make it **Public** (this
   keeps GitHub Actions and Pages fully free with no minute limits — see
   [Cost & limits](#7-cost--limits-honest-numbers)).
3. Upload every file from this project into that new repository, keeping
   the exact same folder structure shown above.
   - Easiest way: on the repo page, click **"Add file" → "Upload files"**,
     drag the whole project folder in, and commit.

### Step 2 — Turn on GitHub Pages (this gives you the live dashboard link)

1. In your repository, go to **Settings → Pages**.
2. Under "Build and deployment", set **Source: Deploy from a branch**.
3. Set **Branch: `main`**, folder **`/docs`**, then click **Save**.
4. Wait about 1–2 minutes. Your dashboard will be live at:
   `https://<your-username>.github.io/<your-repo-name>/`

### Step 3 — Turn on GitHub Actions (this runs the checks automatically)

Nothing to do here — the workflow file `.github/workflows/monitor.yml` is
already included. GitHub Actions is enabled by default for new repos. You
can watch it run under the **"Actions"** tab. It will run automatically on
its schedule; you can also click **"Run workflow"** there any time to run
an immediate check.

### Step 4 — (Recommended) Set up Telegram notifications

See the dedicated [Telegram setup](#6-telegram-notification-setup-exact-steps)
section below.

### Step 5 — Add a service later (no code required)

Open `config/services.yaml`, copy an existing service block, change its
`id`, `name`, `url`, and save. The next scheduled run (within 5 minutes)
will pick it up automatically. Full field reference is documented as
comments at the top of that file.

---

## 6. Telegram notification setup (exact steps)

1. Open Telegram, search for **`@BotFather`**, and start a chat with it.
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g.
   `bbmp_status_alerts_bot`).
3. BotFather replies with a **token** that looks like
   `123456789:ABCdefGhIJKlmNoPQRstuVWxyz`. Copy it — this is your
   `TELEGRAM_BOT_TOKEN`. **Never paste this into the dashboard, a public
   file, or a commit — it only ever goes into a GitHub Secret (next step).**
4. Start a chat with **your new bot** (search its username, click Start).
5. Find your numeric **chat ID**:
   - Send any message to your bot, then open in a browser:
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Look for `"chat":{"id": 123456789, ...}` in the response — that
     number is your `TELEGRAM_CHAT_ID`.
6. In your GitHub repository: **Settings → Secrets and variables →
   Actions → New repository secret**. Add two secrets:
   - `TELEGRAM_BOT_TOKEN` = the token from step 3
   - `TELEGRAM_CHAT_ID` = the number from step 5
7. That's it — no code changes needed. The very next scheduled run (or a
   manual "Run workflow") will use them automatically.

**Test it:** trigger the workflow manually (Actions tab → "BBMP/GBA Status
Monitor" → "Run workflow"). If any service is (or becomes) DOWN, or already
in a DOWN state gets confirmed, you'll get a message within a couple of runs.

### (Optional) Email notifications

Add these repository secrets instead/as well: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`. For Gmail: use an
[App Password](https://myaccount.google.com/apppasswords) (not your normal
password) as `SMTP_PASS`, `smtp.gmail.com` as `SMTP_HOST`, and `587` as
`SMTP_PORT`. Any other SMTP provider (Outlook, Brevo/Sendinblue free tier,
Zoho, etc.) works the same way.

---

## 7. Cost & limits (honest numbers)

Target cost: **₹0/month.** Here's exactly why, and where the free-tier
ceilings are:

| Component | Free tier limit | Is it enough here? |
|---|---|---|
| **GitHub Actions** (public repo) | **Unlimited** minutes for public repositories | Yes, completely unlimited — this is the reason the setup instructions recommend a Public repo. |
| **GitHub Actions** (private repo, if you choose that instead) | 2,000 minutes/month (Free plan) | A run takes ~20–40 seconds. At 5-minute intervals that's ~8,640 runs/month × ~0.5 min ≈ 4,300 minutes — **over** the private-repo free limit. If you want a private repo, either raise the interval to ~15 minutes (≈1,440 min/month, comfortably inside the free tier) or keep the repo public (recommended; nothing sensitive is stored — see below). |
| **Cron scheduling precision** | GitHub's scheduler is best-effort | During busy periods, a `*/5` cron can slip to every 10–15 minutes. This is a real, documented GitHub limitation, not a bug in this project. |
| **GitHub Actions inactivity** | Scheduled workflows are automatically disabled after **60 days with no repository activity** | Not an issue here — every successful run commits data, which counts as activity, so the schedule keeps itself alive indefinitely as long as it keeps running. If you ever pause it for 60+ days, just re-enable it from the Actions tab. |
| **GitHub Pages** | Free for public repos; soft bandwidth guideline ~100GB/month | Enormous overkill for a personal status page — not a concern. |
| **Telegram Bot API** | Free, no rate limit relevant to this use case | Fully sufficient. |
| **Repository size** | Free, effectively unlimited for this use case | `history.jsonl` is pruned to the last 35 days automatically to keep the repo small. |

**What happens if a limit is exceeded?** On a public repo, none of the
above limits realistically bind. If you deliberately choose a private repo
and exceed the 2,000 free Actions minutes in a month, GitHub will simply
stop running the workflow (or bill you, if you've opted into paid usage —
which is off by default) until the next billing cycle; it will not affect
GitHub Pages or your existing data.

---

## 8. Safety & scope: what this project will never do

- Every single check is **one safe HTTP GET request**. Nothing is ever
  submitted, no forms are filled, no logins are attempted, no OTPs are
  requested, no payments are made, and no government records are touched
  or modified in any way.
- It identifies itself honestly via a custom `User-Agent` header rather
  than pretending to be a browser, and does not attempt to bypass CAPTCHA,
  WAF/Cloudflare protection, rate limits, or any other access control. If a
  service cannot be reliably checked this way, it is documented as such
  (see the "reliable vs. basic-only" table above) rather than faked.
- No personal data is collected, stored, or required. The dashboard needs
  no login.
- No secrets (Telegram token, chat ID, SMTP credentials) ever appear in
  the frontend, in a committed file, or in a log line — they exist only as
  GitHub encrypted secrets, injected as environment variables at run time
  (see `scripts/notify.py`).

---

## 9. Known limitations

- **Bot-protection / WAF false positives:** some Indian government sites
  return non-200 codes (e.g. 403) to automated/non-browser traffic even
  when the site is perfectly fine for a real visitor. If you see a service
  persistently and *consistently* reported as DOWN right after setup while
  you can open it fine in a browser, this is the likely cause — add the
  observed status code to that service's `expect_status` list in
  `config/services.yaml` (this only weakens the check back down to "server
  reachable", which is an honest, documented trade-off, not a bypass of
  any protection).
- **JavaScript apps:** e-Khata, Building Permission (OBPAS), and the
  Grievance portal are JS single-page apps. A GET request can confirm the
  server responds but cannot confirm the app finishes loading or that a
  logged-in workflow succeeds.
- **"Website reachable" ≠ "application usable":** clearly labelled on
  every card via the `verified_note`, per the project's core design
  requirement — never oversold.
- **Cron timing is best-effort:** see the GitHub Actions row in the cost
  table above.
- **Manual "Check all services now" button — how it really works:** since
  the dashboard is a static site with no backend server of its own, this
  button cannot trigger a fresh *server-side* check on demand. Instead it
  runs a client-side, browser-based reachability probe (a `no-cors` fetch
  race against a timeout) for an instant approximate second opinion — it
  is explicitly labelled as approximate on-screen, and never overwrites
  the authoritative status coming from the GitHub Actions monitor above
  it. To force an immediate authoritative check instead, go to your repo's
  **Actions** tab and click **"Run workflow"** — it finishes in well under
  a minute.
- **First few minutes after setup:** every service starts in a
  ⚪ "checking" state until it has been observed enough consecutive times
  to confirm a status — this is deliberate (see false-positive protection
  below), not a bug.

---

## 10. False-positive protection (how thresholds work)

Configured in `config/services.yaml → settings:`

| Setting | Default | Meaning |
|---|---|---|
| `fail_threshold` | 2 | consecutive failed checks before declaring DOWN |
| `success_threshold` | 2 | consecutive successful checks before declaring RECOVERED |
| `degraded_threshold` | 2 | consecutive keyword-mismatches (site up, wrong/missing content) before declaring DEGRADED |
| `repeat_notify_minutes` | 720 (12h) | if still DOWN, send a reminder at most this often (0 = notify once only) |
| `timeout_seconds` | 20 | how long to wait for a response before it counts as a timeout failure |

All of these are plain numbers in a YAML file — change them any time with
no code edits.

---

## 11. Testing performed in this session

All automated tests live in `scripts/tests/` and run with:

```bash
pip install -r requirements.txt
pip install pytest
python -m pytest scripts/tests/ -v
```

They exercise a **local throwaway HTTP test server** (not real BBMP
sites), so they're fast, deterministic, and runnable offline/in CI.

| # | What was tested | Result |
|---|---|---|
| 1 | Successful check (200 + correct keyword) | ✅ Pass |
| 2 | Successful reachability, keyword mismatch (degraded signal) | ✅ Pass |
| 3 | Failed check — HTTP 404 | ✅ Pass |
| 4 | Failed check — HTTP 500 | ✅ Pass |
| 5 | Timeout handling (slow server, short timeout) | ✅ Pass |
| 6 | Connection-refused handling (closed port) | ✅ Pass |
| 7 | DNS resolution failure handling (invalid hostname) | ✅ Pass |
| 8 | A single failed check does NOT mark a service DOWN | ✅ Pass |
| 9 | 2 consecutive failures DO mark a service DOWN, with incident opened | ✅ Pass |
| 10 | A single success while DOWN does NOT mark it recovered | ✅ Pass |
| 11 | 2 consecutive successes DO mark it RECOVERED, with incident closed + duration recorded | ✅ Pass |
| 12 | "Flapping" (one success between failures) correctly resets the recovery streak | ✅ Pass |
| 13 | Repeated keyword mismatches escalate to DEGRADED after threshold | ✅ Pass |
| 14 | Incident open/close bookkeeping (timestamps, duration) | ✅ Pass |
| 15 | First-ever successful check moves an UNKNOWN service to OPERATIONAL | ✅ Pass |
| 16 | Overall dashboard banner never falsely claims "ALL OPERATIONAL" before anything is confirmed (`CHECKING` state) | ✅ Pass |
| 17 | Full simulated cycle: UNKNOWN → OPERATIONAL → (1 fail, still up) → DOWN → (1 success, still down) → RECOVERED, checking `status.json`/`incidents.json`/`history.jsonl` output at every step | ✅ Pass |
| 18 | Notifications silently skipped (no crash) when secrets are absent | ✅ Pass |
| 19 | `--dry-run` never calls the real Telegram/SMTP network calls | ✅ Pass |
| 20 | Notification failure (Telegram API error) handled gracefully, doesn't crash the run | ✅ Pass |
| 21 | No secret values are hard-coded anywhere in `notify.py` | ✅ Pass |
| 22 | Dashboard renders correctly at mobile width (375px) — verified via the responsive CSS media query and manual layout review | ✅ Reviewed (see note below) |
| 23 | End-to-end deployability using only free services | ✅ Verified against GitHub's published pricing docs (see [Cost & limits](#7-cost--limits-honest-numbers)) |

**Note on real BBMP endpoints:** this development environment's own
network is restricted to a small allow-list of package-registry domains,
so it could not make live outbound requests to the real BBMP/GBA servers
to test against them directly (attempts were intercepted by that
environment's own proxy, not by the government sites). Per the standard
guidance for this kind of tooling, real-endpoint behavior was instead
validated by fetching each URL's content directly during the research
phase (confirming each one resolves and serves real content — see Section
2), and the monitoring **engine itself** was fully tested against a
controlled local test server covering every failure mode. Once deployed
to GitHub Actions (which has normal internet access), the very first
scheduled run will immediately show real live results on your dashboard —
watch the **Actions** tab for its output.

**Note on mobile testing:** the CSS uses a mobile-first responsive grid
(`auto-fill, minmax(300px, 1fr)`) with an explicit breakpoint at 520px that
stacks the header buttons and adjusts font size; this was reviewed against
common phone viewport widths (360–430px) rather than tested in a real
mobile browser session, since this environment cannot launch one. Please
sanity-check the live dashboard on your own phone after deployment — if
anything looks off, it's a quick CSS fix.

---

## 12. How to access the live dashboard

Once Pages is enabled (Step 2 above):

```
https://<your-github-username>.github.io/<your-repo-name>/
```

Bookmark it, add it to your phone's home screen, or check it any time —
no login required.

---

## 13. Final checklist

- [x] Current official BBMP/GBA URLs researched and verified (Section 2)
- [x] Duplicate/legacy/obsolete links identified and documented, excluded from monitoring
- [x] Reliable vs. basic-only checks clearly distinguished per service
- [x] "Website reachable" vs "application usable" distinguished in both code and UI
- [x] No form submission, login, payment, or record modification anywhere in the codebase
- [x] Live dashboard: overall status, per-service status/response time/uptime/outage duration, 24h timeline, incident history — responsive layout
- [x] Automatic monitoring via free GitHub Actions cron, no personal computer required
- [x] Telegram notifications implemented (UP→DOWN and DOWN→UP, plus configurable repeat reminder), with outage start/recovery time/downtime/reason
- [x] Optional email notifications implemented
- [x] Consecutive-failure / consecutive-success confirmation thresholds, all configurable
- [x] HTTP status codes, timeouts, DNS errors, and connection errors all recorded distinctly
- [x] Historical data: 24h/7d/30d uptime %, response-time capture, incident history, visual timeline
- [x] Manual "Check all services now" + honest documentation of its browser-side limitation
- [x] Configuration file (`config/services.yaml`) — add a service with zero code changes
- [x] No secrets in frontend or repository; all via GitHub Secrets / env vars
- [x] No login required to view the dashboard
- [x] 20 automated tests written and passing against a local test server (checker, state machine, notifications, full end-to-end cycle)
- [x] Free-tier limitations explicitly quantified (Section 7)
- [x] Target cost confirmed: **₹0/month** on a public repository
