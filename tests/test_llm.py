"""LLM client tests. All run offline — no real network call is ever allowed
to succeed here; a monkeypatched httpx.post that raises AssertionError if
invoked is the enforcement mechanism for the "replay mode -> no network"
invariant (project specification section 3.4).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from fact_layer import llm


@pytest.fixture(autouse=True)
def _isolated_llm_env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.delenv("LLM_API_KEYS", raising=False)
    monkeypatch.setenv("LLM_MODE", "replay")
    llm._stats.update(calls=0, cache_hits=0, network_calls=0, estimated_input_tokens=0,
                      repair_attempts=0, key_rotations=0)
    llm._reset_key_rotation()
    yield


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted here")


def test_replay_cache_miss_raises_without_network(monkeypatch):
    monkeypatch.setattr(httpx, "post", _no_network)

    with pytest.raises(llm.ReplayCacheMiss):
        llm.complete([{"role": "user", "content": "hello"}])


def test_replay_cache_hit_returns_cached_response_without_network(monkeypatch):
    monkeypatch.setattr(httpx, "post", _no_network)

    messages = [{"role": "user", "content": "hello"}]
    provider, model, _ = llm._config()
    key = llm._cache_key(provider, model, messages)
    llm._save_cache(key, provider, model, messages, "cached response")

    result = llm.complete(messages)

    assert result == "cached response"
    assert llm._stats["cache_hits"] == 1
    assert llm._stats["network_calls"] == 0


class _FakeResponse:
    def __init__(self, content: str = "", status_code: int = 200, text: str = "", headers: dict = None):
        self._content = content
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_live_call_populates_cache_then_replay_hits_it_offline(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse("live response"))

    messages = [{"role": "user", "content": "live please"}]
    result = llm.complete(messages)
    assert result == "live response"
    assert llm._stats["network_calls"] == 1

    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    result2 = llm.complete(messages)
    assert result2 == "live response"
    assert llm._stats["cache_hits"] == 1


def test_json_repair_pass_retries_once_on_bad_json(monkeypatch):
    responses = iter(["not json", '{"ok": true}'])
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(next(responses))

    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete([{"role": "user", "content": "give me json"}], schema_hint='{"ok": bool}')

    assert result == '{"ok": true}'
    assert len(calls) == 2
    assert llm._stats["repair_attempts"] == 1


def test_missing_model_raises_clear_config_error(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    with pytest.raises(llm.LLMError):
        llm.complete([{"role": "user", "content": "hello"}])


# --------------------------------------------------------------------------
# Multi-key rotation
# --------------------------------------------------------------------------

def test_rotates_to_next_key_on_quota_exhausted_response(monkeypatch):
    """A 429 whose body mentions a daily/quota limit must rotate to the next
    key immediately — not burn the whole backoff budget on a dead key."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "key-one,key-two")

    seen_keys = []

    def fake_post(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        seen_keys.append(key)
        if key == "key-one":
            return _FakeResponse(status_code=429, text='{"error": "daily quota exceeded"}')
        return _FakeResponse("response from key two", status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete([{"role": "user", "content": "hello"}])

    assert result == "response from key two"
    assert seen_keys == ["key-one", "key-two"]   # exactly one attempt on the dead key, no backoff loop
    assert llm._stats["key_rotations"] == 1


def test_rotates_to_next_key_on_401(monkeypatch):
    """An invalid/revoked key (401/403) rotates immediately too, not just
    quota errors."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "bad-key,good-key")

    def fake_post(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        if key == "bad-key":
            return _FakeResponse(status_code=401, text="invalid api key")
        return _FakeResponse("ok from good key", status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete([{"role": "user", "content": "hello"}])
    assert result == "ok from good key"


def test_all_keys_exhausted_raises_clear_error(monkeypatch):
    """When every configured key fails, the error must say so clearly rather
    than looking like a single generic HTTP failure."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "key-one,key-two")
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _FakeResponse(status_code=429, text='{"error": "daily quota exceeded"}'),
    )

    with pytest.raises(llm.LLMError, match="all 2 configured API key"):
        llm.complete([{"role": "user", "content": "hello"}])


def test_subsequent_call_skips_already_exhausted_key(monkeypatch):
    """Once a key is marked exhausted, later calls in the same process must
    not waste an attempt on it again."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "key-one,key-two")

    attempts_on_key_one = []

    def fake_post(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        if key == "key-one":
            attempts_on_key_one.append(1)
            return _FakeResponse(status_code=429, text='{"error": "daily quota exceeded"}')
        return _FakeResponse("ok", status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    llm.complete([{"role": "user", "content": "first call"}])
    llm.complete([{"role": "user", "content": "second call, different cache key"}])

    assert len(attempts_on_key_one) == 1, "key-one should only be tried once across both calls"


def test_reset_key_rotation_makes_exhausted_keys_eligible_again():
    llm._key_rotation["exhausted_indices"] = {0}
    llm._reset_key_rotation()
    assert llm._key_rotation["exhausted_indices"] == set()
    assert llm._key_rotation["current_index"] == 0


# --------------------------------------------------------------------------
# Rolling quota window: wait it out on the same key rather than rotating.
# Discovered against the real Groq API: keys sharing one org/account share
# one TPD quota, so rotating between them on a rolling-window 429 achieves
# nothing — but the error message gives an exact short wait, and honoring it
# lets the run continue on the SAME key instead of burning through the pool.
# --------------------------------------------------------------------------

def test_parse_wait_hint_minutes_and_seconds():
    assert llm._parse_wait_hint("Please try again in 1m54.048s.") == 114.048


def test_parse_wait_hint_seconds_only():
    assert llm._parse_wait_hint("Please try again in 12s.") == 12.0


def test_parse_wait_hint_absent_returns_none():
    assert llm._parse_wait_hint("no timing information here") is None


def test_waits_out_short_quota_window_on_same_key_no_rotation(monkeypatch):
    """A quota 429 with a short 'try again in Ns' hint must be waited out on
    the SAME key — multiple keys from one account share one quota pool, so
    rotating on this specific error shape buys nothing."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "key-one,key-two")

    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    quota_text = ('{"error":{"message":"Rate limit reached ... on tokens per day (TPD): '
                  'Limit 200000, Used 199000. Please try again in 5.5s."}}')

    def fake_post(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        assert key == "key-one", "must retry the SAME key, not rotate"
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(status_code=429, text=quota_text)
        return _FakeResponse("recovered", status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete([{"role": "user", "content": "hello"}])

    assert result == "recovered"
    assert calls["n"] == 2
    assert sleeps == [7.5]   # 5.5s hint + 2.0s buffer
    assert llm._stats["key_rotations"] == 0


def test_rotates_when_quota_wait_hint_too_long(monkeypatch):
    """A quota 429 whose wait hint implies a real multi-hour reset (not a
    rolling window) must still rotate rather than block for hours."""
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEYS", "key-one,key-two")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    long_wait_text = '{"error":{"message":"quota exceeded, daily limit. Please try again in 7200s."}}'

    def fake_post(url, headers=None, json=None, timeout=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        if key == "key-one":
            return _FakeResponse(status_code=429, text=long_wait_text)
        return _FakeResponse("ok from key two", status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = llm.complete([{"role": "user", "content": "hello"}])
    assert result == "ok from key two"
    assert llm._stats["key_rotations"] == 1
