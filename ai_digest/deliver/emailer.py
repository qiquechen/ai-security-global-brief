"""SMTP 发信（支持附件）。"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Iterable

from .. import config

logger = logging.getLogger(__name__)


class MailError(RuntimeError):
    pass


def send_email(subject: str, recipients: str | list[str] | None = None,
               text_body: str = "", docx_path: str | Path | None = None,
               attachment_paths: Iterable[str | Path] | None = None,
               smtp_host: str | None = None, smtp_port: int | None = None,
               smtp_user: str | None = None, smtp_pass: str | None = None) -> None:
    """发送邮件。docx_path 存在则作为附件附上。"""
    host = smtp_host or config.SMTP_HOST
    port = smtp_port or config.SMTP_PORT
    user = smtp_user or config.SMTP_USER
    pwd = smtp_pass or config.SMTP_PASS
    if not (host and user and pwd):
        raise MailError("SMTP 配置不完整（请填 .env 的 SMTP_*）")

    if recipients is None:
        recipients = config.RECIPIENT
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    if not recipients:
        raise MailError("未配置收件人 RECIPIENT")

    msg = EmailMessage()
    msg["From"] = formataddr((config.MAIL_SUBJECT_PREFIX.strip("【】"), user))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text_body or "见附件。")

    attachments = [Path(path) for path in (attachment_paths or [])]
    if docx_path:
        attachments.insert(0, Path(docx_path))
    seen: set[Path] = set()
    for attachment in attachments:
        if attachment in seen:
            continue
        seen.add(attachment)
        if not attachment.exists():
            raise MailError(f"附件不存在：{attachment}")
        # 固定报告附件的标准类型，避免 Windows 注册表覆盖 MIME 映射。
        content_type = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".zip": "application/zip",
        }.get(attachment.suffix.lower())
        if content_type is None:
            content_type = mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        msg.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
        )

    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(user, pwd)
        server.send_message(msg)
    logger.info("已发送邮件至 %s", recipients)
