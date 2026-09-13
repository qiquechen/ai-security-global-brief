"""失败告警：把整理/发送阶段的异常或失败，用现有 SMTP 发一封简短告警邮件。

收件人优先取 .env 的 ALERT_RECIPIENT（逗号分隔），未配置时回退到 RECIPIENT。
告警本身失败只记日志，不影响主流程。
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from . import config

logger = logging.getLogger(__name__)


def _alert_recipients() -> list[str]:
    raw = os.getenv("ALERT_RECIPIENT", "").strip() or config.RECIPIENT
    return [r.strip() for r in raw.split(",") if r.strip()]


def send_alert(subject: str, body: str = "") -> bool:
    """发送告警邮件。返回是否发送成功。"""
    recipients = _alert_recipients()
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS and recipients):
        logger.warning("告警未发送（SMTP 或收件人未配置）：%s", subject)
        return False
    try:
        msg = EmailMessage()
        msg["From"] = formataddr((config.MAIL_SUBJECT_PREFIX.strip("【】"), config.SMTP_USER))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = f"【摘报告警】{subject}"
        msg.set_content(body or subject)
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)
        logger.info("已发送告警至 %s：%s", recipients, subject)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("告警邮件发送失败：%s", exc)
        return False
