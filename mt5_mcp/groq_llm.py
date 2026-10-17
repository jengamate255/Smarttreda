"""Groq LLM provider for the Multi-Asset Trading Intelligence Platform.

This module provides LLM-powered intelligence for trading decisions:
- Pre-trade devil's advocate (challenges setups before execution)
- Post-trade review (analyzes outcomes)
- Market regime detection (risk-on/off, trending/ranging)
- Multi-agent debate (bull/bear/judge consensus)
- News sentiment analysis (dynamic impact scoring)

Architecture:
- Groq free tier primary (fast, rate-limited)
- Ollama local fallback (free, unlimited, requires local install)
- All LLM outputs are hypotheses, not signals — validated by market data
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("mt5_mcp.groq_llm")

# Groq models (free tier: 30 RPM, 14.4K TPD)
GROQ_MODELS = {
    "llama3-8b": "openai/gpt-oss-20b",
    "llama3-70b": "openai/gpt-oss-120b",
    "gemma2-9b": "qwen/qwen3.8-27b",
    "mixtral-8x7b": "openai/gpt-oss-120b",
}

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama3-8b")

# API keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Rate limit tracking
_last_groq_call = 0.0
_groq_call_count = 0
_groq_rate_limit_until = 0.0

# Whether LLM is enabled
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
    """Check if any LLM provider is available."""
    return ENABLED and (bool(GROQ_API_KEY) or _ollama_available())


def _ollama_available() -> bool:
    """Check if Ollama is running locally."""
    try:
        import httpx
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def check_health() -> bool:
    """Check if LLM API is accessible (Groq or Ollama)."""
    if not is_enabled():
        return False
    # Try Groq first
    if GROQ_API_KEY:
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
            if resp.status_code == 200:
                return True
        except Exception as exc:
            logger.debug("Groq health check failed: %s", exc)
    # Try Ollama
    return _ollama_available()


def _generate_ollama(
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> GroqResponse:
    """Fallback to local Ollama (free, unlimited, no API key needed)."""
    import httpx
    # Ollama uses a different API format
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=120.0,  # Local models can be slower
    )
    if resp.status_code != 200:
        logger.error("Ollama API error: %s - %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Ollama API returned status {resp.status_code}")
    data = resp.json()
    message = data.get("message", {})
    return GroqResponse(
        task_id=f"ollama_{uuid.uuid4().hex[:8]}",
        agent_id="ollama_local",
        content=message.get("content", ""),
        usage={"prompt_tokens": data.get("prompt_eval_count", 0),
               "completion_tokens": data.get("eval_count", 0)},
        model=OLLAMA_MODEL,
        finish_reason="stop",
    )


def _check_groq_rate_limit() -> bool:
    """Check if we're within Groq free tier limits (30 RPM)."""
    global _last_groq_call, _groq_call_count, _groq_rate_limit_until
    now = time.time()
    # If we're in rate limit cooldown, check if it's expired
    if _groq_rate_limit_until > now:
        return False
    # Reset counter every minute
    if now - _last_groq_call > 60:
        _groq_call_count = 0
    # Allow if under 25 RPM (leave headroom)
    return _groq_call_count < 25


