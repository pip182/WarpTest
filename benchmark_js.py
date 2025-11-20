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


def wrap_code_for_capture(code: str, print_result: bool = False) -> str:
    # Wrap user code to capture console output and the returned value as JSON.
    # If print_result is True, print to original console (not the wrapped one) so it's not captured in logs
    print_stmt = "if (__origConsole.log) __origConsole.log(__payload);" if print_result else ""
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
    var errorMsg = String(e);
    var stack = (e && e.stack) ? " | stack: " + e.stack : "";
    var fullError = "exception: " + errorMsg + stack;
    __logs.push(fullError);
    // Also log to original console if available
    if (__origConsole.error) {{
      __origConsole.error(fullError);
    }}
    throw e;
  }}
  var __payload;
  try {{
    __payload = JSON.stringify({{ result: __result, logs: __logs }});
  }} catch (jsonErr) {{
    var jsonErrorMsg = "JSON serialization error: " + String(jsonErr);
    __logs.push(jsonErrorMsg);
    if (__origConsole.error) {{
      __origConsole.error(jsonErrorMsg);
    }}
    __payload = JSON.stringify({{ result: String(__result), logs: __logs, error: String(jsonErr) }});
  }}
  {print_stmt}
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


def bundle_peer_runners_if_needed(code: str, script_path: Path) -> str:
    """If running context_with.js, bundle peer runner code for embedded engines that don't have require."""
    if script_path.name != "context_with.js":
        return code

    # Check if peer runner functions are already defined in the code
    # (they would be if already bundled or if running in node-cli/node-http)
    has_heavy = "function heavyWork" in code
    has_json_parse = "function jsonParseBench" in code
    has_numeric_loop = "function numericLoop" in code

    if has_heavy and has_json_parse and has_numeric_loop:
        # Already has peer runners, no need to bundle
        return code

    # Need to bundle peer runners for embedded engines
    examples_dir = script_path.parent
    peer_files = ["heavy.js", "json_parse.js", "numeric_loop.js"]
    bundled_code = code

    for peer_file in peer_files:
        peer_path = examples_dir / peer_file
        if peer_path.exists():
            peer_code = peer_path.read_text(encoding="utf-8")
            # Prepend peer runner code - they'll register on globalThis.benchRunners
            # when executed, making them available to context_with.js
            bundled_code = peer_code + "\n" + bundled_code

    return bundled_code


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
    # For context_with.js, we need to bundle peer runners since require won't work with node -e
    code = load_code(script_path)
    if script_path.name == "context_with.js":
        # Bundle peer runners for node-cli too
        code = bundle_peer_runners_if_needed(code, script_path)
    # Use print_result=True so the JSON is printed to stdout for node-cli
    wrapped_code = wrap_code_for_capture(code, print_result=True)

    def _run() -> None:
        try:
            # Use node -e to execute the wrapped code
            proc = subprocess.run(
                ["node", "-e", wrapped_code],
                check=True,
                capture_output=True,
                text=True,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()

            # Parse the JSON payload from stdout
            # The wrapped code returns JSON, which will be printed to stdout
            # Console logs will also be in stdout, so we need to find the JSON line
            payload = None
            if out:
                # Try to find JSON in the output (usually the last line)
                lines = out.splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            payload = parse_payload(json.loads(line))
                            break
                        except (json.JSONDecodeError, ValueError):
                            continue

                # If we couldn't parse JSON, log the output
                if not payload and not first["done"]:
                    logger.info(
                        "node-cli stdout (%s):\n%s",
                        f"{len(lines)} lines",
                        _summarize_text(out),
                    )

            if err:
                if not first["done"]:
                    logger.warning(
                        "node-cli stderr (%s):\n%s",
                        f"{len(err.splitlines())} lines",
                        _summarize_text(err),
                    )

            if payload:
                _run._last_payload = payload  # type: ignore[attr-defined]
                if not first["done"]:
                    logs = payload.get("logs") or []
                    logger.info(
                        "node-cli result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
            else:
                _run._last_payload = None  # type: ignore[attr-defined]

            first["done"] = True
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
            logger.error("node-cli failed with exit code %d", exc.returncode)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("node-cli unexpected error: %s", exc)
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
        logger.debug("Starting Node server: %s on port %d", self.server_path, self.port)
        self.proc = subprocess.Popen(
            ["node", str(self.server_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            wait_until = time.time() + 5
            check_count = 0
            while time.time() < wait_until:
                if self.proc.poll() is not None:
                    try:
                        stdout, stderr = self.proc.communicate(timeout=1)
                    except Exception:  # noqa: BLE001
                        stdout, stderr = b"", b""
                    stdout_msg = stdout.decode("utf-8", errors="replace").strip()
                    stderr_msg = stderr.decode("utf-8", errors="replace").strip()
                    message = stderr_msg or stdout_msg or "No error message available"
                    logger.error(
                        "Node server process exited with code %d. stdout: %s, stderr: %s",
                        self.proc.returncode,
                        stdout_msg[:500],
                        stderr_msg[:500],
                    )
                    raise RuntimeError(
                        f"Node server failed to start (exit {self.proc.returncode}). {message}"
                    )
                if self.healthy():
                    logger.debug("Node server is healthy")
                    return
                check_count += 1
                if check_count % 10 == 0:
                    # Log progress every second
                    logger.debug("Waiting for Node server to become ready... (%.1fs)", time.time() - (wait_until - 5))
                time.sleep(0.1)
            # Server didn't become healthy, but process is still running
            logger.error("Node server did not become healthy within timeout")
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
        except Exception as exc:
            # Log the exception for debugging
            if self.proc and self.proc.poll() is not None:
                # Server has died, try to get error output
                try:
                    stdout, stderr = self.proc.communicate(timeout=0.1)
                    if stderr:
                        logger.debug("Node server stderr: %s", stderr.decode("utf-8", errors="replace")[:200])
                except Exception:  # noqa: BLE001
                    pass
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
    """
    Create a runner that executes code via the Node HTTP server.

    The server must already be started before this runner is called.
    Only the HTTP request/response time is measured, not server startup.
    """
    wrapped_code = wrap_code_for_capture(code)
    payload = json.dumps({"code": wrapped_code}).encode("utf-8")
    url = f"http://127.0.0.1:{port}/run"
    headers = {"Content-Type": "application/json"}

    def _run() -> None:
        # Only measure the HTTP request/response time
        # Server is already running, so no startup overhead
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
                logs = payload_parsed.get("logs") or []
                logger.info(
                    "node-http result=%s logs=%s:\n%s",
                    _format_result(payload_parsed.get("result")),
                    len(logs),
                    _summarize_logs(logs),
                )
                # Check for errors in logs
                error_logs = [log for log in logs if (isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())) or (isinstance(log, dict) and log.get("level") in ("error", "warn"))]
                if error_logs:
                    logger.warning("node-http detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
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
                logs = payload.get("logs") or []
                logger.info(
                    "py-mini-racer result=%s logs=%s:\n%s",
                    _format_result(payload.get("result")),
                    len(logs),
                    _summarize_logs(logs),
                )
                # Check for errors in logs
                error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                if error_logs:
                    logger.warning("py-mini-racer detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                first["done"] = True
        except Exception as exc:
            logger.exception("py-mini-racer run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                    logs = payload.get("logs") or []
                    logger.info(
                        "jsrun result=%s logs=%s:\n%s",
                        _format_result(payload.get("result")),
                        len(logs),
                        _summarize_logs(logs),
                    )
                    # Check for errors in logs
                    error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                    if error_logs:
                        logger.warning("jsrun detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                    if stats is not None:
                        logger.info("jsrun runtime stats: %s", _format_result(stats))
                    first["done"] = True
            except Exception as exc:
                logger.exception("jsrun run failed: %s", exc)
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
                logs = payload.get("logs") or []
                logger.info(
                    "js2py result=%s logs=%s:\n%s",
                    _format_result(payload.get("result")),
                    len(logs),
                    _summarize_logs(logs),
                )
                # Check for errors in logs
                error_logs = [log for log in logs if isinstance(log, str) and ("error" in log.lower() or "exception" in log.lower())]
                if error_logs:
                    logger.warning("js2py detected errors in logs: %s", _summarize_logs(error_logs, max_items=3))
                first["done"] = True
        except Exception as exc:
            logger.exception("js2py run failed: %s", exc)
            raise

    return _run


def register_engines(
    script_path: Path, code: str, port: int, server_path: Path
) -> Tuple[List[Tuple[str, Callable[[], None]]], Optional[NodeServer]]:
    """
    Register all available engines and start the Node server if needed.

    The Node server is started here (before benchmarks run) and must be stopped
    by the caller after benchmarks complete. This ensures server start/stop times
    are not included in performance measurements.
    """
    engines: List[Tuple[str, Callable[[], None]]] = []
    node_server: Optional[NodeServer] = None

    # Bundle peer runners for embedded engines (they don't have require)
    bundled_code = bundle_peer_runners_if_needed(code, script_path)

    if shutil.which("node"):
        engines.append(("node-cli", node_cli_runner(script_path)))
        try:
            # Start the server BEFORE creating runners to ensure it's ready
            # Server start time is NOT included in benchmark timings
            node_server = NodeServer(server_path, port)
            logger.debug("Starting Node server before benchmarks...")
            node_server.start()  # This blocks until server is ready
            logger.debug("Node server is ready")

            # For context_with.js, bundle peer runners for node-http too since require might not work reliably
            http_code = bundle_peer_runners_if_needed(code, script_path) if script_path.name == "context_with.js" else code
            engines.append(("node-http-server", node_server_runner(port, http_code)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping Node HTTP server: %s", exc)
            node_server = None
    else:
        logger.warning("Skipping Node engines: `node` executable not found.")

    # Use bundled code for embedded engines
    mr = mini_racer_runner(bundled_code)
    if mr:
        engines.append(("py-mini-racer", mr))
    else:
        logger.info("Skipping py-mini-racer: module not installed.")

    jr = jsrun_runner(bundled_code)
    if jr:
        engines.append(("jsrun", jr))
    else:
        logger.info("Skipping jsrun: module not installed or unsupported API.")

    j2 = js2py_runner(bundled_code)
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


def verify_result(payload: Dict[str, object], script_name: str) -> Tuple[bool, List[str]]:
    """Verify that the script completed correctly based on verification data."""
    if not isinstance(payload, dict):
        return False, ["Payload is not a dictionary"]

    verification = payload.get("verification")
    if not verification:
        return True, []  # No verification data means we can't verify, but it's not an error

    issues = []

    if script_name == "numeric_loop.js":
        if isinstance(verification, dict):
            if not verification.get("completed", False):
                issues.append("Script did not complete all iterations")
            if verification.get("iterations") != verification.get("expectedIterations"):
                issues.append(
                    f"Iteration mismatch: {verification.get('iterations')} != {verification.get('expectedIterations')}"
                )

    elif script_name == "heavy.js":
        if isinstance(verification, dict):
            if not verification.get("completed", False):
                issues.append("Script did not complete")
            if verification.get("finalUserCount", 0) == 0:
                issues.append("No users in final object")
            if verification.get("lastUpdated") is None:
                issues.append("lastUpdated not set in metadata")

    elif script_name == "json_parse.js":
        if isinstance(verification, dict):
            if not verification.get("completed", False):
                issues.append("Script did not complete")
            if verification.get("finalUserCount", 0) == 0:
                issues.append("No users in final parsed object")

    elif script_name == "context_with.js":
        if isinstance(verification, dict):
            if not verification.get("completed", False):
                issues.append("Script did not complete")
            if verification.get("deepCount", 0) == 0:
                issues.append("Deep counter is zero (may indicate incomplete execution)")
            if verification.get("userCount", 0) == 0:
                issues.append("No users in context")
            if verification.get("inventoryCount", 0) == 0:
                issues.append("No inventory items in context")

    return len(issues) == 0, issues


def check_completion(payload: Dict[str, object], script_name: str) -> Tuple[bool, List[str]]:
    """Check if the script completed successfully based on logs and return value."""
    if not isinstance(payload, dict):
        return False, ["Payload is not a dictionary"]

    issues = []
    logs = payload.get("logs") or []
    result_val = payload.get("result")

    # Check for error/exception in logs
    error_found = False
    for log in logs:
        if isinstance(log, str):
            if "error:" in log.lower() or "exception:" in log.lower():
                error_found = True
                issues.append(f"Error in logs: {log[:100]}")
        elif isinstance(log, dict) and log.get("level") in ("error", "warn"):
            error_found = True
            issues.append(f"Error in logs: {log.get('message', '')[:100]}")

    # Check for completion indicators in logs based on script type
    if script_name == "heavy.js":
        has_start = any("heavy workload started" in str(log) for log in logs)
        has_finish = any("heavy workload finished" in str(log) for log in logs)
        if not has_start:
            issues.append("Missing 'started' log entry")
        if not has_finish:
            issues.append("Missing 'finished' log entry - script may have been interrupted")
        if has_start and not has_finish:
            issues.append("Script started but did not finish - possible early termination")

    elif script_name == "numeric_loop.js":
        has_start = any("numeric loop started" in str(log) for log in logs)
        has_finish = any("numeric loop finished" in str(log) for log in logs)
        if not has_start:
            issues.append("Missing 'started' log entry")
        if not has_finish:
            issues.append("Missing 'finished' log entry - script may have been interrupted")
        if has_start and not has_finish:
            issues.append("Script started but did not finish - possible early termination")

    elif script_name == "json_parse.js":
        has_start = any("json parse started" in str(log) for log in logs)
        has_finish = any("json parse finished" in str(log) for log in logs)
        if not has_start:
            issues.append("Missing 'started' log entry")
        if not has_finish:
            issues.append("Missing 'finished' log entry - script may have been interrupted")
        if has_start and not has_finish:
            issues.append("Script started but did not finish - possible early termination")

    elif script_name == "context_with.js":
        has_start = any("Context script start" in str(log) for log in logs)
        has_finish = any("Context script end" in str(log) for log in logs)
        if not has_start:
            issues.append("Missing 'start' log entry")
        if not has_finish:
            issues.append("Missing 'end' log entry - script may have been interrupted")
        if has_start and not has_finish:
            issues.append("Script started but did not finish - possible early termination")

        # Check for peer runner completion
        peer_logs_count = sum(1 for log in logs if any(peer in str(log) for peer in ["[heavy]", "[json_parse]", "[numeric_loop]"]))
        if peer_logs_count < 6:  # Should have at least start/finish for each peer
            issues.append(f"Expected more peer runner logs (found {peer_logs_count} entries)")

    # Check if result is null when it shouldn't be
    if result_val is None and logs:
        # Result can be null if the script doesn't return anything, but we should check logs
        pass  # This is okay for some scripts

    return len(issues) == 0, issues


def print_report(results: List[EngineOutcome], script_name: Optional[str] = None) -> None:
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
        verification = payload.get("verification") if isinstance(payload, dict) else None

        print(f"{Colors.GREEN}{headline}{Colors.RESET} result={_format_result(result_val)}")
        if verification:
            print(f"    verification: {_format_result(verification, max_len=200)}")
        if logs:
            preview = _summarize_logs(logs, indent="    ")
            print(f"    logs ({len(logs)}):\n{preview}")
        else:
            print("    logs: none")

    # Completion verification section
    if script_name:
        print(f"\n{Colors.CYAN}=== Completion Verification ==={Colors.RESET}")
        all_completed = True
        for outcome in results:
            if not outcome.ok:
                print(f"{Colors.RED}{outcome.name:18}{Colors.RESET} ✗ failed to run")
                all_completed = False
                continue

            payload = outcome.payload or {}
            completed, issues = check_completion(payload, script_name)
            if completed:
                print(f"{Colors.GREEN}{outcome.name:18}{Colors.RESET} ✓ completed successfully")
            elif issues:
                all_completed = False
                print(f"{Colors.YELLOW}{outcome.name:18}{Colors.RESET} ⚠ completion issues:")
                for issue in issues:
                    print(f"    - {issue}")
            else:
                print(f"{Colors.YELLOW}{outcome.name:18}{Colors.RESET} ? unable to verify completion")

    # Verification section
    if script_name:
        print(f"\n{Colors.CYAN}=== Verification Results ==={Colors.RESET}")
        all_verified = True
        for outcome in results:
            if not outcome.ok:
                continue
            payload = outcome.payload or {}
            verified, issues = verify_result(payload, script_name)
            if verified:
                print(f"{Colors.GREEN}{outcome.name:18}{Colors.RESET} ✓ verified")
            elif issues:
                all_verified = False
                print(f"{Colors.YELLOW}{outcome.name:18}{Colors.RESET} ⚠ verification issues:")
                for issue in issues:
                    print(f"    - {issue}")
            else:
                print(f"{Colors.YELLOW}{outcome.name:18}{Colors.RESET} ? no verification data")

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
            # Register engines and start server BEFORE benchmarks
            # Server start time is NOT included in measurements
            engines, node_server = register_engines(
                script, code, port=3210, server_path=repo_root / "node_server.js"
            )
            try:
                # Run benchmarks - server is already running, so no startup overhead
                results = run_benchmarks(engines, iterations=1)
                script_name = script.name if hasattr(script, "name") else str(script)
                print_report(results, script_name=script_name)
            finally:
                # Stop server AFTER benchmarks complete
                # Server stop time is NOT included in measurements
                if node_server:
                    logger.debug("Stopping Node server after benchmarks...")
                    node_server.stop()
                    logger.debug("Node server stopped")
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
            # Register engines and start server BEFORE benchmarks
            # Server start time is NOT included in measurements
            engines, node_server = register_engines(
                script, code, args.port, args.server_path
            )
            try:
                # Run benchmarks - server is already running, so no startup overhead
                results = run_benchmarks(engines, args.iterations)
                script_name = script.name if hasattr(script, "name") else str(script)
                print_report(results, script_name=script_name)
            finally:
                # Stop server AFTER benchmarks complete
                # Server stop time is NOT included in measurements
                if node_server:
                    logger.debug("Stopping Node server after benchmarks...")
                    node_server.stop()
                    logger.debug("Node server stopped")
        return exit_code
    try:
        code = load_code(args.script)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load script: %s", exc)
        return 1
    # Register engines and start server BEFORE benchmarks
    # Server start time is NOT included in measurements
    engines, node_server = register_engines(args.script, code, args.port, args.server_path)
    try:
        # Run benchmarks - server is already running, so no startup overhead
        results = run_benchmarks(engines, args.iterations)
        script_name = args.script.name if hasattr(args.script, "name") else str(args.script)
        print_report(results, script_name=script_name)
    finally:
        # Stop server AFTER benchmarks complete
        # Server stop time is NOT included in measurements
        if node_server:
            logger.debug("Stopping Node server after benchmarks...")
            node_server.stop()
            logger.debug("Node server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
