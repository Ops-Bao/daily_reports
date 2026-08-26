# Daily digest pipeline

Two Slack digests every morning at 7:00 Paris, built from the 9 restaurant
shift-report sheets.

- **Ops digest** → `#shortyshort` (`C0A6VHL0CCF`)
- **Food-quality digest** → DM to Jisoo (`U078L6FSV8T`)

```
Control Panel sheet ──► config.py ──┐
                                    ├──► run_daily.py ──► post_digest.py ──► Slack
9 restaurant sheets ──► extract_report.py ──► overall_quality.py
                                          └─► food_quality.py
```

## Why GitHub Actions

The code already lives in GitHub, the job is a once-a-day batch that runs for
seconds, and Actions gives you cron, encrypted secrets, run logs, and a
"re-run" button with no infrastructure to own. Cloud Functions would add a
deploy step and a Cloud Scheduler job to buy nothing this pipeline needs.

Keep Cloud Functions in mind for **Pipeline 2** (the PDF mirroring with reply
routing) — that one is event-driven and genuinely wants an HTTP endpoint.

## Setup — five steps

### 1. Google service account

1. Google Cloud Console → new project (or reuse one) → **Enable the Google
   Sheets API**.
2. IAM → Service Accounts → create one, e.g. `daily-digest`.
3. Keys → Add key → JSON → download it.
4. Copy the service account's email (`daily-digest@….iam.gserviceaccount.com`).

### 2. Share the sheets with it

Share **the Control Panel and all 9 restaurant sheets** with that email as
**Viewer**. This is the step that most often gets missed on one sheet — that
location then shows up as `⚠️ … error` in the digest, which is the intended
behaviour, not a crash.

Fastest route: if the sheets sit in a shared Drive folder, share the folder once.

### 3. Slack app

1. api.slack.com/apps → Create New App → From scratch, in your workspace.
2. OAuth & Permissions → Bot Token Scopes: **`chat:write`** and **`im:write`**
   (`im:write` is what allows the DM to Jisoo).
3. Install to Workspace → copy the **Bot User OAuth Token** (`xoxb-…`).
4. `#shortyshort` is **private**, so the bot cannot post until it is invited.
   In that channel, run:
   ```
   /invite @YourBotName
   ```

### 4. Repository secrets and variables

Settings → Secrets and variables → Actions.

**Secrets**

| Name | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the entire contents of the JSON key file |
| `SLACK_BOT_TOKEN` | `xoxb-…` |

**Variables**

| Name | Value |
|---|---|
| `OPS_DESTINATION` | `C0A6VHL0CCF` |
| `FOOD_DESTINATION` | `U078L6FSV8T` |
| `ALERT_DESTINATION` | `C0A6VHL0CCF` (or your own user ID for private alerts) |

### 5. First run — dry run first

Actions → **Daily digest** → Run workflow → leave *dry run* checked. The digests
print to the job log without posting. Read them, then re-run with dry run
unchecked to post for real.

## Operating it

**Add or pause a restaurant** — edit the Control Panel sheet. Set `Include` to
`FALSE` to pause one. No code change, no redeploy.

**Change the tab name or run hour** — also the Control Panel. Tab names are
discovered by content, so renaming a Control Panel tab won't break anything.

**Backfill a specific day** — Run workflow → set date to `2026-08-24`.

**Test one restaurant** — locally: `python run_daily.py --only PB --dry-run`

### Scheduling and DST

GitHub cron is UTC only, so the workflow fires at both 05:00 and 06:00 UTC and
`--check-hour` makes the wrong one exit immediately. You get 7:00 Paris all year
without editing anything twice a year. Note that Actions cron can be delayed
several minutes under load — if 7:00 sharp matters, move the cron 15 minutes
earlier rather than expecting the minute to be exact.

## How failures behave

| Situation | What happens |
|---|---|
| One sheet unreachable | `⚠️ NAME — error…` line in both digests; rest posts normally |
| Sheet date isn't today | `⚠️ NAME — la feuille indique 24/08/2026`; **numbers are not posted** |
| A row was renamed in a sheet | Digest posts; a separate 🔧 alert lists the missing labels |
| The whole job crashes | 🚨 alert with the traceback, so silence never means "all fine" |

