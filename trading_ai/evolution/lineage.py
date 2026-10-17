from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column, String, Float, DateTime, Text, JSON, Integer, Boolean,
    create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()


class MutationType(str, Enum):
    PARAMETER_CHANGE = "parameter_change"
    REGIME_FILTER = "regime_filter"
    SESSION_FILTER = "session_filter"
    ENTRY_LOGIC = "entry_logic"
    EXIT_LOGIC = "exit_logic"
    RISK_MANAGEMENT = "risk_management"
    COMBINATION = "combination"


class PromotionStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class LineageRecord(Base):
    __tablename__ = "strategy_lineage"

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False, index=True)
    parent_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    promotion_status = Column(String(32), nullable=False, default=PromotionStatus.ACTIVE.value)

    mutation_type = Column(String(32), nullable=True)
    mutation_description = Column(Text, nullable=True)
    hypothesis_id = Column(String(36), nullable=True, index=True)
    experiment_id = Column(String(36), nullable=True)
    gatekeeper_evaluation_id = Column(String(36), nullable=True)

    parameters = Column(JSON, default={})
    parent_parameters = Column(JSON, default={})
    promotion_decision = Column(String(32), nullable=True)
    promotion_reasons = Column(JSON, default=[])
    metrics = Column(JSON, default={})
    metadata_json = Column(JSON, default={})

    rollback_target = Column(String(36), nullable=True)


@dataclass
class StrategyVersion:
    id: str
    version: str
    parent_id: str | None
    created_at: datetime
    promotion_status: PromotionStatus
    mutation_type: MutationType | None = None
    mutation_description: str | None = None
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    gatekeeper_evaluation_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    parent_parameters: dict[str, Any] = field(default_factory=dict)
    promotion_decision: str | None = None
    promotion_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineageTree:
    root: StrategyVersion
    ancestors: list[StrategyVersion]
    descendants: list[StrategyVersion]


