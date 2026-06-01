#!/usr/bin/env python3
"""
Celery Light/Heavy monitor (queues + CPU/memory) with a simple curses TUI.

Requirements:
- redis (already in requirements.txt)
- Linux with `ps` available

Usage:
  python monitor_celery.py

Keys:
- q: quit

Environment:
- REDIS_URL (defaults to redis://redis:6379/0)
- LIGHT_QUEUE (defaults to "light")
- HEAVY_QUEUE (defaults to "heavy")
"""

import curses
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import redis

# Enable Celery inspect for active/reserved counts
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
try:
    from django.conf import settings as django_settings

    from otto.celery import app as celery_app

    REDIS_URL = django_settings.REDIS_URL
    LIGHT_QUEUE = django_settings.LIGHT_QUEUE
    HEAVY_QUEUE = django_settings.HEAVY_QUEUE
except Exception:
    celery_app = None
    REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    LIGHT_QUEUE = os.environ.get("LIGHT_QUEUE", "light")
    HEAVY_QUEUE = os.environ.get("HEAVY_QUEUE", "heavy")


@dataclass
class Proc:
    pid: int
    ppid: int
    pcpu: float
    pmem: float
    cmd: str


@dataclass
class WorkerStats:
    name: str
    master_pid: int | None
    total_cpu: float
    total_mem: float
    process_count: int


def get_redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL)


def get_queue_lengths(r: redis.Redis, queues: List[str]) -> Dict[str, int]:
    lengths: Dict[str, int] = {}
    for q in queues:
        try:
            lengths[q] = int(r.llen(q))
        except Exception:
            # In case of permission or key missing, treat as 0
            lengths[q] = 0
    return lengths


