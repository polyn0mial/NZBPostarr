<p align="center">
  <img src="docs/images/banner.svg" alt="NZBPostarr: prepare, post and submit NZBs from one self-hosted web UI" width="100%">
</p>

<p align="center">
  <a href="https://github.com/polyn0mial/nzbpostarr/actions/workflows/quality.yml"><img src="https://img.shields.io/github/actions/workflow/status/polyn0mial/nzbpostarr/quality.yml?branch=main&label=Quality" alt="Quality workflow status"></a>
  <a href="https://github.com/polyn0mial/nzbpostarr/releases/latest"><img src="https://img.shields.io/github/v/release/polyn0mial/nzbpostarr?label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/polyn0mial/nzbpostarr" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#features">Features</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#upgrading">Upgrading</a> ·
  <a href="#faq">FAQ</a>
</p>

---

**NZBPostarr** is a self-hosted, browser-based Usenet posting manager for
content you own or are authorized to distribute. Point it at your folders and it
classifies what it finds, packs it with `rar` and `parpar`, posts it through
`nyuu`, builds the NZB and submits it to every indexer you have enabled. One
indexer failing never takes the others down with it. It can also restream an
existing NZB from one Usenet server to another without downloading it to disk.

Everything the web UI does is also available from a headless CLI, so it runs
just as well from cron or as an unattended daemon.

## Features

**Queue and staging**
- Shared upload queue with pause, resume, priority promotion (Yield), retry, and
  Stop versus Stop + Clear; queue state and Recently Finished history persist
  across restarts
- Staging for review before posting, plus Force Upload to bypass staging and
  review without losing the chosen items
- Bulk selection that honours per-folder `allow_bulk_selection`, enforced on the
  server; excluded folders stay manually selectable
- Folder monitor (`watchdog`) that queues new items once they stop changing
- Duplicate detection per indexer, and skip-file patterns per category

**Automatic classification**
- TV, movie, anime, disc, music and ebook detection from folder and file names,
  using `guessit` plus heuristics, including season and episode structure
- Nested episode layouts and DISC, ISO, BDMV and VIDEO_TS sources
- Optional anime lookup against the Jikan (MyAnimeList) API with a local cache
- Anything without evidence is `Misc`, never a guessed `Movie`; a manual
  category override is always available

**Posting with rar, parpar and nyuu**
- RAR split volumes, PAR2 recovery with `parpar`, and posting with `nyuu`
  across one or more NNTP servers
- Configurable volume size, article size, connection counts, retries and size
  limits
- Optional NFO with embedded MediaInfo output, and an optional upload
  `readme.txt`
- A process reaper that cleans up orphaned `nyuu`, `rar` and `parpar` processes

**NZB creation and per-indexer submission**
- Generated NZBs are kept in `data/nzbs/`
- Indexers are YAML definitions: no code change to add one
- Each indexer is submitted to and reported on independently, with its own
  enable, priority and backfill switches; completion history is recorded per
  destination

**NZB restreaming**
- Repost an NZB's articles server-to-server without downloading to disk
- Stream monitors watch a folder for new `.nzb` files and queue a repost job
  for each

**History, known issues and stats**
- Filterable upload history with per-indexer results
- Known Issues groups failed submissions by indexer and error signature, with
  counts, a sample message and first/last seen; noisy signatures can be muted
- Live CPU, memory, disk and network panels on the dashboard, a Stats page with
  historical charts, and `--headless stats --watch`

**Backups and updates**
- Full backups from Settings, written to `backup_folder`
- In-app updater: the dashboard shows when an update is available; install it
  from Settings or the CLI and roll back to any earlier snapshot

**Headless CLI and daemon mode**
- Every major action as a `--headless` subcommand with `--json` output
- Background daemon with start, status and stop on Windows, macOS and Linux,
  optional tmux session, and a systemd unit from the setup wizard
- Auto-bootstrap: the first run creates its own venv and installs pinned
  dependencies, then launches the setup wizard

## Screenshots

<table>
  <tr>
    <td align="center"><img src="docs/images/dashboard.png" width="420" alt="Dashboard with live system stats and the active queue"><br><sub>Dashboard</sub></td>
    <td align="center"><img src="docs/images/queue.png" width="420" alt="Queue page with pending folders, categories and staging"><br><sub>Queue</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/images/history.png" width="420" alt="History page with per-indexer results and known issues"><br><sub>History</sub></td>
    <td align="center"><img src="docs/images/settings.png" width="420" alt="Settings page with folders, servers and indexers"><br><sub>Settings</sub></td>
  </tr>
</table>

## Quick start