def generate(
    messages: List[Dict[str, str]],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> GroqResponse:
    """Generate a completion with Groq primary + Ollama fallback.

    Rate limit handling: Groq free tier = 30 RPM. We track calls and
    auto-fallback to Ollama when approaching limit or on 429.
    """
    global _last_groq_call, _groq_call_count, _groq_rate_limit_until

    if not is_enabled():
        raise RuntimeError("LLM is not enabled. Set GROQ_API_KEY or install Ollama.")

    actual_model = model or DEFAULT_MODEL
    actual_temperature = temperature if temperature is not None else 0.3
    actual_max_tokens = max_tokens if max_tokens is not None else 4096

    # Try Groq first if enabled and not rate-limited
    if GROQ_API_KEY and _check_groq_rate_limit():
        try:
            import httpx
            _last_groq_call = time.time()
            _groq_call_count += 1

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

            if resp.status_code == 429:
                logger.warning("Groq 429 rate-limited, entering 60s cooldown")
                _groq_rate_limit_until = time.time() + 60
                # Fall through to Ollama
            elif resp.status_code != 200:
                logger.error("Groq API error: %s - %s", resp.status_code, resp.text[:500])
                if resp.status_code in (500, 502, 503):
                    # Server error, try Ollama
                    pass
                else:
                    raise RuntimeError(f"Groq API returned status {resp.status_code}")
            else:
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

        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Groq generation error, trying Ollama: %s", exc)

    # Fallback to Ollama (local, free, unlimited)
    if _ollama_available():
        try:
            return _generate_ollama(messages, actual_temperature, actual_max_tokens)
        except Exception as exc:
            logger.error("Ollama fallback failed: %s", exc)
            raise RuntimeError(f"Both Groq and Ollama failed: {exc}") from exc

    raise RuntimeError("No LLM provider available (Groq rate-limited + Ollama unavailable)")


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


# ---------------------------------------------------------------------------
# Intelligence features: devil's advocate, regime detection, debate, etc.
# ---------------------------------------------------------------------------


class DevilsAdvocateResult(BaseModel):
    """Result of pre-trade devil's advocate analysis."""
    should_veto: bool = False
    confidence: float = 0.0  # 0-100
    counter_arguments: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    summary: str = ""


def devils_advocate(
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    health_score: float = 0.0,
    pattern_direction: str = "",
    zone_type: str = "",
    trend_conflict: bool = False,
    news_events: list[str] | None = None,
) -> DevilsAdvocateResult:
    """Pre-trade devil's advocate: challenge the setup before execution.

    Returns a verdict on whether to veto the trade based on LLM analysis
    of counter-arguments and risk factors.
    """
    if not is_enabled():
        return DevilsAdvocateResult(should_veto=False, summary="LLM disabled")

    news_text = "\n".join(f"- {e}" for e in (news_events or [])) or "None"
    rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

    system_prompt = """You are a skeptical trading risk analyst. Your job is to find reasons NOT to take a trade.
Be critical and identify weaknesses, risks, and counter-arguments.
Respond in JSON format with: should_veto (bool), confidence (0-100), counter_arguments (list), risk_factors (list), summary (string)."""

    user_prompt = f"""Analyze this trade setup and find reasons to REJECT it:

Symbol: {symbol}
Direction: {side}
Entry: {entry}
Stop Loss: {sl}
Take Profit: {tp}
Risk:Reward = {rr:.2f}

Technical context:
- Health score: {health_score}/100
- Pattern direction: {pattern_direction or "neutral"}
- Zone type: {zone_type or "none"}
- MTF trend conflict: {"YES" if trend_conflict else "no"}

Upcoming news events:
{news_text}

What could go wrong? What's the strongest argument AGAINST this trade?
Consider: market regime, news risk, technical weaknesses, correlation risks."""

    try:
        resp = generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=500,
        )
        # Parse JSON response
        import json
        content = resp.content.strip()
        # Try to extract JSON from markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        data = json.loads(content)
        return DevilsAdvocateResult(
            should_veto=data.get("should_veto", False),
            confidence=float(data.get("confidence", 0)),
            counter_arguments=data.get("counter_arguments", []),
            risk_factors=data.get("risk_factors", []),
            summary=data.get("summary", ""),
        )
    except Exception as exc:
        logger.warning("devil's advocate failed (allowing trade): %s", exc)
        return DevilsAdvocateResult(should_veto=False, summary=f"LLM error: {exc}")


class RegimeClassification(BaseModel):
    """LLM-classified market regime."""
    regime: Literal["risk_on", "risk_off", "trending_up", "trending_down", "ranging", "high_volatility", "low_volatility"]
    confidence: float = 0.0  # 0-100
    reasoning: str = ""
    strategy_hint: str = ""


def classify_regime(
    symbol: str,
    price_action_summary: str,
    vix_level: float | None = None,
    recent_news: list[str] | None = None,
) -> RegimeClassification:
    """Classify current market regime using LLM.

    Helps route to appropriate strategy (trend-following vs mean-reversion).
    """
    if not is_enabled():
        return RegimeClassification(regime="ranging", confidence=50, reasoning="LLM disabled")

    news_text = "\n".join(f"- {n}" for n in (recent_news or [])) or "No major news"

    system_prompt = """You are a market regime classifier. Analyze the data and classify the current regime.
Respond in JSON: regime (one of: risk_on, risk_off, trending_up, trending_down, ranging, high_volatility, low_volatility),
confidence (0-100), reasoning (string), strategy_hint (string - which strategy family works best)."""

    user_prompt = f"""Classify the market regime for {symbol}:

Price action summary:
{price_action_summary}

VIX level: {vix_level or "unknown"}

Recent news:
{news_text}

What regime are we in? What strategy family works best right now?"""

    try:
        resp = generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=300,
        )
        import json
        content = resp.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        data = json.loads(content)
        return RegimeClassification(
            regime=data.get("regime", "ranging"),
            confidence=float(data.get("confidence", 50)),
            reasoning=data.get("reasoning", ""),
            strategy_hint=data.get("strategy_hint", ""),
        )
    except Exception as exc:
        logger.warning("regime classification failed: %s", exc)
        return RegimeClassification(regime="ranging", confidence=50, reasoning=f"LLM error: {exc}")


