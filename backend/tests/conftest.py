import os

# Must be set before the app reads settings. Tests never call Gemini, Gmail or a real webhook.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://rfq:rfq@localhost:5434/rfq_desk_test"
)
os.environ["INBOUND_WEBHOOK_SECRET"] = "test-inbound"
os.environ["OUTBOUND_WEBHOOK_URL"] = ""
os.environ["OUTBOUND_WEBHOOK_SECRET"] = "test-outbound"
os.environ["TOKEN_ENCRYPTION_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, get_engine, get_sessionmaker, init_db  # noqa: E402
from app.extraction import ExtractedLine, ExtractedRfq, ExtractionError, ExtractionResult  # noqa: E402
from app.main import app, get_extractor, get_reranker  # noqa: E402
from app.seed import seed  # noqa: E402


class StubExtractor:
    """Returns canned extractions keyed by email subject, and counts calls."""

    def __init__(self):
        self.responses: dict[str, ExtractedRfq] = {}
        self.error: str | None = None
        self.calls = 0

    def extract(self, email):
        self.calls += 1
        if self.error:
            raise ExtractionError(self.error)
        return ExtractionResult(data=self.responses[email.subject], model="stub-model",
                                prompt_version="test", duration_ms=5)


def extraction(**overrides) -> ExtractedRfq:
    data = dict(
        is_rfq=True,
        customer_name="Jane Miller",
        customer_company="Northfield Packaging",
        customer_email="buyer@northfield.example",
        customer_phone=None,
        requested_delivery_date="2026-09-30",
        ship_to="2200 Webster St, Dayton, OH",
        notes=None,
        header_confidence=0.95,
        suspicious_instructions=[],
        lines=[
            ExtractedLine(part_number="BRG-6204-2RS", description="Ball bearing 6204-2RS", quantity=40,
                          uom="EA", confidence=0.97),
            ExtractedLine(part_number="pt vbelt b52", description="V-belt B52", quantity=10, uom="EA",
                          confidence=0.9),
        ],
    )
    data.update(overrides)
    return ExtractedRfq(**data)


@pytest.fixture(scope="session", autouse=True)
def database():
    from app import models  # noqa: F401

    Base.metadata.drop_all(get_engine())
    init_db()


@pytest.fixture(autouse=True)
def clean_tables(database):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with get_sessionmaker()() as s:
        seed(s)


@pytest.fixture
def session():
    with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def stub():
    return StubExtractor()


@pytest.fixture
def client(stub):
    app.dependency_overrides[get_extractor] = lambda: stub
    app.dependency_overrides[get_reranker] = lambda: None  # SQL-only matching; re-ranking has its own tests
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
