"""Append-only operation events, correlated across nested stages."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import threading
import time
import uuid

from . import config

_run = ContextVar("audit_run", default=None)
_lock = threading.Lock()


def format_duration(seconds) -> str:
    """把秒数格式化为易读的中文时长，例如 1分23秒 / 12.3秒。"""
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{seconds:.1f}秒"


def event(stage, status, **details):
    now = datetime.now(timezone.utc)
    record = dict(time=now.isoformat(), run_id=_run.get(), stage=stage,
                  status=status, **details)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, (config.LOG_DIR / f"operations-{now:%Y%m%d}.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


@contextmanager
def operation(stage, **details):
    token = _run.set(_run.get() or uuid.uuid4().hex)
    started = time.monotonic()
    result = dict(details)
    try:
        event(stage, "started", **details)
        yield result
    except Exception as exc:
        event(stage, "failed", elapsed_seconds=round(time.monotonic() - started, 3),
              error_type=type(exc).__name__, **result)
        raise
    else:
        event(stage, "completed", elapsed_seconds=round(time.monotonic() - started, 3),
              **result)
    finally:
        _run.reset(token)
