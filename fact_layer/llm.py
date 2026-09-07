"""
LLM client: provider-agnostic chat completion with a mandatory replay cache.

Project invariant 4: graders run with no API key and no network. Every call
this module makes is cached at cache/llm/{sha256(provider+model+messages)}.json;
LLM_MODE=replay serves exclusively from that cache and raises a clear error on
a miss rather than dialing out, so a fresh clone reproduces the demo offline.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fact_layer.llm")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "llm")

_PROVIDER_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

_MAX_RETRIES_PER_KEY = 4   # bounded backoff before giving up on a key and rotating to the next
_BASE_BACKOFF = 1.0        # seconds; doubles each retry

# Response text fragments that indicate a quota-shaped 429 (daily/monthly cap,
# billing, tokens-per-day) rather than a plain transient rate limit. This is
# NOT the same as "wait a long time" — providers like Groq report a TPD limit
# using a rolling window and tell you exactly how long until it clears, which
# is often under a minute, not until tomorrow. See _parse_wait_hint.
_QUOTA_EXHAUSTED_HINTS = ("per day", "daily", "rpd", "tpd", "quota", "billing", "insufficient_quota")

# If the provider's error message gives a concrete "try again in Ns" wait and
# it's under this many seconds, wait it out on the SAME key rather than
# rotating — multiple keys from the same account/org share one quota pool
# (confirmed: all 4 keys here hit the identical TPD ceiling), so rotating
# achieves nothing in that case and just burns through the key list for free.
# This pipeline runs as an unattended batch job, not a live request path, so
# blocking up to an hour to let a daily quota partially refill is the right
# trade-off — a TPD wait hint under a day means it resolves today regardless.
_MAX_QUOTA_WAIT_SECONDS = 3600.0
_MAX_QUOTA_WAIT_RETRIES = 10   # each capped at _MAX_QUOTA_WAIT_SECONDS, so up to ~10h before giving up

_WAIT_HINT_RE = re.compile(r"try again in\s+(?:(\d+)m)?(\d+(?:\.\d+)?)s", re.I)


def _parse_wait_hint(text: str) -> Optional[float]:
    """Extract a 'try again in 1m54.048s' / 'try again in 12s' style hint
    from a provider error message, in seconds. None if no such hint exists."""
    m = _WAIT_HINT_RE.search(text)
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    seconds = float(m.group(2))
    return minutes * 60 + seconds


class LLMError(RuntimeError):
    """Any LLM-layer failure: bad config, replay miss, exhausted retries."""


class ReplayCacheMiss(LLMError):
    """LLM_MODE=replay and no cached response exists for this exact call."""


# --------------------------------------------------------------------------
# Call/token accounting — printed once at process exit
# --------------------------------------------------------------------------

_stats = {
    "calls": 0,
    "cache_hits": 0,
    "network_calls": 0,
    "estimated_input_tokens": 0,
    "repair_attempts": 0,
    "key_rotations": 0,
}


def _print_summary() -> None:
    if _stats["calls"] == 0:
        return
    print(
        f"[fact_layer.llm] calls={_stats['calls']} cache_hits={_stats['cache_hits']} "
        f"network_calls={_stats['network_calls']} repair_attempts={_stats['repair_attempts']} "
        f"key_rotations={_stats['key_rotations']} estimated_input_tokens={_stats['estimated_input_tokens']}"
    )


atexit.register(_print_summary)


# --------------------------------------------------------------------------
# Config — provider/model/mode read fresh from env on every call so tests
# can monkeypatch os.environ without reimporting the module.
# --------------------------------------------------------------------------

def _config() -> tuple[str, str, str]:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("LLM_MODEL", "").strip()
    # Default to replay, not live: this pipeline must run offline out of the
    # box for graders with no key, per project invariant 4.
    mode = os.environ.get("LLM_MODE", "replay").strip().lower()

    if not model:
        raise LLMError("LLM_MODEL is not set. Never hardcode a model string — set it in .env.")
    if provider not in _PROVIDER_ENDPOINTS:
        raise LLMError(f"LLM_PROVIDER must be one of {sorted(_PROVIDER_ENDPOINTS)}, got {provider!r}.")
    return provider, model, mode


# --------------------------------------------------------------------------
# API keys — LLM_API_KEYS (comma-separated) and LLM_API_KEY (single) are
# POOLED together and deduplicated, not either/or: whichever var(s) a key
# ends up in, it's eligible for rotation. A free-tier user can hand this
# several keys and have it rotate automatically when one hits its rate/quota
# limit, instead of the whole pipeline stalling on a single exhausted key.
# --------------------------------------------------------------------------

_key_rotation = {"exhausted_indices": set(), "current_index": 0}


def _reset_key_rotation() -> None:
    """Forget which keys were marked exhausted. Call this to start a fresh
    run with all configured keys eligible again (e.g. after a quota reset),
    or between tests so rotation state doesn't leak across them."""
    _key_rotation["exhausted_indices"] = set()
    _key_rotation["current_index"] = 0


