"""Prepare the daily bundle after the cutoff, for the later delivery task."""
from datetime import datetime
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_digest import config
from ai_digest.alerts import send_alert


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd)


if __name__ == "__main__":
    now = datetime.now(ZoneInfo(config.REPORT_TZ))
    ready = config.DATA_DIR / f"ready-daily-{now:%Y%m%d}.txt"
    try:
        # 先备份数据库（失败只告警，不阻断整理）
        backup_code = _run([sys.executable, str(config.ROOT / "scripts" / "backup_db.py")])
        if backup_code != 0:
            send_alert("数据库备份失败", f"backup_db.py 退出码 {backup_code}")

        code = _run([
            sys.executable, str(config.ROOT / "scripts" / "run_daily.py"),
            "--input", "db", "--at", now.isoformat(), "--ready-file", str(ready),
        ])
    except Exception as exc:  # noqa: BLE001
        send_alert("整理阶段异常", f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)

    if code != 0:
        send_alert(
            "整理阶段失败",
            f"run_daily 退出码={code}；就绪文件：{ready}\n请查看 logs/ 下的日志。",
        )
    raise SystemExit(code)
