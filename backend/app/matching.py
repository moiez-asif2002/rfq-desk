"""Catalog matching in Postgres with pg_trgm.

Score = the best of:
  * exact SKU match after normalising punctuation/case (1.0)
  * trigram similarity between the customer's part number and our SKU
  * word similarity between the customer's description and our product name (discounted,
    because descriptions are fuzzier evidence than part numbers)

A line is only auto-matched when the best candidate clears the accept threshold AND clearly
beats the runner-up; otherwise it goes to a human as "ambiguous" or "unmatched".
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import MatchStatus

CANDIDATES_SQL = text(
    """
    WITH q0 AS (
        SELECT CAST(:part_number AS text) AS pn, CAST(:description AS text) AS descr
    ), q AS (
        SELECT pn, coalesce(descr, '') AS descr,
               regexp_replace(upper(coalesce(pn, '')), '[^A-Z0-9]', '', 'g') AS pn_norm
        FROM q0
    )
    SELECT p.id, p.sku, p.name, p.unit_price, p.uom, p.stock_qty, p.lead_time_days,
           GREATEST(
               CASE WHEN q.pn_norm <> ''
                         AND regexp_replace(upper(p.sku), '[^A-Z0-9]', '', 'g') = q.pn_norm
                    THEN 1.0 ELSE 0.0 END,
               CASE WHEN q.pn IS NOT NULL THEN similarity(p.sku, q.pn) ELSE 0.0 END,
               GREATEST(word_similarity(q.descr, p.name), word_similarity(p.name, q.descr)) * 0.85
           )::float AS score
    FROM products p CROSS JOIN q
    ORDER BY score DESC, p.sku
    LIMIT :limit
    """
)

# Exact SKU matches win outright; fuzzy matches must beat the runner-up by this margin.
MIN_MARGIN = 0.08


@dataclass
class Candidate:
    product_id: int
    sku: str
    name: str
    unit_price: Decimal
    uom: str
    stock_qty: int
    lead_time_days: int
    score: float

    def to_json(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "name": self.name,
            "unit_price": str(self.unit_price),
            "uom": self.uom,
            "stock_qty": self.stock_qty,
            "score": round(self.score, 3),
        }


@dataclass
class MatchResult:
    status: MatchStatus
    best: Candidate | None
    candidates: list[Candidate]


def find_candidates(
    session: Session, part_number: str | None, description: str | None, limit: int = 3
) -> list[Candidate]:
    rows = session.execute(
        CANDIDATES_SQL, {"part_number": part_number, "description": description, "limit": limit}
    ).mappings()
    return [
        Candidate(
            product_id=r["id"],
            sku=r["sku"],
            name=r["name"],
            unit_price=r["unit_price"],
            uom=r["uom"],
            stock_qty=r["stock_qty"],
            lead_time_days=r["lead_time_days"],
            score=r["score"],
        )
        for r in rows
    ]


def classify(candidates: list[Candidate], accept_score: float, min_score: float) -> MatchResult:
    relevant = [c for c in candidates if c.score >= min_score]
    if not relevant:
        return MatchResult(MatchStatus.UNMATCHED, None, candidates)
    best = relevant[0]
    runner_up = relevant[1].score if len(relevant) > 1 else 0.0
    if best.score >= 0.999 or (best.score >= accept_score and best.score - runner_up >= MIN_MARGIN):
        return MatchResult(MatchStatus.MATCHED, best, candidates)
    return MatchResult(MatchStatus.AMBIGUOUS, None, relevant)


def match_line(
    session: Session,
    part_number: str | None,
    description: str | None,
    accept_score: float,
    min_score: float,
) -> MatchResult:
    return classify(find_candidates(session, part_number, description), accept_score, min_score)
