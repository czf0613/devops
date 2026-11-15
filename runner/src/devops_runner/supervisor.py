"""POSIX process-group supervisor: an EOF from the worker also cancels descendants."""

import os
import signal
import subprocess
import sys
import threading
import time


def signal_group(pgid, signum):
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        if sys.platform != "darwin":
            raise
        # XNU killpg1 excludes zombies, then returns EPERM when no eligible
        # processes remain. Do not hide an actual permission failure on a live job.
        result = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == str(pgid) and not parts[1].startswith("Z"):
                raise


def main():
    if os.name == "nt":
        # The parent assigns this process to its Job Object before releasing it.
        if sys.stdin.buffer.read(1) != b"G":
            return 130
        return subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def watch_parent():
        # Only the parent owns this pipe. Build scripts receive /dev/null as stdin.
        while os.read(sys.stdin.fileno(), 1):
            pass
        stop.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    process = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, process_group=0)
    while process.poll() is None and not stop.wait(0.1):
        pass
    code = process.poll()
    # Also terminate background descendants left by a successful shell command.
    signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    signal_group(process.pid, signal.SIGKILL)
    process.wait()
    return code if code is not None else 130


if __name__ == "__main__":
    raise SystemExit(main())
