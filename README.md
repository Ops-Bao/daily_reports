# Daily digest pipeline

Two Slack digests every morning at 05:45 Paris, built from the 9 restaurant
shift-report sheets. The digest covers **yesterday** (the last closed day).

Every extracted field is appended to an **Archive** tab (one row per restaurant
per day). The digests are deliberately lossy — about a dozen fields out of
forty — so the archive, not Slack, is the record you analyse later.

- **Ops digest** → `#shortyshort` (`C0A6VHL0CCF`)
- **Food-quality digest** → DM to Jisoo (`U078L6FSV8T`)

```
Control Panel sheet ──► config.py ──┐
                                    ├──► run_daily.py ──► post_digest.py ──► Slack
9 restaurant sheets ──► extract_report.py ──► overall_quality.py
                                          └─► food_quality.py
```

## Why GitHub Actions, and why not its cron

The code already lives in GitHub, the job is a once-a-day batch that runs for
seconds, and Actions gives you encrypted secrets, run logs, and a "re-run"
button with no infrastructure to own.

What Actions does **not** give you is a punctual clock. `schedule` runs are
low priority: in September 2026 the 05:00 UTC cron was observed starting at
11:30–13:20 Paris for days in a row. So the real trigger is a 10-line
Cloudflare Worker (`worker/`) that starts both workflows through the API at
05:45 Paris — API-started runs begin within seconds. GitHub's own crons stay
as a fallback. Both jobs are **idempotent** (they look in Slack before posting),
so however many triggers fire, exactly one digest and one set of PDFs go out.

## Setup — six steps

### 1. Google service account

1. Google Cloud Console → new project (or reuse one) → **Enable the Google
   Sheets API**.
2. IAM → Service Accounts → create one, e.g. `daily-digest`.
3. Keys → Add key → JSON → download it.
4. Copy the service account's email (`daily-digest@….iam.gserviceaccount.com`).

### 2. Share the sheets with it

Share the **9 restaurant sheets** with that email as **Viewer**, and the
**Control Panel as Editor** — the Archive tab is written back to it. (If you
point `Archive sheet URL` at a different spreadsheet, that one needs Editor and
the Control Panel can stay Viewer.) This is the step that most often gets missed on one sheet — that
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
| `ANTHROPIC_API_KEY` | optional — the AI briefing is skipped without it |

If that key was created at the **organisation** level rather than inside a
workspace, the API rejects it with `anthropic-workspace-id`. Either add a
repository *variable* `ANTHROPIC_WORKSPACE_ID` (Console → Settings →
Workspaces → the `wrkspc_…` in the URL), or create a new key from inside a
workspace and skip the variable.

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

### 6. The Cloudflare Worker — the clock and the doorbell

One Worker does two jobs: starts both pipelines at 05:45 Paris, and starts
`route` the instant the reviewer posts a comment.

1. GitHub → Settings → Developer settings → Fine-grained tokens → new token,
   repository access: this repo only, permission **Contents: Read and write**
   (that is what `repository_dispatch` checks — not the "Actions" permission).
2. Edit `worker/wrangler.toml`: set `REVIEWER_ID` to the same `U…` you put in
   the repo variables.
3. `npx` needs Node.js — install it first (`winget install OpenJS.NodeJS.LTS`),
   or skip wrangler entirely and paste `worker/src/index.js` into the
   Cloudflare dashboard (Workers → Create → Edit code), setting the vars,
   secrets and the three cron triggers in the UI.
   In `worker/`: `npx wrangler login`, `npx wrangler deploy`, then
   `npx wrangler secret put GITHUB_TOKEN` and
   `npx wrangler secret put SLACK_SIGNING_SECRET` (Slack app → Basic
   Information → Signing Secret).
4. Deploying prints the Worker URL (the dashboard shows it too). In the Slack app → **Event
   Subscriptions** → on, paste that URL as the Request URL (Slack verifies it
   immediately — the Worker answers the challenge), then under *Subscribe to
   bot events* add **`message.im`** and reinstall the app when prompted.

Test it: post a threaded reply in the reviewer's DM. A "PDF review loop" run
should appear in the Actions tab within a few seconds. `npx wrangler tail`
shows the Worker's own log if it does not.

No-code alternative for the 05:45 part only (you lose instant routing): any
scheduler that can POST with headers (cron-job.org, Cloud Scheduler) can call
`POST https://api.github.com/repos/Ops-Bao/daily_reports/dispatches` with
`{"event_type":"run-digest"}`, and the same with
`{"event_type":"run-cron-job","client_payload":{"mode":"collect"}}`.

