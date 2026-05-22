"""
hermes_brain.py — Ollama-backed LLM gate for Hermes.

Design rules:
  1. Veto-only. The brain can BLOCK a trade that the rule engine approved.
     It never creates a trade on its own. Strict safety subset of the rules.
  2. Safe-fail. Any error, timeout, malformed response → neutral verdict
     (no veto). The trading loop must never block on the LLM.
  3. Off by default. USE_LLM_BRAIN=true to enable. Default verdicts are
     no-op so the bot's behavior is unchanged until you flip the switch.
  4. Audited. Every call is appended to data/llm_decisions.jsonl so the
     LLM gate can be backtested against the rule-only baseline.

Env vars:
  USE_LLM_BRAIN          "true"/"false" (default "false")
  OLLAMA_URL             default "http://localhost:11434"
  OLLAMA_MODEL_FAST      default "llama3.2:3b"
  OLLAMA_MODEL_DEEP      default "" (disabled). e.g. "kimi-k2.6:cloud"
  LLM_TIMEOUT_S          default "6"
  LLM_LOG_PATH           default "<cwd>/data/llm_decisions.jsonl"
  LLM_ESCALATE_BAND      "low,high" score band that triggers deep model
                         re-check (default "0.35,0.65"). Borderline only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


ENABLED = _env_bool("USE_LLM_BRAIN", False)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "llama3.2:3b")
MODEL_DEEP = os.getenv("OLLAMA_MODEL_DEEP", "").strip()
TIMEOUT_S = _env_float("LLM_TIMEOUT_S", 6.0)
LOG_PATH = os.getenv("LLM_LOG_PATH", os.path.join(_HERE, "data", "llm_decisions.jsonl"))

_band_raw = os.getenv("LLM_ESCALATE_BAND", "0.35,0.65")
try:
    _lo, _hi = (float(x) for x in _band_raw.split(",", 1))
    ESCALATE_BAND: Tuple[float, float] = (_lo, _hi)
except (ValueError, TypeError):
    ESCALATE_BAND = (0.35, 0.65)


# Neutral verdict — what we return when the brain is off or anything fails.
# score 0.5 = "no opinion", veto False = "don't override the rules".
_NEUTRAL_ENTRY = {"score": 0.5, "veto": False, "reason": "brain_disabled", "model": None, "latency_ms": 0}
_NEUTRAL_NARRATIVE = {"score": 50.0, "theme": "", "reason": "brain_disabled", "model": None, "latency_ms": 0}


# ── Logging ──

def _append_log(record: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.debug(f"llm log write failed: {e}")


# ── Ollama HTTP ──

async def _ollama_generate(model: str, prompt: str, system: str = "") -> Optional[str]:
    """Single POST to /api/generate. Returns response text or None on any failure."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        # think:false disables hidden chain-of-thought for reasoning models
        # (kimi-k2.6, etc). Drops latency from 25-60s to ~5s. Non-reasoning
        # models ignore the field.
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 512},
    }
    if system:
        payload["system"] = system

    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{OLLAMA_URL}/api/generate", json=payload) as resp:
                if resp.status != 200:
                    logger.debug(f"ollama {model} HTTP {resp.status}")
                    return None
                body = await resp.json()
                return body.get("response", "")
    except Exception as e:
        logger.debug(f"ollama {model} call failed: {e}")
        return None


