import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

from app.extraction import EmailAttachment, EmailInput

MAX_ATTACHMENTS = 10


@dataclass
class ParsedEmail:
    message_id: str | None
    content_hash: str
    from_email: str
    from_name: str | None
    subject: str
    body_text: str
    received_at: datetime | None
    attachments: list[EmailAttachment] = field(default_factory=list)

    def to_input(self) -> EmailInput:
        return EmailInput(
            from_email=self.from_email,
            from_name=self.from_name,
            subject=self.subject,
            body_text=self.body_text,
            received_at=self.received_at or datetime.now().astimezone(),
            attachments=self.attachments,
        )


def parse_eml(raw: bytes) -> ParsedEmail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    from_name, from_email = parseaddr(str(msg.get("From", "")))
    body = msg.get_body(preferencelist=("plain",))
    attachments = [
        EmailAttachment(part.get_filename() or "attachment", part.get_content_type(),
                        part.get_payload(decode=True) or b"")
        for part in msg.iter_attachments()
    ][:MAX_ATTACHMENTS]
    try:
        received_at = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        received_at = None
    return ParsedEmail(
        message_id=str(msg["Message-ID"]) if msg["Message-ID"] else None,
        content_hash=hashlib.sha256(raw).hexdigest(),
        from_email=(from_email or "unknown@unknown").lower(),
        from_name=from_name or None,
        subject=str(msg.get("Subject", "")),
        body_text=body.get_content() if body else "",
        received_at=received_at,
        attachments=attachments,
    )
