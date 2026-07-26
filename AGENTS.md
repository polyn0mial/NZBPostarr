# NZBPostarr AI Contributor Guide

This file is the operating guide for AI coding agents and automated
contributors working in this repository. Human-facing installation and usage
documentation remains in `README.md`.

## Project Summary

NZBPostarr is a Python 3.12+ FastAPI application that prepares authorized
content with `rar` and `parpar`, posts it through `nyuu`, creates NZBs, and
submits them to configured indexers. The browser UI is rendered with Jinja
templates and uses bundled JavaScript and Tailwind CSS assets.

The repository intentionally uses a flat application layout. Do not recreate a
self-titled `nzbpostarr/` source directory.

## Repository Map

- `main.py` — bootstrapper and command-line entry point.
- `app.py` — FastAPI application, routes, and runtime orchestration.
- `setup.py` — interactive setup and optional Linux systemd installation.
- `version.py` — canonical application version.
- `core/` — configuration, database, registry, redaction, and shared utilities.
- `logic/` — queue, processing, posting, monitoring, statistics, and updater logic.
- `indexers/` — public YAML indexer definitions and the extension template.
- `webui/` — Jinja templates, frontend source, build scripts, and built assets.
- `tests/` — backend, integration, frontend, and release-safety tests.
- `.github/release.py` — public-tree validation and release archive builder.
- `.github/workflows/` — CI and tagged-release automation.

## Files That Are Local Runtime State

Never commit, package, overwrite, or delete these as part of a normal code
change:

- `.env`
- `.config/nzbpostarr/config.yaml`
- `data/`
- `.local/`
- `.venv/`
- `indexers/readme/readme.txt`
- credentials, API keys, private keys, cookies, databases, logs, NZBs, media,
  RAR/PAR files, deployment inventories, or server-specific scripts

Use `core/config.defaults.yaml` and
`indexers/readme/readme.example.txt` for publishable examples. Review
`.gitignore` and run the release-tree check before every public release.

## Fresh Installation

Required system tools:

- Python 3.12 or newer
- `nyuu` and `parpar`, normally installed with npm
- `rar`
- Optional: `tmux` and `mediainfo`

The normal user installation is:

```bash
git clone https://github.com/polyn0mial/nzbpostarr.git
cd nzbpostarr
python3 main.py
```

`main.py` creates `.venv`, installs the pinned Python runtime from
`requirements.lock`, and launches the first-run setup when no configuration
exists.

For an explicit Linux setup followed by a direct launch:

```bash
python3 main.py --setup
python3 main.py --direct
```

The setup wizard can install a systemd service. Configuration defaults to
`.config/nzbpostarr/config.yaml`; `NZBPOSTARR_CONFIG` may point elsewhere.
Nested settings may be supplied with `NZBP_` environment variables, for
example `NZBP_API_KEYS__GEEK`.

## Development Environment

Create and activate a virtual environment, then install the pinned runtime and
development tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install --group dev
```

PowerShell activation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install --group dev
```

Install and build the frontend separately:

```bash
cd webui
npm ci
npm run build
cd ..
```

Node.js 22 is the CI baseline. Built CSS and JavaScript under `webui/assets/`
are tracked; rebuild and commit them whenever their source changes.

## Run and Test

Launch the web application:

```bash
python main.py
```

Useful CLI smoke checks:

```bash
python main.py --headless --help
python main.py --headless stats
python main.py --headless pending
```

Run the same core checks enforced by CI:

```bash
python -m compileall -q app.py main.py setup.py version.py core logic
python .github/release.py check
ruff check app.py main.py setup.py version.py core logic .github/*.py tests
mypy
pytest tests -q
python -m pip_audit -r requirements.lock
```

Frontend checks:

```bash
cd webui
npm ci
npm run build
npm audit --audit-level=high
cd ..
git diff --exit-code -- webui/assets
```

Add regression tests for behavior changes. Do not fix a failing test by
weakening an assertion unless the product contract intentionally changed.

## Change Guidelines

- Inspect `git status` before editing and preserve unrelated user changes.
- Keep queue state transitions explicit and durably persisted.
- Treat per-indexer submission failures as isolated failures; one indexer must
  not take down the posting job or other indexers.
- Preserve completed-job history across restarts and upgrades.
- Keep credentials and credential-bearing URLs out of logs and API responses;
  use `core/redaction.py`.
- Keep filesystem scanning and large-file work away from request/event-loop
  hot paths.
- Reuse the shared configuration, registry, queue, and processing helpers
  instead of creating parallel implementations.
- Update source and built frontend assets together.
- Update `requirements.lock` deliberately and rerun both tests and dependency
  audits after dependency changes.
- Avoid adding a dependency for behavior that is small and clearer in the
  standard library; prefer a maintained package when it replaces substantial,
  security-sensitive, or protocol-heavy bespoke code.

## Deployment and Upgrade

Tagged releases are the supported deployment artifacts. For a new deployment,
download and extract the release ZIP, then run:

```bash
python3 main.py --setup
python3 main.py --direct
```

For an upgrade:

1. Pause the queue and allow active external tools to reach a safe checkpoint.
2. Back up `data/history/usenet_uploads.db` and the configuration file.
3. Stop the NZBPostarr process or systemd service.
4. Replace only tracked application files with the new release.
5. Keep `.config/`, `data/`, and the operator-owned indexer readme intact.
6. Start the service and verify the UI, queue state, database history, and
   reported revision before resuming work.

Do not add a real host, password, token, private deployment configuration, or
one operator's deployment utility to the public repository.

## Public Release Procedure

1. Set the intended version in `version.py`.
2. Run all backend and frontend checks above.
3. Validate and build the source package:

   ```bash
   python .github/release.py check
   python .github/release.py build --version X.Y.Z
   ```

4. Commit and push `main`; wait for the `Quality` workflow to pass.
5. Tag that exact commit as `vX.Y.Z` and push the tag.
6. Confirm the `Release` workflow publishes both the ZIP and its SHA-256 file.

Never move an existing public tag. Create a new version for changed release
contents.
