"""Health monitoring for the trading AI system."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    alert_id: str
    severity: AlertSeverity
    component: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity.value,
            "component": self.component,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged,
        }


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 2),
            "last_check": self.last_check.isoformat(),
            "metadata": self.metadata,
        }


class HealthMonitor:
    """Monitors system health across all components.

    Checks:
        - Database connectivity
        - Data feed status
        - Agent health
        - Memory usage
        - Disk space

    Alerts on:
        - Agent failures
        - Data gaps
        - Performance degradation
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._check_interval: float = config.get("check_interval", 30.0)
        self._data_gap_threshold: float = config.get("data_gap_threshold", 60.0)
        self._memory_warning_pct: float = config.get("memory_warning_pct", 80.0)
        self._memory_critical_pct: float = config.get("memory_critical_pct", 95.0)
        self._disk_warning_pct: float = config.get("disk_warning_pct", 85.0)
        self._disk_critical_pct: float = config.get("disk_critical_pct", 95.0)

        self._components: dict[str, ComponentHealth] = {}
        self._alerts: list[Alert] = []
        self._alert_counter = 0
        self._running = False
        self._check_task: asyncio.Task[None] | None = None
        self._last_data_times: dict[str, datetime] = {}
        self._agent_health: dict[str, dict[str, Any]] = {}
        self._start_time = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._check_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Health monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
            self._check_task = None
        logger.info("Health monitor stopped")

    async def _monitoring_loop(self) -> None:
        while self._running:
            try:
                await self.run_checks()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Monitoring loop error: %s", exc)
            await asyncio.sleep(self._check_interval)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def run_checks(self) -> dict[str, Any]:
        start = time.monotonic()

        await asyncio.gather(
            self._check_database(),
            self._check_data_feed(),
            self._check_agents(),
            self._check_memory(),
            self._check_disk(),
            return_exceptions=True,
        )

        elapsed = (time.monotonic() - start) * 1000
        logger.debug("Health checks completed in %.1fms", elapsed)
        return self.get_status()

    async def _check_database(self) -> None:
        start = time.monotonic()
        try:
            from trading_ai.database.engine import get_engine

            engine = get_engine()
            if engine is not None:
                async with engine.connect() as conn:
                    await conn.execute(
                        __import__("sqlalchemy").text("SELECT 1")
                    )
                elapsed = (time.monotonic() - start) * 1000
                self._components["database"] = ComponentHealth(
                    name="database",
                    status=HealthStatus.HEALTHY,
                    latency_ms=elapsed,
                )
            else:
                self._components["database"] = ComponentHealth(
                    name="database",
                    status=HealthStatus.DEGRADED,
                    message="Engine not initialized",
                )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            self._components["database"] = ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=str(exc),
                latency_ms=elapsed,
            )
            self._create_alert(
                AlertSeverity.CRITICAL,
                "database",
                f"Database connectivity failure: {exc}",
            )

    async def _check_data_feed(self) -> None:
        now = datetime.now(timezone.utc)
        gaps: list[str] = []
        for symbol, last_time in self._last_data_times.items():
            elapsed = (now - last_time).total_seconds()
            if elapsed > self._data_gap_threshold:
                gaps.append(symbol)

        if gaps:
            self._components["data_feed"] = ComponentHealth(
                name="data_feed",
                status=HealthStatus.DEGRADED,
                message=f"Data gaps detected: {', '.join(gaps)}",
                metadata={"gaps": gaps},
            )
            self._create_alert(
                AlertSeverity.WARNING,
                "data_feed",
                f"Data feed gap for: {', '.join(gaps)}",
            )
        else:
            self._components["data_feed"] = ComponentHealth(
                name="data_feed",
                status=HealthStatus.HEALTHY,
                metadata={"symbols_tracked": len(self._last_data_times)},
            )

    async def _check_agents(self) -> None:
        unhealthy = [
            name
            for name, info in self._agent_health.items()
            if not info.get("is_healthy", True)
        ]

        if unhealthy:
            self._components["agents"] = ComponentHealth(
                name="agents",
                status=HealthStatus.UNHEALTHY,
                message=f"Unhealthy agents: {', '.join(unhealthy)}",
                metadata={"unhealthy_agents": unhealthy},
            )
            self._create_alert(
                AlertSeverity.CRITICAL,
                "agents",
                f"Agent failures detected: {', '.join(unhealthy)}",
            )
        else:
            self._components["agents"] = ComponentHealth(
                name="agents",
                status=HealthStatus.HEALTHY,
                metadata={"healthy_agents": list(self._agent_health.keys())},
            )

    async def _check_memory(self) -> None:
        try:
            import psutil

            mem = psutil.virtual_memory()
            pct = mem.percent

            if pct >= self._memory_critical_pct:
                status = HealthStatus.UNHEALTHY
                severity = AlertSeverity.CRITICAL
            elif pct >= self._memory_warning_pct:
                status = HealthStatus.DEGRADED
                severity = AlertSeverity.WARNING
            else:
                status = HealthStatus.HEALTHY
                severity = AlertSeverity.INFO

            self._components["memory"] = ComponentHealth(
                name="memory",
                status=status,
                metadata={
                    "used_pct": round(pct, 1),
                    "used_gb": round(mem.used / (1024**3), 2),
                    "total_gb": round(mem.total / (1024**3), 2),
                },
            )

            if status != HealthStatus.HEALTHY:
                self._create_alert(
                    severity,
                    "memory",
                    f"Memory usage at {pct:.1f}%",
                )
        except ImportError:
            self._components["memory"] = ComponentHealth(
                name="memory",
                status=HealthStatus.HEALTHY,
                message="psutil not available, skipping check",
            )

    async def _check_disk(self) -> None:
        try:
            disk = shutil.disk_usage(os.getcwd())
            pct = (disk.used / disk.total) * 100

            if pct >= self._disk_critical_pct:
                status = HealthStatus.UNHEALTHY
                severity = AlertSeverity.CRITICAL
            elif pct >= self._disk_warning_pct:
                status = HealthStatus.DEGRADED
                severity = AlertSeverity.WARNING
            else:
                status = HealthStatus.HEALTHY
                severity = AlertSeverity.INFO

            self._components["disk"] = ComponentHealth(
                name="disk",
                status=status,
                metadata={
                    "used_pct": round(pct, 1),
                    "used_gb": round(disk.used / (1024**3), 2),
                    "total_gb": round(disk.total / (1024**3), 2),
                },
            )

            if status != HealthStatus.HEALTHY:
                self._create_alert(
                    severity,
                    "disk",
                    f"Disk usage at {pct:.1f}%",
                )
        except Exception as exc:
            self._components["disk"] = ComponentHealth(
                name="disk",
                status=HealthStatus.DEGRADED,
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # External updates
    # ------------------------------------------------------------------

    def record_data_tick(self, symbol: str) -> None:
        self._last_data_times[symbol] = datetime.now(timezone.utc)

    def update_agent_health(self, agent_name: str, is_healthy: bool, metadata: dict[str, Any] | None = None) -> None:
        self._agent_health[agent_name] = {
            "is_healthy": is_healthy,
            "last_update": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _create_alert(self, severity: AlertSeverity, component: str, message: str) -> None:
        self._alert_counter += 1
        alert = Alert(
            alert_id=f"alert-{self._alert_counter:06d}",
            severity=severity,
            component=component,
            message=message,
        )
        self._alerts.append(alert)
        if len(self._alerts) > 500:
            self._alerts = self._alerts[-500:]

        log_fn = {
            AlertSeverity.INFO: logger.info,
            AlertSeverity.WARNING: logger.warning,
            AlertSeverity.CRITICAL: logger.critical,
        }.get(severity, logger.info)
        log_fn("ALERT [%s] %s: %s", severity.value, component, message)

    def get_alerts(
        self,
        severity: AlertSeverity | None = None,
        component: str | None = None,
        unacknowledged_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        filtered = self._alerts
        if severity:
            filtered = [a for a in filtered if a.severity == severity]
        if component:
            filtered = [a for a in filtered if a.component == component]
        if unacknowledged_only:
            filtered = [a for a in filtered if not a.acknowledged]
        return [a.to_dict() for a in filtered[-limit:]]

    def acknowledge_alert(self, alert_id: str) -> bool:
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False

    # ------------------------------------------------------------------
    # Dashboard data
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        overall = HealthStatus.HEALTHY
        for comp in self._components.values():
            if comp.status == HealthStatus.UNHEALTHY:
                overall = HealthStatus.UNHEALTHY
                break
            if comp.status == HealthStatus.DEGRADED:
                overall = HealthStatus.DEGRADED

        uptime = time.monotonic() - self._start_time
        return {
            "overall_status": overall.value,
            "uptime_seconds": round(uptime, 1),
            "components": {name: comp.to_dict() for name, comp in self._components.items()},
            "active_alerts": len([a for a in self._alerts if not a.acknowledged]),
            "total_alerts": len(self._alerts),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_dashboard_data(self) -> dict[str, Any]:
        return {
            "status": self.get_status(),
            "recent_alerts": self.get_alerts(limit=20),
            "agent_health": dict(self._agent_health),
            "data_feed_status": {
                symbol: {
                    "last_update": ts.isoformat(),
                    "age_seconds": (datetime.now(timezone.utc) - ts).total_seconds(),
                }
                for symbol, ts in self._last_data_times.items()
            },
        }
