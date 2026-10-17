"""Event-driven Agent Orchestrator coordinating the full trading pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4

from trading_ai.agents.base import BaseAgent
from trading_ai.config.events import (
    BaseEvent,
    EventType,
    MarketDataUpdated,
    MarketStateUpdated,
    PerformanceUpdated,
    ResearchTriggered,
    RiskChecked,
    SignalGenerated,
    TradeClosed,
    TradeExecuted,
)
from trading_ai.config.settings import settings

logger = logging.getLogger(__name__)

Handler = Callable[[BaseEvent], Coroutine[Any, Any, None]]


class AgentHealth:
    __slots__ = ("name", "last_heartbeat", "error_count", "is_healthy", "last_error")

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_heartbeat: float = time.monotonic()
        self.error_count: int = 0
        self.is_healthy: bool = True
        self.last_error: str | None = None

    def record_success(self) -> None:
        self.last_heartbeat = time.monotonic()
        self.is_healthy = True

    def record_error(self, error: Exception) -> None:
        self.error_count += 1
        self.last_error = str(error)
        if self.error_count >= 5:
            self.is_healthy = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "last_heartbeat": self.last_heartbeat,
            "error_count": self.error_count,
            "is_healthy": self.is_healthy,
            "last_error": self.last_error,
        }


class EventMetrics:
    __slots__ = ("total_events", "events_by_type", "handler_times")

    def __init__(self) -> None:
        self.total_events: int = 0
        self.events_by_type: dict[str, int] = defaultdict(int)
        self.handler_times: dict[str, list[float]] = defaultdict(list)

    def record(self, event_type: EventType, handler_name: str, elapsed: float) -> None:
        self.total_events += 1
        self.events_by_type[event_type.value] += 1
        self.handler_times[handler_name].append(elapsed)
        if len(self.handler_times[handler_name]) > 100:
            self.handler_times[handler_name] = self.handler_times[handler_name][-100:]

    def to_dict(self) -> dict[str, Any]:
        avg_times: dict[str, float] = {}
        for name, times in self.handler_times.items():
            avg_times[name] = sum(times) / len(times) if times else 0.0
        return {
            "total_events": self.total_events,
            "events_by_type": dict(self.events_by_type),
            "avg_handler_times_ms": avg_times,
        }


class AgentOrchestrator:
    """Coordinates all agents in an event-driven trading pipeline.

    Flow:
        MarketDataUpdated
            -> MarketStateUpdated
            -> SignalGenerated
            -> RiskChecked
            -> TradeExecuted
            -> TradeClosed
            -> PerformanceUpdated
            -> ResearchTriggered
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self._agents: dict[str, BaseAgent] = {}
        self._health: dict[str, AgentHealth] = {}
        self._metrics = EventMetrics()
        self._event_log: list[dict[str, Any]] = []
        self._running = False
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._max_log_size = 1000

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    def register_agent(self, agent: BaseAgent) -> None:
        self._agents[agent.name] = agent
        self._health[agent.name] = AgentHealth(agent.name)
        logger.info("Registered agent: %s", agent.name)

    def get_agent(self, name: str) -> BaseAgent | None:
        return self._agents.get(name)

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed handler to %s", event_type.value)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            logger.warning("Orchestrator already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Orchestrator started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Orchestrator stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: BaseEvent) -> None:
        await self._event_queue.put(event)

    def dispatch_sync(self, event: BaseEvent) -> None:
        """Non-async push for use from sync contexts."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error("Event queue full, dropping event %s", event.event_type.value)

    async def _process_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await self._dispatch_event(event)

    async def _dispatch_event(self, event: BaseEvent) -> None:
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            logger.debug("No handlers for %s", event.event_type.value)
            return

        self._log_event(event)

        for handler in handlers:
            handler_name = handler.__qualname__
            start = time.monotonic()
            try:
                await handler(event)
                elapsed = (time.monotonic() - start) * 1000
                self._metrics.record(event.event_type, handler_name, elapsed)
                agent_name = getattr(handler, "_agent_name", None)
                if agent_name and agent_name in self._health:
                    self._health[agent_name].record_success()
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                self._metrics.record(event.event_type, handler_name, elapsed)
                agent_name = getattr(handler, "_agent_name", None)
                if agent_name and agent_name in self._health:
                    self._health[agent_name].record_error(exc)
                logger.exception(
                    "Handler %s failed for event %s: %s",
                    handler_name,
                    event.event_type.value,
                    exc,
                )

    def _log_event(self, event: BaseEvent) -> None:
        entry = {
            "event_id": str(event.event_id),
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
        }
        self._event_log.append(entry)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

    # ------------------------------------------------------------------
    # Convenience: build the default pipeline wiring
    # ------------------------------------------------------------------

    def wire_default_pipeline(
        self,
        market_data_agent: BaseAgent | None = None,
        market_state_agent: BaseAgent | None = None,
        signal_agent: BaseAgent | None = None,
        risk_agent: BaseAgent | None = None,
        execution_agent: BaseAgent | None = None,
        performance_agent: BaseAgent | None = None,
        research_agent: BaseAgent | None = None,
    ) -> None:
        """Wire the standard event chain between agents.

        Each agent is wrapped so that when its output event arrives, the
        handler calls ``agent.process(event)`` and then dispatches the
        next event in the pipeline.
        """
        if market_data_agent:
            self.register_agent(market_data_agent)
            self.subscribe(
                EventType.MARKET_DATA_UPDATED,
                self._make_handler(market_data_agent, EventType.MARKET_STATE_UPDATED),
            )

        if market_state_agent:
            self.register_agent(market_state_agent)
            self.subscribe(
                EventType.MARKET_STATE_UPDATED,
                self._make_handler(market_state_agent, EventType.SIGNAL_GENERATED),
            )

        if signal_agent:
            self.register_agent(signal_agent)
            self.subscribe(
                EventType.SIGNAL_GENERATED,
                self._make_handler(signal_agent, EventType.RISK_CHECKED),
            )

        if risk_agent:
            self.register_agent(risk_agent)
            self.subscribe(
                EventType.RISK_CHECKED,
                self._make_handler(risk_agent, EventType.TRADE_EXECUTED),
            )

        if execution_agent:
            self.register_agent(execution_agent)
            self.subscribe(
                EventType.TRADE_EXECUTED,
                self._make_handler(execution_agent, EventType.TRADE_CLOSED),
            )

        if performance_agent:
            self.register_agent(performance_agent)
            self.subscribe(
                EventType.TRADE_CLOSED,
                self._make_handler(performance_agent, EventType.PERFORMANCE_UPDATED),
            )

        if research_agent:
            self.register_agent(research_agent)
            self.subscribe(
                EventType.PERFORMANCE_UPDATED,
                self._make_handler(research_agent, EventType.RESEARCH_TRIGGERED),
            )

    def _make_handler(
        self, agent: BaseAgent, next_event_type: EventType
    ) -> Handler:
        """Create an async handler that processes an event and emits the next."""
        agent._agent_name = agent.name  # type: ignore[attr-defined]

        async def handler(event: BaseEvent) -> None:
            logger.debug("Agent '%s' processing %s", agent.name, event.event_type.value)
            result = agent.process(event)  # type: ignore[arg-type]
            if isinstance(result, dict) and result:
                next_event = self._build_next_event(next_event_type, event, result)
                if next_event is not None:
                    await self.dispatch(next_event)

        handler._agent_name = agent.name  # type: ignore[attr-defined]
        return handler

    @staticmethod
    def _build_next_event(
        next_type: EventType, prev: BaseEvent, result: dict[str, Any]
    ) -> BaseEvent | None:
        """Construct the next event from the previous event and agent result."""
        if next_type == EventType.MARKET_STATE_UPDATED:
            return MarketStateUpdated(
                symbol=result.get("symbol", getattr(prev, "symbol", "")),
                regime=result.get("regime", ""),
                volatility=result.get("volatility", 0.0),
                trend_strength=result.get("trend_strength", 0.0),
                session=result.get("session", ""),
            )
        if next_type == EventType.SIGNAL_GENERATED:
            return SignalGenerated(
                signal_id=result.get("signal_id", uuid4()),
                symbol=result.get("symbol", getattr(prev, "symbol", "")),
                direction=result.get("direction", ""),
                confidence=result.get("confidence", 0.0),
                entry_price=result.get("entry_price", 0.0),
                stop_loss=result.get("stop_loss", 0.0),
                take_profit=result.get("take_profit", 0.0),
                risk_reward=result.get("risk_reward", 0.0),
                strategy_version=result.get("strategy_version", 0),
                reasoning=result.get("reasoning", {}),
            )
        if next_type == EventType.RISK_CHECKED:
            return RiskChecked(
                signal_id=result.get("signal_id", uuid4()),
                approved=result.get("approved", False),
                position_size=result.get("position_size", 0.0),
                risk_amount=result.get("risk_amount", 0.0),
                reason=result.get("reason", ""),
            )
        if next_type == EventType.TRADE_EXECUTED:
            return TradeExecuted(
                trade_id=result.get("trade_id", uuid4()),
                signal_id=result.get("signal_id", uuid4()),
                symbol=result.get("symbol", ""),
                direction=result.get("direction", ""),
                entry_price=result.get("entry_price", 0.0),
                stop_loss=result.get("stop_loss", 0.0),
                take_profit=result.get("take_profit", 0.0),
                position_size=result.get("position_size", 0.0),
                strategy_version=result.get("strategy_version", 0),
            )
        if next_type == EventType.TRADE_CLOSED:
            return TradeClosed(
                trade_id=result.get("trade_id", uuid4()),
                exit_price=result.get("exit_price", 0.0),
                pnl=result.get("pnl", 0.0),
                r_multiple=result.get("r_multiple", 0.0),
                exit_reason=result.get("exit_reason", ""),
                duration_seconds=result.get("duration_seconds", 0),
                mae=result.get("mae", 0.0),
                mfe=result.get("mfe", 0.0),
            )
        if next_type == EventType.PERFORMANCE_UPDATED:
            return PerformanceUpdated(
                strategy_version=result.get("strategy_version", 0),
                total_trades=result.get("total_trades", 0),
                win_rate=result.get("win_rate", 0.0),
                profit_factor=result.get("profit_factor", 0.0),
                sharpe_ratio=result.get("sharpe_ratio", 0.0),
                max_drawdown=result.get("max_drawdown", 0.0),
                expectancy=result.get("expectancy", 0.0),
            )
        if next_type == EventType.RESEARCH_TRIGGERED:
            return ResearchTriggered(
                task_type=result.get("task_type", "auto"),
                symbol=result.get("symbol", getattr(prev, "symbol", "")),
                regime=result.get("regime", ""),
                priority=result.get("priority", 0),
                question=result.get("question", ""),
            )
        return None

    # ------------------------------------------------------------------
    # Health & status
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        agents_status = {}
        for name, health in self._health.items():
            agents_status[name] = health.to_dict()

        return {
            "running": self._running,
            "registered_agents": len(self._agents),
            "agents": agents_status,
            "queue_size": self._event_queue.qsize(),
            "metrics": self._metrics.to_dict(),
            "recent_events": self._event_log[-20:],
        }

    def get_metrics(self) -> dict[str, Any]:
        return self._metrics.to_dict()

    def get_event_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._event_log[-limit:]
