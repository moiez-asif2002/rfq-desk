"""Signed, idempotent, retried export of approved quotes to a downstream system (n8n, CRM, ERP)."""

import json
import logging
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings
from app.models import OutboundDelivery, Rfq, RfqLine, RfqStatus
from app.quote import quote_number, quote_total
from app.security import IDEMPOTENCY_HEADER, SIGNATURE_HEADER, sign_payload

log = logging.getLogger(__name__)

EVENT = "rfq.approved"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def build_payload(rfq: Rfq) -> dict:
    return {
        "event": EVENT,
        "quote_number": quote_number(rfq),
        "rfq_id": rfq.id,
        "approved_by": rfq.approved_by,
        "approved_at": rfq.approved_at.isoformat() if rfq.approved_at else None,
        "customer": {
            "name": rfq.customer_name,
            "company": rfq.customer_company,
            "email": rfq.customer_email,
            "phone": rfq.customer_phone,
        },
        "requested_delivery_date": rfq.requested_delivery_date,
        "ship_to": rfq.ship_to,
        "assigned_rep": rfq.assigned_rep.email if rfq.assigned_rep else None,
        "currency": "USD",
        "total": str(quote_total(rfq)),
        "lines": [
            {
                "line_no": line.line_no,
                "sku": line.product.sku if line.product else None,
                "name": line.product.name if line.product else line.raw_description,
                "customer_part_number": line.raw_part_number,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
            }
            for line in rfq.lines
        ],
    }


def export_rfq(session: Session, rfq_id: int, settings: Settings,
               client: httpx.Client | None = None, sleep=time.sleep) -> OutboundDelivery | None:
    rfq = session.scalars(
        select(Rfq).where(Rfq.id == rfq_id)
        .options(selectinload(Rfq.lines).selectinload(RfqLine.product), selectinload(Rfq.assigned_rep))
    ).one()
    if not settings.outbound_webhook_url:
        log.info("no OUTBOUND_WEBHOOK_URL configured; skipping export for rfq %s", rfq_id)
        return None

    # One delivery row per approval. A retry of this function reuses it instead of double-sending.
    key = f"{EVENT}:{rfq.id}:{int(rfq.approved_at.timestamp())}"
    session.execute(
        insert(OutboundDelivery)
        .values(rfq_id=rfq.id, event=EVENT, url=settings.outbound_webhook_url, idempotency_key=key,
                payload=build_payload(rfq))
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    delivery = session.scalars(
        select(OutboundDelivery).where(OutboundDelivery.idempotency_key == key).with_for_update()
    ).one()
    if delivery.status == "delivered":
        session.commit()
        return delivery

    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
    own_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        while delivery.attempts < settings.outbound_max_attempts:
            delivery.attempts += 1
            headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign_payload(settings.outbound_webhook_secret, body),
                IDEMPOTENCY_HEADER: key,
            }
            try:
                resp = client.post(delivery.url, content=body, headers=headers)
                delivery.last_status_code = resp.status_code
                if resp.is_success:
                    delivery.status = "delivered"
                    delivery.delivered_at = datetime.now(UTC)
                    delivery.last_error = None
                    break
                delivery.last_error = resp.text[:500]
                if resp.status_code not in RETRYABLE_STATUS:
                    break
            except httpx.HTTPError as exc:
                delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if delivery.attempts < settings.outbound_max_attempts:
                sleep(2 ** (delivery.attempts - 1))  # 1s, 2s, ...
    finally:
        if own_client:
            client.close()

    if delivery.status != "delivered":
        delivery.status = "failed"
    else:
        rfq.status = RfqStatus.EXPORTED
    audit.record(session, f"export.{delivery.status}", rfq_id=rfq.id, url=delivery.url,
                 attempts=delivery.attempts, status_code=delivery.last_status_code)
    session.commit()
    return delivery
