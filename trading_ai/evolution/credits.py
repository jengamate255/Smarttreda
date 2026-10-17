"""Mutation credit ledger: meta-learn which mutation types actually improve strategies."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from trading_ai.evolution.lineage import MutationType

Base = declarative_base()


class CreditRecord(Base):
    __tablename__ = "mutation_credits"

    mutation_type = Column(String(32), primary_key=True)
    attempts = Column(Integer, default=0)
    successes = Column(Integer, default=0)
    total_delta_pf = Column(Float, default=0.0)
    total_delta_exp = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow)


@dataclass
class MutationScore:
    mutation_type: MutationType
    attempts: int
    successes: int
    success_rate: float
    avg_delta_pf: float
    weight: float


class MutationCreditLedger:
    """Tracks which mutation types historically improve fitness.

    weight = (successes + 1) / (attempts + 2) * (1 + avg_delta_pf clipped)
    Laplace smoothing prevents zero-weight lockout of unexplored types.
    """

    def __init__(self, db_url: str = "sqlite:///mutation_credits.db"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def record(
        self,
        mutation_type: MutationType,
        improved: bool,
        delta_pf: float = 0.0,
        delta_exp: float = 0.0,
    ) -> None:
        db: Session = self.SessionLocal()
        try:
            rec = db.query(CreditRecord).filter(
                CreditRecord.mutation_type == mutation_type.value
            ).first()
            if rec is None:
                rec = CreditRecord(mutation_type=mutation_type.value, attempts=0, successes=0)
                db.add(rec)
            rec.attempts += 1
            if improved:
                rec.successes += 1
            rec.total_delta_pf = (rec.total_delta_pf or 0.0) + delta_pf
            rec.total_delta_exp = (rec.total_delta_exp or 0.0) + delta_exp
            rec.last_updated = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def scores(self) -> list[MutationScore]:
        db: Session = self.SessionLocal()
        try:
            rows = db.query(CreditRecord).all()
        finally:
            db.close()
        out: list[MutationScore] = []
        for r in rows:
            attempts = r.attempts or 0
            successes = r.successes or 0
            sr = successes / attempts if attempts else 0.0
            avg_dpf = (r.total_delta_pf / attempts) if attempts else 0.0
            # Laplace-smoothed success rate, boosted by average PF delta
            weight = ((successes + 1) / (attempts + 2)) * (1.0 + max(-0.5, min(1.0, avg_dpf)))
            out.append(
                MutationScore(
                    mutation_type=MutationType(r.mutation_type),
                    attempts=attempts,
                    successes=successes,
                    success_rate=sr,
                    avg_delta_pf=avg_dpf,
                    weight=max(0.01, weight),
                )
            )
        return out

    def weights_dict(self) -> dict[str, float]:
        return {s.mutation_type.value: s.weight for s in self.scores()}

    def choose(
        self,
        candidates: list[MutationType],
        rng: random.Random | None = None,
        explore_epsilon: float = 0.2,
    ) -> MutationType:
        """Epsilon-greedy: with prob explore_epsilon pick uniformly; else weighted by credits."""
        if not candidates:
            raise ValueError("No candidate mutation types")
        rng = rng or random.Random()
        if rng.random() < explore_epsilon or not candidates:
            return rng.choice(candidates)
        w = self.weights_dict()
        weights = [max(0.01, w.get(m.value, 0.5)) for m in candidates]
        total = sum(weights)
        r = rng.random() * total
        acc = 0.0
        for m, wt in zip(candidates, weights):
            acc += wt
            if r <= acc:
                return m
        return candidates[-1]

    def get_scores_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "mutation_type": s.mutation_type.value,
                "attempts": s.attempts,
                "successes": s.successes,
                "success_rate": round(s.success_rate, 4),
                "avg_delta_pf": round(s.avg_delta_pf, 4),
                "weight": round(s.weight, 4),
            }
            for s in sorted(self.scores(), key=lambda x: x.weight, reverse=True)
        ]