def _api_keys() -> list[str]:
    keys: list[str] = []
    multi = os.environ.get("LLM_API_KEYS", "").strip()
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    single = os.environ.get("LLM_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def _looks_like_quota_exhausted(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return any(hint in text for hint in _QUOTA_EXHAUSTED_HINTS)


# --------------------------------------------------------------------------
# Replay cache
# --------------------------------------------------------------------------

def _cache_key(provider: str, model: str, messages: list[dict]) -> str:
    blob = provider + model + json.dumps(messages, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _load_cache(key: str) -> Optional[str]:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["response"]


def _save_cache(key: str, provider: str, model: str, messages: list[dict], response: str) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(key)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"provider": provider, "model": model, "messages": messages, "response": response}, f, indent=2)
    os.replace(tmp, path)


def _estimate_tokens(messages: list[dict]) -> int:
    # Rough chars/4 heuristic — good enough for a call-budget sanity check,
    # not for billing; providers report the real usage in their response.
    chars = sum(len(m.get("content", "")) for m in messages)
    return chars // 4


# --------------------------------------------------------------------------
# HTTP with retry/backoff and multi-key rotation
#
# Key rotation is the OUTER loop, backoff is the INNER loop: each key gets a
# bounded number of backoff attempts for genuinely transient errors (429s
# that don't look like a hard quota, 5xx), and a key that either looks
# quota-exhausted or is still failing after its backoff budget gets marked
# exhausted and skipped for the rest of this process — the next key is tried
# immediately rather than waiting out a quota that won't reset soon.
# --------------------------------------------------------------------------

def _post_with_retry(url: str, keys: list[str], payload: dict) -> httpx.Response:
    if not keys:
        raise LLMError("LLM_MODE=live but no API key is configured (set LLM_API_KEY or LLM_API_KEYS).")

    last_error_text = ""
    last_status = None

    for _ in range(len(keys)):
        idx = _key_rotation["current_index"] % len(keys)
        if idx in _key_rotation["exhausted_indices"]:
            _key_rotation["current_index"] = (idx + 1) % len(keys)
            continue

        key = keys[idx]
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        delay = _BASE_BACKOFF
        key_exhausted = False
        transient_attempt = 0
        quota_wait_attempt = 0

        while True:
            try:
                resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
            except httpx.RequestError as e:
                last_error_text = str(e)
                transient_attempt += 1
                if transient_attempt >= _MAX_RETRIES_PER_KEY:
                    key_exhausted = True
                    break
                time.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (401, 403):
                logger.warning("key #%d rejected (status %s) — rotating to next key", idx + 1, resp.status_code)
                last_error_text = resp.text[:500]
                last_status = resp.status_code
                key_exhausted = True
                break

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error_text = resp.text[:500]
                last_status = resp.status_code

                if _looks_like_quota_exhausted(resp):
                    # A quota-shaped 429 with a short concrete wait hint is a
                    # rolling window, not a hard reset — and if several keys
                    # share one account's quota (as happened here: 4 keys,
                    # one org, one TPD ceiling), rotating between them buys
                    # nothing. Wait it out on the SAME key instead.
                    wait_hint = _parse_wait_hint(resp.text)
                    if (wait_hint is not None and wait_hint <= _MAX_QUOTA_WAIT_SECONDS
                            and quota_wait_attempt < _MAX_QUOTA_WAIT_RETRIES):
                        quota_wait_attempt += 1
                        wait = wait_hint + 2.0   # small buffer past the provider's own estimate
                        logger.warning(
                            "key #%d hit a rolling quota window (%.1fs per provider) — "
                            "waiting %.1fs then retrying same key (%d/%d)",
                            idx + 1, wait_hint, wait, quota_wait_attempt, _MAX_QUOTA_WAIT_RETRIES,
                        )
                        time.sleep(wait)
                        continue
                    logger.warning(
                        "key #%d quota-exhausted with no short wait hint (or wait budget used up) "
                        "— rotating to next key", idx + 1,
                    )
                    key_exhausted = True
                    break

                transient_attempt += 1
                if transient_attempt >= _MAX_RETRIES_PER_KEY:
                    logger.warning("key #%d still failing (status %s) after %d attempts — rotating to next key",
                                  idx + 1, resp.status_code, _MAX_RETRIES_PER_KEY)
                    key_exhausted = True
                    break
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                logger.warning("retrying %s after status %s (wait=%.1fs, attempt=%d/%d, key #%d)",
                              url, resp.status_code, wait, transient_attempt, _MAX_RETRIES_PER_KEY, idx + 1)
                time.sleep(wait)
                delay *= 2
                continue

            # non-retryable status (e.g. 400 bad request) — no key would fix this
            raise LLMError(f"{url} returned {resp.status_code}: {resp.text[:500]}")

        if key_exhausted:
            _key_rotation["exhausted_indices"].add(idx)
            _key_rotation["current_index"] = (idx + 1) % len(keys)
            _stats["key_rotations"] += 1

    remaining = len(keys) - len(_key_rotation["exhausted_indices"])
    raise LLMError(
        f"all {len(keys)} configured API key(s) exhausted or failing calling {url} "
        f"(last status {last_status}): {last_error_text}. "
        f"{'Add more keys to LLM_API_KEYS or wait for quota reset.' if remaining <= 0 else ''}"
    )


