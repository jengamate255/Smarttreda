from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column, String, Float, DateTime, Text, JSON, Integer,
    create_engine, Index, ForeignKey, Table,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

Base = declarative_base()


class NodeType(str, Enum):
    MARKET = "market"
    SYMBOL = "symbol"
    TIMEFRAME = "timeframe"
    REGIME = "regime"
    STRATEGY = "strategy"
    TRADE = "trade"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    RESULT = "result"
    STRATEGY_VERSION = "strategy_version"


graph_edges = Table(
    "graph_edges",
    Base.metadata,
    Column("id", String(36), primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("source_id", String(36), nullable=False, index=True),
    Column("target_id", String(36), nullable=False, index=True),
    Column("edge_type", String(32), nullable=False, index=True),
    Column("weight", Float, default=1.0),
    Column("metadata", JSON, default={}),
    Column("created_at", DateTime, default=datetime.utcnow),
    Index("ix_edge_composite", "source_id", "target_id", "edge_type"),
)

graph_nodes = Table(
    "graph_nodes",
    Base.metadata,
    Column("id", String(36), primary_key=True),
    Column("node_type", String(32), nullable=False, index=True),
    Column("label", String(256), nullable=False),
    Column("data", JSON, default={}),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)


@dataclass
class GraphNode:
    id: str
    node_type: NodeType
    label: str
    data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": n.id, "type": n.node_type.value, "label": n.label, "data": n.data}
                for n in self.nodes
            ],
            "edges": [
                {"source": e.source_id, "target": e.target_id, "type": e.edge_type, "weight": e.weight}
                for e in self.edges
            ],
        }


