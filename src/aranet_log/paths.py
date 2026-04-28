import os
import sys
from pathlib import Path


def default_db_path() -> Path:
    env = os.environ.get("ARANET_LOG_DB")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "aranet-log"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
        base = base / "aranet-log"
    return base / "readings.db"


def ensure_db_dir(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