def _call_provider(provider: str, model: str, keys: list[str], messages: list[dict], max_tokens: int) -> str:
    url = _PROVIDER_ENDPOINTS[provider]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    resp = _post_with_retry(url, keys, payload)
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _complete_once(messages: list[dict], max_tokens: int) -> str:
    provider, model, mode = _config()
    key = _cache_key(provider, model, messages)

    cached = _load_cache(key)
    if cached is not None:
        _stats["cache_hits"] += 1
        return cached

    if mode == "replay":
        raise ReplayCacheMiss(
            f"LLM_MODE=replay and no cached response at cache/llm/{key}.json for this exact "
            f"(provider, model, messages) call. Re-run once with LLM_MODE=live and a valid "
            f"LLM_API_KEY (or LLM_API_KEYS) to populate the cache, then commit the new cache file."
        )

    keys = _api_keys()
    if not keys:
        raise LLMError("LLM_MODE=live but no API key is set (LLM_API_KEY or LLM_API_KEYS).")

    _stats["network_calls"] += 1
    _stats["estimated_input_tokens"] += _estimate_tokens(messages)
    response = _call_provider(provider, model, keys, messages, max_tokens)
    _save_cache(key, provider, model, messages, response)
    return response


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def complete(messages: list[dict], *, schema_hint: Optional[str] = None, max_tokens: int = 2000) -> str:
    """Chat-complete `messages`, returning the raw text content.

    If `schema_hint` is given, the response is expected to be JSON matching
    it: an instruction to respond with valid JSON is appended, and on a parse
    failure the call is retried exactly once with the parse error fed back to
    the model. Both attempts are logged and independently cache-keyed (their
    message lists differ, so they get distinct cache entries).
    """
    _stats["calls"] += 1

    call_messages = messages
    if schema_hint:
        call_messages = messages + [
            {"role": "user", "content": f"Respond with valid JSON only, matching this schema: {schema_hint}"}
        ]

    content = _complete_once(call_messages, max_tokens)

    if not schema_hint:
        return content

    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError as e:
        # First attempt deterministic local repair on truncated arrays
        locally_repaired = _repair_truncated_json_str(cleaned)
        if locally_repaired is not None:
            return locally_repaired

        logger.warning("JSON parse failed on first attempt: %s | content=%r", e, cleaned[:300])
        _stats["repair_attempts"] += 1
        repair_messages = call_messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"That was not valid JSON ({e}). Reply again with ONLY valid JSON."},
        ]
        repaired = _complete_once(repair_messages, max_tokens)
        cleaned_rep = re.sub(r'<think>.*?</think>', '', repaired, flags=re.DOTALL).strip()
        if cleaned_rep.startswith("```"):
            lines = cleaned_rep.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned_rep = "\n".join(lines).strip()
        try:
            json.loads(cleaned_rep)
            logger.info("JSON repair succeeded on retry")
            return cleaned_rep
        except json.JSONDecodeError as e2:
            logger.warning("JSON repair FAILED on retry: %s | content=%r", e2, cleaned_rep[:300])
        return repaired


def _repair_truncated_json_str(text: str) -> Optional[str]:
    """Attempt deterministic repair on truncated JSON array output."""
    t = text.strip()
    idx = t.find("[")
    if idx >= 0:
        t = t[idx:]
    else:
        return None

    last_complete = -1
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(t):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_complete = i
    if last_complete <= 0:
        return None
    repaired = t[:last_complete + 1].rstrip().rstrip(",") + "]"
    try:
        json.loads(repaired)
        logger.info("Local JSON repair recovered complete objects from truncated array")
        return repaired
    except json.JSONDecodeError:
        return None