class DebateResult(BaseModel):
    """Multi-agent debate outcome."""
    bull_score: float = 0.0  # 0-100
    bear_score: float = 0.0  # 0-100
    judge_confidence: float = 0.0  # 0-100, judge's confidence in the winner
    winner: Literal["BUY", "SELL", "NONE"] = "NONE"
    bull_arguments: list[str] = Field(default_factory=list)
    bear_arguments: list[str] = Field(default_factory=list)
    judge_reasoning: str = ""


def multi_agent_debate(
    symbol: str,
    setup_details: str,
    technical_data: dict[str, Any],
) -> DebateResult:
    """Run a multi-agent debate: bull vs bear, then judge weighs evidence.

    Returns confidence-weighted verdict that can feed into confluence.
    """
    if not is_enabled():
        return DebateResult(winner="NONE", judge_reasoning="LLM disabled")

    tech_summary = "\n".join(f"- {k}: {v}" for k, v in technical_data.items())

    # Bull agent
    bull_prompt = f"""You are the BULL agent. Argue FOR taking this trade.

{setup_details}

Technical data:
{tech_summary}

Make the strongest case for WHY this trade should work. List 3-5 key arguments.
Respond in JSON: score (0-100 confidence), arguments (list of strings)."""

    # Bear agent
    bear_prompt = f"""You are the BEAR agent. Argue AGAINST taking this trade.

{setup_details}

Technical data:
{tech_summary}

Make the strongest case for WHY this trade will fail. List 3-5 key risks.
Respond in JSON: score (0-100 confidence), arguments (list of strings)."""

    try:
        # Run bull and bear in parallel (conceptually - sequential here for simplicity)
        bull_resp = generate_structured(
            system_prompt="You are a bullish trading analyst. Always find reasons to BUY.",
            user_prompt=bull_prompt,
            temperature=0.5,
            max_tokens=400,
        )
        bear_resp = generate_structured(
            system_prompt="You are a bearish trading analyst. Always find reasons to SELL or avoid.",
            user_prompt=bear_prompt,
            temperature=0.5,
            max_tokens=400,
        )

        import json
        # Parse bull
        bull_content = bull_resp.content.strip()
        if "```json" in bull_content:
            bull_content = bull_content.split("```json")[1].split("```")[0].strip()
        elif "```" in bull_content:
            bull_content = bull_content.split("```")[1].split("```")[0].strip()
        bull_data = json.loads(bull_content)

        # Parse bear
        bear_content = bear_resp.content.strip()
        if "```json" in bear_content:
            bear_content = bear_content.split("```json")[1].split("```")[0].strip()
        elif "```" in bear_content:
            bear_content = bear_content.split("```")[1].split("```")[0].strip()
        bear_data = json.loads(bear_content)

        bull_score = float(bull_data.get("score", 50))
        bear_score = float(bear_data.get("score", 50))

        # Judge agent
        judge_prompt = f"""You are the JUDGE. Weigh the bull and bear arguments and make a final verdict.

BULL case (score {bull_score}/100):
{chr(10).join(f'- {a}' for a in bull_data.get('arguments', []))}

BEAR case (score {bear_score}/100):
{chr(10).join(f'- {a}' for a in bear_data.get('arguments', []))}

Technical data:
{tech_summary}

Who wins? What's your confidence in the verdict?
Respond in JSON: winner ("BUY" or "SELL" or "NONE"), confidence (0-100), reasoning (string)."""

        judge_resp = generate_structured(
            system_prompt="You are an impartial judge. Weigh evidence objectively and make a final call.",
            user_prompt=judge_prompt,
            temperature=0.3,
            max_tokens=300,
        )

        judge_content = judge_resp.content.strip()
        if "```json" in judge_content:
            judge_content = judge_content.split("```json")[1].split("```")[0].strip()
        elif "```" in judge_content:
            judge_content = judge_content.split("```")[1].split("```")[0].strip()
        judge_data = json.loads(judge_content)

        return DebateResult(
            bull_score=bull_score,
            bear_score=bear_score,
            judge_confidence=float(judge_data.get("confidence", 50)),
            winner=judge_data.get("winner", "NONE"),
            bull_arguments=bull_data.get("arguments", []),
            bear_arguments=bear_data.get("arguments", []),
            judge_reasoning=judge_data.get("reasoning", ""),
        )
    except Exception as exc:
        logger.warning("multi-agent debate failed: %s", exc)
        return DebateResult(winner="NONE", judge_reasoning=f"LLM error: {exc}")


