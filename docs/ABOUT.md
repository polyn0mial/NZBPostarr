# NZBPostarr

> Watches folders and auto-posts content to multiple Usenet indexers, or restreams NZBs server-to-server.

<!-- about HAND-OWNED above the GENERATED marker. Edit freely; the docs tool carries it back into the docs tool's Codex. -->

## What it is

NZBPostarr is a self-hosted, browser-based Usenet posting manager for content the operator owns or is authorized to distribute. It watches configured folders, classifies what it finds (TV/movie/anime/disc/music/ebook), packages it with rar and parpar, posts it to Usenet via nyuu, and submits the resulting NZB to any number of configured Newznab-style indexers - all from a web UI, a headless CLI, or an unattended folder/daemon mode. It also supports the reverse direction: re-streaming an existing NZB straight from one Usenet server to another without downloading to disk first.

## Things not to forget

_The intricacies worth remembering: the gotchas, the half-built parts, the decisions whose
reason lives nowhere else. the docs tool never overwrites this section._

- The MCP endpoint for AI agents ships off by default and needs a manual `pip install mcp` on top of a normal install, because the mcp SDK is deliberately kept out of requirements.lock - agent tool access is opt-in, not just config-gated. anchors: `api/mcp.py`
- A muted known-issue signature has no expiry by design - MutedIssue is keyed only on (indexer_id, signature) with no timestamp/TTL field, so once muted it stays hidden from the History page forever until someone manually unmutes it, even after the underlying failure is fixed. anchors: `core/db/models.py`, `core/db/issues.py`
- Duplicate detection (destinations_for / destinations_for_batch in core/db/ledger.py) is scoped per indexer_id, not global across the whole install - an item already posted to one indexer will still be offered and posted to a different configured indexer that has no record of it. anchors: `core/db/ledger.py`
- There is no per-user account model - a single shared web password (enable_password/web_password) optionally gates the whole UI and API, and it is off by default on the assumption of a trusted network, so anyone who can reach the port has full control unless an operator turns auth on. anchors: `api/auth.py`, `core/auth.py`
- Running `python3 main.py` bootstraps its own virtualenv and installs pinned dependencies itself on first launch - there is no separate manual `pip install -r requirements` step operators are expected to run first. anchors: `cli/launcher/`
- The entire indexer plugin surface is YAML, not code - IndexerRegistry auto-loads every file dropped into indexers/, so adding a new Newznab-style site is a config-only change (copy _template.yaml or newznab.example.yaml), never a code change. anchors: `core/indexers/registry.py`
- A background process reaper periodically kills orphaned nyuu/rar/parpar child processes left behind by crashed or interrupted jobs - anyone touching job cancellation or crash handling needs to know this is the only cleanup path for those external tool processes. anchors: `logic/system/reaper.py`
- Self-update (install_from_github) takes an exclusive operation lock and refuses to run if an update or rollback is already in progress, applying the new release in place while preserving runtime data and keeping prior snapshots for rollback. anchors: `logic/system/updater.py`

<!-- about GENERATED BEGIN - rewritten by the docs tool; edit the Codex, not this -->

## What the docs tool knows about this project

Everything from here down is generated from this project's project notes
(`codex/projects/nzb-uploadrr.md` in the the docs tool clone) and is **rewritten on every publish** -
edit the dossier, not this block. Everything ABOVE the marker is yours.

### At a glance

- **Ships as:** web app (self-hosted) - git clone or GitHub release ZIP, run with `python3 main.py`; the app bootstraps its own Python venv and installs pinned deps on first launch; also usable entirely as a `--headless` CLI
- **Written in:** Python (136 files), JavaScript (58 files)
- **Built with:** Playwright, pytest
- **Tests:** 58 test file(s) (49 pytest, 9 node)
- **CI:** `quality.yml`, `release.yml`
- **Domain:** usenet, nzb, nntp, nyuu, parpar, rar, newznab, indexer, guessit, release-name-parsing, anime-detection, mediainfo
- **Remote:** https://github.com/polyn0mial/nzbpostarr

### Architecture

- `(root: app.py, main.py, setup.py, version.py)` - FastAPI composition root (app.py); bootstrap entry point (main.py); interactive first-run installer (setup.py); single source of the version string (version.py).
- `webui/` - Server-rendered Jinja pages plus Vue 3 ES-module page controllers (queue, dashboard, history, settings, stats), built with esbuild/Tailwind via npm.
- `api/` - One FastAPI router per surface, plus the optional MCP endpoint (api/mcp.py).
- `cli/` - Headless CLI commands, launcher and daemon control.
- `logic/` - Business logic in packages (jobs, pipeline, pending, classify, stream, stats, system): upload queue and job execution, pending-folder scan/classification, the posting and indexer-submission pipeline, Usenet streaming/reposting, stream monitors, stats collector, backup/self-updater/process reaper; plus the service runtime (logic/runtime.py), the settings service (logic/settings.py) and the folder monitor (logic/autoupload.py).
- `core/` - Shared foundation: Pydantic config model, SQLAlchemy models/queries (core/db/), the indexer YAML registry (core/indexers/) and HTTP submission builder, guessit-backed release-name parsing, and formatting, filesystem, path, process, media and redaction helpers.
- `indexers/` - One YAML file per Usenet indexer (auth, categories, submission shape) - the entire indexer plugin surface; add a site with no code change.
- `tests/` - pytest suites grouped by domain (queueing, pending scan, indexers, streaming, etc.) with fixtures and mocked indexers.
- `.github/` - CI workflows (quality.yml, release.yml) and release.py, which checks for leaked secrets/config and packages public source-tree releases.
- `docs/` - Project docs plus docs/todo/, the open-work folder the docs tool also reads.
- `tools/` - Measured accuracy baseline script for the name-level content classifier, and an offline dump of the pending classifier's verdicts for one folder.

