"""全局配置：读取 .env，统一路径常量。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（ai-digest/）
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
SAMPLE_DIR = ROOT / "sample"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


_ensure_dirs()

# ---- DeepSeek ----
def _load_deepseek_key() -> str:
    direct = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if direct and direct != "sk-xxxx":
        return direct
    configured_file = os.getenv("DEEPSEEK_API_FILE", "").strip()
    candidates = [Path(configured_file).expanduser()] if configured_file else []
    # 默认支持把密钥文件放在项目目录的同级；文件不进入工程压缩包或Git仓库。
    candidates.append(ROOT.parent / "deepseek_API.txt")
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = re.search(r"sk-[A-Za-z0-9_-]{20,}", raw)
        if match:
            return match.group(0)
    return ""


DEEPSEEK_API_KEY = _load_deepseek_key()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash")
DEEPSEEK_THINKING_MODE = os.getenv("DEEPSEEK_THINKING_MODE", "disabled")
DEEPSEEK_FILTER_THINKING_MODE = os.getenv("DEEPSEEK_FILTER_THINKING_MODE", "disabled")
DEEPSEEK_SUMMARY_THINKING_MODE = os.getenv("DEEPSEEK_SUMMARY_THINKING_MODE", "disabled")
LLM_LOG_RESPONSE = os.getenv("LLM_LOG_RESPONSE", "true").strip().lower() not in {"0", "false", "no", ""}

# ---- SMTP ----
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
RECIPIENT = os.getenv("RECIPIENT", "")
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "【AI安全治理摘报】")
# 原文默认逐个发送；总数严格超过阈值时，即使关闭开关也强制打包。
MAIL_ORIGINALS_ZIP = os.getenv("MAIL_ORIGINALS_ZIP", "false").strip().lower() not in {
    "0", "false", "no", "",
}
MAIL_ORIGINALS_ZIP_THRESHOLD = int(os.getenv("MAIL_ORIGINALS_ZIP_THRESHOLD", "10"))
if MAIL_ORIGINALS_ZIP_THRESHOLD < 0:
    raise ValueError("MAIL_ORIGINALS_ZIP_THRESHOLD 必须为非负整数")

# ---- 采集网络（H2）----
# PROXY 作为旧配置名继续兼容；新配置支持主、备两条代理线路。
PROXY_PRIMARY = os.getenv("PROXY_PRIMARY", "").strip() or os.getenv("PROXY", "").strip()
PROXY_BACKUP = os.getenv("PROXY_BACKUP", "").strip()
PROXY = PROXY_PRIMARY
INGEST_CONNECTIVITY_TEST_URL = os.getenv(
    "INGEST_CONNECTIVITY_TEST_URL", "https://www.google.com/generate_204"
).strip()
INGEST_CONNECTIVITY_CHECK = os.getenv(
    "INGEST_CONNECTIVITY_CHECK", "true"
).lower() not in {"0", "false", "no"}
INGEST_WORKERS = max(1, int(os.getenv("INGEST_WORKERS", "6")))
INGEST_LNC_ENABLED = os.getenv("INGEST_LNC_ENABLED", "true").lower() not in {
    "0", "false", "no", ""
}
INGEST_LNC_START_TIMEOUT = max(10.0, float(os.getenv("INGEST_LNC_START_TIMEOUT", "45")))
INGEST_LNC_PAGE_TIMEOUT_MS = max(
    5000, int(os.getenv("INGEST_LNC_PAGE_TIMEOUT_MS", "35000"))
)
INGEST_LNC_RENDER_DELAY = max(0.0, float(os.getenv("INGEST_LNC_RENDER_DELAY", "0.5")))
INGEST_LNC_PRUNING_THRESHOLD = min(
    1.0, max(0.0, float(os.getenv("INGEST_LNC_PRUNING_THRESHOLD", "0.48")))
)
INGEST_LNC_SCAN_FULL_PAGE = os.getenv(
    "INGEST_LNC_SCAN_FULL_PAGE", "false"
).lower() not in {"0", "false", "no", ""}

# ---- 出报 ----
REJECTION_RETENTION_DAYS = int(os.getenv("REJECTION_RETENTION_DAYS", "7"))
if REJECTION_RETENTION_DAYS <= 0:
    raise ValueError("REJECTION_RETENTION_DAYS 必须为正整数")
# 每日统计窗口在06:00截止，06:05提前准备，07:00只投递。
REPORT_CUTOFF_HOUR = int(os.getenv("REPORT_CUTOFF_HOUR", os.getenv("REPORT_HOUR", "6")))
REPORT_HOUR = REPORT_CUTOFF_HOUR  # 兼容旧调用方
DELIVERY_HOUR = int(os.getenv("DELIVERY_HOUR", "7"))
REPORT_TZ = os.getenv("REPORT_TZ", "Asia/Shanghai")
# DB_PATH 仅填文件名（默认 app.db），会自动放到 DATA_DIR 下
DB_PATH = DATA_DIR / os.getenv("DB_PATH", "app.db")


def has_deepseek_key() -> bool:
    return bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "sk-xxxx")


def has_smtp_config() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)

# 分类保底与历史扩窗；机构加权只影响同重要度内排序。
REPORT_MIN_PER_GROUP = int(os.getenv("REPORT_MIN_PER_GROUP", "5"))
REPORT_MAX_EXPANSION_DAYS = int(os.getenv("REPORT_MAX_EXPANSION_DAYS", "30"))
REPORT_INSTITUTION_WEIGHT = float(os.getenv("REPORT_INSTITUTION_WEIGHT", "1.2"))
if REPORT_MIN_PER_GROUP < 0 or REPORT_MAX_EXPANSION_DAYS < 0:
    raise ValueError("材料保底数量和扩窗天数必须为非负数")
if not 1 <= REPORT_INSTITUTION_WEIGHT <= 2:
    raise ValueError("机构权重必须在1到2之间")