class NewsSentimentResult(BaseModel):
    """LLM-analyzed news sentiment and impact."""
    impact_score: float = 0.0  # 0-100, higher = more impactful
    sentiment: Literal["bullish", "bearish", "neutral", "mixed"] = "neutral"
    volatility_expected: bool = False
    blackout_minutes: int = 0  # Suggested minutes to avoid trading
    reasoning: str = ""


def analyze_news_sentiment(
    currency: str,
    event_title: str,
    forecast: str = "",
    previous: str = "",
    context: str = "",
) -> NewsSentimentResult:
    """Analyze a news event's potential market impact using LLM.

    Returns dynamic impact score and suggested blackout window.
    """
    if not is_enabled():
        return NewsSentimentResult(impact_score=50, reasoning="LLM disabled")

    system_prompt = """You are a news impact analyst. Evaluate how much this news event will move the market.
Respond in JSON: impact_score (0-100), sentiment ("bullish"/"bearish"/"neutral"/"mixed"),
volatility_expected (bool), blackout_minutes (int - how long to avoid trading), reasoning (string)."""

    user_prompt = f"""Analyze this news event for {currency}:

Event: {event_title}
Forecast: {forecast or "unknown"}
Previous: {previous or "unknown"}

Context:
{context or "No additional context"}

How much will this move the market? What's the expected sentiment impact?
How many minutes should traders avoid trading around this event?"""

    try:
        resp = generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=300,
        )
        import json
        content = resp.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        data = json.loads(content)
        return NewsSentimentResult(
            impact_score=float(data.get("impact_score", 50)),
            sentiment=data.get("sentiment", "neutral"),
            volatility_expected=data.get("volatility_expected", False),
            blackout_minutes=int(data.get("blackout_minutes", 0)),
            reasoning=data.get("reasoning", ""),
        )
    except Exception as exc:
        logger.warning("news sentiment analysis failed: %s", exc)
        return NewsSentimentResult(impact_score=50, reasoning=f"LLM error: {exc}")


class PostTradeReview(BaseModel):
    """Post-trade analysis and lessons learned."""
    thesis_correct: bool = False
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    would_take_again: bool = False
    confidence_in_review: float = 0.0  # 0-100


def post_trade_review(
    symbol: str,
    side: str,
    entry: float,
    exit_price: float,
    sl: float,
    tp: float,
    pnl: float,
    hold_time_minutes: int,
    original_thesis: str = "",
    market_context: str = "",
) -> PostTradeReview:
    """Post-trade review: analyze what happened and extract lessons.

    Called after a trade closes (win or loss) to improve future decisions.
    """
    if not is_enabled():
        return PostTradeReview(thesis_correct=False, lessons=["LLM disabled"], confidence_in_review=0)

    result = "WIN" if pnl > 0 else "LOSS"
    rr_achieved = abs(exit_price - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

    system_prompt = """You are a trading performance analyst. Review this closed trade and extract lessons.
Be honest and constructive. Focus on what can be improved.
Respond in JSON: thesis_correct (bool), what_went_right (list), what_went_wrong (list),
lessons (list), would_take_again (bool), confidence_in_review (0-100)."""

    user_prompt = f"""Review this {result} trade:

Symbol: {symbol}
Direction: {side}
Entry: {entry}
Exit: {exit_price}
SL: {sl}
TP: {tp}
P&L: ${pnl:.2f}
Hold time: {hold_time_minutes} minutes
R:R achieved: {rr_achieved:.2f}

Original thesis:
{original_thesis or "Not provided"}

Market context:
{market_context or "Not provided"}

What went right? What went wrong? What lessons can we learn?
Would you take this trade again with the same setup?"""

    try:
        resp = generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=500,
        )
        import json
        content = resp.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        data = json.loads(content)
        return PostTradeReview(
            thesis_correct=data.get("thesis_correct", False),
            what_went_right=data.get("what_went_right", []),
            what_went_wrong=data.get("what_went_wrong", []),
            lessons=data.get("lessons", []),
            would_take_again=data.get("would_take_again", False),
            confidence_in_review=float(data.get("confidence_in_review", 50)),
        )
    except Exception as exc:
        logger.warning("post-trade review failed: %s", exc)
        return PostTradeReview(thesis_correct=False, lessons=[f"LLM error: {exc}"], confidence_in_review=0)