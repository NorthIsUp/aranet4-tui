import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import default_db_path

LABEL = "com.aranet-log"


def _agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "aranet-log.log"


def _resolve_program() -> str:
    # Prefer the installed console script; fall back to `python -m` invocation.
    exe = shutil.which("aranet-log")
    if exe:
        return exe
    raise SystemExit(
        "Could not find the `aranet-log` script on PATH. "
        "Install the package (e.g. `uv tool install aranet-log` or `pipx install aranet-log`) "
        "and retry."
    )


def install_launchctl(address: str, interval: int = 300, db: Path | None = None) -> None:
    if sys.platform != "darwin":
        raise SystemExit("--install-launchctl is only supported on macOS.")

    program = _resolve_program()
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    args = [program, address, "--once"]
    if db is not None:
        args += ["--db", str(db.expanduser().resolve())]

    plist = {
        "Label": LABEL,
        "ProgramArguments": args,
        "StartInterval": int(interval),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": True,
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        },
    }
    if db is not None:
        plist["EnvironmentVariables"]["ARANET_LOG_DB"] = str(db.expanduser().resolve())

    target = _agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    # If already loaded, unload first so the new plist takes effect.
    if target.exists():
        subprocess.run(["launchctl", "unload", str(target)], check=False)

    with target.open("wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "load", str(target)], check=True)
    print(f"Installed {LABEL} -> {target}")
    print(f"Logs: {log}")
    print(f"DB:   {db.expanduser().resolve() if db else default_db_path()}")


def uninstall_launchctl() -> None:
    if sys.platform != "darwin":
        raise SystemExit("--uninstall-launchctl is only supported on macOS.")
    target = _agent_path()
    if target.exists():
        subprocess.run(["launchctl", "unload", str(target)], check=False)
        target.unlink()
        print(f"Removed {target}")
    else:
        print(f"No launchd agent at {target}")
