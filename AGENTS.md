# Repository Guidelines

This repository is currently minimal; use the conventions below when adding code so the structure stays predictable and friendly for new contributors. Keep changes small, documented, and paired with automated checks.

## Current Project Snapshot
- Benchmarks user-supplied JavaScript across engines via `benchmark_js.py`; Node CLI and a lightweight HTTP `node_server.js` are included, Python engines (`py_mini_racer`, `jsrun`, `js2py`) are optional.
- Example workloads live in `examples/` (e.g., `context_with.js` stresses large nested globals accessed via `with`).
- Activate the local venv with `source .venv/bin/activate` (Python 3.13.7) before running scripts.
- Run a single workload with `python benchmark_js.py --script examples/context_with.js --iterations 5`; run the full set with `./scripts/run_all_benchmarks.py`.

## Project Structure & Module Organization
- Place runtime code in `src/`, split by feature or domain (e.g., `src/auth/`, `src/api/`).
- Keep tests in `tests/` mirroring the `src/` layout; add fixtures under `tests/fixtures/`.
- Store helper tooling in `scripts/` (bash or language-specific), make them executable, and document required environment variables.
- Put long-form docs in `docs/` and design assets in `assets/` or `public/`; keep generated files out of version control.

## Logging & Safety
- Python runner supports `-v/--verbose` or `BENCH_VERBOSE=1`; defaults to INFO, DEBUG when verbose.
- Node server logs requests when `VERBOSE=1`/`DEBUG=1`/`LOG_LEVEL=debug`; errors always go to stderr.
- Startup failures now tear down the Node server process to avoid leaks; benchmark harness reports script load errors clearly.
- Example workloads log start/finish messages and their `benchRunners.*` exports now return `{ result, logs }`; the Node HTTP server also echoes collected logs in responses.
- CLI output uses ANSI colors when a TTY is detected; set `NO_COLOR=1` to disable.
- The benchmark harness logs Node HTTP results (truncated) and the count of console log entries returned by the server; `node-cli` logs stdout/stderr from the first run (truncated).
- Embedded engines (py-mini-racer/jsrun/js2py) wrap JS code to capture `console.*` output and returned values, logged on first run.
- Errors are captured and logged: stdout/stderr on failures, embedded engines log thrown exceptions (with stacks when present) in captured logs.
- Embedded engine payloads are normalized to JSON so logged results/logs are human-readable (no opaque JS objects).
- Final report includes a “Captured Output Summary” showing result values and log previews per engine.
- Example scripts emit richer console output (init/start/end and per-run summaries) to make captured logs more informative in the final report.
- `jsrun` uses its `Runtime` when available, logging any runtime stats alongside results.

## Build, Test, and Development Commands
- Prefer a single entrypoint per task in `scripts/` or a `Makefile`. Suggested targets:
  - `make setup` or `./scripts/setup.sh` — install dependencies.
  - `make dev` or `./scripts/dev.sh` — run the main app locally; accept configuration via `.env`.
  - `make test` or `./scripts/test.sh` — execute the full test suite; keep fast by default and gate slow runs behind `SLOW=1`.
- If adding a new language stack, include a brief usage note in `docs/README.md` and keep commands reproducible in CI.

## Coding Style & Naming Conventions
- Follow language-standard formatters (e.g., `ruff format`/`black` for Python, `prettier` for JS/TS); declare tool versions in a `pyproject.toml` or `package.json`.
- Prefer 4-space indentation for Python, 2-space for JS/TS/JSON/YAML. Use `PascalCase` for classes/types, `snake_case` for Python modules, and `kebab-case` for CLI/script names.
- Keep functions small and side-effect-light; avoid single files exceeding ~400 lines unless clearly modularized.

## Testing Guidelines
- Mirror code structure: `tests/<area>/test_<module>.py` or `<module>.test.ts`. Co-locate lightweight unit tests; keep integration/end-to-end under `tests/integration/`.
- Aim for ≥80% branch/line coverage; add regression tests for every bug fix.
- Use deterministic data (seed random generators, freeze time) and avoid network calls by default; mock external services behind clear interfaces.

## Commit & Pull Request Guidelines
- Write imperative, concise commit subjects (e.g., `Add auth token refresh`); wrap body at ~72 characters and link issues with `Refs #123` or `Fixes #123` where relevant.
- For PRs: include a short “What/Why,” test evidence (`make test` output or screenshots for UI), and note any follow-ups or known gaps. Keep diffs focused; split refactors from feature changes when possible.

## Security & Configuration Tips
- Never commit secrets; keep local overrides in `.env.local` (gitignored) and document required variables in `.env.example`.
- Review dependencies before adding them; prefer minimal, well-maintained packages and justify new services or network integrations.
