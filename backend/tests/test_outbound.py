import httpx

from app.config import get_settings
from app.models import Rfq
from app.outbound import export_rfq
from app.security import IDEMPOTENCY_HEADER, SIGNATURE_HEADER, verify_signature
from tests.conftest import extraction
from tests.test_api_flow import webhook


def approved_rfq_id(client, stub) -> int:
    stub.responses["RFQ 1001"] = extraction()
    rfq_id = webhook(client).json()["rfq_id"]
    assert client.post(f"/api/rfqs/{rfq_id}/approve", json={}).status_code == 200
    return rfq_id


def settings_with_url():
    return get_settings().model_copy(update={"outbound_webhook_url": "https://hooks.example/rfq"})


def test_export_is_signed_delivered_once_and_marks_exported(client, stub, session):
    rfq_id = approved_rfq_id(client, stub)
    received: list[httpx.Request] = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: received.append(r) or httpx.Response(200)))

    delivery = export_rfq(session, rfq_id, settings_with_url(), client=http, sleep=lambda _: None)
    assert delivery.status == "delivered"
    request = received[0]
    assert verify_signature("test-outbound", request.content, request.headers[SIGNATURE_HEADER])
    assert request.headers[IDEMPOTENCY_HEADER].startswith(f"rfq.approved:{rfq_id}:")

    # A retried job must not send the same approval twice.
    export_rfq(session, rfq_id, settings_with_url(), client=http, sleep=lambda _: None)
    assert len(received) == 1
    session.expire_all()
    assert session.get(Rfq, rfq_id).status == "exported"


def test_export_retries_transient_errors_then_fails(client, stub, session):
    rfq_id = approved_rfq_id(client, stub)
    calls = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(503)))

    delivery = export_rfq(session, rfq_id, settings_with_url(), client=http, sleep=lambda _: None)
    assert delivery.status == "failed"
    assert delivery.attempts == 3 == len(calls)
    session.expire_all()
    assert session.get(Rfq, rfq_id).status == "approved"


def test_export_does_not_retry_client_errors(client, stub, session):
    rfq_id = approved_rfq_id(client, stub)
    calls = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(400)))

    delivery = export_rfq(session, rfq_id, settings_with_url(), client=http, sleep=lambda _: None)
    assert delivery.status == "failed"
    assert len(calls) == 1
