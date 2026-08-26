"""LLM interface.

The LLM is NOT a decision maker. It explains market state, reviews the
structured model output, summarizes reasons, detects contradictions, and
produces human-readable trade reasoning. Quantitative models remain the source
of numerical decisions.

Backends plug in behind `LLMProvider`. Currently shipped:
  - NoopProvider: rule-based templated summary (always available)
  - OllamaProvider: talks to a local Ollama server (llama3 etc.) via stdlib HTTP

Later backends (Hugging Face, APIs) implement the same protocol without
touching the trading engine.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Protocol


class LLMProvider(Protocol):
    name: str

    def explain(self, decision: dict[str, Any], context: dict[str, Any]) -> str:
        ...


class NoopProvider:
    """Deterministic templated summary; no external dependency."""

    name = "noop"

    def explain(self, decision: dict[str, Any], context: dict[str, Any]) -> str:
        signal = decision.get("signal", "NO_TRADE")
        lines = [
            f"Decision: {signal}",
            f"Setup probability: {decision.get('setup_probability')}",
            f"Direction probabilities: buy={decision.get('buy_probability')}, "
            f"sell={decision.get('sell_probability')}, no_trade={decision.get('no_trade_probability')}",
            f"P(TP before SL): {decision.get('tp_before_sl_probability')}",
            f"Market regime: {context.get('market_regime', 'UNKNOWN')}",
            f"Session: {context.get('session', 'UNKNOWN')}",
        ]
        if decision.get("reasons"):
            lines.append("Reasons: " + "; ".join(decision["reasons"]))
        if decision.get("contradictions"):
            lines.append("CONTRADICTIONS: " + "; ".join(decision["contradictions"]))
        return "\n".join(lines)


class OllamaProvider:
    """Local Ollama backend (https://ollama.com). Model e.g. llama3.2."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def explain(self, decision: dict[str, Any], context: dict[str, Any]) -> str:
        prompt = (
            "You are a review assistant for a quantitative FX trading agent. "
            "The numbers are produced by supervised ML models; you must NOT invent "
            "trade calls. Summarize the state, check for internal contradictions, "
            "and describe risks in plain language.\n\n"
            f"DECISION JSON:\n{json.dumps(decision, indent=2, default=str)}\n"
            f"CONTEXT:\n{json.dumps(context, indent=2, default=str)}\n"
            "Produce a short summary (max 120 words)."
        )
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("response", "").strip()
        except Exception as exc:
            return f"Ollama unavailable ({exc}); no LLM explanation generated."


def build_provider(cfg) -> LLMProvider:
    backend = str(cfg.get("llm.backend", "noop")).lower()
    if backend == "ollama":
        return OllamaProvider(
            base_url=cfg.get("llm.ollama_base_url", "http://localhost:11434"),
            model=cfg.get("llm.ollama_model", "llama3.2"),
        )
    return NoopProvider()
