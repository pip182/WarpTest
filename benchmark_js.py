#!/usr/bin/env python3
"""Benchmark a user-provided JavaScript file across multiple Python and Node execution engines."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


logger = logging.getLogger("bench")


class Colors:
    _enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    GREEN = "\033[92m" if _enabled else ""
    RED = "\033[91m" if _enabled else ""
    CYAN = "\033[96m" if _enabled else ""
    YELLOW = "\033[93m" if _enabled else ""
    RESET = "\033[0m" if _enabled else ""


def _format_result(value: object, max_len: int = 400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _summarize_text(
    text: str,
    max_lines: Optional[int] = None,
    max_len: Optional[int] = None,
    indent: str = "  ",
) -> str:
    if not text:
        return "no output"
    lines = text.splitlines()
    total = len(lines)
    preview = lines if max_lines is None else lines[:max_lines]
    body = "\n".join(f"{indent}{line}" for line in preview)
    if max_len is not None and len(body) > max_len:
        body = body[: max_len - 3] + "..."
    suffix = ""
    if max_lines is not None and total > max_lines:
        suffix = f"\n{indent}... (truncated, {total} lines)"
    return f"{body}{suffix}"


def _summarize_logs(
    logs: object,
    max_items: Optional[int] = None,
    max_len: Optional[int] = None,
    indent: str = "  ",
) -> str:
    if not isinstance(logs, list) or not logs:
        return "no logs"
    items = []
    for entry in (logs if max_items is None else logs[:max_items]):
        if isinstance(entry, dict):
            msg = entry.get("message")
            level = entry.get("level", "log")
            if msg is None:
                continue
            items.append(f"{level}: {msg}")
        else:
            items.append(str(entry))
    body = "\n".join(f"{indent}{line}" for line in items)
    if max_len is not None and len(body) > max_len:
        body = body[: max_len - 3] + "..."
    suffix = ""
    if max_items is not None and len(logs) > max_items:
        suffix = f"\n{indent}... (truncated, {len(logs)} entries)"
    return f"{body}{suffix}"


def wrap_code_for_capture(code: str) -> str:
    # Wrap user code to capture console output and the returned value as JSON.
    return f"""
