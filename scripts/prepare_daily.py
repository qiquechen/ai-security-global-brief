"""Prepare the daily bundle after the cutoff, for the later delivery task."""
from datetime import datetime
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_digest import config

if __name__ == "__main__":
    now = datetime.now(ZoneInfo(config.REPORT_TZ))
    ready = config.DATA_DIR / f"ready-daily-{now:%Y%m%d}.txt"
    raise SystemExit(subprocess.call([
        sys.executable, str(config.ROOT / "scripts" / "run_daily.py"),
        "--input", "db", "--at", now.isoformat(), "--ready-file", str(ready),
    ]))