### Features

23 recorded - 22 shipped, 1 partial, 0 planned. Each `path:line` is where the feature is DEFINED, checked by the docs tool.

**Shipped**

- **Web dashboard UI** - Browser UI for starting uploads, watching the live queue, browsing history, and monitoring system stats from any device on the network. - `api/`, `webui/index.html`
- **Multi-indexer YAML plugin system** - Add a new Usenet indexer by dropping a YAML definition in indexers/ - no code changes; ships with 6 built-in Newznab-compatible sites plus a documented template. - `core/indexers/registry.py`, `indexers/_template.yaml:1`
- **Automated NZB posting pipeline** - Packages selected content into RAR/PAR2 parts, builds the NZB, and posts it via nyuu to every enabled indexer in one job. - `logic/pipeline/`, `logic/pipeline/submit.py`
- **Headless / CLI mode** - Every web-UI action (upload, queue control, stats, config, updates) is also a scriptable `--headless` subcommand with `--json` output for cron/automation. - `cli/commands/`, `cli/parser.py`
- **Direct NZB reposting / Usenet-to-Usenet streaming** - Restreams an existing NZB's articles from one Usenet server straight to another without downloading to disk, from the CLI or a saved monitor. - `logic/stream/repost.py`, `cli/commands/stream.py`
- **Stream monitors** - Watches a folder for new .nzb files and automatically queues a repost job for each one found, for long-running unattended reposting. - `logic/stream/monitors.py`
- **Folder monitor** - Watches configured content folders and auto-triggers an upload once a new item settles (stops changing size), no manual scan needed. - `logic/autoupload.py`
- **Upload queue with pause/resume/priority** - Concurrent upload queue with pause/resume, priority promotion, job retry, and persistence across restarts. - `logic/jobs/engine.py`, `logic/jobs/store.py`
- **Duplicate detection** - Checks a candidate item against past uploads per indexer and skips content already posted there. - `core/db/ledger.py`
- **Upload history & stats** - Browsable, filterable/grouped history of every completed upload with per-indexer results, plus hourly and detailed stat charts backed by SQLite. - `core/db/history.py`, `core/db/stats.py`
- **Live system & network stats** - Live CPU/memory/network/disk and per-process resource panel with historical charts, viewable in the UI or via `--headless stats --watch`. - `logic/stats/collector.py`, `cli/commands/stats.py`
- **Anime detection & caching** - Looks up a release's anime status against the Jikan (MyAnimeList) API with rate-limited batching and a persistent local cache, used to route content to the right category. - `logic/classify/anime.py`, `logic/classify/anime.py`
- **Automatic content classification** - Infers category (TV/movie/anime/disc/music/ebook/etc.) and season/episode structure from folder and file names via guessit plus custom heuristics, with a manual override path per item. - `logic/classify/`, `logic/pending/tree.py`, `core/release_name.py`
- **Cross-platform daemon mode** - Runs as a background daemon (start/status/stop) on Windows, macOS, and Linux, with an optional persistent tmux session and a wizard-generated systemd unit for production. - `cli/launcher/daemon.py`, `cli/launcher/tmux.py`, `setup.py`
- **Auto-bootstrap installer** - Running `python3 main.py` creates its own virtualenv and installs pinned dependencies on first run - no manual setup step. - `cli/launcher/bootstrap.py`
- **First-run setup wizard** - Interactive guided installer configures content folders, NNTP servers, and indexer API keys on first launch when no config file is present. - `setup.py`
- **Process reaper** - Periodically finds and kills orphaned nyuu/rar/parpar child processes left behind by crashed or interrupted jobs. - `logic/system/reaper.py`
- **Self-update / rollback** - Checks GitHub releases for newer versions, downloads and applies them in place while protecting runtime data, with rollback to any previous backup snapshot. - `logic/system/updater.py`, `logic/system/backup.py`
- **Login / session auth** - Optional shared web password gates the UI and API behind a signed session cookie; off by default for trusted-network deployments. - `api/auth.py`, `core/auth.py`
- **Settings & raw config editor** - Edit all settings (folders, servers, indexers, security) through UI forms, or view/edit the raw YAML directly, with secrets masked in every API response. - `api/settings.py`, `logic/settings.py`
- **NFO / MediaInfo generation** - Generates a technical .nfo file with embedded MediaInfo output for each upload when the optional mediainfo tool is installed, used in release packaging. - `logic/pipeline/`
- **Known issues view** - A collapsible Known Issues panel on the History page groups failed indexer submissions by (indexer, normalized error signature), showing occurrence count, affected-item count, a sample message, and first/last-seen timestamps for each signature. Each issue can be muted or unmuted, backed by a MutedIssue model keyed on (indexer_id, signature), so a noisy known failure can be quieted from the default view without losing its upload history. Exposed via GET /api/uploads/errors/grouped, POST /api/uploads/errors/mute and /errors/unmute, and `--headless issues`. - `core/db/issues.py`, `core/db/models.py`, `api/history.py`, `cli/commands/history.py`, `webui/history.html`, `webui/assets/js/pages/history/known-issues.js`

