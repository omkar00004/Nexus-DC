"""BaseAgent: provider-routing LLM access + shared KG/event-bus plumbing.

Roles route to the model registry in core/config.py:
    "pro"        -> Gemini MODEL_REASONING_PRO   (high-stakes synthesis)
    "flash"      -> Gemini MODEL_REASONING_FLASH (high-volume reasoning)
    "extraction" -> Groq   MODEL_EXTRACTION      (fast structured extraction)

Rate-limit strategy (latency-first): a 429 NEVER sleeps. The call rotates
INSTANTLY down a Gemini model chain, and if the whole chain is limited it
crosses providers to the Groq key pool. Sleeping is the last resort, not the
first response.

All agents follow the same pattern: deterministic computation first, LLM only
for nuance/narrative; results go to the KG and the event bus, never directly
to other agents.
"""
import json
import re
import time
from abc import ABC, abstractmethod

from core import config, event_bus, observability
from core.knowledge_graph import KnowledgeGraph

# proven-available free-tier fallbacks, tried in order after the configured model
_GEMINI_FALLBACKS = ["gemini-3.1-flash-lite", "gemini-2.0-flash"]


def _clean(text: str) -> str:
    # project convention: plain hyphens, never em/en dashes, in all output
    return text.replace("—", "-").replace("–", "-")


def _tolerant_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
        if start == -1:
            raise
        for end in range(len(text) - 1, start, -1):
            if text[end] in "}]":
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
        raise


class BaseAgent(ABC):
    name = "BASE"

    def __init__(self, kg: KnowledgeGraph | None = None):
        if kg is not None:
            self.kg = kg
        else:
            self.kg = KnowledgeGraph()
            if config.KG_PATH.exists():
                self.kg.load()
            else:
                self.kg.populate_from_cache()
                self.kg.save()
        self._gemini = None

    # ------------------------------------------------------------------ LLM

    def _gemini_client(self):
        if self._gemini is None:
            from google import genai
            self._gemini = genai.Client(api_key=config.GEMINI_API_KEY)
        return self._gemini

    def _model_for(self, role: str) -> str:
        return {
            "pro": config.MODEL_REASONING_PRO,
            "flash": config.MODEL_REASONING_FLASH,
            "extraction": config.MODEL_EXTRACTION,
        }[role]

    def call_llm(self, prompt: str, system: str | None = None, role: str = "flash",
                 temperature: float | None = None, max_tokens: int | None = None,
                 fast: bool = False) -> str:
        """fast=True caps the model's hidden thinking - right for interactive
        paths (ORACLE chat, GUIDE summaries) where latency beats deliberation."""
        temperature = config.TEMP_NARRATIVE if temperature is None else temperature
        max_tokens = max_tokens or config.MAX_TOKENS_DEFAULT
        if role == "extraction":
            return self._groq_generate(prompt, system, temperature, max_tokens)
        return self._gemini_generate(self._model_for(role), prompt, system,
                                     temperature, max_tokens,
                                     thinking_budget=0 if fast else None)

    def call_llm_structured(self, prompt: str, schema: dict, system: str | None = None,
                            role: str = "flash", temperature: float | None = None,
                            max_tokens: int | None = None):
        """Structured JSON output; schema enforcement with tolerant fallback."""
        temperature = config.TEMP_EXTRACTION if temperature is None else temperature
        max_tokens = max_tokens or config.MAX_TOKENS_NARRATIVE
        text = self._gemini_generate(
            self._model_for(role if role != "extraction" else "flash"),
            prompt, system, temperature, max_tokens, response_schema=schema,
        )
        return _tolerant_json(text)

    def _groq_generate(self, prompt: str, system: str | None, temperature: float,
                       max_tokens: int) -> str:
        from core.llm_pool import groq_pool
        resp = groq_pool().chat(
            model=config.MODEL_EXTRACTION,
            messages=([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens,
        )
        return _clean(resp.choices[0].message.content or "")

    def _gemini_generate(self, model: str, prompt: str, system: str | None,
                         temperature: float, max_tokens: int,
                         response_schema: dict | None = None,
                         thinking_budget: int | None = None) -> str:
        from google.genai import errors, types

        client = self._gemini_client()
        chain = [model] + [m for m in _GEMINI_FALLBACKS if m != model]
        # schema mode first, then schema-embedded-in-prompt if the API rejects it
        modes = ("schema", "prompt") if response_schema else ("plain",)
        last_exc = None

        for mode in modes:
            prompt_eff = prompt
            cfg_kwargs = dict(temperature=temperature, max_output_tokens=max_tokens,
                              system_instruction=system)
            if thinking_budget is not None:
                try:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=thinking_budget)
                except Exception:
                    pass  # SDK/model without thinking control: run as-is
            if response_schema:
                cfg_kwargs["response_mime_type"] = "application/json"
                if mode == "schema":
                    cfg_kwargs["response_schema"] = response_schema
                else:
                    prompt_eff = (prompt + "\n\nRespond ONLY with JSON matching this "
                                  "schema:\n" + json.dumps(response_schema))
            for mid in chain:  # 429 -> next model INSTANTLY, never sleep
                t0 = time.time()
                try:
                    resp = client.models.generate_content(
                        model=mid, contents=prompt_eff,
                        config=types.GenerateContentConfig(**cfg_kwargs),
                    )
                    if resp.text:
                        um = getattr(resp, "usage_metadata", None)
                        observability.trace_generation(
                            name=f"{self.name.lower()}.gemini", model=mid,
                            input_text=prompt_eff, output_text=resp.text,
                            latency_s=time.time() - t0,
                            usage={"input": getattr(um, "prompt_token_count", None),
                                   "output": getattr(um, "candidates_token_count", None)}
                            if um else None,
                        )
                        return _clean(resp.text)
                    last_exc = RuntimeError(f"empty response from {mid}")
                except errors.ClientError as exc:
                    last_exc = exc
                    observability.trace_generation(
                        name=f"{self.name.lower()}.gemini", model=mid,
                        input_text=prompt_eff, output_text=None,
                        latency_s=time.time() - t0, error=str(exc.code))
                    if exc.code == 429:
                        continue  # rotate down the chain immediately
                    if "thinking" in str(exc).lower() and "thinking_config" in cfg_kwargs:
                        cfg_kwargs.pop("thinking_config", None)  # model rejects the cap
                        continue
                    break  # non-429 (e.g. schema rejected): try next mode
                except Exception as exc:
                    last_exc = exc
                    break

        # whole Gemini chain limited -> cross provider to the Groq pool
        try:
            groq_prompt = prompt
            if response_schema:
                groq_prompt += ("\n\nRespond ONLY with JSON matching this schema:\n"
                                + json.dumps(response_schema))
            return self._groq_generate(groq_prompt, system, temperature, max_tokens)
        except Exception as exc:
            raise RuntimeError(
                f"all providers exhausted (gemini chain {chain} then groq): "
                f"{last_exc} / {exc}")

    # ------------------------------------------------------- shared plumbing

    def publish_event(self, event_type: str, entity_id: str, entity_type: str,
                      severity: str, description: str, risk_score: float) -> dict:
        return event_bus.publish({
            "agent": self.name, "event_type": event_type, "entity_id": entity_id,
            "entity_type": entity_type, "severity": severity,
            "description": description, "risk_score": round(float(risk_score), 3),
        })

    @abstractmethod
    def run(self, **kwargs) -> dict:
        ...
