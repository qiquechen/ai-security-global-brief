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
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
DEEPSEEK_REASONER_MODEL = os.getenv("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")

# ---- SMTP ----
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
RECIPIENT = os.getenv("RECIPIENT", "")
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "【AI安全治理摘报】")

# ---- 采集代理（H2）----
PROXY = os.getenv("PROXY", "")

# ---- 出报 ----
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "6"))
REPORT_TZ = os.getenv("REPORT_TZ", "Asia/Shanghai")
# DB_PATH 仅填文件名（默认 app.db），会自动放到 DATA_DIR 下
DB_PATH = DATA_DIR / os.getenv("DB_PATH", "app.db")


def has_deepseek_key() -> bool:
    return bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "sk-xxxx")


def has_smtp_config() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)