(function() {{
  var __logs = [];
  var __origConsole = (typeof console !== "undefined") ? console : {{}};
  function __log() {{
    var msg = Array.prototype.map.call(arguments, String).join(" ");
    __logs.push(msg);
    if (__origConsole.log) __origConsole.log.apply(__origConsole, arguments);
  }}
  function __warn() {{
    var msg = Array.prototype.map.call(arguments, String).join(" ");
    __logs.push("warn: " + msg);
    if (__origConsole.warn) __origConsole.warn.apply(__origConsole, arguments);
  }}
  function __error() {{
    var msg = Array.prototype.map.call(arguments, String).join(" ");
    __logs.push("error: " + msg);
    if (__origConsole.error) __origConsole.error.apply(__origConsole, arguments);
  }}
  console = {{ log: __log, info: __log, warn: __warn, error: __error }};
  var __result = null;
  try {{
    __result = (function() {{ {code} }})();
  }} catch (e) {{
    var stack = (e && e.stack) ? " | stack: " + e.stack : "";
    __logs.push("exception: " + e + stack);
    throw e;
  }}
  var __payload;
  try {{
    __payload = JSON.stringify({{ result: __result, logs: __logs }});
  }} catch (jsonErr) {{
    __payload = JSON.stringify({{ result: String(__result), logs: __logs, error: String(jsonErr) }});
  }}
  return __payload;
}})();
""".strip()


def parse_payload(value: object) -> Dict[str, object]:
    if value is None:
        return {"result": None, "logs": []}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return {"result": value, "logs": []}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:  # noqa: BLE001
            return {"result": str(value), "logs": []}
    return {"result": value, "logs": []}


def extract_jsrun_stats(runtime: object) -> Optional[object]:
    if runtime is None:
        return None
    try:
        if hasattr(runtime, "get_stats") and callable(getattr(runtime, "get_stats")):
            return runtime.get_stats()
        if hasattr(runtime, "stats"):
            return runtime.stats
        if hasattr(runtime, "get_profile") and callable(getattr(runtime, "get_profile")):
            return runtime.get_profile()
    except Exception:  # noqa: BLE001
        return None
    return None


@dataclass
class EngineOutcome:
    name: str
    timings: List[float]
    error: Optional[str] = None
    payload: Optional[Dict[str, object]] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def load_code(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")
    return path.read_text(encoding="utf-8")


def time_runs(runner: Callable[[], None], iterations: int) -> List[float]:
    durations: List[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        runner()
        durations.append(time.perf_counter() - start)
    return durations


def summarize_timings(timings: List[float]) -> str:
    median = statistics.median(timings)
    mean = statistics.mean(timings)
    fastest = min(timings)
    slowest = max(timings)
    return f"mean={mean:.4f}s median={median:.4f}s min={fastest:.4f}s max={slowest:.4f}s"


def node_cli_runner(script_path: Path) -> Callable[[], None]:
    first = {"done": False}

    def _run() -> None:
        try:
            if not first["done"]:
                proc = subprocess.run(
                    ["node", str(script_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                out = (proc.stdout or "").strip()
                err = (proc.stderr or "").strip()
                if out:
                    logger.info(
                        "node-cli stdout (%s):\n%s",
                        f"{len(out.splitlines())} lines",
                        _summarize_text(out),
                    )
                if err:
                    logger.warning(
                        "node-cli stderr (%s):\n%s",
                        f"{len(err.splitlines())} lines",
                        _summarize_text(err),
                    )
                first["done"] = True
                return

            subprocess.run(
                ["node", str(script_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _run._last_payload = None  # type: ignore[attr-defined]
        except subprocess.CalledProcessError as exc:  # noqa: BLE001
            out = (exc.stdout or "").strip()
            err = (exc.stderr or "").strip()
            if out:
                logger.error(
                    "node-cli stdout on error (%s):\n%s",
                    f"{len(out.splitlines())} lines",
                    _summarize_text(out),
                )
            if err:
                logger.error(
                    "node-cli stderr on error (%s):\n%s",
                    f"{len(err.splitlines())} lines",
                    _summarize_text(err),
                )
            raise

    return _run


class NodeServer:
    def __init__(self, server_path: Path, port: int) -> None:
        self.port = port
        self.proc: Optional[subprocess.Popen] = None
        self.server_path = server_path

    def start(self) -> None:
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        self.proc = subprocess.Popen(
            ["node", str(self.server_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            wait_until = time.time() + 5
            while time.time() < wait_until:
                if self.proc.poll() is not None:
                    try:
                        stdout, stderr = self.proc.communicate(timeout=1)
                    except Exception:  # noqa: BLE001
                        stdout, stderr = b"", b""
                    message = stderr.decode().strip() or stdout.decode().strip()
                    raise RuntimeError(
                        f"Node server failed to start (exit {self.proc.returncode}). {message}"
                    )
                if self.healthy():
                    return
                time.sleep(0.1)
            raise RuntimeError("Timed out waiting for Node server to become ready.")
        except Exception:
            logger.exception("Node server failed to start.")
            # Ensure we do not leak a running Node process on startup failure.
            self.stop()
            raise

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/health", timeout=0.5
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            # Drain pipes to avoid zombies on some platforms.
            self.proc.communicate(timeout=1)
        except Exception:
            pass
        self.proc = None


def node_server_runner(port: int, code: str) -> Callable[[], None]:
    wrapped_code = wrap_code_for_capture(code)
    payload = json.dumps({"code": wrapped_code}).encode("utf-8")
    url = f"http://127.0.0.1:{port}/run"
    headers = {"Content-Type": "application/json"}

    def _run() -> None:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Node server responded with HTTP {resp.status}")
                data = resp.read()
                try:
                    parsed = json.loads(data.decode("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"Invalid JSON from server: {exc}") from exc
                if not parsed.get("ok", False):
                    logs = parsed.get("logs") or []
                    logger.error(
                        "node-http error=%s logs=%s:\n%s",
                        _format_result(parsed.get("error")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    raise RuntimeError(f"Node server error: {parsed.get('error')}")
                payload_parsed = parse_payload(parsed.get("result"))
                _run._last_payload = payload_parsed  # type: ignore[attr-defined]
                logger.info(
                    "node-http result=%s logs=%s:\n%s",
                    _format_result(payload_parsed.get("result")),
                    len(payload_parsed.get("logs") or []),
                    _summarize_logs(payload_parsed.get("logs")),
                )
        except Exception:
            logger.exception("node-http run failed")
            raise

    return _run


def mini_racer_runner(code: str) -> Optional[Callable[[], None]]:
    try:
        from py_mini_racer import py_mini_racer
    except ImportError:
        return None

    ctx = py_mini_racer.MiniRacer()
    wrapped = wrap_code_for_capture(code)
    first = {"done": False}

    def _run() -> None:
        try:
            payload_raw = ctx.eval(wrapped)
            payload = parse_payload(payload_raw)
            _run._last_payload = payload  # type: ignore[attr-defined]
            if not first["done"]:
                logger.info(
                    "py-mini-racer result=%s logs=%s:\n%s",
                    _format_result(payload.get("result")),
                    len(payload.get("logs") or []),
                    _summarize_logs(payload.get("logs") or []),
                )
                first["done"] = True
        except Exception:
            logger.exception("py-mini-racer run failed")
            raise

    return _run


def jsrun_runner(code: str) -> Optional[Callable[[], None]]:
    module = importlib.util.find_spec("jsrun")
    if module is None:
        return None
    jsrun = importlib.import_module("jsrun")
    wrapped = wrap_code_for_capture(code)
    first = {"done": False}
    runner: Optional[Callable[[], None]] = None
    if hasattr(jsrun, "Runtime"):
        runtime = jsrun.Runtime()

        def _run() -> None:
            try:
                payload = parse_payload(runtime.eval(wrapped))
                stats = extract_jsrun_stats(runtime)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run
        return runner

    if hasattr(jsrun, "JavaScript"):
        context = jsrun.JavaScript()

        def _run() -> None:
            try:
                payload = parse_payload(context.eval(wrapped))
                stats = extract_jsrun_stats(context)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run
    elif hasattr(jsrun, "Function"):
        # Try to use jsrun.Function if present (for environments exposing built-in 'Function')
        context = jsrun.Function()

        def _run() -> None:
            try:
                payload = parse_payload(context.eval(wrapped))
                stats = extract_jsrun_stats(context)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run
    elif hasattr(jsrun, "eval_js"):

        def _run() -> None:
            try:
                payload = parse_payload(jsrun.eval_js(wrapped))
                stats = extract_jsrun_stats(jsrun)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run
    elif hasattr(jsrun, "run_string"):

        def _run() -> None:
            try:
                payload = parse_payload(jsrun.run_string(wrapped))
                stats = extract_jsrun_stats(jsrun)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run
    elif hasattr(jsrun, "eval"):  # Modern jsrun API

        def _run() -> None:
            try:
                payload = parse_payload(jsrun.eval(wrapped))
                stats = extract_jsrun_stats(jsrun)
                if stats is not None:
                    payload["runtime_stats"] = stats
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(payload.get("logs") or []),
                        _summarize_logs(payload.get("logs") or []),
                    )
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception:
                logger.exception("jsrun run failed")
                raise

        runner = _run

    return runner


def js2py_runner(code: str) -> Optional[Callable[[], None]]:
    try:
        import js2py
    except ImportError:
        return None
    wrapped = wrap_code_for_capture(code)
    first = {"done": False}

    def _run() -> None:
        try:
            payload = parse_payload(js2py.eval_js(wrapped))
            _run._last_payload = payload  # type: ignore[attr-defined]
            if not first["done"]:
                logger.info(
                    "js2py result=%s logs=%s:\n%s",
                    _format_result(payload.get("result")),
                    len(payload.get("logs") or []),
                    _summarize_logs(payload.get("logs") or []),
                )
                first["done"] = True
        except Exception:
            logger.exception("js2py run failed")
            raise

    return _run


def register_engines(
    script_path: Path, code: str, port: int, server_path: Path
) -> Tuple[List[Tuple[str, Callable[[], None]]], Optional[NodeServer]]:
    engines: List[Tuple[str, Callable[[], None]]] = []
    node_server: Optional[NodeServer] = None

    if shutil.which("node"):
        engines.append(("node-cli", node_cli_runner(script_path)))
        try:
            node_server = NodeServer(server_path, port)
            node_server.start()
            engines.append(("node-http-server", node_server_runner(port, code)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping Node HTTP server: %s", exc)
            node_server = None
    else:
        logger.warning("Skipping Node engines: `node` executable not found.")

    mr = mini_racer_runner(code)
    if mr:
        engines.append(("py-mini-racer", mr))
    else:
        logger.info("Skipping py-mini-racer: module not installed.")

    jr = jsrun_runner(code)
    if jr:
        engines.append(("jsrun", jr))
    else:
        logger.info("Skipping jsrun: module not installed or unsupported API.")

    j2 = js2py_runner(code)
    if j2:
        engines.append(("js2py", j2))
    else:
        logger.info("Skipping js2py: module not installed.")

    return engines, node_server


def run_benchmarks(
    engines: List[Tuple[str, Callable[[], None]]], iterations: int
) -> List[EngineOutcome]:
    results: List[EngineOutcome] = []
    for name, runner in engines:
        logger.info("Running %s for %s iteration(s)...", name, iterations)
        try:
            timings = time_runs(runner, iterations)
            payload = runner.__dict__.get("_last_payload") if hasattr(runner, "__dict__") else None
            results.append(EngineOutcome(name=name, timings=timings, payload=payload))
        except Exception as exc:  # noqa: BLE001
            results.append(EngineOutcome(name=name, timings=[], error=str(exc)))
    return results


def print_report(results: List[EngineOutcome]) -> None:
    print(f"\n{Colors.CYAN}=== Benchmark Results ==={Colors.RESET}")
    for outcome in results:
        if outcome.ok:
            summary = summarize_timings(outcome.timings)
            print(f"{Colors.GREEN}{outcome.name:18}{Colors.RESET} {summary}")
        else:
            print(f"{Colors.RED}{outcome.name:18} failed:{Colors.RESET} {outcome.error}")

    print(f"\n{Colors.CYAN}=== Captured Output Summary ==={Colors.RESET}")
    for outcome in results:
        headline = f"{outcome.name:18}"
        if not outcome.ok:
            print(f"{Colors.RED}{headline}{Colors.RESET} failed (no payload)")
            continue
        payload = outcome.payload or {}
        logs = payload.get("logs") if isinstance(payload, dict) else None
        result_val = payload.get("result") if isinstance(payload, dict) else None
        print(f"{Colors.GREEN}{headline}{Colors.RESET} result={_format_result(result_val)}")
        if logs:
            preview = _summarize_logs(logs, indent="    ")
            print(f"    logs ({len(logs)}):\n{preview}")
        else:
            print("    logs: none")

    ok_results = [r for r in results if r.ok and r.timings]
    if ok_results:
        fastest = min(ok_results, key=lambda r: statistics.mean(r.timings))
        slowest = max(ok_results, key=lambda r: statistics.mean(r.timings))
        print(f"\n{Colors.CYAN}=== Performance Summary ==={Colors.RESET}")
        print(
            f"Fastest: {Colors.GREEN}{fastest.name}{Colors.RESET} "
            f"(mean {statistics.mean(fastest.timings):.4f}s, min {min(fastest.timings):.4f}s)"
        )
        print(
            f"Slowest: {Colors.YELLOW}{slowest.name}{Colors.RESET} "
            f"(mean {statistics.mean(slowest.timings):.4f}s, max {max(slowest.timings):.4f}s)"
        )
        if fastest is not slowest:
            ratio = statistics.mean(slowest.timings) / statistics.mean(fastest.timings)
            print(f"Slowest/fastest mean ratio: {ratio:.2f}x")
    print()


def parse_args(argv: List[str]) -> argparse.Namespace:
    default_verbose = os.environ.get("BENCH_VERBOSE", "").lower() in {"1", "true", "yes"}
    parser = argparse.ArgumentParser(
        description="Run a user-provided JS file across multiple execution engines."
    )
    parser.add_argument(
        "--script",
        type=Path,
        help="Path to the JavaScript file to execute.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of times to run each engine (default: 5).",
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
        default=Path(__file__).with_name("node_server.js"),
        help="Path to the Node server file.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default_verbose,
        help="Enable verbose logging (or set BENCH_VERBOSE=1).",
    )
    return parser.parse_args(argv)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logger.debug("Logging initialized at level %s", logging.getLevelName(level))


def main(argv: List[str]) -> int:
    if not argv:
        setup_logging(verbose=True)
        repo_root = Path(__file__).resolve().parent
        examples_dir = repo_root / "examples"
        scripts = sorted(examples_dir.glob("*.js"))
        if not scripts:
            print("No example scripts found to run.", file=sys.stderr)
            return 1
        logger.info(
            "No arguments provided; running all example scripts in %s with 1 iteration each.",
            examples_dir,
        )
        for script in scripts:
            try:
                display_name = script.relative_to(repo_root)
            except ValueError:
                display_name = script
            logger.info("=== Running benchmarks for %s ===", display_name)
            code = load_code(script)
            engines, node_server = register_engines(
                script, code, port=3210, server_path=repo_root / "node_server.js"
            )
            try:
                results = run_benchmarks(engines, iterations=1)
                print_report(results)
            finally:
                if node_server:
                    node_server.stop()
        return 0

    args = parse_args(argv)
    setup_logging(verbose=args.verbose)
    if not args.script:
        logger.info("No --script provided; defaulting to examples/*.js with provided flags.")
        repo_root = Path(__file__).resolve().parent
        examples_dir = repo_root / "examples"
        scripts = sorted(examples_dir.glob("*.js"))
        if not scripts:
            logger.error("No example scripts found in %s", examples_dir)
            return 1
        exit_code = 0
        for script in scripts:
            try:
                display_name = script.relative_to(repo_root)
            except ValueError:
                display_name = script
            logger.info("=== Running benchmarks for %s ===", display_name)
            try:
                code = load_code(script)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load script %s: %s", script, exc)
                exit_code = 1
                continue
            engines, node_server = register_engines(
                script, code, args.port, args.server_path
            )
            try:
                results = run_benchmarks(engines, args.iterations)
                print_report(results)
            finally:
                if node_server:
                    node_server.stop()
        return exit_code
    try:
        code = load_code(args.script)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load script: %s", exc)
        return 1
    engines, node_server = register_engines(args.script, code, args.port, args.server_path)
    try:
        results = run_benchmarks(engines, args.iterations)
        print_report(results)
    finally:
        if node_server:
            node_server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
