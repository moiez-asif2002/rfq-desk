import base64
import binascii
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app import audit, gmail
from app.config import Settings, get_settings
from app.db import get_session, get_sessionmaker, init_db
from app.emails import MAX_ATTACHMENTS, parse_eml
from app.extraction import (
    MAX_ATTACHMENT_BYTES,
    EmailAttachment,
    EmailInput,
    ExtractionError,
    ExtractionResult,
    Extractor,
    GeminiExtractor,
)
from app.matching import find_candidates
from app.models import (
    Attachment,
    AuditEvent,
    InboundMessage,
    MatchStatus,
    OutboundDelivery,
    Product,
    Rfq,
    RfqLine,
    RfqStatus,
)
from app.outbound import export_rfq
from app.pipeline import ApprovalError, approve_rfq, ingest_message, process_rfq, refresh_review_state
from app.rerank import GeminiReranker, RerankDecision, Reranker, RerankInput
from app.security import constant_time_equals
from app.serializers import detail_json, summary_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rfq_desk")

OPEN = (RfqStatus.NEEDS_REVIEW, RfqStatus.READY)
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="RFQ Desk", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_methods=["*"],
    allow_headers=["*"],
)

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class LazyGeminiExtractor:
    """Build the Gemini client on first use so a missing credential fails one RFQ, not the API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._inner: GeminiExtractor | None = None

    def extract(self, email: EmailInput) -> ExtractionResult:
        if self._inner is None:
            try:
                self._inner = GeminiExtractor(self.settings)
            except Exception as exc:
                raise ExtractionError(f"Gemini client is not configured: {exc}") from exc
        return self._inner.extract(email)


@lru_cache
def _default_extractor() -> Extractor:
    return LazyGeminiExtractor(get_settings())


def get_extractor() -> Extractor:
    return _default_extractor()


ExtractorDep = Annotated[Extractor, Depends(get_extractor)]


class LazyGeminiReranker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._inner: GeminiReranker | None = None

    def rerank(self, items: list[RerankInput]) -> dict[int, RerankDecision]:
        if self._inner is None:
            self._inner = GeminiReranker(self.settings)
        return self._inner.rerank(items)


@lru_cache
def _default_reranker() -> Reranker | None:
    settings = get_settings()
    return LazyGeminiReranker(settings) if settings.enable_rerank else None


def get_reranker() -> Reranker | None:
    return _default_reranker()


RerankerDep = Annotated[Reranker | None, Depends(get_reranker)]


# --------------------------------------------------------------------------- background jobs
# Kept as plain functions with their own DB session so they can move to Celery/Cloud Tasks unchanged.

def run_processing(rfq_id: int, extractor: Extractor, reranker: Reranker | None, settings: Settings) -> None:
    with get_sessionmaker()() as session:
        try:
            process_rfq(session, rfq_id, extractor, settings, reranker)
        except Exception as exc:
            log.exception("processing rfq %s crashed", rfq_id)
            session.rollback()
            rfq = session.get(Rfq, rfq_id)
            if rfq:
                rfq.status = RfqStatus.FAILED
                audit.record(session, "processing.crashed", rfq_id=rfq_id, error=str(exc)[:2000])
                session.commit()


def run_post_approval(rfq_id: int, settings: Settings) -> None:
    with get_sessionmaker()() as session:
        rfq = session.get(Rfq, rfq_id)
        if rfq is None:
            return
        if gmail.current_account(session) is not None and not rfq.gmail_draft_id:
            try:
                draft_id = gmail.create_reply_draft(session, settings, rfq.message, rfq.customer_email,
                                                    rfq.draft_reply or "")
                rfq.gmail_draft_id = draft_id
                audit.record(session, "gmail.draft_created", rfq_id=rfq_id, draft_id=draft_id)
            except Exception as exc:
                log.exception("gmail draft failed for rfq %s", rfq_id)
                audit.record(session, "gmail.draft_failed", rfq_id=rfq_id, error=str(exc)[:500])
            session.commit()
        try:
            export_rfq(session, rfq_id, settings)
        except Exception as exc:
            log.exception("export failed for rfq %s", rfq_id)
            session.rollback()
            audit.record(session, "export.crashed", rfq_id=rfq_id, error=str(exc)[:500])
            session.commit()


# --------------------------------------------------------------------------- helpers

def load_rfq(session: Session, rfq_id: int, lock: bool = False) -> Rfq:
    stmt = select(Rfq).where(Rfq.id == rfq_id).options(
        selectinload(Rfq.message).selectinload(InboundMessage.attachments),
        selectinload(Rfq.lines).selectinload(RfqLine.product),
        selectinload(Rfq.assigned_rep),
    )
    if lock:
        stmt = stmt.with_for_update(of=Rfq)
    rfq = session.scalars(stmt).first()
    if rfq is None:
        raise HTTPException(404, "RFQ not found")
    return rfq


def rfq_detail(session: Session, rfq_id: int) -> dict:
    session.expire_all()
    rfq = load_rfq(session, rfq_id)
    events = list(session.scalars(select(AuditEvent).where(AuditEvent.rfq_id == rfq_id).order_by(AuditEvent.id)))
    deliveries = list(session.scalars(
        select(OutboundDelivery).where(OutboundDelivery.rfq_id == rfq_id).order_by(OutboundDelivery.id)))
    return detail_json(rfq, events, deliveries)


def require_open(rfq: Rfq) -> None:
    if rfq.status not in OPEN:
        raise HTTPException(409, f"RFQ is {rfq.status}; edits are only allowed while in review")


# --------------------------------------------------------------------------- health

@app.get("/api/health")
def health(session: SessionDep, settings: SettingsDep):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "model": settings.gemini_model, "gemini_backend": settings.gemini_backend}


# --------------------------------------------------------------------------- intake

class InboundAttachment(BaseModel):
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=127)
    content_base64: str


class InboundEmail(BaseModel):
    message_id: str = Field(min_length=1, max_length=255)
    from_email: EmailStr
    from_name: str | None = None
    subject: str = ""
    text: str = Field("", max_length=200_000)
    received_at: datetime | None = None
    attachments: list[InboundAttachment] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


def _decode_attachments(items: list[InboundAttachment]) -> list[EmailAttachment]:
    decoded = []
    for item in items:
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, f"attachment {item.filename!r} is not valid base64") from exc
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, f"attachment {item.filename!r} exceeds size limit")
        decoded.append(EmailAttachment(item.filename, item.content_type.lower(), content))
    return decoded


@app.post("/api/webhooks/inbound-email", status_code=202)
def inbound_email_webhook(
    payload: InboundEmail,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
    reranker: RerankerDep,
    x_webhook_secret: Annotated[str | None, Header()] = None,
):
    """Inbound email from a mail provider, n8n or Make. Safe to retry: deduplicated on message_id."""
    if not x_webhook_secret or not constant_time_equals(x_webhook_secret, settings.inbound_webhook_secret):
        raise HTTPException(401, "invalid webhook secret")
    result = ingest_message(
        session,
        source="webhook",
        external_id=f"webhook:{payload.message_id}",
        rfc_message_id=payload.message_id,
        from_email=payload.from_email,
        from_name=payload.from_name,
        subject=payload.subject,
        body_text=payload.text,
        received_at=payload.received_at,
        attachments=_decode_attachments(payload.attachments),
    )
    if not result.duplicate:
        background.add_task(run_processing, result.rfq_id, extractor, reranker, settings)
    return {"rfq_id": result.rfq_id, "duplicate": result.duplicate}


@app.post("/api/rfqs/upload", status_code=202)
def upload_email(
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
    reranker: RerankerDep,
    file: Annotated[UploadFile, File(description=".eml file")],
):
    raw = file.file.read(MAX_ATTACHMENT_BYTES * 2 + 1)
    if len(raw) > MAX_ATTACHMENT_BYTES * 2:
        raise HTTPException(413, "file too large")
    if not (file.filename or "").lower().endswith(".eml"):
        raise HTTPException(422, "upload an .eml file")

    parsed = parse_eml(raw)
    result = ingest_message(
        session,
        source="upload",
        external_id=f"upload:{parsed.message_id or parsed.content_hash}",
        rfc_message_id=parsed.message_id,
        from_email=parsed.from_email,
        from_name=parsed.from_name,
        subject=parsed.subject,
        body_text=parsed.body_text,
        received_at=parsed.received_at,
        attachments=parsed.attachments,
    )
    if not result.duplicate:
        background.add_task(run_processing, result.rfq_id, extractor, reranker, settings)
    return {"rfq_id": result.rfq_id, "duplicate": result.duplicate}


# --------------------------------------------------------------------------- review queue

@app.get("/api/rfqs")
def list_rfqs(session: SessionDep, status: Annotated[str | None, Query()] = None):
    stmt = (
        select(Rfq)
        .options(selectinload(Rfq.message), selectinload(Rfq.lines), selectinload(Rfq.assigned_rep))
        .order_by(Rfq.created_at.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(Rfq.status.in_(status.split(",")))
    counts = dict(session.execute(select(Rfq.status, func.count()).group_by(Rfq.status)).all())
    return {"items": [summary_json(r) for r in session.scalars(stmt)], "counts": counts}


@app.get("/api/rfqs/{rfq_id}")
def get_rfq(rfq_id: int, session: SessionDep):
    return rfq_detail(session, rfq_id)


@app.get("/api/attachments/{attachment_id}")
def get_attachment(attachment_id: int, session: SessionDep):
    att = session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "attachment not found")
    safe_name = "".join(c for c in att.filename if c.isalnum() or c in "._- ") or "attachment"
    # Only render known-safe types inline (PDF viewer needs an unsandboxed frame); everything else downloads.
    inline = att.content_type in INLINE_TYPES
    return Response(
        content=att.content,
        media_type=att.content_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


class RfqUpdate(BaseModel):
    customer_name: str | None = None
    customer_company: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    requested_delivery_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    ship_to: str | None = None
    notes: str | None = None


@app.patch("/api/rfqs/{rfq_id}")
def update_rfq(rfq_id: int, body: RfqUpdate, session: SessionDep, settings: SettingsDep,
               x_user: Annotated[str, Header()] = "reviewer@demo"):
    rfq = load_rfq(session, rfq_id, lock=True)
    require_open(rfq)
    changes = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if getattr(rfq, field) != value:
            changes[field] = {"from": getattr(rfq, field), "to": value}
            setattr(rfq, field, value)
    if changes:
        refresh_review_state(rfq, settings)
        audit.record(session, "rfq.edited", actor=f"user:{x_user}", rfq_id=rfq.id, changes=changes)
        session.commit()
    return rfq_detail(session, rfq_id)


class LineUpdate(BaseModel):
    product_id: int | None = None
    quantity: Decimal | None = Field(None, gt=0)
    unit_price: Decimal | None = Field(None, ge=0)


@app.patch("/api/rfqs/{rfq_id}/lines/{line_id}")
def update_line(rfq_id: int, line_id: int, body: LineUpdate, session: SessionDep, settings: SettingsDep,
                x_user: Annotated[str, Header()] = "reviewer@demo"):
    rfq = load_rfq(session, rfq_id, lock=True)
    require_open(rfq)
    line = next((ln for ln in rfq.lines if ln.id == line_id), None)
    if line is None:
        raise HTTPException(404, "line not found")

    updates = body.model_dump(exclude_unset=True)
    changes = {}
    if "product_id" in updates and updates["product_id"] != line.product_id:
        product = session.get(Product, updates["product_id"]) if updates["product_id"] else None
        if updates["product_id"] and product is None:
            raise HTTPException(422, "unknown product")
        changes["product"] = {"from": line.product.sku if line.product else None,
                              "to": product.sku if product else None}
        line.product = product
        line.match_status = MatchStatus.MANUAL if product else MatchStatus.UNMATCHED
        if "unit_price" not in updates:
            line.unit_price = product.unit_price if product else None
    for field in ("quantity", "unit_price"):
        if field in updates and updates[field] != getattr(line, field):
            changes[field] = {"from": str(getattr(line, field)), "to": str(updates[field])}
            setattr(line, field, updates[field])
    if changes:
        session.flush()
        refresh_review_state(rfq, settings)
        audit.record(session, "line.edited", actor=f"user:{x_user}", rfq_id=rfq.id, line_no=line.line_no,
                     changes=changes)
        session.commit()
    return rfq_detail(session, rfq_id)


@app.delete("/api/rfqs/{rfq_id}/lines/{line_id}")
def delete_line(rfq_id: int, line_id: int, session: SessionDep, settings: SettingsDep,
                x_user: Annotated[str, Header()] = "reviewer@demo"):
    rfq = load_rfq(session, rfq_id, lock=True)
    require_open(rfq)
    line = next((ln for ln in rfq.lines if ln.id == line_id), None)
    if line is None:
        raise HTTPException(404, "line not found")
    rfq.lines.remove(line)
    session.flush()
    refresh_review_state(rfq, settings)
    audit.record(session, "line.deleted", actor=f"user:{x_user}", rfq_id=rfq.id, line_no=line.line_no,
                 description=line.raw_description)
    session.commit()
    return rfq_detail(session, rfq_id)


class ApproveBody(BaseModel):
    approver: EmailStr = "reviewer@demo.example"


@app.post("/api/rfqs/{rfq_id}/approve")
def approve(rfq_id: int, body: ApproveBody, background: BackgroundTasks, session: SessionDep,
            settings: SettingsDep):
    try:
        approve_rfq(session, rfq_id, str(body.approver))
    except ApprovalError as exc:
        session.rollback()
        raise HTTPException(409, {"message": "RFQ cannot be approved yet", "problems": exc.problems}) from exc
    background.add_task(run_post_approval, rfq_id, settings)
    return rfq_detail(session, rfq_id)


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@app.post("/api/rfqs/{rfq_id}/reject")
def reject(rfq_id: int, body: RejectBody, session: SessionDep,
           x_user: Annotated[str, Header()] = "reviewer@demo"):
    rfq = load_rfq(session, rfq_id, lock=True)
    if rfq.status not in (*OPEN, RfqStatus.FAILED):
        raise HTTPException(409, f"RFQ is {rfq.status}")
    rfq.status = RfqStatus.REJECTED
    audit.record(session, "rfq.rejected", actor=f"user:{x_user}", rfq_id=rfq.id, reason=body.reason)
    session.commit()
    return rfq_detail(session, rfq_id)


@app.post("/api/rfqs/{rfq_id}/reprocess", status_code=202)
def reprocess(rfq_id: int, background: BackgroundTasks, session: SessionDep, settings: SettingsDep,
              extractor: ExtractorDep, reranker: RerankerDep):
    rfq = load_rfq(session, rfq_id, lock=True)
    if rfq.status not in (*OPEN, RfqStatus.FAILED, RfqStatus.REJECTED):
        raise HTTPException(409, f"RFQ is {rfq.status}")
    rfq.status = RfqStatus.PROCESSING
    audit.record(session, "rfq.reprocess_requested", rfq_id=rfq.id)
    session.commit()
    background.add_task(run_processing, rfq_id, extractor, reranker, settings)
    return {"rfq_id": rfq_id, "status": rfq.status}


@app.post("/api/rfqs/{rfq_id}/retry-export", status_code=202)
def retry_export(rfq_id: int, background: BackgroundTasks, session: SessionDep, settings: SettingsDep):
    rfq = load_rfq(session, rfq_id)
    if rfq.status != RfqStatus.APPROVED:
        raise HTTPException(409, "only approved, not-yet-exported RFQs can be re-exported")
    session.query(OutboundDelivery).filter(
        OutboundDelivery.rfq_id == rfq_id, OutboundDelivery.status == "failed"
    ).update({"status": "pending", "attempts": 0})
    session.commit()
    background.add_task(run_post_approval, rfq_id, settings)
    return {"rfq_id": rfq_id}


@app.get("/api/products")
def search_products(session: SessionDep, q: Annotated[str, Query(min_length=1, max_length=200)]):
    return [c.to_json() for c in find_candidates(session, q, q, limit=10)]


# --------------------------------------------------------------------------- gmail

@app.get("/api/gmail/status")
def gmail_status(session: SessionDep):
    account = gmail.current_account(session)
    return {
        "connected": account is not None,
        "email": account.email if account else None,
        "last_synced_at": account.last_synced_at.isoformat() if account and account.last_synced_at else None,
    }


@app.get("/api/gmail/connect")
def gmail_connect(session: SessionDep, settings: SettingsDep):
    try:
        return RedirectResponse(gmail.start_authorization(session, settings))
    except FileNotFoundError as exc:
        raise HTTPException(500, "Gmail OAuth client file not found; see README") from exc


@app.get("/api/gmail/oauth/callback")
def gmail_callback(session: SessionDep, settings: SettingsDep, state: str = "", code: str = "",
                   error: str | None = None):
    if error:
        return RedirectResponse(f"{settings.frontend_url}/?gmail=denied")
    try:
        gmail.complete_authorization(session, settings, state, code)
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"{settings.frontend_url}/?gmail=connected")


@app.post("/api/gmail/sync")
def gmail_sync(background: BackgroundTasks, session: SessionDep, settings: SettingsDep, extractor: ExtractorDep,
               reranker: RerankerDep):
    try:
        created = gmail.sync_inbox(session, settings)
    except gmail.GmailNotConnected as exc:
        raise HTTPException(409, str(exc)) from exc
    for rfq_id in created:
        background.add_task(run_processing, rfq_id, extractor, reranker, settings)
    return {"created": created}
