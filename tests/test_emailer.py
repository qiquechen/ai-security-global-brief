import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from ai_digest.deliver.emailer import send_email


class EmailerTests(unittest.TestCase):
    def test_mixed_attachments_have_correct_types_names_and_content(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "ai_digest.deliver.emailer.smtplib.SMTP_SSL"
        ) as smtp:
            report = Path(directory) / "摘要合集.docx"
            report.write_bytes(b"report content")
            archive_path = Path(directory) / "原文_20260909.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("新闻媒体/原文.docx", b"original content")
            send_email(
                "测试邮件", recipients="reader@example.com", text_body="见附件。",
                docx_path=report, attachment_paths=[report, archive_path, archive_path],
                smtp_host="smtp.example.com", smtp_port=465,
                smtp_user="sender@example.com", smtp_pass="test-password",
            )
            server = smtp.return_value.__enter__.return_value
            server.send_message.assert_called_once()
            message = server.send_message.call_args.args[0]
            attachments = list(message.iter_attachments())
            self.assertEqual(2, len(attachments))
            self.assertEqual(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                attachments[0].get_content_type(),
            )
            self.assertEqual("application/zip", attachments[1].get_content_type())
            for attachment, path in zip(attachments, [report, archive_path]):
                self.assertEqual(path.name, attachment.get_filename())
                self.assertEqual(path.read_bytes(), attachment.get_payload(decode=True))


if __name__ == "__main__":
    unittest.main()
