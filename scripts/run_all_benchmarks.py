#!/usr/bin/env python3
"""Run all available JavaScript benchmarks in this repository."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run benchmark_js.py across multiple JavaScript files."
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of times to run each engine per script (default: 5).",
    )
    parser.add_argument(
        "--scripts",
        nargs="*",
        help=(
            "JavaScript files or directories (globs allowed). "
            "Defaults to all *.js files in examples/."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3210,
        help="Port for the Node HTTP server (default: 3210).",
    )
    parser.add_argument(
        "--server-path",
        type=Path,
        default=None,
        help="Optional path to node_server.js if moved.",
    )
    return parser.parse_args()


def expand_script_paths(entries: Iterable[str], repo_root: Path) -> List[Path]:
    scripts: list[Path] = []
    for entry in entries:
        path = Path(entry)
        candidates: list[Path]
        if path.is_dir():
            candidates = sorted(path.glob("*.js"))
        else:
            # Support glob patterns relative to repo root.
            if any(ch in entry for ch in ["*", "?", "["]):
                candidates = sorted(repo_root.glob(entry))
            else:
                candidates = [path]
        for candidate in candidates:
            if candidate.suffix == ".js" and candidate.exists():
                scripts.append(candidate.resolve())
    # Deduplicate while preserving order.
    seen = set()
    unique_scripts = []
    for script in scripts:
        if script not in seen:
            seen.add(script)
            unique_scripts.append(script)
    return unique_scripts


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    args = parse_args()

    python_executable_candidates = [
        repo_root / ".venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ]
    python_executable = next(
        (str(path) for path in python_executable_candidates if path.exists()),
        sys.executable,
    )

    if args.server_path is None:
        server_path = repo_root / "node_server.js"
    else:
        server_path = Path(args.server_path).resolve()

    if args.scripts:
        script_candidates = expand_script_paths(args.scripts, repo_root)
    else:
        script_candidates = sorted((repo_root / "examples").glob("*.js"))

    if not script_candidates:
        print("No JavaScript scripts found to benchmark.", file=sys.stderr)
        return 1

    benchmark_path = repo_root / "benchmark_js.py"
    for script in script_candidates:
        try:
            display_name = script.relative_to(repo_root)
        except ValueError:
            display_name = script
        print(f"\n=== Running benchmarks for {display_name} ===", flush=True)
        cmd = [
            python_executable,
            str(benchmark_path),
            "--script",
            str(script),
            "--iterations",
            str(args.iterations),
            "--port",
            str(args.port),
            "--server-path",
            str(server_path),
        ]
        result = subprocess.run(cmd, cwd=repo_root)
        if result.returncode != 0:
            print(
                f"Benchmark failed for {display_name} with exit code {result.returncode}.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
