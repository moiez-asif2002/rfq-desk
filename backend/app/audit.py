from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(session: Session, action: str, *, actor: str = "system", rfq_id: int | None = None,
           **detail: Any) -> None:
    session.add(AuditEvent(rfq_id=rfq_id, actor=actor, action=action, detail=detail))
