from decimal import Decimal

from app.models import AuditEvent, OutboundDelivery, Rfq, RfqLine
from app.pipeline import approval_problems
from app.quote import quote_number, quote_total


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def line_json(line: RfqLine) -> dict:
    p = line.product
    return {
        "id": line.id,
        "line_no": line.line_no,
        "raw_part_number": line.raw_part_number,
        "raw_description": line.raw_description,
        "quantity": _num(line.quantity),
        "uom": line.uom,
        "confidence": line.confidence,
        "match_status": line.match_status,
        "match_score": line.match_score,
        "match_method": line.match_method,
        "match_reason": line.match_reason,
        "candidates": line.candidates,
        "unit_price": _num(line.unit_price),
        "extended": _num(line.quantity * line.unit_price) if line.quantity and line.unit_price else None,
        "product": {
            "id": p.id, "sku": p.sku, "name": p.name, "uom": p.uom, "stock_qty": p.stock_qty,
            "lead_time_days": p.lead_time_days, "list_price": _num(p.unit_price),
        } if p else None,
    }


def summary_json(rfq: Rfq) -> dict:
    return {
        "id": rfq.id,
        "quote_number": quote_number(rfq),
        "status": rfq.status,
        "source": rfq.message.source,
        "subject": rfq.message.subject,
        "from_email": rfq.message.from_email,
        "from_name": rfq.message.from_name,
        "received_at": rfq.message.received_at.isoformat(),
        "customer_name": rfq.customer_name,
        "customer_company": rfq.customer_company,
        "line_count": len(rfq.lines),
        "total": _num(quote_total(rfq)),
        "flag_codes": sorted({f["code"] for f in rfq.review_flags or []}),
        "assigned_rep": rfq.assigned_rep.name if rfq.assigned_rep else None,
        "created_at": rfq.created_at.isoformat(),
    }


def detail_json(rfq: Rfq, events: list[AuditEvent], deliveries: list[OutboundDelivery]) -> dict:
    return {
        **summary_json(rfq),
        "customer_email": rfq.customer_email,
        "customer_phone": rfq.customer_phone,
        "requested_delivery_date": rfq.requested_delivery_date,
        "ship_to": rfq.ship_to,
        "notes": rfq.notes,
        "header_confidence": rfq.header_confidence,
        "review_flags": rfq.review_flags or [],
        "extraction_model": rfq.extraction_model,
        "prompt_version": rfq.prompt_version,
        "extraction_ms": rfq.extraction_ms,
        "draft_reply": rfq.draft_reply,
        "gmail_draft_id": rfq.gmail_draft_id,
        "approved_by": rfq.approved_by,
        "approved_at": rfq.approved_at.isoformat() if rfq.approved_at else None,
        "assigned_rep_email": rfq.assigned_rep.email if rfq.assigned_rep else None,
        "message": {
            "body_text": rfq.message.body_text,
            "attachments": [
                {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size_bytes": a.size_bytes}
                for a in rfq.message.attachments
            ],
        },
        "lines": [line_json(line) for line in rfq.lines],
        "approval_problems": approval_problems(rfq),
        "audit": [
            {"id": e.id, "actor": e.actor, "action": e.action, "detail": e.detail,
             "created_at": e.created_at.isoformat()}
            for e in events
        ],
        "deliveries": [
            {"id": d.id, "event": d.event, "url": d.url, "status": d.status, "attempts": d.attempts,
             "last_status_code": d.last_status_code, "last_error": d.last_error,
             "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None}
            for d in deliveries
        ],
    }
