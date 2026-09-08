"""每小时采集调度入口，调用融合后的 H2 ingest main。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_crawl")

DISABLED_MARK = Path(__file__).resolve().parent.parent / "data" / "CRAWL_DISABLED"


def main() -> int:
    if DISABLED_MARK.exists():
        logger.info("发现 CRAWL_DISABLED 标记，跳过本次采集。")
        return 0

    try:
        from ai_digest.ingest import run as ingest_run
    except ImportError:
        logger.exception("无法导入 ai_digest.ingest.run，采集任务失败。")
        return 2

    return int(ingest_run.main())


if __name__ == "__main__":
    sys.exit(main())
