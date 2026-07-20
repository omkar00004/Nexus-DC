"""Optional Langfuse tracing for every LLM call: model, latency, tokens, errors.

Strictly additive: a no-op unless LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are
set in .env, and every Langfuse call is exception-guarded so observability can
never take down the demo. View traces at LANGFUSE_HOST (cloud.langfuse.com by
default) once keys are configured.
"""
import atexit

from core import config

_client = None
_enabled = None


def _get_client():
    global _client, _enabled
    if _enabled is None:
        _enabled = bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY)
        if _enabled:
            try:
                from langfuse import Langfuse

                _client = Langfuse(
                    public_key=config.LANGFUSE_PUBLIC_KEY,
                    secret_key=config.LANGFUSE_SECRET_KEY,
                    host=config.LANGFUSE_HOST,
                )
                atexit.register(lambda: _client.flush())
            except Exception as exc:
                print(f"[observability] langfuse disabled ({exc})")
                _enabled = False
    return _client if _enabled else None


def trace_generation(name: str, model: str, input_text, output_text,
                     latency_s: float, usage: dict | None = None,
                     error: str | None = None) -> None:
    """Record one LLM generation. Silent no-op without keys."""
    client = _get_client()
    if client is None:
        return
    try:
        with client.start_as_current_generation(
            name=name, model=model,
            input=str(input_text)[:4000],
        ) as gen:
            gen.update(
                output=str(output_text)[:4000] if output_text else None,
                usage_details=usage or {},
                metadata={"latency_s": round(latency_s, 3),
                          **({"error": error} if error else {})},
            )
    except Exception:
        pass  # observability must never break the pipeline
