"""Send an explicitly selected ready batch; never generate at delivery time."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_digest import config
from ai_digest.alerts import send_alert
from ai_digest.audit import format_duration, operation
from ai_digest.deliver.prepared import load_prepared, send_prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready-file", type=Path,
                        help="就绪指针文件；默认取当天的 data/ready-daily-YYYYMMDD.txt")
    parser.add_argument("--check", action="store_true",
                        help="只校验清单和附件，不发送邮件（用于发送前预检）")
    args = parser.parse_args()
    ready = args.ready_file or config.DATA_DIR / (
        "ready-daily-" + datetime.now(ZoneInfo(config.REPORT_TZ)).strftime("%Y%m%d") + ".txt")
    if args.check:
        with operation("delivery.check", ready_file=str(ready)):
            _manifest_path(ready)
            _, payload, attachments = load_prepared(ready.read_text(encoding="utf-8").strip())
            print(f"主题：{payload['subject']}")
            print(f"正文：{payload['body']}")
            print(f"准备时间（UTC）：{payload.get('prepared_at', '未记录')}")
            print(f"附件{len(attachments)}份：")
            for path in attachments:
                print(f"- {path}（{path.stat().st_size / 1024:.1f} KB）")
        return 0
    with operation("delivery.command", ready_file=str(ready)):
        _manifest_path(ready)
        started = time.monotonic()
        send_prepared(ready.read_text(encoding="utf-8").strip())
        print(f"发送完成，耗时：{format_duration(time.monotonic() - started)}")


def _manifest_path(ready: Path) -> Path:
    if not ready.is_file():
        raise FileNotFoundError(f"本期邮件尚未准备完成：{ready}；请检查准备日志")
    return ready


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"发送失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        send_alert("发送阶段失败", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
