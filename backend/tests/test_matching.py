from decimal import Decimal

from app.matching import Candidate, classify, match_line
from app.models import MatchStatus

ACCEPT, MIN = 0.75, 0.3


def cand(sku: str, score: float) -> Candidate:
    return Candidate(product_id=1, sku=sku, name=sku, unit_price=Decimal("1"), uom="EA", stock_qty=1,
                     lead_time_days=1, score=score)


def test_part_number_with_different_punctuation_matches_exactly(session):
    result = match_line(session, "brg 6204/2rs", None, ACCEPT, MIN)
    assert result.status == MatchStatus.MATCHED
    assert result.best.sku == "BRG-6204-2RS"
    assert result.best.score == 1.0


def test_similar_skus_do_not_get_confused(session):
    result = match_line(session, "BRG-6205-2RS", "bearing", ACCEPT, MIN)
    assert result.best.sku == "BRG-6205-2RS"


def test_description_only_finds_the_right_product_first(session):
    result = match_line(session, None, "UCP205 pillow block bearing 25mm bore", ACCEPT, MIN)
    assert result.status != MatchStatus.UNMATCHED
    assert result.candidates[0].sku == "BRG-UCP205"


def test_unknown_item_is_not_forced_onto_a_catalog_product(session):
    result = match_line(session, None, "zqx flux capacitor", ACCEPT, MIN)
    assert result.status == MatchStatus.UNMATCHED
    assert result.best is None


def test_close_runner_up_is_ambiguous():
    result = classify([cand("A", 0.82), cand("B", 0.79)], ACCEPT, MIN)
    assert result.status == MatchStatus.AMBIGUOUS
    assert result.best is None


def test_exact_match_wins_even_with_close_runner_up():
    result = classify([cand("A", 1.0), cand("B", 0.97)], ACCEPT, MIN)
    assert result.status == MatchStatus.MATCHED


def test_clear_winner_is_matched():
    result = classify([cand("A", 0.9), cand("B", 0.5)], ACCEPT, MIN)
    assert result.status == MatchStatus.MATCHED
    assert result.best.sku == "A"
