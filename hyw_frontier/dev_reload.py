"""Source-only local development supervisor; no model or search requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

POLL_SECONDS = 0.5
DEBOUNCE_SECONDS = 0.8
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", ".pytest_cache", ".ruff_cache",
             "tests", "test", "dist", "out", "logs"}
SOURCE_SUFFIXES = {".py", ".md", ".json", ".html", ".css", ".js", ".mjs", ".ts", ".svg",
                   ".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".ttf", ".otf"}


def watch_roots() -> tuple[Path, ...]:
    package = Path(__file__).resolve().parent
    return package, package.parent / "md2png" / "src"


def snapshot(roots: tuple[Path, ...]) -> dict[Path, tuple[int, int]]:
    files = {}
    for root in roots:
        for directory, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
            for name in names:
                path = Path(directory) / name
                if name.startswith(".") or path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue  # Editors may atomically replace a file during this scan.
                files[path] = (stat.st_mtime_ns, stat.st_size)
    return files


def stop_child(child: subprocess.Popen | None):
    if child is None:
        return
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Only the process group created by this supervisor, never a port owner.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def serve_with_reload(home: Path | None, port: int, timeout: float) -> int:
    if port == 0:
        raise ValueError("自动重启需要固定端口；随机端口请使用 serve --no-reload --port 0")
    roots = watch_roots()
    command = [sys.executable, "-m", "hyw_frontier.cli", "--timeout", str(timeout)]
    if home is not None:
        command += ["--home", str(home.expanduser().resolve())]
    command += ["serve", "--port", str(port), "--no-reload"]
    environment = {**os.environ, "HYW_FRONTIER_RELOAD": "1"}
    stopped = Event()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda *_: stopped.set())
    child = None
    opener = build_opener(ProxyHandler({}))  # Readiness stays on loopback even with proxy env vars.
    try:
        baseline = observed = snapshot(roots)
        changed_at = None
        ready = False
        print("自动重启已启用：监听提示词、工具配置、代码和前端；保存稳定后重启并清空会话。", flush=True)
        print("监听目录：" + "、".join(str(root) for root in roots if root.exists()), flush=True)

        def start():
            return subprocess.Popen(command, env=environment, start_new_session=True)

        child = start()
        launched_at = time.monotonic()
        while not stopped.wait(POLL_SECONDS):
            current = snapshot(roots)
            now = time.monotonic()
            if current != observed:
                observed, changed_at = current, now
            if changed_at is not None and now - changed_at >= DEBOUNCE_SECONDS:
                if observed != baseline:
                    changed = [path for path in observed.keys() | baseline.keys()
                               if observed.get(path) != baseline.get(path)]
                    print("检测到文件变化，正在重启：" + "、".join(path.name for path in sorted(changed)[:5]), flush=True)
                    stop_child(child)
                    if stopped.is_set():
                        break
                    baseline = observed
                    child = start()
                    launched_at, ready = time.monotonic(), False
                changed_at = None
            if child is not None and child.poll() is not None:
                print(f"调试服务已退出（{child.returncode}）；监听继续，修复并保存文件后自动启动。", flush=True)
                child = None
            if child is not None and not ready:
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/config", timeout=1) as response:
                        config = json.load(response)
                    ready = config.get("development", {}).get("pid") == child.pid
                except (URLError, OSError, ValueError):
                    pass
                if ready:
                    print(f"自动重启服务就绪：http://127.0.0.1:{port}（/api/config 已确认，PID {child.pid}）", flush=True)
                elif time.monotonic() - launched_at > 60:
                    print("启动后60秒未通过 /api/config 就绪检查，停止本次服务；等待文件修复。", flush=True)
                    stop_child(child)
                    child = None
        return 0
    finally:
        stop_child(child)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
