"""Groq key pool + optional NVIDIA NIM fallback.

Pool behaviour:
- chat() round-robins across ALL configured Groq keys, so N free-tier keys
  give ~N x 8k TPM of effective extraction budget.
- A key that returns 429/rate-limit is put on cooldown (parsed retry hint or
  20 s) and skipped until it recovers; the call transparently moves to the
  next key.
- If every Groq key is cooling down and NIM_API_KEY is set, the call falls
  through to NVIDIA NIM (OpenAI-compatible /chat/completions) - slower but
  a different provider entirely, so correlated outages don't sink the demo.
"""
import itertools
import re
import time

import httpx
from groq import Groq

from core import config


class _PooledKey:
    def __init__(self, key: str):
        self.key = key
        self.client = Groq(api_key=key)
        self.cooldown_until = 0.0

    @property
    def available(self) -> bool:
        return time.time() >= self.cooldown_until


class GroqPool:
    def __init__(self, keys: list[str] | None = None):
        keys = keys or config.GROQ_API_KEYS
        if not keys:
            raise RuntimeError("no Groq API keys configured (GROQ_API_KEYS)")
        self._keys = [_PooledKey(k) for k in keys]
        self._rr = itertools.cycle(range(len(self._keys)))

    def _next_available(self) -> _PooledKey | None:
        for _ in range(len(self._keys)):
            pk = self._keys[next(self._rr)]
            if pk.available:
                return pk
        return None

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc)
        return "429" in s or "rate_limit" in s or "Request too large" in s or "413" in s

    @staticmethod
    def _cooldown_seconds(exc: Exception) -> float:
        m = re.search(r"try again in (\d+(?:\.\d+)?)", str(exc))
        return min(float(m.group(1)) + 1, 60) if m else 20.0

    def chat(self, **kwargs):
        """chat.completions.create across the pool; NIM as last resort."""
        last_exc = None
        # one full sweep of the pool (each attempt on a different key)
        for _ in range(len(self._keys)):
            pk = self._next_available()
            if pk is None:
                break
            t0 = time.time()
            try:
                try:
                    resp = pk.client.chat.completions.create(**kwargs)
                except TypeError:
                    kwargs.pop("reasoning_effort", None)
                    resp = pk.client.chat.completions.create(**kwargs)
                _trace_groq(kwargs, resp, time.time() - t0)
                return resp
            except Exception as exc:
                last_exc = exc
                if self._is_rate_limit(exc):
                    pk.cooldown_until = time.time() + self._cooldown_seconds(exc)
                    continue
                raise
        if config.NIM_API_KEY:
            return nim_chat(**kwargs)
        # everything cooling down and no NIM: wait out the shortest cooldown once
        soonest = min(k.cooldown_until for k in self._keys)
        wait = max(soonest - time.time(), 0) + 0.5
        if wait <= 65:
            time.sleep(wait)
            pk = self._next_available()
            if pk:
                return pk.client.chat.completions.create(**kwargs)
        raise RuntimeError(f"all Groq keys rate-limited (no NIM fallback configured): {last_exc}")

    @property
    def size(self) -> int:
        return len(self._keys)


def _trace_groq(kwargs: dict, resp, latency_s: float) -> None:
    try:
        from core import observability
        usage = getattr(resp, "usage", None)
        observability.trace_generation(
            name="groq.chat", model=kwargs.get("model", "?"),
            input_text=kwargs.get("messages", [])[-1].get("content", "")[:2000],
            output_text=resp.choices[0].message.content,
            latency_s=latency_s,
            usage={"input": getattr(usage, "prompt_tokens", None),
                   "output": getattr(usage, "completion_tokens", None)} if usage else None,
        )
    except Exception:
        pass


class _NimChoice:
    def __init__(self, payload):
        msg = payload["choices"][0]["message"]
        self.message = type("M", (), {"content": msg.get("content", "")})()
        self.finish_reason = payload["choices"][0].get("finish_reason")


class _NimResponse:
    def __init__(self, payload):
        self.choices = [_NimChoice(payload)]


def nim_chat(**kwargs) -> _NimResponse:
    """OpenAI-compatible call to NVIDIA NIM; mirrors the Groq response shape."""
    body = {
        "model": config.NIM_MODEL,
        "messages": kwargs["messages"],
        "temperature": kwargs.get("temperature", 0.2),
        "max_tokens": kwargs.get("max_tokens", 2000),
    }
    rf = kwargs.get("response_format")
    if rf and rf.get("type") == "json_object":
        body["response_format"] = rf  # NIM supports json_object on most models
    resp = httpx.post(
        f"{config.NIM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.NIM_API_KEY}"},
        json=body, timeout=120,
    )
    resp.raise_for_status()
    return _NimResponse(resp.json())


_pool: GroqPool | None = None


def groq_pool() -> GroqPool:
    global _pool
    if _pool is None:
        _pool = GroqPool()
    return _pool
