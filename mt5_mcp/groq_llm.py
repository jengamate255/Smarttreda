"""Groq LLM provider for the Multi-Asset Trading Intelligence Platform.

This module provides a Groq API client for LLM-powered agent orchestration.
Groq is used as ONE input source among many in the trading decision pipeline,
never as an automatic signal generator.

The platform's fundamental principle: LLM outputs are contextual information,
not trading signals. All conclusions must be supported by market data evidence.

Key design principles:
- LLM outputs are treated as hypotheses, not directives
- Every LLM-derived conclusion must be validated by market data
- The Red Team Agent actively attacks LLM-generated theses
- Configurable via environment variables, can be disabled
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("mt5_mcp.groq_llm")

# Groq model constants - currently available models (Sept 2025)
GROQ_MODELS = {
    "qwen3-8b": "qwen/qwen3.8-27b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "allam-2-7b": "allam-2-7b",
    "compound": "groq/compound",
    "compound-mini": "groq/compound-mini",
}

# Default model from environment - Qwen 3.8B is widely available and capable
DEFAULT_MODEL = "qwen3-8b"

# API key from environment (required)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Whether LLM is enabled (default: True, can be set to false via env)
ENABLED = True


class GroqResponse(BaseModel):
    """Structured response from Groq LLM."""
    task_id: str
    agent_id: str
    content: str
    usage: Optional[Dict[str, Any]] = None
    model: str = DEFAULT_MODEL
    timestamp: str = ""
    finish_reason: Optional[str] = None
    system_fingerprint: Optional[str] = None


class GroqRequest(BaseModel):
    """Request structure for Groq LLM calls."""
    model: str = Field(default=DEFAULT_MODEL)
    messages: List[Dict[str, str]]
    temperature: float = Field(default=0.3)
    max_tokens: int = Field(default=4096)
    timeout_seconds: int = Field(default=30)


def is_enabled() -> bool:
    """Check if Groq LLM is enabled."""
    return ENABLED and bool(GROQ_API_KEY)


def check_health() -> bool:
    """Check if Groq API is accessible."""
    if not is_enabled():
        return False
    try:
        import httpx
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        payload = {
            "model": GROQ_MODELS.get(DEFAULT_MODEL, DEFAULT_MODEL),
            "messages": [{"role": "user", "content": "health check"}],
            "max_tokens": 1,
        }
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Groq health check failed: %s", exc)
        return False


def generate(
    messages: List[Dict[str, str]],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> GroqResponse:
    """Generate a completion from Groq LLM.

    This is a structured API - the output is treated as contextual information,
    NOT as automatic trading signals. All LLM-derived hypotheses must be
    validated by market data and go through the full agent pipeline.

    Args:
        messages: List of message dicts with 'role' and 'content'
        model: Groq model name (uses default from env if not specified)
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate

    Returns:
        GroqResponse with the generated content and metadata

    Raises:
        RuntimeError: If Groq is not enabled or API call fails
    """
    if not is_enabled():
        raise RuntimeError("Groq LLM is not enabled. Set GROQ_ENABLED=false or provide GROQ_API_KEY.")

    actual_model = model or DEFAULT_MODEL
    actual_temperature = temperature if temperature is not None else 0.3
    actual_max_tokens = max_tokens if max_tokens is not None else 4096

    try:
        import httpx

        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

        payload = {
            "model": GROQ_MODELS.get(actual_model, actual_model),
            "messages": messages,
            "temperature": actual_temperature,
            "max_tokens": actual_max_tokens,
        }

        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0,
        )

        if resp.status_code != 200:
            logger.error("Groq API error: %s - %s", resp.status_code, resp.text)
            raise RuntimeError(f"Groq API returned status {resp.status_code}")

        data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        return GroqResponse(
            task_id=f"groq_{uuid.uuid4().hex[:8]}",
            agent_id="groq_orchestrator",
            content=message.get("content", ""),
            usage=data.get("usage", {}),
            model=actual_model,
            finish_reason=choice.get("finish_reason"),
            system_fingerprint=data.get("system_fingerprint"),
        )

    except Exception as exc:
        logger.error("Groq generation error: %s", exc, exc_info=True)
        raise RuntimeError(f"Groq LLM generation failed: {exc}") from exc


def generate_structured(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> GroqResponse:
    """Generate a structured completion with system/user prompts.

    Convenience wrapper for common LLM interaction pattern.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return generate(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Agent integration - Groq as ONE input in the trading intelligence pipeline
# ---------------------------------------------------------------------------

class GroqAgentOutput(BaseModel):
    """Output from Groq LLM as input to the orchestrator.

    This output is treated as contextual intelligence, not as authoritative
    trade direction. The orchestrator evaluates it alongside market data,
    structural analysis, risk assessments, and Red Team findings.

    Note: This is a standalone model to avoid circular imports with the
    orchestrator. It mirrors the structure of AgentOutput from orchestrator.
    """

    task_id: str
    agent_id: str
    observation: str
    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # task_ids this depends on
    status: str = "completed"
    timestamp: str = ""
    llm_model: str = DEFAULT_MODEL
    llm_temperature: float = 0.3
    llm_confidence: Optional[float] = Field(default=None, description="LLM-assigned confidence if present")
    evidence_type: Literal["hypothesis", "analysis", "summarization", "classification"] = "hypothesis"


def llm_to_agent_output(task_id: str, content: str, agent_id: str = "groq_orchestrator",
                        llm_model: str = DEFAULT_MODEL, llm_temperature: float = 0.3,
                        llm_confidence: float | None = None,
                        evidence_type: Literal["hypothesis", "analysis", "summarization", "classification"] = "hypothesis") -> GroqAgentOutput:
    """Convert a Groq response to a structured AgentOutput.

    This is the conversion point where LLM output enters the structured
    agent communication pipeline. The orchestrator then evaluates this output
    alongside ALL other evidence sources.
    """
    return GroqAgentOutput(
        task_id=task_id,
        agent_id=agent_id,
        observation=content,
        hypothesis=content,  # LLM content becomes the hypothesis
        evidence=[f"groq_{llm_model}_temp{llm_temperature}"],
        dependencies=[],
        status="completed",
        llm_model=llm_model,
        llm_temperature=llm_temperature,
        llm_confidence=llm_confidence,
        evidence_type=evidence_type,
    )