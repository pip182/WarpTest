# JavaScript Execution Benchmarks

This project benchmarks a user-supplied JavaScript file across a few execution backends:

- Node CLI (`node`).
- Node HTTP server (`node_server.js`) to measure server overhead.
- Python engines: `py_mini_racer`, `jsrun`, and `js2py` when installed.

## Quick Start

```bash
python benchmark_js.py --script examples/heavy.js --iterations 3
```

Run every included workload in one shot:

```bash
./scripts/run_all_benchmarks.py --iterations 3
```

What happens:
- The script is read once and executed `iterations` times per engine.
- The Node HTTP server is started automatically (default port `3210`) and shut down after the run.
- Engines without installed dependencies are skipped with a note.
- If local networking is restricted, the Node HTTP server benchmark will be skipped; the CLI benchmark will still run.
- Example test script `examples/heavy.js` mixes math, strings, regex, arrays, JSON, and large-object manipulation to simulate varied workloads.

## Dependencies

- Node.js installed and on `PATH` (required for both Node engines).
- Optional Python packages for in-process engines:
  - `py_mini_racer`
  - `jsrun`
  - `js2py`

Install what you need, for example:

```bash
pip install py-mini-racer jsrun
```

## Usage

Key flags:
- `--script`: Path to the JavaScript file to run (required).
- `--iterations`: Number of executions per engine (default: 5).
- `--port`: Port for the Node HTTP server (default: 3210).
- `--server-path`: Path to the Node server file if you move or modify it.
- `-v/--verbose` or `BENCH_VERBOSE=1`: Enable verbose logging.
- Color output is on when stdout is a TTY; disable with `NO_COLOR=1`.

Example with a custom file and more iterations:

```bash
python benchmark_js.py --script /path/to/your/heavy.js --iterations 10
```

Results are printed with per-engine mean/median/min/max timings. Running arbitrary code is dangerous; keep tests local and on trusted scripts.

## Example workloads

- `examples/heavy.js`: mixed math, strings, regex, arrays, JSON, and large-object churn.
- `examples/numeric_loop.js`: tight arithmetic/bitwise loop for raw numeric throughput.
- `examples/json_parse.js`: repeated JSON parse/stringify on a medium payload.
- `examples/context_with.js`: nested object access with a global context and `with` scope usage.

Without `--script`, the runner will execute all scripts under `examples/` with your provided flags (e.g., `python benchmark_js.py --verbose --iterations 3`).

Each example emits a few `console.log` statements (start/finish) and their exported `benchRunners.*` functions return a `{ result, logs }` object; the Node HTTP server also returns collected logs in its JSON response.
The benchmark harness logs Node HTTP `result` values (truncated) and the number of console log lines seen; node-cli logs the first run's stdout/stderr (truncated).
Embedded engines (py-mini-racer/jsrun/js2py) wrap code to capture `console.*` output and the returned value, logging both on the first run.
When available, `jsrun` uses its `Runtime` to collect extra stats (logged alongside results).
Errors are captured too: failures log stderr/stdout where available, and wrapped engines include thrown exceptions (with stacks when present) in their captured logs.
Captured payloads from embedded engines are normalized to JSON so results/logs print clearly instead of opaque objects.
After each run, a “Captured Output Summary” shows per-engine result values and a brief log preview.
Examples now log richer progress and a summary line to make console output more useful when captured via Node HTTP or embedded engines.