The staleness guard matters most. Without it, a manager who forgets to roll the
date forward means yesterday's figures get republished this morning as today's —
and nobody notices, because the message looks completely normal.

## Pipeline 2 — PDF review loop

Managers drop a PDF into their own `#bf-managers-…` channel just after service
(observed: 23:45–00:30). This loop puts all nine in front of one reviewer and
carries her comments back.

```
#bf-managers-pb ──PDF──► reviewer's DM ──she replies in thread──► back as a
                                                    threaded reply under the PDF
```

`mirror_pdfs.py collect` — runs at 07:10, gathers the last 24h of PDFs and DMs
one per restaurant to `REVIEWER_ID`, each with a permalink to the original.

`mirror_pdfs.py route` — runs every 15 minutes, 06:00–19:00. Forwards her thread
replies into the origin channel as replies under the manager's own PDF message.

### No server and no database

Two tricks avoid both:

- **The permalink is the routing table.** A Slack permalink already encodes the
  origin channel and timestamp. The link she clicks to jump to the source is the
  same link the router parses to know where her comment belongs.
- **A ✅ reaction is the "already sent" flag.** Slack rejects a duplicate
  reaction, so the claim is atomic and it doubles as a visible receipt for her.
  If the forward then fails, the reaction is removed so the next run retries.

### Extra Slack scopes for this half

On top of `chat:write` and `im:write`: `groups:history` (read the private manager
channels), `im:history` (read her DM thread), `files:read`, `files:write`,
`reactions:write`, `reactions:read`, `users:read`.

The bot must be `/invite`d into **all nine manager channels** — they're private.

### Extra variables

| Name | Value |
|---|---|
| `REVIEWER_ID` | the reviewer's Slack user ID (`U…`) |
| `PDF_CHANNELS` | optional override, e.g. `PB=C0133HV2QSV,GB=GR3JU1HJ5,…` |

Channel IDs for eight of the nine restaurants are already filled in as defaults
in `mirror_pdfs.py`. **PBB (Petit Bao Bastille) is still missing** — find its
manager channel ID and add it.

### Two things to know before switching it on

**Her comments post as the bot, not as her.** Slack does not let an app speak as
a person. Managers will see `💬 *Name* — comment`. Tell them once, or it reads
oddly the first morning.

**DM delivery has no cover.** If she's away, nothing gets reviewed and nobody can
see that. A shared private channel would fix it. If you want to keep DMs, the
alert on missing PDFs partly covers the gap — say the word and I'll add a daily
"reviewed / not reviewed" tally into `#shortyshort` too.

## Design rules preserved from the original scripts

- **Numbers never pass through an LLM.** Every figure is read verbatim from a
  labelled row and parsed by the French-number parser.
- **Label-based, not row-index-based.** Inserting a row in one restaurant's
  sheet doesn't silently shift its values.
- **AI hooks stay commented out.** Both formatters keep their optional prose-only
  hooks (`summarize_general`, `filter_food_quality`), untouched and inactive.

## What changed in your original files

`extract_report.py`

- Implemented `load_grid_from_sheets` (was `NotImplementedError`), including
  right-padding rows — the API truncates trailing blanks, which would otherwise
  turn empty cells into false "label introuvable" warnings.
- Labels now map to **every** matching row, not just the first. `TOP 3` occupies
  three rows in the sheet, so the original kept item 1 and silently dropped 2 and 3.
- Added `date_iso` to `meta` to make the staleness guard possible.

`overall_quality.py`, `food_quality.py` — unchanged apart from being imported as
modules.

## ⚠️ One bug in the source spreadsheet, not the code

In Petit Bao EM's `Rapport Jour New` (25/08), the **`CA HT ON SITE` daily TOTAL
reads `718,18 €`** — but MIDI (`987,82`) + SOIR (`2 426,95`) is `3 414,77 €`.
`718,18 €` is exactly the `CA HT TAKE AWAY` total, so that TOTAL cell is
pointing at the wrong column.

`CA HT` and `COUVERTS` totals both check out, so this looks isolated to that one
cell. The digest doesn't print the ON SITE total today, so nothing is wrong in
Slack right now — but it's worth fixing before anyone builds on that cell, and
worth checking whether the same formula was copied into the other 8 sheets.
