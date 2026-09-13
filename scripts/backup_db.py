"""备份 data/app.db 到 data/backup/，只保留最近 N 份（.env 的 BACKUP_KEEP，默认14）。

用法（venv 激活）：
  python scripts\\backup_db.py
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_digest import config


def main() -> int:
    keep = max(1, int(os.getenv("BACKUP_KEEP", "14")))
    source = config.DB_PATH
    if not source.exists():
        print(f"数据库不存在，跳过备份：{source}")
        return 0
    dest_dir = config.DATA_DIR / "backup"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest = dest_dir / f"app-{stamp}.db"
    shutil.copy2(source, dest)  # 同日重复运行则覆盖

    backups = sorted(dest_dir.glob("app-*.db"))
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    remaining = sorted(dest_dir.glob("app-*.db"))
    print(f"已备份：{source} -> {dest}；当前保留 {len(remaining)} 份（上限 {keep}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