def parse_ps() -> Dict[int, Proc]:
    # pid,ppid,pcpu,pmem,command
    # Use --no-headers to simplify parsing
    out = subprocess.check_output(
        [
            "ps",
            "-eo",
            "pid,ppid,pcpu,pmem,command",
            "--no-headers",
        ],
        text=True,
    )
    procs: Dict[int, Proc] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Split first 4 columns, rest is command
        parts = line.split(maxsplit=4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pcpu = float(parts[2])
            pmem = float(parts[3])
            cmd = parts[4]
            procs[pid] = Proc(pid, ppid, pcpu, pmem, cmd)
        except ValueError:
            continue
    return procs


def find_worker_master_pids(procs: Dict[int, Proc]) -> Tuple[int | None, int | None]:
    light_master = None
    heavy_master = None
    for pid, p in procs.items():
        # Heuristics: master line contains "celery" and "worker" and queue token
        if "celery" in p.cmd and "worker" in p.cmd:
            if f" -Q {LIGHT_QUEUE}" in p.cmd or f" --queues {LIGHT_QUEUE}" in p.cmd:
                light_master = pid
            if f" -Q {HEAVY_QUEUE}" in p.cmd or f" --queues {HEAVY_QUEUE}" in p.cmd:
                heavy_master = pid
    return light_master, heavy_master


def collect_descendants(master_pid: int, procs: Dict[int, Proc]) -> List[int]:
    # Build PPID -> [pid] map
    children: Dict[int, List[int]] = {}
    for pid, p in procs.items():
        children.setdefault(p.ppid, []).append(pid)
    # BFS to collect all descendants
    result: List[int] = [master_pid]
    queue = [master_pid]
    seen = {master_pid}
    while queue:
        cur = queue.pop(0)
        for child in children.get(cur, []):
            if child not in seen:
                seen.add(child)
                result.append(child)
                queue.append(child)
    return result


def sum_worker_stats(
    name: str, master_pid: int | None, procs: Dict[int, Proc]
) -> WorkerStats:
    if master_pid is None or master_pid not in procs:
        return WorkerStats(
            name=name, master_pid=None, total_cpu=0.0, total_mem=0.0, process_count=0
        )
    pids = collect_descendants(master_pid, procs)
    total_cpu = sum(procs[pid].pcpu for pid in pids if pid in procs)
    total_mem = sum(procs[pid].pmem for pid in pids if pid in procs)
    return WorkerStats(
        name=name,
        master_pid=master_pid,
        total_cpu=total_cpu,
        total_mem=total_mem,
        process_count=len(pids),
    )


def safe_addstr(stdscr, y: int, x: int, text: str, attr: Optional[int] = None):
    try:
        h, w = stdscr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        if len(text) > max(0, w - x - 1):
            text = text[: max(0, w - x - 1)]
        if attr is None:
            stdscr.addstr(y, x, text)
        else:
            stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


_resize_pending = False


def _on_sigwinch(signum, frame):
    global _resize_pending
    _resize_pending = True


def group_workers_by_queue(
    active_queues: Dict[str, List[dict]],
) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {LIGHT_QUEUE: [], HEAVY_QUEUE: []}
    for worker, qlist in (active_queues or {}).items():
        names = {q.get("name") for q in qlist if isinstance(q, dict)}
        if LIGHT_QUEUE in names:
            groups[LIGHT_QUEUE].append(worker)
        if HEAVY_QUEUE in names:
            groups[HEAVY_QUEUE].append(worker)
    return groups


def count_active_reserved(i, workers: List[str]) -> Tuple[int, int]:
    active = i.active() or {}
    reserved = i.reserved() or {}
    a = sum(len(active.get(w, [])) for w in workers)
    r = sum(len(reserved.get(w, [])) for w in workers)
    return a, r


def draw_screen(stdscr, r: redis.Redis, refresh_sec: float = 1.0):
    global _resize_pending
    curses.curs_set(0)
    stdscr.nodelay(True)
    signal.signal(signal.SIGWINCH, _on_sigwinch)
    start_ts = time.time()

    while True:
        # Handle keypress
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break

        # Handle resize
        if _resize_pending:
            _resize_pending = False
            try:
                h, w = stdscr.getmaxyx()
                curses.resizeterm(h, w)
                stdscr.erase()
            except curses.error:
                pass

        # Gather data
        queues = [LIGHT_QUEUE, HEAVY_QUEUE]
        lengths = get_queue_lengths(r, queues)
        procs = parse_ps()
        light_master, heavy_master = find_worker_master_pids(procs)
        light_stats = sum_worker_stats("light", light_master, procs)
        heavy_stats = sum_worker_stats("heavy", heavy_master, procs)

        # Render
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title = "Celery monitor (q to quit)"
        safe_addstr(stdscr, 0, 2, title, curses.A_BOLD)
        safe_addstr(stdscr, 1, 2, f"Redis: {REDIS_URL}")

        # Queues
        safe_addstr(stdscr, 3, 2, "Queues:", curses.A_UNDERLINE)
        safe_addstr(
            stdscr, 4, 4, f"{LIGHT_QUEUE:<8} pending: {lengths.get(LIGHT_QUEUE, 0):>6}"
        )
        safe_addstr(
            stdscr, 5, 4, f"{HEAVY_QUEUE:<8} pending: {lengths.get(HEAVY_QUEUE, 0):>6}"
        )

        # Workers summary
        safe_addstr(stdscr, 7, 2, "Workers:", curses.A_UNDERLINE)
        # Celery inspect for active/reserved per group
        light_ar = (0, 0)
        heavy_ar = (0, 0)
        if celery_app is not None:
            try:
                i = celery_app.control.inspect(timeout=1.0)
                groups = group_workers_by_queue(i.active_queues() or {})
                light_ar = count_active_reserved(i, groups.get(LIGHT_QUEUE, []))
                heavy_ar = count_active_reserved(i, groups.get(HEAVY_QUEUE, []))
            except Exception:
                pass
        safe_addstr(
            stdscr,
            8,
            4,
            f"light  pid: {str(light_stats.master_pid):>6}  cpu%: {light_stats.total_cpu:6.2f}  mem%: {light_stats.total_mem:6.2f}  procs: {light_stats.process_count:3}  active: {light_ar[0]:2}  reserved: {light_ar[1]:2}",
        )
        safe_addstr(
            stdscr,
            9,
            4,
            f"heavy  pid: {str(heavy_stats.master_pid):>6}  cpu%: {heavy_stats.total_cpu:6.2f}  mem%: {heavy_stats.total_mem:6.2f}  procs: {heavy_stats.process_count:3}  active: {heavy_ar[0]:2}  reserved: {heavy_ar[1]:2}",
        )

        # Footer
        elapsed = time.time() - start_ts
        safe_addstr(
            stdscr,
            h - 2,
            2,
            f"Updated: {time.strftime('%H:%M:%S')}  (uptime {int(elapsed)}s)",
        )
        safe_addstr(
            stdscr,
            h - 1,
            2,
            "Tip: workers must include -Q light/heavy for detection.",
        )

        stdscr.refresh()
        time.sleep(refresh_sec)


def main():
    # Make Ctrl-C exit cleanly
    signal.signal(signal.SIGINT, lambda s, f: exit(0))

    r = get_redis_client()
    curses.wrapper(lambda stdscr: draw_screen(stdscr, r))


if __name__ == "__main__":
    main()
