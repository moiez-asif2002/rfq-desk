from decimal import Decimal

from app.config import get_settings
from app.extraction import ExtractedLine
from app.matching import Candidate
from app.models import MatchStatus, Product
from app.pipeline import match_lines
from app.rerank import LineChoice, RerankDecision, RerankInput, filter_choices


class StubReranker:
    def __init__(self, decisions: dict[int, RerankDecision] | None = None, error: str | None = None):
        self.decisions = decisions or {}
        self.error = error
        self.items: list[RerankInput] | None = None

    def rerank(self, items):
        self.items = items
        if self.error:
            raise RuntimeError(self.error)
        return self.decisions


def line(description: str, part_number: str | None = None, quantity: float = 1) -> ExtractedLine:
    return ExtractedLine(part_number=part_number, description=description, quantity=quantity, uom=None, confidence=0.9)


def sku_of(session, product_id):
    return session.get(Product, product_id).sku


def test_exact_sku_is_accepted_without_calling_the_model(session):
    stub = StubReranker()
    [m], error = match_lines(session, [line("bearing", "brg-6204-2rs")], get_settings(), stub)
    assert (m.status, m.method, error) == (MatchStatus.MATCHED, "exact_sku", None)
    assert stub.items is None


def test_confident_model_pick_is_auto_matched_and_explained(session):
    stub = StubReranker({1: RerankDecision("SAF-GLV-CUT-A4-L", 0.94, "ANSI A4 and size L both match.")})
    [m], _ = match_lines(session, [line("Cut resistant gloves ANSI A4, size L", quantity=120)], get_settings(), stub)

    assert m.status == MatchStatus.MATCHED
    assert sku_of(session, m.product_id) == "SAF-GLV-CUT-A4-L"
    assert (m.method, m.reason) == ("ai_rerank", "ANSI A4 and size L both match.")
    # The model saw both sizes, so it had to use the attribute to decide.
    assert {"SAF-GLV-CUT-A4-L", "SAF-GLV-CUT-A4-M"} <= {c.sku for c in stub.items[0].candidates}


def test_unsure_model_pick_is_suggested_but_needs_a_human(session):
    stub = StubReranker({1: RerankDecision("MTR-3PH-5HP-184T", 0.6, "HP matches but voltage not stated.")})
    [m], _ = match_lines(session, [line("5HP 3-phase motor")], get_settings(), stub)
    assert m.status == MatchStatus.AMBIGUOUS
    assert m.product_id is None
    assert m.candidates[0].sku == "MTR-3PH-5HP-184T"


def test_model_saying_none_leaves_the_line_unmatched(session):
    stub = StubReranker({1: RerankDecision(None, 0.9, "Custom machined shaft; no candidate is a shaft.")})
    shaft = line("Custom machined drive shaft 25mm x 600mm, bearing seat")
    [m], _ = match_lines(session, [shaft], get_settings(), stub)
    assert (m.status, m.product_id, m.method) == (MatchStatus.UNMATCHED, None, "ai_rerank")


def test_reranker_outage_falls_back_to_sql_rules(session):
    stub = StubReranker(error="429 quota exceeded")
    [m], error = match_lines(session, [line("5HP 3-phase TEFC motors, 184T frame")], get_settings(), stub)
    assert "quota exceeded" in error
    assert m.method in ("trigram", None)
    assert m.status != MatchStatus.MATCHED or m.method == "trigram"


def test_model_cannot_select_a_product_retrieval_did_not_offer():
    cand = Candidate(product_id=1, sku="SAF-GLV-CUT-A4-L", name="Gloves L", unit_price=Decimal("7.90"), uom="PR",
                     stock_qty=10, lead_time_days=3, score=0.7)
    items = [RerankInput(1, None, "gloves size L", 10, None, [cand])]
    decisions = filter_choices(
        [LineChoice(line_no=1, chosen_sku="MTR-3PH-5HP-184T", confidence=0.99, reason="trust me"),
         LineChoice(line_no=7, chosen_sku="SAF-GLV-CUT-A4-L", confidence=0.99, reason="unknown line")],
        items,
    )
    assert decisions[1].sku is None
    assert 7 not in decisions