## Rehearsing against a test channel

Both pipelines can be run for real without touching production: the digest
takes `--to`, and the PDF mirror reads `REVIEWER_ID` from the environment.

**Before the first rehearsal**, `/invite` the bot into the test channel — it is
private, so the bot cannot post there otherwise:

```
/invite @YourBotName
```

### From GitHub (no local setup)

Actions → **Daily digest** → Run workflow, then set:

| Field | Value |
|---|---|
| dry run | **unticked** (you want a real message) |
| date | the day you want, or blank for D-1 |
| to | your test channel ID, e.g. `C0BSV1E70E6` |
| no_archive | **ticked** (don't append rehearsal rows to real data) |

Everything — both digests, the AI briefing, and any alert — lands in the test
channel. Production variables are untouched, so a forgotten setting cannot
leak into `#shortyshort`.

For the PDF loop: Actions → **PDF review loop** → Run workflow → mode `collect`,
dry run **ticked**. That prints which PDF it found in each of the nine manager
channels without sending anything — the fastest way to prove the bot is in all
nine. A channel the bot is missing from shows as `channel unreadable`.

### From a terminal

```powershell
python -m pip install -r requirements.txt
$env:SLACK_BOT_TOKEN   = "xoxb-..."
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content service-account.json -Raw
python run_daily.py --to C0BSV1E70E6 --no-archive
```

Drop `--to` for a dry run that posts nowhere: `python run_daily.py --dry-run`.

For the PDF mirror, point it at yourself instead of the reviewer so the
mirrored PDFs arrive in your own DM:

```powershell
$env:REVIEWER_ID = "U09DX08QCV9"   # your own Slack user ID
python mirror_pdfs.py collect --dry-run   # list what it would send
python mirror_pdfs.py collect             # actually send them to your DM
python mirror_pdfs.py route --dry-run     # after replying in the thread
```

`route` is safe to rehearse: it only forwards replies written by whoever
`REVIEWER_ID` names, and each forwarded reply is claimed with a ✅ reaction so
it cannot be sent twice.

## Operating it

**Add or pause a restaurant** — edit the Control Panel sheet. Set `Include` to
`FALSE` to pause one. No code change, no redeploy.

**Move the archive elsewhere** — add an `Archive sheet URL` row to the Control
Panel settings (or set `ARCHIVE_SPREADSHEET_ID`), and share that file as Editor.
It defaults to a tab called `Archive` inside the Control Panel itself.

**Change the tab name** — also the Control Panel. Tab names are discovered by
content, so renaming a Control Panel tab won't break anything. (The Control
Panel's *Run hour* row is no longer read — the time is set by the Worker's
`DIGEST_HOUR_PARIS` and the workflow crons.)

**Re-post a day on purpose** — Run workflow → tick *force*. Without it a second
run for the same day exits with "already posted".

**Backfill a specific day** — Run workflow → set date to `2026-08-24`.

**Test one restaurant** — locally: `python run_daily.py --only PB --dry-run`

**Run the self-tests** — from the repo root: `python -m tests.test_extract` and
`python -m tests.test_mirror`. Both workflows run them before doing anything.

### Scheduling, DST and idempotency

Three clocks can start the digest: the Worker (05:45 Paris, DST-aware, the one
that matters), and four GitHub crons at 03:43–06:43 UTC as a safety net. None
of them needs to know which one it is: before reading a single sheet, the job
asks Slack whether a message with today's header (`📊 *DAILY OPS CHECK-IN* —
dd/mm/yyyy`) is already in `#shortyshort` (and the food header in Jisoo's DM).
If yes, it exits in one API call. The PDF `collect` does the same with the
permalinks already in the reviewer's DM.

This replaces the old `--check-hour` gate, which made a late-starting run
"succeed" by doing nothing — the failure mode that went unnoticed for weeks.

## How failures behave

| Situation | What happens |
|---|---|
| Run starts hours late (GitHub queue) | It still posts, unless an earlier trigger already did — then it exits "already posted" |
| One sheet unreachable | `⚠️ NAME — error…` line in both digests; rest posts normally |
| Sheet date isn't the target day (D-1) | `⚠️ NAME — la feuille indique 24/08/2026`; **numbers are not posted** |
| A row was renamed in a sheet | Digest posts; a separate 🔧 alert lists the missing labels |
| The whole job crashes | 🚨 alert with the traceback to `ALERT_DESTINATION`, falling back to `OPS_DESTINATION` if unset |

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