**Partial - exists but incomplete, gated off, or known broken**

- **MCP endpoint for AI agents** - Exposes read-only (and, if explicitly enabled, job-control) tools over MCP at /mcp with its own bearer token; off by default and needs a manual `pip install mcp` since the SDK isn't bundled. - `api/mcp.py`

### Where to add a new one

- **a new Usenet indexer** - copy indexers/newznab.example.yaml (or _template.yaml for non-standard sites), rename it, fill in host/categories; loaded automatically by the registry. anchors: `core/indexers/registry.py`
- **a new HTTP API route** - add a handler to the router of the matching surface module in api/ (api/jobs.py, api/pending.py, api/indexers.py, etc.); app.py only includes the routers. anchors: `api/`
- **a new headless CLI command** - add a cmd_* function under cli/commands/, its subparser in cli/parser.py's build_headless_parser, and its dispatch entry in cli/run.py. anchors: `cli/commands/`, `cli/parser.py`, `cli/run.py`
- **a new WebUI page** - add a template under webui/ plus a page controller folder under webui/assets/js/pages/<page>/, extending page-base.js; serve it by adding it to the PAGES table in api/pages.py. anchors: `api/pages.py`, `webui/assets/js/page-base.js:1`
- **a new DB-backed record type** - add a SQLAlchemy model in core/db/models.py beside Upload/JobHistory/SystemStat and a matching query helper module in core/db/. anchors: `core/db/models.py`
- **a new content-classification rule** - extend the heuristics in logic/classify/ (classify_video_name_result and friends in logic/classify/names.py) or the guessit fallback in core/release_name.py. anchors: `logic/classify/names.py`

### Gaps and wants

- No per-user accounts or RBAC - one shared web password (or none at all) protects the whole UI and API.
- MCP AI-agent endpoint is off by default and requires a manual `pip install mcp`; the SDK is deliberately excluded from requirements.lock.
- No built-in TLS - README instructs operators to put it behind a reverse proxy or trusted network before exposing it beyond localhost.
- **Job-completion webhook notifications** [webhooks-out] suggested (effort M; feature) - Let an operator point a webhook (Discord/Slack/generic JSON) at job success/failure events so they don't have to keep the dashboard open to know an upload finished or an indexer rejected it. Evidence: Grepping every .py file for webhook|discord|slack|notify_url|ntfy returns zero matches anywhere in the codebase; the only recorded notification-shaped want is an in-app stats threshold check (posthog-derived alerts idea), which fires on system stats, not on job/indexer outcomes - outbound notification on job completion is a distinct, unrecorded gap for an unattended daemon/monitor tool. anchors: `core/`, `core/db/`
- **Indexer connectivity test endpoint** [plugin-system] suggested (effort S; feature) - A "Test connection" action per configured indexer that fires a lightweight auth/search call and reports success or the exact error, instead of the operator only learning a bad API key or wrong category code when a real upload fails. Evidence: api/indexers.py exposes only GET "" and GET "/{indexer_id}" (list/read) and POST "/reload"; there is no test or verify route, yet the README itself warns that indexer submission shapes differ even between two Newznab sites and tells operators to "confirm the submit endpoint, API-key parameter name, and category codes" by hand before use. anchors: `api/`, `api/`
- **Export upload history to CSV** [export-import] suggested (effort S; feature) - Add a CSV export alongside the existing JSON output for upload history, so operators can open it in a spreadsheet or archive it outside the SQLite DB. Evidence: cmd_history's --json flag is the only machine-readable output format; grepping the History page scripts (webui/assets/js/pages/history/) for export|download|csv returns no matches despite History being one of the core dashboard pages with filtering/grouping already built. anchors: `cli/commands/history.py`
- **Auto-expiring known-issue mutes** [error-tracking] suggested (effort S; tweak) - Let a muted known-issue signature auto-unmute after N days (or on next occurrence past a date), so a since-fixed recurring failure doesn't stay silently hidden forever because someone forgot to unmute it. Evidence: MutedIssue's own docstring states a signature "stays muted across every future occurrence of the same recurring failure until explicitly unmuted" - the model has no expiry/TTL field, only indexer_id + signature, so a mute is permanent by construction. anchors: `core/db/models.py`

---

_Generated by the docs tool on 2026-09-09 from a project notes stamped 2026-09-05. Regenerate after the product moves; the docs tool reports drift._
<!-- about GENERATED END sha=cff39263203f -->