def _parse_score(raw: Optional[str]) -> Optional[Dict]:
    """Parse {score, veto, reason} from model output. Tolerates JSON wrapped in prose."""
    if not raw:
        return None
    raw = raw.strip()
    # Try direct JSON first; fall back to first {...} block.
    for candidate in (raw, _first_json_block(raw)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        score = obj.get("score")
        if score is None:
            continue
        try:
            score = float(score)
        except (ValueError, TypeError):
            continue
        score = max(0.0, min(1.0, score))
        veto = bool(obj.get("veto", False))
        reason = str(obj.get("reason", ""))[:200]
        return {"score": score, "veto": veto, "reason": reason}
    return None


def _first_json_block(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Public API: entry gate ──

ENTRY_SYSTEM = (
    "You are a risk reviewer for a Solana memecoin trading bot. "
    "You see ONE token candidate that has already passed liquidity, volume, "
    "and social-presence rules. Your job is to spot obvious red flags the "
    "rules missed (suspicious name, dead momentum, pump-and-dump shape, fake "
    "socials, brand-new pool of an established symbol). Be skeptical but not paranoid. "
    "\n\n"
    "IDENTITY: each token has a 'mint' (Solana address) and 'age_hours'. "
    "Established tokens have age in days/weeks/months. A pool less than a "
    "few hours old using a famous symbol (BONK, WIF, JUP, SOL, PEPE) is "
    "almost certainly a copycat — flag it. The real established token will "
    "have age_hours in the hundreds or thousands. Use 'market_cap_usd' and "
    "'fdv_usd' as additional legitimacy signals: real establish tokens "
    "have MCAP in the millions to billions."
    "\n\n"
    "Respond ONLY with compact JSON: "
    '{"score": 0.0-1.0, "veto": true|false, "reason": "<=20 words"}. '
    "\n\n"
    "SCORE DIRECTION (critical): score is a QUALITY score. "
    "1.0 = clean and tradeable. 0.0 = obvious scam. "
    "Higher is BETTER. Lower is WORSE. "
    "veto=true means BLOCK THE TRADE. "
    "Set veto=true ONLY when score < 0.25. "
    "Never set veto=true with a high score — that is contradictory."
)


def _token_brief(token: Dict) -> str:
    """Compact one-shot description of a token for the LLM. Keeps tokens cheap.

    Identity fields (mint, age, fdv, market cap) matter for distinguishing
    established legit tokens from same-symbol copycats.
    """
    base = token.get("baseToken", {}) or {}
    sym = base.get("symbol") or token.get("symbol", "?")
    name = base.get("name") or token.get("name", "")
    mint = (
        base.get("address")
        or token.get("tokenAddress")
        or token.get("mint")
        or token.get("address")
        or ""
    )
    liq = float((token.get("liquidity") or {}).get("usd", 0))
    vol = float((token.get("volume") or {}).get("h24", 0))
    pc = token.get("priceChange") or {}
    info = token.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    txns_h24 = (token.get("txns") or {}).get("h24") or {}

    # Derive pair age. DexScreener gives pairCreatedAt in ms.
    age_hours = token.get("pairAge_h") or token.get("age_hours")
    if age_hours is None:
        created_ms = token.get("pairCreatedAt")
        if created_ms:
            try:
                from time import time as _now
                age_hours = round((_now() * 1000 - float(created_ms)) / 3_600_000, 1)
            except (ValueError, TypeError):
                age_hours = None

    return json.dumps({
        "symbol": sym,
        "name": name,
        "mint": mint,
        "chain": token.get("chainId", "solana"),
        "price_usd": float(token.get("priceUsd", 0) or 0),
        "liquidity_usd": round(liq),
        "volume_24h_usd": round(vol),
        "market_cap_usd": round(float(token.get("marketCap", 0) or 0)),
        "fdv_usd": round(float(token.get("fdv", 0) or 0)),
        "change_5m_pct": float(pc.get("m5", 0) or 0),
        "change_1h_pct": float(pc.get("h1", 0) or 0),
        "change_24h_pct": float(pc.get("h24", 0) or 0),
        "buys_24h": int(txns_h24.get("buys", 0) or 0),
        "sells_24h": int(txns_h24.get("sells", 0) or 0),
        "n_socials": len(socials),
        "has_website": bool(websites),
        "age_hours": age_hours,
    })


async def score_entry(token: Dict) -> Dict:
    """LLM veto check on an entry candidate.

    Returns {score, veto, reason, model, latency_ms}. When ENABLED is False
    or anything fails, returns the neutral verdict (veto=False).
    """
    if not ENABLED:
        return dict(_NEUTRAL_ENTRY)

    sym = (token.get("baseToken", {}) or {}).get("symbol") or token.get("symbol", "?")
    brief = _token_brief(token)
    prompt = f"Token candidate:\n{brief}\n\nReturn the JSON verdict."

    t0 = time.monotonic()
    raw = await _ollama_generate(MODEL_FAST, prompt, system=ENTRY_SYSTEM)
    parsed = _parse_score(raw)
    model_used = MODEL_FAST

    # Escalate borderline scores to the deep model if configured.
    if parsed and MODEL_DEEP and ESCALATE_BAND[0] <= parsed["score"] <= ESCALATE_BAND[1]:
        deep_raw = await _ollama_generate(MODEL_DEEP, prompt, system=ENTRY_SYSTEM)
        deep_parsed = _parse_score(deep_raw)
        if deep_parsed:
            parsed = deep_parsed
            model_used = MODEL_DEEP

    latency_ms = int((time.monotonic() - t0) * 1000)

    if parsed is None:
        verdict = {**_NEUTRAL_ENTRY, "reason": "parse_fail_or_timeout", "model": model_used, "latency_ms": latency_ms}
    else:
        verdict = {**parsed, "model": model_used, "latency_ms": latency_ms}

    _append_log({
        "ts": datetime.utcnow().isoformat() + "Z",
        "kind": "entry",
        "symbol": sym,
        "brief": json.loads(brief),
        "verdict": verdict,
        "raw": raw,
    })
    return verdict


# ── Public API: narrative scoring ──

NARRATIVE_SYSTEM = (
    "You score crypto memecoin narratives on a 0-100 scale by how strong and "
    "currently-trending the theme is on Solana. AI, agent, dog/cat memes, "
    "political/election plays, and celebrity tokens score higher when timely. "
    "Generic random tokens score lower. "
    "Respond ONLY with compact JSON: "
    '{"score": 0-100, "theme": "<=4 words", "reason": "<=15 words"}.'
)


async def score_narrative(symbol: str, name: str = "") -> Dict:
    """LLM narrative score for a token. Returns {score 0-100, theme, ...}."""
    if not ENABLED:
        return dict(_NEUTRAL_NARRATIVE)

    prompt = json.dumps({"symbol": symbol, "name": name})
    t0 = time.monotonic()
    raw = await _ollama_generate(MODEL_FAST, prompt, system=NARRATIVE_SYSTEM)
    latency_ms = int((time.monotonic() - t0) * 1000)

    parsed_block = _first_json_block(raw) if raw else None
    score = 50.0
    theme = ""
    reason = "parse_fail_or_timeout"
    if parsed_block:
        try:
            obj = json.loads(parsed_block)
            score = max(0.0, min(100.0, float(obj.get("score", 50))))
            theme = str(obj.get("theme", ""))[:40]
            reason = str(obj.get("reason", ""))[:200]
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    verdict = {"score": score, "theme": theme, "reason": reason, "model": MODEL_FAST, "latency_ms": latency_ms}
    _append_log({
        "ts": datetime.utcnow().isoformat() + "Z",
        "kind": "narrative",
        "symbol": symbol,
        "name": name,
        "verdict": verdict,
        "raw": raw,
    })
    return verdict


# ── Health check ──

async def health() -> Dict:
    """Returns {ok, models, url}. Used by startup smoke checks."""
    try:
        timeout = aiohttp.ClientTimeout(total=3.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{OLLAMA_URL}/api/tags") as resp:
                if resp.status != 200:
                    return {"ok": False, "url": OLLAMA_URL, "error": f"HTTP {resp.status}"}
                body = await resp.json()
                models = [m.get("name") for m in body.get("models", [])]
                return {
                    "ok": True,
                    "url": OLLAMA_URL,
                    "models": models,
                    "fast_available": MODEL_FAST in models,
                    "deep_available": (MODEL_DEEP in models) if MODEL_DEEP else None,
                }
    except Exception as e:
        return {"ok": False, "url": OLLAMA_URL, "error": str(e)}
