from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RfqStatus(StrEnum):
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    APPROVED = "approved"
    EXPORTED = "exported"
    REJECTED = "rejected"
    FAILED = "failed"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    MANUAL = "manual"


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    uom: Mapped[str] = mapped_column(String(16), default="EA")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=5)

    __table_args__ = (
        Index("ix_products_sku_trgm", "sku", postgresql_using="gin", postgresql_ops={"sku": "gin_trgm_ops"}),
        Index("ix_products_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )


class SalesRep(Base):
    __tablename__ = "sales_reps"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # webhook | gmail | upload
    # Provider message id; the unique constraint is what makes intake idempotent.
    external_id: Mapped[str] = mapped_column(String(255), unique=True)
    thread_id: Mapped[str | None] = mapped_column(String(255))
    rfc_message_id: Mapped[str | None] = mapped_column(String(998))  # for In-Reply-To threading
    from_email: Mapped[str] = mapped_column(String(255))
    from_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(998), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    attachments: Mapped[list["Attachment"]] = relationship(back_populates="message", cascade="all, delete-orphan")
    rfq: Mapped["Rfq"] = relationship(back_populates="message")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("inbound_messages.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)

    message: Mapped[InboundMessage] = relationship(back_populates="attachments")


class Rfq(Base):
    __tablename__ = "rfqs"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("inbound_messages.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default=RfqStatus.PROCESSING, index=True)

    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_company: Mapped[str | None] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255))
    customer_phone: Mapped[str | None] = mapped_column(String(64))
    requested_delivery_date: Mapped[str | None] = mapped_column(String(10))
    ship_to: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    header_confidence: Mapped[float | None]
    # [{code, message, line_no?}] — every reason a human needs to look at this RFQ.
    review_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    extraction_model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    extraction_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_ms: Mapped[int | None]

    assigned_rep_id: Mapped[int | None] = mapped_column(ForeignKey("sales_reps.id"))
    draft_reply: Mapped[str | None] = mapped_column(Text)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    message: Mapped[InboundMessage] = relationship(back_populates="rfq")
    assigned_rep: Mapped[SalesRep | None] = relationship()
    lines: Mapped[list["RfqLine"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan", order_by="RfqLine.line_no"
    )


class RfqLine(Base):
    __tablename__ = "rfq_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int]
    raw_part_number: Mapped[str | None] = mapped_column(String(128))
    raw_description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    uom: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None]

    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    match_status: Mapped[str] = mapped_column(String(16), default=MatchStatus.UNMATCHED)
    match_score: Mapped[float | None]
    match_method: Mapped[str | None] = mapped_column(String(16))  # exact_sku | trigram | ai_rerank | manual
    match_reason: Mapped[str | None] = mapped_column(Text)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    rfq: Mapped[Rfq] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()


class AuditEvent(Base):
    """Append-only: the app never updates or deletes rows here."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int | None] = mapped_column(ForeignKey("rfqs.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(255))  # system | ai:<model> | user:<email>
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()


class OutboundDelivery(Base):
    __tablename__ = "outbound_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(ForeignKey("rfqs.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(2048))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | delivered | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_status_code: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    encrypted_token: Mapped[str] = mapped_column(Text)  # Fernet-encrypted OAuth token JSON
    connected_at: Mapped[datetime] = _now()
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    """Short-lived CSRF state + PKCE verifier for the Gmail OAuth round trip."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = _now()
