"""Intake -> extraction -> matching -> review gating.

Each stage writes an audit event so a reviewer can see exactly what the AI proposed and what a
human changed. Nothing leaves the system (no email draft, no export) until a human approves.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings
from app.extraction import EmailAttachment, EmailInput, ExtractedLine, ExtractionError, Extractor
from app.matching import Candidate, classify, find_candidates
from app.models import (
    Attachment,
    InboundMessage,
    MatchStatus,
    Product,
    Rfq,
    RfqLine,
    RfqStatus,
    SalesRep,
)
from app.rerank import Reranker, RerankInput

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OPEN_STATUSES = (RfqStatus.PROCESSING, RfqStatus.NEEDS_REVIEW, RfqStatus.READY)


@dataclass
class IngestResult:
    rfq_id: int
    message_id: int
    duplicate: bool


def ingest_message(
    session: Session,
    *,
    source: str,
    external_id: str,
    from_email: str,
    subject: str,
    body_text: str,
    received_at: datetime | None = None,
    from_name: str | None = None,
    thread_id: str | None = None,
    rfc_message_id: str | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> IngestResult:
    """Store an inbound email exactly once. Re-delivery of the same external_id is a no-op."""
    stmt = (
        insert(InboundMessage)
        .values(
            source=source,
            external_id=external_id,
            thread_id=thread_id,
            rfc_message_id=rfc_message_id,
            from_email=from_email.strip().lower(),
            from_name=from_name,
            subject=subject[:998],
            body_text=body_text,
            received_at=received_at or datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["external_id"])
        .returning(InboundMessage.id)
    )
    message_id = session.scalar(stmt)
    if message_id is None:
        existing = session.execute(
            select(InboundMessage.id, Rfq.id).join(Rfq).where(InboundMessage.external_id == external_id)
        ).one()
        session.rollback()
        return IngestResult(rfq_id=existing[1], message_id=existing[0], duplicate=True)

    for att in attachments or []:
        session.add(
            Attachment(
                message_id=message_id,
                filename=att.filename[:255],
                content_type=att.content_type,
                size_bytes=len(att.content),
                sha256=hashlib.sha256(att.content).hexdigest(),
                content=att.content,
            )
        )
    rfq = Rfq(message_id=message_id, status=RfqStatus.PROCESSING, review_flags=[])
    session.add(rfq)
    session.flush()
    audit.record(
        session, "email.received", rfq_id=rfq.id, source=source, external_id=external_id,
        attachments=[a.filename for a in attachments or []],
    )
    session.commit()
    return IngestResult(rfq_id=rfq.id, message_id=message_id, duplicate=False)


def _email_input(message: InboundMessage) -> EmailInput:
    return EmailInput(
        from_email=message.from_email,
        from_name=message.from_name,
        subject=message.subject,
        body_text=message.body_text,
        received_at=message.received_at,
        attachments=[EmailAttachment(a.filename, a.content_type, a.content) for a in message.attachments],
    )


def _domain(email: str | None) -> str | None:
    return email.rsplit("@", 1)[1].lower() if email and "@" in email else None


def assign_rep(session: Session, lines: list[RfqLine]) -> SalesRep | None:
    """Route to the rep who covers the dominant product category; tie-break on open workload."""
    categories = [line.product.category for line in lines if line.product is not None]
    dominant = max(set(categories), key=categories.count) if categories else None

    open_load = (
        select(Rfq.assigned_rep_id, func.count().label("n"))
        .where(Rfq.status.in_(OPEN_STATUSES))
        .group_by(Rfq.assigned_rep_id)
        .subquery()
    )
    query = (
        select(SalesRep)
        .outerjoin(open_load, open_load.c.assigned_rep_id == SalesRep.id)
        .order_by(func.coalesce(open_load.c.n, 0), SalesRep.id)
    )
    if dominant:
        rep = session.scalars(query.where(SalesRep.categories.any(dominant))).first()
        if rep:
            return rep
    return session.scalars(query).first()


RERANK_POOL = 5
RERANK_MIN_SCORE = 0.1


@dataclass
class LineMatch:
    status: MatchStatus
    product_id: int | None
    score: float | None
    candidates: list[Candidate]
    method: str | None
    reason: str | None = None


def match_lines(
    session: Session, lines: list[ExtractedLine], settings: Settings, reranker: Reranker | None
) -> tuple[list[LineMatch], str | None]:
    """SQL retrieves candidates; exact SKUs are accepted directly; everything else is re-ranked by the model.

    Returns the matches and, if the re-ranker failed, its error (lines then fall back to SQL-only rules).
    """
    pools = [
        [c for c in find_candidates(session, line.part_number, line.description, limit=RERANK_POOL)
         if c.score >= RERANK_MIN_SCORE]
        for line in lines
    ]
    results: list[LineMatch | None] = []
    pending: list[RerankInput] = []
    for line_no, (line, pool) in enumerate(zip(lines, pools, strict=True), start=1):
        if pool and pool[0].score >= 0.999:
            results.append(LineMatch(MatchStatus.MATCHED, pool[0].product_id, 1.0, pool[:3], "exact_sku"))
            continue
        results.append(None)
        if pool and reranker is not None:
            pending.append(RerankInput(line_no, line.part_number, line.description, line.quantity, line.uom, pool))

    decisions, error = {}, None
    if pending:
        try:
            decisions = reranker.rerank(pending)
        except Exception as exc:
            log.warning("re-ranking failed, falling back to SQL matching: %s", exc)
            error = f"{type(exc).__name__}: {exc}"[:500]

    for idx, pool in enumerate(pools):
        if results[idx] is not None:
            continue
        decision = decisions.get(idx + 1)
        if decision is None:
            sql = classify(pool, settings.match_accept_score, settings.match_min_score)
            results[idx] = LineMatch(
                sql.status, sql.best.product_id if sql.best else None, sql.best.score if sql.best else None,
                pool[:3], "trigram" if sql.best else None,
            )
        elif decision.sku is None:
            results[idx] = LineMatch(MatchStatus.UNMATCHED, None, None, pool[:3], "ai_rerank", decision.reason)
        else:
            chosen = next(c for c in pool if c.sku == decision.sku)
            ordered = [chosen, *[c for c in pool if c is not chosen]]
            confident = decision.confidence >= settings.rerank_accept_confidence
            results[idx] = LineMatch(
                MatchStatus.MATCHED if confident else MatchStatus.AMBIGUOUS,
                chosen.product_id if confident else None,
                decision.confidence, ordered[:3], "ai_rerank", decision.reason,
            )
    return [r for r in results if r is not None], error


def compute_review_flags(rfq: Rfq, settings: Settings) -> list[dict]:
    flags: list[dict] = []
    raw = rfq.extraction_raw or {}

    if raw.get("suspicious_instructions"):
        flags.append({
            "code": "prompt_injection",
            "severity": "high",
            "message": "Sender text tried to instruct the system; it was ignored.",
            "evidence": raw["suspicious_instructions"],
        })
    if not rfq.customer_email or not EMAIL_RE.match(rfq.customer_email):
        flags.append({"code": "missing_field", "field": "customer_email", "message": "No valid customer email."})
    elif _domain(rfq.customer_email) != _domain(rfq.message.from_email):
        flags.append({
            "code": "sender_mismatch",
            "message": f"Quote contact {rfq.customer_email} differs from sender {rfq.message.from_email}.",
        })
    if not rfq.requested_delivery_date:
        flags.append({"code": "missing_field", "field": "requested_delivery_date",
                      "message": "No delivery date requested."})
    if (rfq.header_confidence or 0) < settings.review_confidence_threshold:
        flags.append({"code": "low_confidence", "message": "Customer details were hard to read."})
    if not rfq.lines:
        flags.append({"code": "no_lines", "message": "No line items were found."})

    for line in rfq.lines:
        if line.quantity is None or line.quantity <= 0:
            flags.append({"code": "missing_field", "field": "quantity", "line_no": line.line_no,
                          "message": f"Line {line.line_no}: quantity missing."})
        why = f" {line.match_reason}" if line.match_reason else ""
        if line.match_status == MatchStatus.AMBIGUOUS:
            flags.append({"code": "ambiguous_match", "line_no": line.line_no,
                          "message": f"Line {line.line_no}: likely match needs confirmation.{why}"})
        elif line.match_status == MatchStatus.UNMATCHED:
            flags.append({"code": "unmatched", "line_no": line.line_no,
                          "message": f"Line {line.line_no}: no catalog match.{why}"})
        if (line.confidence or 0) < settings.review_confidence_threshold:
            flags.append({"code": "low_confidence", "line_no": line.line_no,
                          "message": f"Line {line.line_no}: extraction confidence is low."})
        if line.product and line.quantity and line.product.stock_qty < line.quantity:
            flags.append({"code": "stock_short", "line_no": line.line_no,
                          "message": f"Line {line.line_no}: only {line.product.stock_qty} in stock."})
    return flags


# Stock shortfalls are useful context for the rep but don't by themselves need review.
INFORMATIONAL_FLAGS = {"stock_short"}


def refresh_review_state(rfq: Rfq, settings: Settings) -> None:
    """Recompute flags after a human edit. Injection evidence from extraction is kept."""
    rfq.review_flags = compute_review_flags(rfq, settings)
    blocking = [f for f in rfq.review_flags if f["code"] not in INFORMATIONAL_FLAGS]
    rfq.status = RfqStatus.NEEDS_REVIEW if blocking else RfqStatus.READY


def process_rfq(
    session: Session, rfq_id: int, extractor: Extractor, settings: Settings, reranker: Reranker | None = None
) -> Rfq:
    rfq = session.scalars(
        select(Rfq)
        .where(Rfq.id == rfq_id)
        .options(selectinload(Rfq.message).selectinload(InboundMessage.attachments), selectinload(Rfq.lines))
        .with_for_update(of=Rfq)
    ).one()
    if rfq.status not in (RfqStatus.PROCESSING, RfqStatus.FAILED, RfqStatus.NEEDS_REVIEW, RfqStatus.READY):
        raise ValueError(f"RFQ {rfq_id} is {rfq.status}; cannot re-process")

    try:
        result = extractor.extract(_email_input(rfq.message))
    except ExtractionError as exc:
        rfq.status = RfqStatus.FAILED
        audit.record(session, "extraction.failed", rfq_id=rfq.id, error=str(exc)[:2000])
        session.commit()
        return rfq

    data = result.data
    actor = f"ai:{result.model}"
    rfq.extraction_model = result.model
    rfq.prompt_version = result.prompt_version
    rfq.extraction_ms = result.duration_ms
    rfq.extraction_raw = data.model_dump()

    if not data.is_rfq:
        rfq.status = RfqStatus.REJECTED
        rfq.review_flags = [{"code": "not_rfq", "message": "Message is not a quote request."}]
        audit.record(session, "extraction.not_rfq", actor=actor, rfq_id=rfq.id)
        session.commit()
        return rfq

    rfq.customer_name = data.customer_name
    rfq.customer_company = data.customer_company
    rfq.customer_email = (data.customer_email or rfq.message.from_email).strip().lower()
    rfq.customer_phone = data.customer_phone
    rfq.requested_delivery_date = data.requested_delivery_date
    rfq.ship_to = data.ship_to
    rfq.notes = data.notes
    rfq.header_confidence = data.header_confidence

    rfq.lines.clear()
    session.flush()
    matches, rerank_error = match_lines(session, data.lines, settings, reranker)
    if rerank_error:
        audit.record(session, "rerank.failed", rfq_id=rfq.id, error=rerank_error)
    for i, (extracted, match) in enumerate(zip(data.lines, matches, strict=True), start=1):
        product = session.get(Product, match.product_id) if match.product_id else None
        rfq.lines.append(
            RfqLine(
                line_no=i,
                raw_part_number=extracted.part_number,
                raw_description=extracted.description,
                quantity=Decimal(str(extracted.quantity)) if extracted.quantity is not None else None,
                uom=extracted.uom,
                confidence=extracted.confidence,
                product=product,
                match_status=match.status,
                match_score=match.score,
                match_method=match.method,
                match_reason=match.reason,
                candidates=[c.to_json() for c in match.candidates],
                unit_price=product.unit_price if product else None,
            )
        )
    session.flush()

    rep = assign_rep(session, rfq.lines)
    rfq.assigned_rep_id = rep.id if rep else None
    rfq.review_flags = compute_review_flags(rfq, settings)
    blocking = [f for f in rfq.review_flags if f["code"] not in INFORMATIONAL_FLAGS]
    rfq.status = RfqStatus.NEEDS_REVIEW if blocking else RfqStatus.READY

    audit.record(
        session, "extraction.completed", actor=actor, rfq_id=rfq.id,
        prompt_version=result.prompt_version, duration_ms=result.duration_ms,
        match_methods=[line.match_method for line in rfq.lines],
        lines=len(rfq.lines), flags=[f["code"] for f in rfq.review_flags],
        assigned_rep=rep.email if rep else None, status=rfq.status,
    )
    session.commit()
    return rfq


class ApprovalError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def approval_problems(rfq: Rfq) -> list[str]:
    problems = []
    if not rfq.customer_email or not EMAIL_RE.match(rfq.customer_email):
        problems.append("Customer email is missing or invalid.")
    if not rfq.lines:
        problems.append("Quote has no line items.")
    for line in rfq.lines:
        if line.product_id is None:
            problems.append(f"Line {line.line_no}: choose a catalog product.")
        elif line.unit_price is None or line.unit_price < 0:
            problems.append(f"Line {line.line_no}: unit price is required.")
        if line.quantity is None or line.quantity <= 0:
            problems.append(f"Line {line.line_no}: quantity must be positive.")
    return problems


def approve_rfq(session: Session, rfq_id: int, approver: str) -> Rfq:
    from app.quote import render_quote_email

    rfq = session.scalars(
        select(Rfq)
        .where(Rfq.id == rfq_id)
        .options(selectinload(Rfq.lines).selectinload(RfqLine.product), selectinload(Rfq.message),
                 selectinload(Rfq.assigned_rep))
        .with_for_update(of=Rfq)  # two reviewers clicking approve at once -> one wins
    ).one()
    if rfq.status not in (RfqStatus.NEEDS_REVIEW, RfqStatus.READY):
        raise ApprovalError([f"RFQ is {rfq.status} and cannot be approved."])
    problems = approval_problems(rfq)
    if problems:
        raise ApprovalError(problems)

    rfq.status = RfqStatus.APPROVED
    rfq.approved_by = approver
    rfq.approved_at = datetime.now(UTC)
    rfq.draft_reply = render_quote_email(rfq)
    audit.record(
        session, "rfq.approved", actor=f"user:{approver}", rfq_id=rfq.id,
        acknowledged_flags=[f["code"] for f in rfq.review_flags],
        total=str(sum((line.quantity or 0) * (line.unit_price or 0) for line in rfq.lines)),
    )
    session.commit()
    return rfq
