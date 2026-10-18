"""Test Groq models - show raw output."""
import os
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
key = os.getenv("GROQ_API_KEY", "")
import httpx

models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
prompt = "You are a trading analyst. Output STRICT JSON only."
user_msg = 'Should we BUY EURUSD? health=85, trend=bullish. Output: {"decision": "APPROVE" or "REJECT", "confidence": 0-100}'

for model in models:
    try:
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.1,
                "max_tokens": 200,
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(f"=== {model} ===")
            print(f"  Raw: {repr(content[:200])}")
        else:
            err = r.json().get("error", {}).get("message", r.text[:100])
            print(f"=== {model} === ERROR {r.status_code}: {err[:100]}")
    except Exception as e:
        print(f"=== {model} === FAIL: {e}")
