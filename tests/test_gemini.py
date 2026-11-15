"""Gemini (google/<model>) support and the guarantee that OpenRouter requests are unchanged.

No network, no keys: Inspect's `mockllm/model`, a recording `get_model`, and a Google Model
built with a dummy key (its config translation runs offline)."""

from __future__ import annotations

from typing import Any

import pytest
from inspect_ai.model import GenerateConfig, get_model

from undercooked import providers
from undercooked.config import AgentSpec, Config, parse_agents
from undercooked.providers import agent_model, no_reasoning_model


def cfg(agents: list[AgentSpec], **decision: Any) -> Config:
    return Config.model_validate(dict(
        name="t", level="forced_coordination", rounds=2, round_s=12, practice=False,
        agents=agents, decision={"flat_s": 2, "post_words_per_s": 10, **decision}))


@pytest.fixture
def made(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get_model(name: str, **kw: Any):
        calls.append((name, kw))
        return get_model("mockllm/model")

    monkeypatch.setattr(providers, "get_model", fake_get_model)
    return calls


OR = AgentSpec(model="openrouter/deepseek/deepseek-v4.1-flash", provider="baidu/fp8")
OR_PIN = {"only": ["baidu/fp8"], "allow_fallbacks": False, "require_parameters": True,
          "data_collection": "deny"}


def snap(calls):
    """Comparable form of the recorded get_model calls."""
    return [(n, {k: (v.model_dump(exclude_none=True) if isinstance(v, GenerateConfig) else v)
                 for k, v in kw.items()}) for n, kw in calls]


# ---- OpenRouter: byte-identical requests (expected values recorded before the Gemini change)
def test_openrouter_requests_unchanged(made):
    on = cfg([OR], reasoning_tokens=777, max_tokens=3333)
    agent_model(on.agents[0], on)
    off = cfg([OR], reasoning="off", max_tokens=3333)
    am = agent_model(off.agents[0], off)
    no_reasoning_model(am, 400)
    mandatory = AgentSpec.parse("openrouter/z-ai/glm-5.3-flash@morph/fp8!reasoning")
    mc = cfg([mandatory], reasoning="off", max_tokens=3333)
    am2 = agent_model(mandatory, mc)
    no_reasoning_model(am2, 400)
    pin2 = {"only": ["morph/fp8"], "allow_fallbacks": False, "require_parameters": True,
            "data_collection": "deny"}
    assert snap(made) == [
        ("openrouter/deepseek/deepseek-v4.1-flash",
         {"provider": OR_PIN, "config": {"max_tokens": 3333, "reasoning_tokens": 777}}),
        ("openrouter/deepseek/deepseek-v4.1-flash",
         {"provider": OR_PIN, "config": {"max_tokens": 3333}, "reasoning_enabled": False}),
        ("openrouter/deepseek/deepseek-v4.1-flash",
         {"provider": OR_PIN, "config": {"max_tokens": 400}, "reasoning_enabled": False}),
        ("openrouter/z-ai/glm-5.3-flash",
         {"provider": pin2, "config": {"max_tokens": 3333, "reasoning_tokens": 1000}}),
        ("openrouter/z-ai/glm-5.3-flash",
         {"provider": pin2, "config": {"max_tokens": 1500, "reasoning_tokens": 500}}),
    ]


# ---- Gemini
G = "google/gemini-2.5-flash"


def test_parse_google_specs():
    s = AgentSpec.parse(G)
    assert (s.model, s.provider, s.reasoning_off) == (G, None, True)
    assert AgentSpec.parse("google/gemini-2.5-pro!reasoning").reasoning_off is False
    mixed = parse_agents(f"{G},openrouter/google/gemma-4-31b-it@deepinfra/fp8")
    assert [(a.model, a.provider) for a in mixed] == [
        (G, None), ("openrouter/google/gemma-4-31b-it", "deepinfra/fp8")]
    with pytest.raises(ValueError, match="no provider|provider pins"):
        AgentSpec.parse(f"{G}@baidu/fp8")
    assert providers.is_google(G) and not providers.is_google("openrouter/google/gemma-4-31b-it")


def test_google_never_touches_openrouter_logic(made, monkeypatch):
    monkeypatch.setattr(providers, "fetch_endpoints",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched endpoints")))
    on = cfg([AgentSpec(model=G)], reasoning_tokens=777, max_tokens=3333)
    am = agent_model(on.agents[0], on)
    assert am.provider is None and am.endpoints is None
    off = cfg([AgentSpec(model=G)], reasoning="off", max_tokens=3333)
    am_off = agent_model(off.agents[0], off)
    no_reasoning_model(am_off, 400)
    keep = cfg([AgentSpec.parse(G + "!reasoning")], reasoning="off", max_tokens=3333)
    agent_model(keep.agents[0], keep)
    no_reasoning_model(agent_model(keep.agents[0], keep), 400)
    assert snap(made) == [
        (G, {"config": {"max_tokens": 3333, "reasoning_tokens": 777}}),
        (G, {"config": {"max_tokens": 3333, "reasoning_tokens": 0}}),
        (G, {"config": {"max_tokens": 400, "reasoning_tokens": 0}}),
        (G, {"config": {"max_tokens": 3333, "reasoning_tokens": 1000}}),
        (G, {"config": {"max_tokens": 3333, "reasoning_tokens": 1000}}),
        (G, {"config": {"max_tokens": 1500, "reasoning_tokens": 500}}),
    ]      # no provider=, no reasoning_enabled=


def test_mixed_team_builds_each_model_its_own_way(made):
    c = cfg([AgentSpec(model=G), OR], reasoning="off")
    [agent_model(s, c) for s in c.agents]
    agent_model(AgentSpec(model="mockllm/model"), c)
    kinds = [(n, "provider" in kw, kw.get("reasoning_enabled")) for n, kw in made]
    assert kinds == [(G, False, None), ("openrouter/deepseek/deepseek-v4.1-flash", True, False),
                     ("mockllm/model", False, False)]


def test_inspect_google_provider_thinking_config():
    """The real Inspect Google provider (offline, dummy key) turns our configs into these thinking
    configs. Skipped without the google-genai package (`uv sync --extra gemini`)."""
    pytest.importorskip("google.genai")
    c_on = providers.generate_config(cfg([AgentSpec(model=G)], reasoning_tokens=777), G)
    c_off = providers.generate_config(cfg([AgentSpec(model=G)], reasoning="off"), G)
    m = get_model(G, config=c_on, api_key="unused", memoize=False)
    t = m.api.chat_thinking_config(c_on)
    assert (t.include_thoughts, t.thinking_budget) == (True, 777)
    t = m.api.chat_thinking_config(c_off)
    assert (t.include_thoughts, t.thinking_budget) == (False, 0)
    # thinking-only models cannot be switched off: Inspect warns and keeps thinking on
    pro = get_model("google/gemini-2.5-pro", api_key="unused", memoize=False)
    assert pro.api.chat_thinking_config(c_off).include_thoughts is True


def test_gemini_example_config_loads():
    from pathlib import Path
    c = Config.load(Path(__file__).resolve().parents[1] / "configs" / "gemini_example.yaml")
    assert len(c.agents) == 2 and all(a.model.startswith("google/") for a in c.agents)
