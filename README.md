# NZBPostarr

**NZBPostarr** is a self-hosted, browser-based Usenet posting manager for
content you own or are authorized to distribute. It wraps `nyuu`, `parpar`,
and `rar` in a web UI for preparing, posting, and submitting NZBs to configured
indexers.

---

## Features

- **Web UI**: manage uploads, view history, and monitor system stats from any browser
- **Auto-bootstrap**: runs `python main.py` and it creates its own venv and installs deps on first run
- **First-run setup wizard**: guided interactive installer for new deployments
- **Multi-indexer support**: submit NZBs to multiple indexers simultaneously via a YAML plugin system
- **Dynamic categories**: add any folder/category without touching code
- **Duplicate detection**: skips content already present on enabled indexers
- **Headless / CLI mode**: `--headless upload tv` for scripted / cron use cases
- **Direct NZB reposting**: `--headless stream /path/to/file.nzb` queues Usenet-to-Usenet stream jobs from the CLI
- **On-demand CLI stats**: `--headless stats` shows a live snapshot without running the WebUI collector
- **Folder monitor**: watches folders via `watchdog` and auto-triggers uploads on new content
- **Cross-platform daemon mode**: background start, status, and stop controls on Windows, macOS, and Linux
- **tmux integration**: offers to run inside a persistent tmux session on first launch
- **Process reaper**: cleans up orphaned nyuu/rar/parpar processes automatically

---

## Requirements

### System packages
| Tool                       | Purpose            | Install                 |
| -------------------------- | ------------------ | ----------------------- |
| **Python 3.12+**           | Runtime            | `apt install python3`   |
| **nyuu**                   | NNTP posting       | `npm install -g nyuu`   |
| **parpar**                 | PAR2 generation    | `npm install -g parpar` |
| **rar**                    | Archive creation   | `apt install rar`       |
| **tmux** *(optional)*      | Persistent session | `apt install tmux`      |
| **mediainfo** *(optional)* | NFO generation     | `apt install mediainfo` |

### Python dependencies

The exact runtime environment is pinned in `requirements.lock`; the
bootstrapper installs it automatically. Development tools are in the `dev`
dependency group in `pyproject.toml` (`pip install --group dev`).

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/polyn0mial/nzbpostarr.git
cd nzbpostarr

# 2. Run - the bootstrapper handles venv + deps automatically
python3 main.py
```

On first run with no config file present, the interactive setup wizard launches automatically and walks you through every option.

To run the WebUI in the background without tmux or systemd:

```bash
python3 main.py --daemon
python3 main.py --status
python3 main.py --stop
```

Daemon state and `daemon.log` live beside the active configuration file.
Production Linux installations should still prefer the systemd service created
by the setup wizard.

---

## Configuration

Settings live in `NZBPOSTARR_CONFIG` when that environment variable is set.
Otherwise, NZBPostarr uses `.config/nzbpostarr/config.yaml`. Use
`core/config.defaults.yaml` as a reference; it is fully documented and
contains no real credentials.

### Key settings

```yaml
base_folder: '/home/youruser/usenet'

folder_paths:
  - path: '${base_folder}/Movies'
    category: movies
  - path: '${base_folder}/TV'
    category: tv

nntp_servers:
  - name: 'Primary'
    host: 'news.yourprovider.com'
    user: 'username'
    pass: 'password'
    port: 563
    ssl: true
    enabled: true
    max_connections: 20
    backbone: ['Hosted']

api_keys:
  geek: 'YOUR_NZBGEEK_KEY'
  planet: 'YOUR_NZBPLANET_KEY'
