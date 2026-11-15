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
    if cfg.decision.reasoning == "off":            # no reasoning budget next to enabled: false
        return GenerateConfig(max_tokens=cfg.decision.max_tokens)
    return GenerateConfig(reasoning_tokens=cfg.decision.reasoning_tokens,
                          max_tokens=cfg.decision.max_tokens)


# Reasoning off. Inspect's OpenRouter provider takes a `reasoning_enabled` model arg (a
# constructor arg, not a GenerateConfig field) and sends it as extra_body
# {"reasoning": {"enabled": false}}; with no reasoning_tokens in the config that is the whole
# "reasoning" object (inspect_ai/model/_providers/openrouter.py, completion_params). So a model
# without reasoning is its own Model instance. Other model APIs get the same arg (the offline
# fakes record it); a non-OpenRouter API that does not accept it cannot be run with reasoning off.
NO_REASONING = {"reasoning_enabled": False}


def reasoning_off(cfg: Config, spec: AgentSpec) -> bool:
    """Does this agent's main model run with reasoning off? (decision.reasoning "off", unless
    its endpoint cannot turn reasoning off: spec.reasoning_off False keeps it on.)"""
    return cfg.decision.reasoning == "off" and spec.reasoning_off


def reasoning_args(cfg: Config, spec: AgentSpec | None = None) -> dict[str, Any]:
    """Model args for the agents' main model: none when reasoning is on."""
    off = cfg.decision.reasoning == "off" and (spec is None or spec.reasoning_off)
    return dict(NO_REASONING) if off else {}


# Some endpoints refuse reasoning off with HTTP 400 "Reasoning is mandatory for this endpoint and
# cannot be disabled" (z-ai/glm-5.3-flash@morph/fp8). For those (AgentSpec.reasoning_off False)
# the forced-answer follow-up keeps reasoning on, with a small reasoning budget inside
# MANDATORY_MAX_TOKENS. An unflagged endpoint that answers a reasoning-off call with that 400 is
# retried once with reasoning on (`with_reasoning_fallback`), and the model then skips straight
# to the fallback for the rest of the run.
MANDATORY = "Reasoning is mandatory"
MANDATORY_MAX_TOKENS = 1500
MANDATORY_REASONING_TOKENS = 500
_fallback_box: ContextVar[list[str] | None] = ContextVar("undercooked_fallback_box", default=None)


@contextmanager
def reasoning_fallbacks() -> Iterator[list[str]]:
    """Collect the reasoning-off calls inside this block (this task) that fell back to reasoning
    on (one entry per call)."""
    box: list[str] = []
    token = _fallback_box.set(box)
    try:
        yield box
    finally:
        _fallback_box.reset(token)


def _pinned(spec: AgentSpec, pin: str | None, config: GenerateConfig, **args: Any) -> Model:
    if not spec.model.startswith(OPENROUTER):
        return track_served_provider(get_model(spec.model, config=config, **args))
    return track_served_provider(get_model(spec.model, provider=provider_options(pin or ""),
                                           config=config, **args))


def reasoning_on_model(spec: AgentSpec, pin: str | None) -> Model:
    """The agent's endpoint with reasoning on and max_tokens MANDATORY_MAX_TOKENS."""
    return _pinned(spec, pin, GenerateConfig(max_tokens=MANDATORY_MAX_TOKENS,
                                             reasoning_tokens=MANDATORY_REASONING_TOKENS))


def with_reasoning_fallback(model: Model, spec: AgentSpec, pin: str | None) -> Model:
    """Make a reasoning-off Model retry a "Reasoning is mandatory" 400 once with reasoning on
    (reasoning_on_model), noting the fallback in the active `reasoning_fallbacks()` box; after the
    first such 400 every later call of this Model goes straight to reasoning on. Patches the
    (memoized) instance once; compaction calls through it are covered too."""
    if getattr(model, "_undercooked_fallback", False):
        return model
    original = model.generate
    state = {"mandatory": False}

    async def generate(input: Any, tools: Any = [], tool_choice: Any = None,
                       config: GenerateConfig = GenerateConfig(), **kw: Any) -> Any:
        if not state["mandatory"]:
            try:
                return await original(input, tools, tool_choice, config, **kw)
            except Exception as e:                       # noqa: BLE001 - re-raised below
                if MANDATORY not in str(e):
                    raise
                state["mandatory"] = True
        box = _fallback_box.get()
        if box is not None:
            box.append(spec.model)
        fallback = reasoning_on_model(spec, pin)
        return await fallback.generate(input, tools, tool_choice, config, **kw)

    model.generate = generate                    # type: ignore[method-assign]
    model._undercooked_fallback = True           # type: ignore[attr-defined]
    return model


def no_reasoning_model(am: AgentModel, max_tokens: int) -> Model:
    """The model of the forced-answer follow-up call (season.py): the agent's endpoint with
    reasoning disabled and a small max_tokens; for an endpoint whose reasoning cannot be turned
    off (spec.reasoning_off False), reasoning on with max_tokens MANDATORY_MAX_TOKENS. get_model
    memoizes, so this is cheap to repeat."""
    if not am.spec.reasoning_off:
        return reasoning_on_model(am.spec, am.provider)
    model = _pinned(am.spec, am.provider, GenerateConfig(max_tokens=max_tokens), **NO_REASONING)
    return with_reasoning_fallback(model, am.spec, am.provider)


def provider_options(pin: str) -> dict[str, Any]:
    return {"only": [pin], "allow_fallbacks": False, "require_parameters": True,
            "data_collection": "deny"}


def agent_model(spec: AgentSpec, cfg: Config) -> AgentModel:
    """The agent's Model. With a provider pinned in the config nothing is fetched (no blocking
    GET), so `endpoints` is None; otherwise the endpoint list is fetched, kept in `endpoints`
    (logged by the season solver) and the cheapest eligible endpoint is pinned."""
    off = reasoning_off(cfg, spec)
    config = generate_config(cfg) if off or cfg.decision.reasoning == "on" else \
        GenerateConfig(reasoning_tokens=cfg.decision.reasoning_tokens,
                       max_tokens=cfg.decision.max_tokens)    # off asked, but mandatory
    args = reasoning_args(cfg, spec)

    def wrap(model: Model, pin: str | None) -> Model:
        return with_reasoning_fallback(model, spec, pin) if off else model

    if not spec.model.startswith(OPENROUTER):
        model = wrap(track_served_provider(get_model(spec.model, config=config, **args)),
                     spec.provider)
        return AgentModel(model, spec, spec.provider)
    model_id = spec.model[len(OPENROUTER):]
    endpoints, error = None, None
    if spec.provider is None:
        try:
            endpoints = fetch_endpoints(model_id)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise RuntimeError(f"cannot list endpoints for {model_id}: {error}") from e
    pin = spec.provider or pick_endpoint(endpoints or [], cfg.providers.min_quant)["tag"]
    model = wrap(track_served_provider(
        get_model(spec.model, provider=provider_options(pin), config=config, **args)), pin)
    return AgentModel(model, spec, pin, endpoints, error)