```bash
git clone https://github.com/polyn0mial/nzbpostarr.git
cd nzbpostarr
python3 main.py
```

The bootstrapper creates `.venv`, installs the pinned runtime from
`requirements.lock`, and launches the interactive setup wizard when no
configuration exists. Then open `http://YOUR_SERVER_IP:8000`.

The default port is `8000`. Override it with `--port 9000` or `port: 9000` in
the configuration file.

## Requirements

| Tool                       | Purpose            | Install                 |
| -------------------------- | ------------------ | ----------------------- |
| **Python 3.12+**           | Runtime            | `apt install python3`   |
| **nyuu**                   | NNTP posting       | `npm install -g nyuu`   |
| **parpar**                 | PAR2 generation    | `npm install -g parpar` |
| **rar**                    | Archive creation   | `apt install rar`       |
| **tmux** *(optional)*      | Persistent session | `apt install tmux`      |
| **mediainfo** *(optional)* | NFO generation     | `apt install mediainfo` |

Python dependencies are pinned in `requirements.lock` and installed
automatically. Development tools are in the `dev` dependency group in
`pyproject.toml` (`pip install --group dev`).

## Configuration

Settings live in the file named by `NZBPOSTARR_CONFIG` when that variable is
set, otherwise in `.config/nzbpostarr/config.yaml`. Use
`core/config.defaults.yaml` as the reference: it documents every option and
contains no real credentials. Most settings can also be edited in the web UI,
including a raw YAML editor; secrets are masked in every API response.

```yaml
base_folder: '/path/to/usenet'

folder_paths:
  - path: '${base_folder}/Movies'
    monitor: false
    allow_bulk_selection: true
  - path: '${base_folder}/TV'
    monitor: false
    allow_bulk_selection: true

nntp_servers:            # the first server is the Primary
  - name: 'Primary'
    host: 'news.example.com'
    user: 'YOUR_USERNAME'
    pass: 'YOUR_PASSWORD'
    port: 563
    ssl: true
    enabled: true
    max_connections: 20
    backbone: ['Hosted']

api_keys:
  geek: 'YOUR_NZBGEEK_API_KEY'
  planet: 'YOUR_NZBPLANET_API_KEY'
```

Per-indexer switches follow the pattern `enable_<id>`, `priority_<id>` and
`backfill_<id>`.

Nested settings can be supplied as environment variables with the `NZBP_`
prefix and `__` between levels:

```bash
export NZBP_API_KEYS__GEEK=your_key_here
```

<details>
<summary><b>Optional MCP endpoint</b></summary>