she replies ──► Slack event ──► Cloudflare Worker ──► GitHub API ──► route (~30-60 s)
```

`mirror_pdfs.py collect` — started by the Worker at 05:45 Paris (fallback crons
03:53–06:53 UTC), gathers the last 24h of PDFs and DMs one per restaurant to
`REVIEWER_ID`, each with a permalink to the original. PDFs whose permalink is
already in her DM are skipped, so repeated runs never send twice.

`mirror_pdfs.py route` — **event-driven**. Slack calls the Worker the moment she
posts a threaded reply, the Worker dispatches this workflow, and her comment
lands in the manager's channel about 30–60 seconds later (almost all of that is
GitHub booting a runner). Forwards her thread replies into the origin channel as
replies under the manager's own PDF message.

A `*/30 5-11 * * *` cron stays as a safety net for events Slack never delivered
— it gives up after 3 retries — and for a Worker outage. Anything that is not
the `53 3,4,5,6` collect cron is treated as a route tick.

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
channels — also used by the digest to check `#shortyshort` before posting),
`im:history` (read her DM thread, and Jisoo's DM for the same check),
`files:read`, `files:write`, `reactions:write`, `reactions:read`, `users:read`.

The bot must be `/invite`d into **all nine manager channels** — they're private.

### Extra variables

| Name | Value |
|---|---|
| `REVIEWER_ID` | the reviewer's Slack user ID (`U…`) — also set it in `worker/wrangler.toml` |
| `PDF_CHANNELS` | optional override, e.g. `PB=C0133HV2QSV,GB=GR3JU1HJ5,…` |

Channel IDs for nine restaurants are already filled in as defaults
in `mirror_pdfs.py`.

### Two things to know before switching it on

**Her comments post as the bot, not as her.** Slack does not let an app speak as
a person. Managers will see `💬 *Name* — comment`. Tell them once, or it reads
oddly the first morning.

**DM delivery has no cover.** If she's away, nothing gets reviewed and nobody can
see that. A shared private channel would fix it. If you want to keep DMs, the
alert on missing PDFs partly covers the gap — say the word and I'll add a daily
"reviewed / not reviewed" tally into `#shortyshort` too.

## The Archive tab

Created on first run if missing. One row per restaurant per day, keyed on
`(date, code)` — re-running a morning does not duplicate it, and a day that was
missed is filled in by re-running with *force*.

Columns are matched **by name**, never by position: reordering columns in the
sheet, or adding a field to `archive.py` later, cannot shift existing values
into the wrong column. A new field is appended to the header; old rows keep
theirs.

Numbers are written as numbers, so `SUM`/pivots work without cleaning. The
`warnings` column records which labels drifted that day — without it you cannot
tell, months later, whether a blank cell means "nothing happened" or "the row
was renamed and we wrote N/A".

If archiving fails the digest still posts, and a 🗄️ alert tells you to re-run
with *force*.

## The AI briefing

The ops digest opens with a 4–8 line French briefing written by Claude
(`claude-opus-5`) — the night's trend, and what needs a call today. Everything
below it is the extractor's, unchanged.

The repo's rule still holds: **numbers never pass through an LLM.** The model
never sees a spreadsheet. It gets figures that were already extracted and
parsed, and is told to quote them verbatim or describe things in words. Then
`verify_numbers()` checks the output — every number in the briefing must be one
we handed it (integers up to 31 are allowed, since "3 sites" and a date are not
financial claims). A briefing containing an untraceable figure is **dropped, not
posted**, and you get a 🧠 alert saying why.

It degrades quietly in every other direction too: no `ANTHROPIC_API_KEY`, an API
error, an empty response — the digest posts exactly as it did before, minus the
briefing. Set `AI_SUMMARY_DISABLED=true` to turn it off, `AI_SUMMARY_MODEL` to
change the model.

Cost: nine restaurants is a few thousand input tokens, so roughly a cent a day.

## Design rules preserved from the original scripts

- **Numbers never pass through an LLM.** Every figure is read verbatim from a
  labelled row and parsed by the French-number parser.
- **Label-based, not row-index-based.** Inserting a row in one restaurant's
  sheet doesn't silently shift its values.
- **AI writes prose, never figures.** The briefing in `ai_summary.py` is the only
  active LLM call, and its output is verified against the extracted numbers
  before it is allowed into Slack. The two per-field hooks in the formatters
  (`summarize_general`, `filter_food_quality`) remain commented out.


