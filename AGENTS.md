# NZBPostarr AI Contributor Guide

This file is the operating guide for AI coding agents and automated
contributors working in this repository. Human-facing installation and usage
documentation remains in `README.md`.

## Project Summary

NZBPostarr is a Python 3.12+ FastAPI application that prepares authorized
content with `rar` and `parpar`, posts it through `nyuu`, creates NZBs, and
submits them to configured indexers. The browser UI is rendered with Jinja
templates with Vue 3 page controllers and Tailwind CSS, built with npm.

The repository intentionally uses a flat application layout. Do not recreate a
self-titled `nzbpostarr/` source directory.

## Repository Map

- `main.py`: bootstrapper and command-line entry point.
- `app.py`: FastAPI composition root; it wires the routers and the runtime and
  holds no route or business logic of its own.
- `setup.py`: interactive setup and optional Linux systemd installation.
- `version.py`: canonical application version.
- `api/`: one FastAPI router per surface (jobs, pending, staging, history,
  settings, indexers, stats, stream, system, auth, pages, assets). `api/mcp.py`
  is the optional Model Context Protocol endpoint mounted at `/mcp`; it needs
  the optional `mcp` package, which is deliberately NOT in `requirements.lock`,
  and stays unmounted without it.
- `cli/`: headless CLI (`cli/commands/`), the launcher and daemon lifecycle
  (`cli/launcher/`), and the daemon client.
- `core/`: configuration and its validated writer (`core/config.py`), the
  database package (`core/db/`), the indexer registry (`core/indexers/`), auth,
  redaction, filesystem, process, path, and formatting helpers.
- `logic/jobs/`: the upload queue, job engine, executors, and the job store.
- `logic/pipeline/`: preparation, packing, posting, submission, and cleanup.
- `logic/pending/`: the pending tree, index, completion, rules, and bulk
  selection.
- `logic/classify/`: media classification and the per-scan filesystem cache.
- `logic/stream/`: Usenet-to-Usenet reposting and stream monitors.
- `logic/stats/`, `logic/system/`: statistics collection; backup, updater,
  lifecycle, and process reaper. `logic/system/removed_paths.txt` lists files a
  release deleted so the updater removes them at startup.
- `indexers/`: public YAML indexer definitions, the extension template, and the
  generic `newznab.example.yaml` profile. Files named `*.example.yaml`,
  `*.template.yaml`, or starting with `_` are never loaded as live indexers.
- `webui/`: Jinja templates, frontend source, build scripts, and built assets.
- `tests/`: backend, integration, frontend, and release-safety tests.
- `tools/classifier_benchmark.py`: measured accuracy benchmark for the media
  classifier. Run it before and after any change to the classification code and do
  not let the score drop. It reports 100.0% (44/44) today.
- `.github/release.py`: public-tree validation and release archive builder.
- `.github/workflows/`: CI and tagged-release automation.

## Files That Are Local Runtime State

Never commit, package, overwrite, or delete these as part of a normal code
change:

- `.env`
- `.config/nzbpostarr/config.yaml`
- `.config/nzbpostarr/daemon-state.json`
- `.config/nzbpostarr/daemon-lifecycle.lock`
- `.config/nzbpostarr/daemon.log`
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
python -m compileall -q app.py main.py setup.py version.py api cli core logic
python .github/release.py check
ruff check app.py main.py setup.py version.py api cli core logic .github/*.py tests
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

## Durable Architecture Decisions

- One owner per fact: every piece of state or policy has exactly one module that
  writes it; everything else reads through that owner.
- `logic/classify/` owns media classification (the per-scan filesystem cache
  token lives in `logic/classify/walk.py`). Snapshot code may add
  filesystem evidence, but the browser must display the server verdict rather
  than run a second filename classifier. Only an explicit `manual_category`
  supplied by the user may override the server result.
- An unclassified video is `Misc` with unknown confidence. Never silently turn
  missing evidence into `Movie`. Keep category, item type, confidence, method,
  and evidence from the same classification result.
- Treat guessit season and episode metadata as strong evidence only when the
  release name has a compatible episode shape. Treat its movie result as strong
  only with an independent year or collection signal.
- Never add title-specific movie, show, or anime exception tables. Correct the
  generic mechanism and add a regression case to the classifier benchmark.
- Keep OMGwtfnzbs' anime-to-TV mapping at its submission boundary. Do not convert
  anime globally for other indexers or for the UI.
- Keep one Pending/Uploads application and the flat source tree. Do not recreate
  duplicate pages, services, or a self-titled source package.
- Treat files under `webui/assets/js/pages/` as authored source and the matching
  `dist/` files as build output. Never replace the source with one giant bundle.
- Enforce bulk-selection exclusions on the server (`logic/pending/selection.py`). Manual selection remains a
  separate, explicit action.
- Preserve per-destination completion history. A folder is not complete merely
  because one destination accepted it.
- Do not interpret a database error as "not uploaded", automatically resume a
  paused queue after deployment, or drop nested search/classification data to
  reduce a payload. Shape large nested data lazily instead.
- Configuration writes must use the validated atomic writer (`core/config.py`
  `save_config`). Do not overwrite
  the active YAML file in place.
- Do not add an indexer YAML from the software name or a guessed URL. Verify the
  actual submission endpoint, authorization, fields, categories, and success
  response from that indexer's documentation or a live authorized test.
  Newznab defines retrieval, not a universal upload API.
- CURL-style submissions (OMGwtfnzbs) follow redirects like any other request
  and are judged by the final page with the indexer's normal success and
  duplicate rules. Do not reintroduce a redirect-only success verdict.
- This is a source-released application, not a Python distribution. The absence
  of a `[project]` table in `pyproject.toml` is intentional; CI enforces the
  Python 3.12 floor on 3.12 and 3.13. Revisit packaging metadata only if the app
  becomes distributable through Python packaging tools.
- Keep the optional `mcp` dependency out of `requirements.lock`, and do not bump
  `pydantic_core` independently of the `pydantic` version that pins it.
- Do not port the private archiver integration, worker routes, or hardcoded
  operator paths from another deployment into this public repository.
- One owner per responsibility. Never split a module by line count; treat
  roughly 700 lines as a soft budget per module, and split only along a
  responsibility boundary, with focused tests for the part that moves.
- Never merge obsolete private history into public history or move an existing
  public tag. Publish changed release contents under a new version.

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
