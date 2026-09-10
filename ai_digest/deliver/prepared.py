"""Immutable report bundles with verified attachment manifests."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from .emailer import send_email
from ..audit import operation


def prepare_mail(output_dir, subject, body, attachments):
    directory = Path(output_dir).resolve()
    manifest = directory / "mail_manifest.json"
    payload = dict(version=1, prepared_at=datetime.now(timezone.utc).isoformat(),
                   subject=subject, body=body, attachments=[
                       dict(path=Path(p).resolve().relative_to(directory).as_posix(),
                            sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
                       for p in attachments])
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def send_prepared(manifest_path):
    manifest, payload, attachments = load_prepared(manifest_path)
    with operation("mail.send", manifest=str(manifest)):
        send_email(payload["subject"], text_body=payload["body"], attachment_paths=attachments)


def load_prepared(manifest_path):
    """读取并校验邮件清单及附件，返回 (清单路径, 内容, 附件路径)。不发送邮件。"""
    manifest = Path(manifest_path).resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not payload.get("attachments"):
        raise ValueError("无效的邮件清单")
    attachments = []
    for entry in payload["attachments"]:
        path = (manifest.parent / entry["path"]).resolve()
        if not path.is_relative_to(manifest.parent):
            raise ValueError("附件路径越界")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"附件已变更，请重新准备：{path.name}")
        attachments.append(path)
    return manifest, payload, attachments