class StrategyLineage:
    def __init__(self, db_url: str = "sqlite:///strategy_lineage.db"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def create_version(
        self,
        version: str,
        parameters: dict[str, Any],
        parent_id: str | None = None,
        mutation_type: MutationType | None = None,
        mutation_description: str | None = None,
        hypothesis_id: str | None = None,
        experiment_id: str | None = None,
        gatekeeper_evaluation_id: str | None = None,
    ) -> str:
        nid = str(uuid.uuid4())
        parent_params = {}
        if parent_id:
            parent = self.get_version(parent_id)
            if parent:
                parent_params = parent.parameters

        db: Session = self.SessionLocal()
        try:
            record = LineageRecord(
                id=nid,
                version=version,
                parent_id=parent_id,
                created_at=datetime.utcnow(),
                promotion_status=PromotionStatus.ACTIVE.value,
                mutation_type=mutation_type.value if mutation_type else None,
                mutation_description=mutation_description,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                gatekeeper_evaluation_id=gatekeeper_evaluation_id,
                parameters=parameters,
                parent_parameters=parent_params,
                metrics={},
                metadata_json={},
            )
            db.add(record)
            db.commit()
            return nid
        finally:
            db.close()

    def get_version(self, version_id: str) -> StrategyVersion | None:
        db: Session = self.SessionLocal()
        try:
            record = db.query(LineageRecord).filter(LineageRecord.id == version_id).first()
            if not record:
                return None
            return self._record_to_version(record)
        finally:
            db.close()

    def get_version_by_label(self, version_label: str) -> StrategyVersion | None:
        db: Session = self.SessionLocal()
        try:
            record = db.query(LineageRecord).filter(LineageRecord.version == version_label).first()
            if not record:
                return None
            return self._record_to_version(record)
        finally:
            db.close()

    def record_promotion(
        self, version_id: str, decision: str, reasons: list[str], metrics: dict[str, Any]
    ):
        db: Session = self.SessionLocal()
        try:
            record = db.query(LineageRecord).filter(LineageRecord.id == version_id).first()
            if record:
                record.promotion_decision = decision
                record.promotion_reasons = reasons
                record.metrics = metrics
                if decision == "promote":
                    record.promotion_status = PromotionStatus.ACTIVE.value
                    self._supersede_siblings(db, version_id)
                elif decision == "reject":
                    record.promotion_status = PromotionStatus.RETIRED.value
                db.commit()
        finally:
            db.close()

    def _supersede_siblings(self, db: Session, active_id: str):
        active = db.query(LineageRecord).filter(LineageRecord.id == active_id).first()
        if not active or not active.parent_id:
            return

        siblings = db.query(LineageRecord).filter(
            LineageRecord.parent_id == active.parent_id,
            LineageRecord.id != active_id,
            LineageRecord.promotion_status == PromotionStatus.ACTIVE.value,
        ).all()

        for sibling in siblings:
            sibling.promotion_status = PromotionStatus.SUPERSEDED.value

    def retire_strategy(self, version_id: str, reason: str):
        db: Session = self.SessionLocal()
        try:
            record = db.query(LineageRecord).filter(LineageRecord.id == version_id).first()
            if record:
                record.promotion_status = PromotionStatus.RETIRED.value
                reasons = record.promotion_reasons or []
                reasons.append(f"Retired: {reason}")
                record.promotion_reasons = reasons
                db.commit()
        finally:
            db.close()

    def rollback(self, target_version_id: str) -> str | None:
        target = self.get_version(target_version_id)
        if not target:
            return None

        all_versions = self.get_all_versions()
        active = [v for v in all_versions if v.promotion_status == PromotionStatus.ACTIVE]
        for v in active:
            self.retire_strategy(v.id, f"Rolled back to {target.version}")

        new_id = self.create_version(
            version=f"{target.version}_rollback_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            parameters=target.parameters.copy(),
            parent_id=target.id,
            mutation_type=MutationType.PARAMETER_CHANGE,
            mutation_description=f"Rollback to {target.version}",
        )

        self.record_promotion(
            new_id, "promote",
            [f"Rollback to {target.version}"],
            target.metrics,
        )

        return new_id

    def get_lineage_tree(self, version_id: str) -> LineageTree | None:
        version = self.get_version(version_id)
        if not version:
            return None

        ancestors = self._get_ancestors(version_id)
        descendants = self._get_descendants(version_id)

        return LineageTree(
            root=version,
            ancestors=ancestors,
            descendants=descendants,
        )

    def _get_ancestors(self, version_id: str, max_depth: int = 50) -> list[StrategyVersion]:
        ancestors = []
        current_id = version_id
        depth = 0

        while depth < max_depth:
            version = self.get_version(current_id)
            if not version or not version.parent_id:
                break
            parent = self.get_version(version.parent_id)
            if not parent:
                break
            ancestors.append(parent)
            current_id = parent.id
            depth += 1

        return ancestors

    def _get_descendants(self, version_id: str, max_depth: int = 50) -> list[StrategyVersion]:
        db: Session = self.SessionLocal()
        try:
            descendants = []
            queue = [(version_id, 0)]

            while queue:
                current_id, depth = queue.pop(0)
                if depth >= max_depth:
                    continue

                children = db.query(LineageRecord).filter(
                    LineageRecord.parent_id == current_id
                ).all()

                for child in children:
                    descendants.append(self._record_to_version(child))
                    queue.append((child.id, depth + 1))

            return descendants
        finally:
            db.close()

    def get_all_versions(self) -> list[StrategyVersion]:
        db: Session = self.SessionLocal()
        try:
            records = db.query(LineageRecord).order_by(LineageRecord.created_at).all()
            return [self._record_to_version(r) for r in records]
        finally:
            db.close()

    def get_active_versions(self) -> list[StrategyVersion]:
        db: Session = self.SessionLocal()
        try:
            records = db.query(LineageRecord).filter(
                LineageRecord.promotion_status == PromotionStatus.ACTIVE.value
            ).all()
            return [self._record_to_version(r) for r in records]
        finally:
            db.close()

    def get_version_chain(self, version_id: str) -> list[dict]:
        ancestors = self._get_ancestors(version_id)
        current = self.get_version(version_id)
        descendants = self._get_descendants(version_id)

        chain = []
        for a in reversed(ancestors):
            chain.append({
                "id": a.id,
                "version": a.version,
                "status": a.promotion_status.value,
                "mutation": a.mutation_description,
            })

        if current:
            chain.append({
                "id": current.id,
                "version": current.version,
                "status": current.promotion_status.value,
                "mutation": "current",
            })

        for d in descendants:
            chain.append({
                "id": d.id,
                "version": d.version,
                "status": d.promotion_status.value,
                "mutation": d.mutation_description,
            })

        return chain

    def get_failed_experiments(self) -> list[dict]:
        db: Session = self.SessionLocal()
        try:
            records = db.query(LineageRecord).filter(
                LineageRecord.promotion_status == PromotionStatus.RETIRED.value
            ).all()
            return [
                {
                    "id": r.id,
                    "version": r.version,
                    "parent_id": r.parent_id,
                    "created_at": r.created_at.isoformat(),
                    "mutation_type": r.mutation_type,
                    "mutation_description": r.mutation_description,
                    "hypothesis_id": r.hypothesis_id,
                    "experiment_id": r.experiment_id,
                    "promotion_decision": r.promotion_decision,
                    "promotion_reasons": r.promotion_reasons,
                    "metrics": r.metrics,
                }
                for r in records
            ]
        finally:
            db.close()

    def _record_to_version(self, record: LineageRecord) -> StrategyVersion:
        return StrategyVersion(
            id=record.id,
            version=record.version,
            parent_id=record.parent_id,
            created_at=record.created_at,
            promotion_status=PromotionStatus(record.promotion_status),
            mutation_type=MutationType(record.mutation_type) if record.mutation_type else None,
            mutation_description=record.mutation_description,
            hypothesis_id=record.hypothesis_id,
            experiment_id=record.experiment_id,
            gatekeeper_evaluation_id=record.gatekeeper_evaluation_id,
            parameters=record.parameters or {},
            parent_parameters=record.parent_parameters or {},
            promotion_decision=record.promotion_decision,
            promotion_reasons=record.promotion_reasons or [],
            metrics=record.metrics or {},
            metadata=record.metadata_json or {},
        )
