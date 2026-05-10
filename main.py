import signal
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def start_process(script_name: str, *extra_args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(BASE_DIR / script_name), *extra_args],
        cwd=str(BASE_DIR),
    )


def main() -> int:
    print("Starting sender bot (bot.py) and control panel bot (panel_bot.py)...")
    sender = start_process("bot.py", "--no-input")
    panel = start_process("panel_bot.py")
    procs = [sender, panel]

    try:
        while True:
            for p in procs:
                rc = p.poll()
                if rc is not None:
                    print(f"Process exited early (pid={p.pid}, code={rc}). Stopping all.")
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping all processes...")
        for p in procs:
            if p.poll() is None:
                p.terminate()

        deadline = time.time() + 8
        while time.time() < deadline and any(p.poll() is None for p in procs):
            time.sleep(0.2)

        for p in procs:
            if p.poll() is None:
                p.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