```

Sensitive values can also be passed as environment variables using the `NZBP_` prefix:
```bash
export NZBP_API_KEYS__GEEK=your_key_here
```

---

## Indexers

Indexer definitions live in `indexers/` as YAML files. No code changes are needed
to add one.

Supported indexers included by default:
- NZBGeek (`geek`)
- NZBPlanet (`planet`)
- NZB.su (`su`)
- DrunkenSlug (`slug`)
- NZBs.in (`in`)
- OMGwtfnzbs (`omg`)

### Adding your own

Most indexers run Newznab/nZEDb and share one submission shape. For those, copy
`indexers/newznab.example.yaml`, rename it to `yourindexer.yaml`, and fill in the
host and category codes:

```bash
cp indexers/newznab.example.yaml indexers/yourindexer.yaml
```

Set `profile: newznab` to opt in explicitly to the standard Newznab request
parameters. For anything that is not a plain Newznab API (header auth, a custom
upload path, curl-style submission), start from `indexers/_template.yaml`
instead, which documents every supported field.

Files named `*.example.yaml`, `*.template.yaml`, or starting with `_` are never
loaded as live indexers, so the bundled examples stay inert until you copy them.

Confirm the submit endpoint, API-key parameter name, and category codes against
your indexer's own API documentation before use. These differ even between sites
that both run Newznab: the six bundled definitions already use three different
upload paths.

---

## Headless / CLI Mode

The full upload pipeline is available without the web UI:

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

# View a one-shot live system snapshot
python3 main.py --headless stats

# Watch live stats continuously
python3 main.py --headless stats --watch

# View pending items
python3 main.py --headless pending
python3 main.py --headless pending tv --folder /srv/incoming/tv --limit 25 --verbose

# View upload history
python3 main.py --headless history

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

# Check, install, or roll back an update
python3 main.py --headless system update check
python3 main.py --headless system update install
python3 main.py --headless system update rollback <BACKUP_ID>

# Stop queue work or restart a launcher-managed daemon
python3 main.py --headless system stop-all
python3 main.py --headless system restart
```

### Scripting

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
commands never respawn the headless command itself. `system restart` safely
cycles a launcher-managed daemon and returns nonzero when an external supervisor
or operator must perform the restart.

---

## MCP Endpoint (optional)

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

---

## Web UI

Once running, open `http://YOUR_SERVER_IP:8000` in a browser.

Default port is `8000`. Override with `--port 9000` or set `port: 9000` in `config.yaml`.

---

## Deployment

For a normal installation, extract a tagged release and run:

```bash
python3 main.py --setup
python3 main.py --direct
```

The setup wizard can create a systemd service on Linux. Run the service under
an account with read access to configured source directories and write access
to NZBPostarr’s data directory. Configuration and runtime data remain outside
tracked source, so replacing the application files does not erase queue history
or credentials.

### Operator release smoke test

Automated tests use fixtures and mocked indexers. Before resuming production
work after a classifier, queue, updater, or frontend change, use authorized test
content and credentials to check the live boundaries:

1. Open a real nested source in Queue and inspect active, queued, and Recently
   Finished rows, category badges, ignored markers, and yield controls.
2. Pause a safe job, promote another, resume the first, and confirm its prior
   progress remains. Compare Stop with Stop + Clear.
3. Confirm Force Upload bypasses staging and review without losing the chosen
   items.
4. Preview a bulk selection across allowed and excluded roots. Confirm excluded
   roots stay out of the bulk action but remain manually selectable.
5. Verify confirmed anime stays Anime in the UI and internally, negative Western
   animation stays TV, and only OMGwtfnzbs receives the submission-time TV
   mapping.
6. Exercise representative nested episode layouts plus DISC, ISO, BDMV, and
   VIDEO_TS sources. Confirm extension, source, episode, and ignored-item rules.
7. Confirm each enabled indexer accepts the intended category and reports its
   own result without blocking other destinations.
8. Restart or update the service, then confirm paused queue state and Recently
   Finished history survive. Resume work manually only after inspection.

For a failure, record a redacted path/name shape, configured folder category,
detector/cache outcome, UI and submission categories, affected indexers, job ID,
state transition, relevant redacted logs, and `/api/system/revision`. Fix the
generic mechanism and add a regression test; never add the title to a production
exception list.

## Public Releases

Every public snapshot is checked for private configuration/runtime files and
scanned for secrets before packaging:

```bash
python .github/release.py check
python .github/release.py build --version 1.0.0
```

The builder creates a ZIP plus SHA-256 checksum under ignored `dist/`. GitHub
Actions performs the same checks and publishes those files for `v*` tags.

## Contributing

Focused pull requests are welcome. Create a branch from `main`, install the
`dev` dependency group, run `npm ci` in `webui`, and add regression
tests for behavior changes. AI coding tools and automated contributors should
read [AGENTS.md](AGENTS.md) for the repository map, setup commands, invariants,
deployment procedure, and public-release checklist. Before opening a pull
request, run:

```bash
ruff check app.py main.py setup.py version.py core logic .github/*.py tests
mypy
pytest tests -q
python .github/release.py check
```

Do not commit credentials, runtime data, personalized readmes, generated Usenet
artifacts, or machine-specific deployment configuration. Keep queue-state
transitions, duplicate handling, and per-indexer failures explicit.

## Responsible Use

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

---

## Data Directory

NZBPostarr stores its runtime state in `data/` (excluded from git):

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

---

## License

MIT - see [LICENSE](LICENSE).