class ResearchGraph:
    def __init__(self, db_url: str = "sqlite:///research_graph.db"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def add_node(self, node_type: NodeType, label: str, data: dict | None = None, node_id: str | None = None) -> str:
        nid = node_id or str(uuid.uuid4())
        db: Session = self.SessionLocal()
        try:
            db.execute(
                graph_nodes.insert().values(
                    id=nid,
                    node_type=node_type.value,
                    label=label,
                    data=data or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            db.commit()
            return nid
        finally:
            db.close()

    def add_edge(
        self, source_id: str, target_id: str, edge_type: str,
        weight: float = 1.0, metadata: dict | None = None,
    ) -> str:
        eid = str(uuid.uuid4())
        db: Session = self.SessionLocal()
        try:
            db.execute(
                graph_edges.insert().values(
                    id=eid,
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=edge_type,
                    weight=weight,
                    metadata=metadata or {},
                    created_at=datetime.utcnow(),
                )
            )
            db.commit()
            return eid
        finally:
            db.close()

    def get_node(self, node_id: str) -> GraphNode | None:
        db: Session = self.SessionLocal()
        try:
            result = db.execute(
                graph_nodes.select().where(graph_nodes.c.id == node_id)
            ).fetchone()
            if result:
                return GraphNode(
                    id=result.id,
                    node_type=NodeType(result.node_type),
                    label=result.label,
                    data=result.data or {},
                    created_at=result.created_at,
                )
            return None
        finally:
            db.close()

    def query_subgraph(
        self, node_type: NodeType | None = None, filters: dict | None = None
    ) -> SubGraph:
        db: Session = self.SessionLocal()
        try:
            node_query = graph_nodes.select()
            if node_type:
                node_query = node_query.where(graph_nodes.c.node_type == node_type.value)

            node_rows = db.execute(node_query).fetchall()
            node_ids = {r.id for r in node_rows}

            edge_query = graph_edges.select().where(
                graph_edges.c.source_id.in_(node_ids) | graph_edges.c.target_id.in_(node_ids)
            )
            edge_rows = db.execute(edge_query).fetchall()

            nodes = [
                GraphNode(
                    id=r.id,
                    node_type=NodeType(r.node_type),
                    label=r.label,
                    data=r.data or {},
                    created_at=r.created_at,
                )
                for r in node_rows
            ]
            edges = [
                GraphEdge(
                    id=r.id,
                    source_id=r.source_id,
                    target_id=r.target_id,
                    edge_type=r.edge_type,
                    weight=r.weight,
                    metadata=r.metadata or {},
                )
                for r in edge_rows
            ]

            if filters:
                filtered_nodes = []
                for n in nodes:
                    match = True
                    for key, val in filters.items():
                        if key == "label" and val.lower() not in n.label.lower():
                            match = False
                        elif key in n.data and n.data[key] != val:
                            match = False
                    if match:
                        filtered_nodes.append(n)

                filtered_ids = {n.id for n in filtered_nodes}
                filtered_edges = [
                    e for e in edges
                    if e.source_id in filtered_ids or e.target_id in filtered_ids
                ]
                return SubGraph(nodes=filtered_nodes, edges=filtered_edges)

            return SubGraph(nodes=nodes, edges=edges)
        finally:
            db.close()

    def find_paths(
        self, source_id: str, target_id: str, max_depth: int = 6
    ) -> list[list[str]]:
        db: Session = self.SessionLocal()
        try:
            adjacency: dict[str, list[tuple[str, str]]] = {}
            edge_rows = db.execute(graph_edges.select()).fetchall()
            for r in edge_rows:
                adjacency.setdefault(r.source_id, []).append((r.target_id, r.edge_type))
                adjacency.setdefault(r.target_id, []).append((r.source_id, f"{r.edge_type}_rev"))

            paths: list[list[str]] = []
            queue: list[list[str]] = [[source_id]]

            while queue:
                current_path = queue.pop(0)
                current_node = current_path[-1]

                if current_node == target_id:
                    paths.append(current_path)
                    continue

                if len(current_path) > max_depth:
                    continue

                for neighbor, edge_type in adjacency.get(current_node, []):
                    if neighbor not in current_path:
                        queue.append(current_path + [neighbor])

            return paths
        finally:
            db.close()

    def get_strategy_lineage(self, strategy_id: str) -> dict:
        db: Session = self.SessionLocal()
        try:
            node = db.execute(
                graph_nodes.select().where(graph_nodes.c.id == strategy_id)
            ).fetchone()
            if not node:
                return {"error": f"Strategy {strategy_id} not found"}

            ancestry = self._build_ancestry(db, strategy_id)
            descendants = self._build_descendants(db, strategy_id)

            return {
                "strategy": {
                    "id": node.id,
                    "label": node.label,
                    "data": node.data,
                },
                "ancestry": ancestry,
                "descendants": descendants,
            }
        finally:
            db.close()

    def _build_ancestry(self, db: Session, node_id: str, depth: int = 0, max_depth: int = 20) -> list[dict]:
        if depth > max_depth:
            return []

        parent_edges = db.execute(
            graph_edges.select().where(
                graph_edges.c.target_id == node_id,
                graph_edges.c.edge_type == "evolves_from",
            )
        ).fetchall()

        ancestors = []
        for edge in parent_edges:
            parent = db.execute(
                graph_nodes.select().where(graph_nodes.c.id == edge.source_id)
            ).fetchone()
            if parent:
                ancestors.append({
                    "id": parent.id,
                    "label": parent.label,
                    "data": parent.data,
                    "edge_metadata": edge.metadata or {},
                    "depth": depth + 1,
                    "ancestors": self._build_ancestry(db, parent.id, depth + 1, max_depth),
                })
        return ancestors

    def _build_descendants(self, db: Session, node_id: str, depth: int = 0, max_depth: int = 20) -> list[dict]:
        if depth > max_depth:
            return []

        child_edges = db.execute(
            graph_edges.select().where(
                graph_edges.c.source_id == node_id,
                graph_edges.c.edge_type == "evolves_from",
            )
        ).fetchall()

        descendants = []
        for edge in child_edges:
            child = db.execute(
                graph_nodes.select().where(graph_nodes.c.id == edge.target_id)
            ).fetchone()
            if child:
                descendants.append({
                    "id": child.id,
                    "label": child.label,
                    "data": child.data,
                    "edge_metadata": edge.metadata or {},
                    "depth": depth + 1,
                    "descendants": self._build_descendants(db, child.id, depth + 1, max_depth),
                })
        return descendants

    def connect_strategy_to_context(
        self, strategy_id: str, symbol_id: str, timeframe_id: str,
        regime_id: str, market_id: str,
    ):
        db: Session = self.SessionLocal()
        try:
            connections = [
                (strategy_id, symbol_id, "trades"),
                (strategy_id, timeframe_id, "operates_on"),
                (strategy_id, regime_id, "performs_in"),
                (symbol_id, market_id, "belongs_to"),
                (timeframe_id, strategy_id, "used_by"),
            ]
            for src, tgt, edge_type in connections:
                existing = db.execute(
                    graph_edges.select().where(
                        graph_edges.c.source_id == src,
                        graph_edges.c.target_id == tgt,
                        graph_edges.c.edge_type == edge_type,
                    )
                ).fetchone()
                if not existing:
                    db.execute(
                        graph_edges.insert().values(
                            id=str(uuid.uuid4()),
                            source_id=src,
                            target_id=tgt,
                            edge_type=edge_type,
                            weight=1.0,
                            metadata={},
                            created_at=datetime.utcnow(),
                        )
                    )
            db.commit()
        finally:
            db.close()

    def record_experiment_link(
        self, hypothesis_id: str, experiment_id: str,
        result_id: str, strategy_id: str,
    ):
        links = [
            (hypothesis_id, experiment_id, "tested_by"),
            (experiment_id, result_id, "produced"),
            (result_id, strategy_id, "evaluates"),
            (strategy_id, hypothesis_id, "generated_from"),
        ]
        db: Session = self.SessionLocal()
        try:
            for src, tgt, edge_type in links:
                db.execute(
                    graph_edges.insert().values(
                        id=str(uuid.uuid4()),
                        source_id=src,
                        target_id=tgt,
                        edge_type=edge_type,
                        weight=1.0,
                        metadata={},
                        created_at=datetime.utcnow(),
                    )
                )
            db.commit()
        finally:
            db.close()

    def get_node_by_label(self, label: str, node_type: NodeType | None = None) -> GraphNode | None:
        db: Session = self.SessionLocal()
        try:
            query = graph_nodes.select().where(graph_nodes.c.label == label)
            if node_type:
                query = query.where(graph_nodes.c.node_type == node_type.value)
            result = db.execute(query).fetchone()
            if result:
                return GraphNode(
                    id=result.id,
                    node_type=NodeType(result.node_type),
                    label=result.label,
                    data=result.data or {},
                    created_at=result.created_at,
                )
            return None
        finally:
            db.close()
