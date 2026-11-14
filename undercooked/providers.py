"""Agent models: OpenRouter endpoint pinning and automatic provider choice.

For `openrouter/...` models every request is pinned to one endpoint (no fallbacks, data
collection denied, and the endpoint must support every parameter we send). If the config leaves
`provider` empty, we list the model's endpoints and take the cheapest with good-enough
quantisation that supports tools and tool_choice. Other model names (e.g. `mockllm/model` in
tests) go straight to `get_model`.
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from inspect_ai.model import GenerateConfig, Model, get_model

from undercooked.config import AgentSpec, Config

OPENROUTER = "openrouter/"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"
QUANTS = ["fp8", "bf16", "fp16", "fp32"]          # in increasing precision
REQUIRED_PARAMS = ("tools", "tool_choice")


@dataclass
class AgentModel:
    model: Model
    spec: AgentSpec
    provider: str | None                      # the pinned endpoint tag
    endpoints: list[dict[str, Any]] | None = field(default=None)   # as OpenRouter listed them
    endpoints_error: str | None = None


def fetch_endpoints(model_id: str, timeout: float = 20) -> list[dict[str, Any]]:
    """GET /models/{author}/{slug}/endpoints (no key needed). `model_id` has no openrouter/."""
    req = urllib.request.Request(ENDPOINTS_URL.format(model=model_id),
                                 headers={"User-Agent": "undercooked"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    return list(body["data"]["endpoints"])


def _price(ep: dict[str, Any]) -> float:
    p = ep.get("pricing") or {}
    return float(p.get("prompt") or 0) + float(p.get("completion") or 0)


def eligible(ep: dict[str, Any], min_quant: str) -> bool:
    quant = (ep.get("quantization") or "").lower()
    ok_quants = QUANTS[QUANTS.index(min_quant):]
    params = set(ep.get("supported_parameters") or [])
    return quant in ok_quants and all(p in params for p in REQUIRED_PARAMS) and bool(ep.get("tag"))


def pick_endpoint(endpoints: list[dict[str, Any]], min_quant: str) -> dict[str, Any]:
    """Cheapest (prompt + completion price) eligible endpoint; ties keep listing order."""
    ok = [ep for ep in endpoints if eligible(ep, min_quant)]
    if not ok:
        raise ValueError(f"no endpoint with quantization >= {min_quant} and "
                         f"{'/'.join(REQUIRED_PARAMS)} support among "
                         f"{[(e.get('tag'), e.get('quantization')) for e in endpoints]}")
    return min(ok, key=_price)


# served_by: OpenRouter names the provider that served a request in the response body
# ("provider"). Inspect keeps the raw response (ModelEvent.call) only for the first few calls per
# model unless log_model_api is on, so we read it where the response arrives instead: the
# OpenAI-compatible APIs pass every raw response dict to `api.on_response`, which we wrap.
# One Model (and so one wrapped on_response) is shared by every agent and season that uses the
# same model string (get_model memoizes), and their calls run concurrently. The box is a
# ContextVar set around each generate (`served_providers`), and on_response runs inside that
# generate, so each call appends only to its own box (tests/test_pipeline.py checks this with
# concurrent calls through one Model). This relies on on_response being called from the task
# that awaits generate (or a task it started), which holds for OpenAI-compatible providers; it
# would not for Inspect's batch mode, which we don't use.
_served_box: ContextVar[list[str] | None] = ContextVar("undercooked_served_box", default=None)


def track_served_provider(model: Model) -> Model:
    """Record the "provider" of every raw response of this model into the active box."""
    api = model.api
    if getattr(api, "_undercooked_tracked", False) or not hasattr(api, "on_response"):
        return model
    original = api.on_response

    def on_response(response: dict[str, Any]) -> None:
        box = _served_box.get()
        if box is not None and isinstance(response, dict) \
                and isinstance(response.get("provider"), str):
            box.append(response["provider"])
        original(response)

    api.on_response = on_response          # type: ignore[method-assign]
    api._undercooked_tracked = True        # type: ignore[attr-defined]
    return model


@contextmanager
def served_providers() -> Iterator[list[str]]:
    """Collect the providers reported by the responses received inside this block (this task)."""
    box: list[str] = []
    token = _served_box.set(box)
    try:
        yield box
    finally:
        _served_box.reset(token)


def generate_config(cfg: Config) -> GenerateConfig:
    return GenerateConfig(reasoning_tokens=cfg.decision.reasoning_tokens,
                          max_tokens=cfg.decision.max_tokens)


def provider_options(pin: str) -> dict[str, Any]:
    return {"only": [pin], "allow_fallbacks": False, "require_parameters": True,
            "data_collection": "deny"}


def agent_model(spec: AgentSpec, cfg: Config) -> AgentModel:
    config = generate_config(cfg)
    if not spec.model.startswith(OPENROUTER):
        model = track_served_provider(get_model(spec.model, config=config))
        return AgentModel(model, spec, spec.provider)
    model_id = spec.model[len(OPENROUTER):]
    endpoints, error = None, None
    try:
        endpoints = fetch_endpoints(model_id)
    except Exception as e:   # only fatal when we must pick automatically
        error = f"{type(e).__name__}: {e}"
        if spec.provider is None:
            raise RuntimeError(f"cannot list endpoints for {model_id}: {error}") from e
    pin = spec.provider or pick_endpoint(endpoints or [], cfg.providers.min_quant)["tag"]
    model = track_served_provider(
        get_model(spec.model, provider=provider_options(pin), config=config))
    return AgentModel(model, spec, pin, endpoints, error)