NZBPostarr can expose a [Model Context Protocol](https://modelcontextprotocol.io)
endpoint so an AI assistant can inspect the queue, read stats and (optionally)
control jobs.

It is **off by default** and the SDK is **not** part of `requirements.lock`,
because it pulls in roughly fifteen further packages that nothing else needs.
To turn it on:

```bash
pip install mcp
```

```yaml
mcp_enabled: true
mcp_token: 'a-long-random-string' # required; no token means no endpoint
mcp_allow_mutations: false # true also exposes the job-control tools
```

The endpoint is served at `/mcp` inside the normal web process, so it sees the
same live queue the UI does. It authenticates with its own bearer token rather
than the browser session cookie:

```
Authorization: Bearer a-long-random-string
```

Read-only tools: `get_status`, `list_jobs`, `get_job`, `get_job_items`,
`get_dashboard`, `get_stats`, `list_indexers`, `get_history`,
`get_recent_errors`, `get_logs`.

Additional tools when `mcp_allow_mutations` is true: `trigger_upload`,
`pause_queue`, `resume_queue`, `pause_job`, `resume_job`, `stop_job`,
`retry_job`.

API keys and NNTP credentials are never included in any tool response.

</details>

## Supported indexers

| Indexer    | ID       |
| ---------- | -------- |
| NZBGeek    | `geek`   |
| NZBPlanet  | `planet` |
| NZB.Life   | `su` (formerly NZB.su) |
| DrunkenSlug| `slug`   |
| NZBs.in    | `in`     |
| OMGwtfnzbs | `omg`    |

Indexer definitions live in `indexers/` as YAML files and are loaded
automatically.

<details>
<summary><b>Adding your own indexer</b></summary>

Most indexers run Newznab/nZEDb and share one submission shape. For those, copy
the generic profile and fill in the host and category codes:

```bash
cp indexers/newznab.example.yaml indexers/yourindexer.yaml
```

Set `profile: newznab` to opt in to the standard Newznab request parameters.
For anything that is not a plain Newznab API (header auth, a custom upload
path, curl-style submission), start from `indexers/_template.yaml`, which
documents every supported field.

Files named `*.example.yaml`, `*.template.yaml`, or starting with `_` are never
loaded as live indexers, so the bundled examples stay inert until you copy them.

Confirm the submit endpoint, API-key parameter name and category codes against
your indexer's own API documentation before use. These differ even between
sites that both run Newznab: the six bundled definitions already use three
different upload paths.

</details>

## Running as a service

To run the web UI in the background without tmux or systemd:

```bash
python3 main.py --daemon
python3 main.py --status
python3 main.py --stop
```

Daemon state and `daemon.log` live beside the active configuration file.

On Linux, production installations should prefer the systemd service the setup
wizard can create:

```bash
python3 main.py --setup
python3 main.py --direct
```

Run the service under an account with read access to your source folders and
write access to NZBPostarr's data directory.

## Upgrading

Tagged releases are the supported deployment artifacts. Configuration and
runtime data stay outside tracked source, so replacing the application files
does not erase queue history or credentials.

**In-app:** check, install or roll back from Settings, or from the CLI:

```bash
python3 main.py --headless system update check
python3 main.py --headless system update install
python3 main.py --headless system update rollback <BACKUP_ID>
```

**Manually from a release ZIP:**

1. Pause the queue and let active external tools reach a safe checkpoint.
2. Back up `data/history/usenet_uploads.db` and the configuration file.
3. Stop the NZBPostarr process or systemd service.
4. Replace only the tracked application files with the new release.
5. Keep `.config/`, `data/` and your own indexer readme intact.
6. Start the service and check the UI, queue state, history and reported
   revision before resuming work.

A paused queue is never resumed automatically after an upgrade.

## Headless CLI

<details>
<summary><b>Command reference</b></summary>

```bash
# Upload all TV content
python3 main.py --headless upload tv

# Upload 5 movies in test mode
python3 main.py --headless upload movies --limit 5 --test

# Upload everything
python3 main.py --headless upload all

# Repost/stream an NZB directly to Usenet
python3 main.py --headless stream /path/to/file.nzb

# Queue stream jobs for every NZB under a folder
python3 main.py --headless stream /path/to/folder --posting-server Primary --indexer geek

# Save a stream monitor definition for the long-running app/WebUI process
python3 main.py --headless stream /path/to/watch-folder --monitor

# Inspect saved stream monitor definitions
python3 main.py --headless stream-monitors list

# One-shot live system snapshot, or watch continuously
python3 main.py --headless stats
python3 main.py --headless stats --watch

# View pending items
python3 main.py --headless pending
python3 main.py --headless pending tv --folder /srv/incoming/tv --limit 25 --verbose

# View upload history
python3 main.py --headless history

# Failed uploads grouped into known issues (indexer + error signature)
python3 main.py --headless issues
python3 main.py --headless issues --destination nzbgeek --since-days 7

# Inspect and control the shared upload queue
python3 main.py --headless queue status
python3 main.py --headless queue pause
python3 main.py --headless queue resume
python3 main.py --headless queue clear
python3 main.py --headless queue job retry <JOB_ID>

# Tail the application log
python3 main.py --headless logs --lines 200

# Read or update a dotted configuration key
python3 main.py --headless config get host
python3 main.py --headless config set debug true

# Stop queue work or restart a launcher-managed daemon
python3 main.py --headless system stop-all
python3 main.py --headless system restart
```

</details>

Every command accepts `--json` and writes machine-readable output to stdout,
with all logging on stderr, so it pipes straight into `jq`:

```bash
python3 main.py --headless queue status --json | jq '.running[].job_id'
```

`status` exits 1 when a required external tool is missing, and `indexers` exits
1 when no indexer is enabled, so a monitoring script can check state without
parsing output.

Configuration updates report `restart_required`; restart the long-lived process
so folder watchers and collectors use the new settings. Update and rollback
commands never respawn the headless command itself. `system restart` cycles a
launcher-managed daemon and returns nonzero when an external supervisor or
operator must perform the restart.

## Data directory

Runtime state lives in `data/` (excluded from git):

| Path                             | Contents                           |
| -------------------------------- | ---------------------------------- |
| `data/history/usenet_uploads.db` | SQLite upload history database     |
| `data/nzbs/`                     | Generated NZB files                |
| `data/tmp/`                      | RAR/PAR2 workspace                 |
| `data/mediainfo/`                | Cached mediainfo NFO files         |
| `data/logs/nzbpostarr.log`       | Application log (rotated at 10 MB) |

The optional upload `readme.txt` is operator-owned runtime configuration. A
neutral `readme.example.txt` ships with the source; edits made in Settings are
excluded from version control and preserved across packaged upgrades.

## Development

Install the pinned runtime and the `dev` group, then build the frontend
(Node.js 22 is the CI baseline):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --group dev
cd webui && npm ci && npm run build && cd ..
```

Checks enforced by CI:

```bash
python -m compileall -q app.py main.py setup.py version.py api cli core logic
python .github/release.py check
ruff check app.py main.py setup.py version.py api cli core logic .github/*.py tests
mypy
pytest tests -q
python -m pip_audit -r requirements.lock

cd webui
npm run lint
npm test
npm run build
cd ..
git diff --exit-code -- webui/assets
```

Built CSS and JavaScript under `webui/assets/` are tracked; rebuild and commit
them whenever their source changes. Add a regression test for every behaviour
change. AI coding tools and automated contributors should read
[AGENTS.md](AGENTS.md) for the repository map, invariants and release
procedure.

<details>
<summary><b>Operator release smoke test</b></summary>

Automated tests use fixtures and mocked indexers. Before resuming production
work after a classifier, queue, updater or frontend change, use authorized test
content and credentials to check the live boundaries:

1. Open a real nested source in Queue and inspect active, queued and Recently
   Finished rows, category badges, ignored markers and yield controls.
2. Pause a safe job, promote another, resume the first, and confirm its prior
   progress remains. Compare Stop with Stop + Clear.
3. Confirm Force Upload bypasses staging and review without losing the chosen
   items.
4. Preview a bulk selection across allowed and excluded roots. Confirm excluded
   roots stay out of the bulk action but remain manually selectable.
5. Verify confirmed anime stays Anime in the UI and internally, negative Western
   animation stays TV, and only OMGwtfnzbs receives the submission-time TV
   mapping.
6. Exercise representative nested episode layouts plus DISC, ISO, BDMV and
   VIDEO_TS sources. Confirm extension, source, episode and ignored-item rules.
7. Confirm each enabled indexer accepts the intended category and reports its
   own result without blocking other destinations.
8. Restart or update the service, then confirm paused queue state and Recently
   Finished history survive. Resume work manually only after inspection.

For a failure, record a redacted path/name shape, configured folder category,
detector/cache outcome, UI and submission categories, affected indexers, job ID,
state transition, relevant redacted logs, and `/api/system/revision`. Fix the
generic mechanism and add a regression test; never add the title to a
production exception list.

</details>

<details>
<summary><b>Public releases</b></summary>

Every public snapshot is checked for private configuration/runtime files and
scanned for secrets before packaging:

```bash
python .github/release.py check
python .github/release.py build --version X.Y.Z
```

The builder creates a ZIP plus SHA-256 checksum under ignored `dist/`. GitHub
Actions performs the same checks and publishes those files for `v*` tags.

</details>

### Contributing

Focused pull requests are welcome. Branch from `main`, run the checks above, and
add regression tests for behaviour changes. Do not commit credentials, runtime
data, personalized readmes, generated Usenet artifacts or machine-specific
deployment configuration. Keep queue-state transitions, duplicate handling and
per-indexer failures explicit.

## FAQ

**Do I need to install Python packages myself?**
No. `python3 main.py` creates its own virtual environment and installs the
pinned dependencies on first run.

**Does NZBPostarr provide content or Usenet access?**
No. You bring your own Usenet provider, indexer accounts and material you are
allowed to distribute.

**Is duplicate detection global?**
No, it is per indexer. An item already posted to one indexer is still offered to
another indexer that has no record of it.

**What happens when one indexer rejects a submission?**
That indexer records its own failure; the posting job and the other indexers
carry on. Repeated failures show up under Known Issues on the History page.

**How long does a muted known issue stay muted?**
Until you unmute it. Mutes have no expiry.

**Can I expose it to the internet?**
There is no built-in TLS and login is off by default. Enable the built-in login
(`enable_password` and `web_password`) and put it behind a reverse proxy or
other network controls first.

## Responsible use

NZBPostarr is a general-purpose posting and automation tool. Use it only with
material you own, material in the public domain, or material you are otherwise
authorized to distribute. You are responsible for complying with applicable
copyright law, your Usenet provider's terms, and each configured indexer's
rules. The project does not provide content, access to Usenet services, or
permission to distribute third-party works.

## Security

Report vulnerabilities through GitHub private vulnerability reporting rather
than a public issue. Do not include working credentials, private NZB contents,
or server logs containing personal paths. Only the latest tagged release is
supported with security fixes. Before exposing this self-hosted application
beyond a trusted network, enable its built-in login or place it behind suitable
network controls.

## License

MIT - see [LICENSE](LICENSE).
