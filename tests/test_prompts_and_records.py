"""What the agents are told, and what the records say, match what the code does.

Season-level tests here drive `Season` directly with a scripted `model_turn` (no Inspect eval, no
model calls) and collect the kitchen records it would log.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any, Callable

import pytest
from inspect_ai.model import get_model
from pydantic import ValidationError

from undercooked import prompts, providers
from undercooked.board import Board
from undercooked.config import AgentSpec, Config
from undercooked.engine import Game, Kitchen
from undercooked.providers import agent_model
from undercooked.replay import DecisionRecord, Event, Post
from undercooked.season import Gen, Season
from undercooked.tools import Turn, act_tool, note_tool, post_tool, read_board_tool, ready_tool

GOAL = "Get the highest team score. You won't score highly unless you work together."
REAL_AGENTS = [AgentSpec(model="openrouter/deepseek/deepseek-v4.1-flash", provider="baidu/fp8"),
               AgentSpec(model="openrouter/google/gemma-4-31b-it", provider="deepinfra/fp8")]
LEAKS = ["deepseek", "gemma", "google", "baidu", "deepinfra", "novita", "openrouter", "fp8",
         "flash", "kitchenfake", "fakecloud", "mockllm"]


def cfg(**kw: Any) -> Config:
    base = dict(name="t", level="forced_coordination", rounds=2, round_s=12, practice=False,
                chat_max_posts=2, agents=[AgentSpec(model="mockllm/model")] * 2,
                decision={"flat_s": 2, "post_words_per_s": 10})
    return Config.model_validate({**base, **kw})


Script = Callable[[str, str, int], list[tuple[str, dict[str, Any]]]]


def scripted_season(c: Config, script: Script, epoch: int = 1) -> Season:
    """A Season whose model turns call tools from `script(agent, text, n)` (n = the agent's turn
    count so far) instead of a model; logged records go to `season.logged`, texts and tool
    results to `season.seen` / `season.results`."""
    s = Season(c, [agent_model(spec, c) for spec in c.agents], epoch)
    s.logged, s.seen, s.results = [], [], []                 # type: ignore[attr-defined]
    s._emit = s.logged.append                                # type: ignore[method-assign]
    count: dict[str, int] = {}

    async def model_turn(a, text, tools, max_gens, done, force=None) -> Gen:  # no follow-ups
        n = count[a.name] = count.get(a.name, -1) + 1
        s.seen.append((a.name, text))
        by = {t.name: t for t in tools}
        for name, kw in script(a.name, text, n):
            s.results.append((a.name, name, await by[name].tool(**kw)))
        return Gen()

    s.model_turn = model_turn                                # type: ignore[method-assign]
    return s


def records(s: Season, kind: type) -> list:
    out = []
    for d in s.logged:                                       # type: ignore[attr-defined]
        if d.get("kind") == kind.model_fields["kind"].default:
            out.append(kind.model_validate(d))
    return out


# ------------------------------------------------------------------ prompts: the goal
def test_goal_sentence_is_exact_and_in_every_system_prompt():
    assert prompts.GOAL == GOAL
    c = cfg(agents=REAL_AGENTS)
    for me, mate in (("Kilo", "Tango"), ("Tango", "Kilo")):
        text = prompts.system_prompt(c, me, mate)
        assert text.rstrip().endswith("GOAL\n" + GOAL)
        assert text.splitlines()[0] == (f"You are {me}, one of two cooks in a kitchen game. "
                                        f"Your teammate is {mate}.")


# ------------------------------------------------------------------ prompts: no model names
def agent_facing_texts(c: Config) -> list[str]:
    """Every text an agent reads that doesn't come from its teammate: system prompt, phase
    messages, chat turns with the kitchen overview, state texts, debrief, cutoff notice,
    compaction prompt and every tool description."""
    s = Season(c, [agent_model(AgentSpec(model="mockllm/model"), c)] * 2, 1)
    texts = [m.text for a in s.agents for m in a.messages]                 # system + intro
    g = Game(Kitchen.load(c.level), s.seated, c)
    for a in s.agents:
        texts += [prompts.kitchen_overview(g, a.name),
                  prompts.chat_turn(c, 1, first_turn=True, overview=prompts.kitchen_overview(
                      g, a.name), new_posts=None, posts_left=2, ready=False, notepad="n"),
                  g.state_text(a.name, unread_posts=1, notepad="n", last_plan_result="done")]
    texts += [prompts.season_intro(c), prompts.practice_start(c),
              prompts.round_start(c, 1, chat_turn_limit=True), prompts.cutoff_notice(3),
              prompts.debrief(c, 1, 20, 1, 0, [20], "n"), prompts.COMPACTION_PROMPT]
    turn = Turn()
    tools = [act_tool(turn, lambda p: None, c.max_plan_len), post_tool(turn),
             read_board_tool(turn, lambda: ""), note_tool(turn), ready_tool(turn)]
    for t in tools:
        texts += [t.description, *[str(v) for v in t.parameters.properties.values()]]
    return texts


def test_no_model_or_provider_names_in_agent_facing_text():
    c = cfg(agents=REAL_AGENTS, practice=True)
    text = "\n".join(agent_facing_texts(c)).lower()
    banned = LEAKS + [spec.model.lower() for spec in c.agents] + [spec.provider.lower()
                                                                  for spec in c.agents]
    assert [w for w in banned if w in text] == []
    assert not re.search(r"\bmodels?\b", text), "agents are never told about models"
    # mixed teams: what each agent is told doesn't depend on who plays which model
    same = cfg(agents=[REAL_AGENTS[0]] * 2, practice=True)
    assert agent_facing_texts(same) == agent_facing_texts(c)


# ------------------------------------------------------------------ providers (no network)
def test_openrouter_requests_are_pinned_without_fallbacks(monkeypatch):
    made: list[tuple[str, dict[str, Any]]] = []

    def fake_get_model(name: str, **kw: Any):
        made.append((name, kw))
        return get_model("mockllm/model")

    def no_fetch(*a: Any, **kw: Any):
        raise AssertionError("fetched endpoints although the provider is pinned")

    monkeypatch.setattr(providers, "get_model", fake_get_model)
    monkeypatch.setattr(providers, "fetch_endpoints", no_fetch)
    c = cfg(agents=REAL_AGENTS, decision={"reasoning_tokens": 777, "max_tokens": 3333})
    am = agent_model(c.agents[0], c)
    [(name, kw)] = made
    assert name == "openrouter/deepseek/deepseek-v4.1-flash"
    assert kw["provider"] == {"only": ["baidu/fp8"], "allow_fallbacks": False,
                              "require_parameters": True, "data_collection": "deny"}
    assert (kw["config"].reasoning_tokens, kw["config"].max_tokens) == (777, 3333)
    assert am.provider == "baidu/fp8" and am.endpoints is None and am.endpoints_error is None

    # no pin: endpoints are fetched, kept for the log, and the cheapest eligible one is pinned
    eps = [{"tag": "dear/fp8", "quantization": "fp8", "supported_parameters": ["tools", "tool_choice"],
            "pricing": {"prompt": "0.2", "completion": "0.2"}},
           {"tag": "cheap/fp8", "quantization": "fp8", "supported_parameters": ["tools", "tool_choice"],
            "pricing": {"prompt": "0.1", "completion": "0.1"}}]
    monkeypatch.setattr(providers, "fetch_endpoints", lambda model_id: eps)
    made.clear()
    am = agent_model(AgentSpec(model="openrouter/x/y"), c)
    assert made[0][1]["provider"]["only"] == ["cheap/fp8"] and am.endpoints == eps


# ------------------------------------------------------------------ config (D1)
def test_realtime_mode_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="realtime mode is not implemented"):
        cfg(decision={"mode": "realtime"})
    path = tmp_path / "c.yaml"
    path.write_text("name: x\nlevel: forced_coordination\ndecision: {mode: realtime}\n"
                    "agents: [{model: m}, {model: m}]\n")
    with pytest.raises(ValidationError, match="realtime mode is not implemented"):
        Config.load(path)
    assert cfg(decision={"mode": "simulated"}).decision.mode == "simulated"


# ------------------------------------------------------------------ act() truncation (D2)
@pytest.mark.parametrize("plan, n", [
    (["wait(1)"] * 6, 6),
    (["wait(1); wait(2)", "wait(3), wait(4)", "wait(5)\nwait(6)"], 6),     # 3 elements
    (["wait(1); wait(2); wait(3); wait(4); wait(5); wait(6); wait(7)"], 7),  # 1 element
])
def test_act_says_when_the_plan_is_cut(plan, n):
    turn = Turn()
    msg = asyncio.run(act_tool(turn, lambda p: None, 5).tool(plan=plan))
    assert "Only the first 5 actions are kept." in msg and f"{n} actions" in msg
    for ok in (["wait(1)"] * 5, ["wait(1); wait(2)", "wait(3), wait(4), wait(5)"]):
        msg = asyncio.run(act_tool(Turn(), lambda p: None, 5).tool(plan=ok))
        assert "Only the first" not in msg


@pytest.mark.parametrize("pipeline", [False, True])
def test_decision_record_logs_the_plan_that_runs(pipeline):
    sent = {0: ["wait(1); wait(1); wait(1)", "wait(1), wait(1), wait(2)"],   # 6 -> first 5
            1: ["wait(1)", "interact(pot_9)", "wait(2)"],                   # stops at pot_9
            2: ["interact(pot_9)"]}                                         # rejected

    def script(agent: str, text: str, n: int):
        return [("act", {"plan": sent.get(n, ["wait(30)"])})]

    c = cfg(rounds=1, round_s=20, decision={"flat_s": 1, "pipeline": pipeline})
    s = scripted_season(c, script)
    asyncio.run(s.round(1))
    for name in s.seated:
        mine = [d for d in records(s, DecisionRecord) if d.agent == name]
        assert [d.plan_sent for d in mine[:3]] == [sent[0], sent[1], sent[2]]
        assert mine[0].plan == ["wait(1)"] * 5
        assert mine[1].plan == ["wait(1)", "interact(pot_9)"]
        assert mine[2].plan == []
        lands = [e for e in records(s, Event) if e.type == "decision_land" and e.agent == name]
        assert [e.data["plan"] for e in lands[:3]] == [d.plan for d in mine[:3]]
    cut = [r for _, tool, r in s.results if tool == "act" and "Only the first" in r]
    assert len(cut) == 2                                          # one per agent


# ------------------------------------------------------------------ cutoff notice (D3)
def test_cutoff_notice_text():
    assert prompts.cutoff_notice(1).endswith("you stood still for 1 s.")
    assert "stood still for 4 s (1 s, plus 3 s for the words you posted)" in prompts.cutoff_notice(4)


@pytest.mark.parametrize("pipeline", [False, True])
def test_cutoff_notice_states_the_real_cost(pipeline):
    words = 25

    def script(agent: str, text: str, n: int):
        if agent == s.agents[0].name and n == 0:              # a cutoff with a 25-word post
            return [("post", {"message": " ".join(["onion"] * words)})]
        return [("act", {"plan": ["wait(2)"]})]

    c = cfg(rounds=1, round_s=12, decision={"flat_s": 2, "pipeline": pipeline})
    s = scripted_season(c, script)
    asyncio.run(s.round(1))
    cost = 1 + math.ceil(words / c.decision.post_words_per_s)
    [cut] = [d for d in records(s, DecisionRecord) if d.cutoff]
    assert cut.lands_t - cut.t == cost == 4
    texts = [t for name, t in s.seen if name == s.agents[0].name]
    assert texts[1].startswith(prompts.cutoff_notice(cost))
    assert prompts.cutoff_notice(cost) not in texts[2]           # said once


# ------------------------------------------------------------------ unlanded decisions (D4)
@pytest.mark.parametrize("pipeline", [False, True])
def test_unlanded_decision_keeps_its_post_but_never_posts(pipeline):
    text = " ".join(["late"] * 25)                                # 2 + 3 s: lands after the end

    def script(agent: str, t: str, n: int):
        if agent != s.agents[0].name:
            return [("act", {"plan": ["wait(30)"]})]
        if "Time 0 /" not in t:
            return [("post", {"message": text}), ("act", {"plan": ["wait(1)"]})]
        return [("act", {"plan": ["wait(2)"]})]

    c = cfg(rounds=1, round_s=6, decision={"flat_s": 2, "pipeline": pipeline})
    s = scripted_season(c, script)
    asyncio.run(s.round(1))
    decisions = records(s, DecisionRecord)
    late = [d for d in decisions if not d.landed]
    assert late and all(d.post == text and d.agent == s.agents[0].name and d.lands_t is None
                        for d in late)
    assert all(d.landed for d in decisions if d.lands_t is not None)
    assert text not in [p.text for p in records(s, Post)]
    assert text not in [p.text for p in s.board.posts]


# ------------------------------------------------------------------ chat rules (D3, R1c)
def test_chat_rules_in_the_system_prompt_match_the_chat():
    c = cfg(chat_max_posts=3)
    text = prompts.system_prompt(c, "Kilo", "Tango")
    assert "up to 3 posts per chat" in text
    assert ("The chat ends when each of you has called ready() or used all your posts" in
            " ".join(text.split()))
    assert "after 5 turns each" in " ".join(text.split())
    assert prompts.chat_turns_each(c) == 5


def run_chat(c: Config, script: Script, r: int = 1, epoch: int = 1) -> Season:
    s = scripted_season(c, script, epoch)
    asyncio.run(s.chat(r))
    return s


def chat_end(s: Season) -> Event:
    return [e for e in records(s, Event) if e.type == "phase_end"][-1]


def test_chat_ends_at_the_turn_limit_and_the_round_says_so(monkeypatch):
    c = cfg(chat_max_posts=2)
    s = run_chat(c, lambda agent, text, n: [])                    # both always pass
    chat = [d for d in records(s, DecisionRecord)]
    assert len(chat) == 2 * prompts.chat_turns_each(c) == 8
    assert chat_end(s).data["turn_limit"] is True
    intros: list[str] = []

    async def play(games, board, intro):
        intros.append(intro)

    monkeypatch.setattr(s, "play", play)
    asyncio.run(s.round(1))
    assert intros == ["Round 1 of 2 starts now. (The chat ended at its turn limit, 4 turns "
                      "each.)"]


def test_chat_ends_when_each_is_ready_or_out_of_posts():
    """One agent ready (posts left), the other out of posts: the chat is over."""
    c = cfg(chat_max_posts=2)
    holder: dict[str, str] = {}

    def script(agent: str, text: str, n: int):
        if agent == holder["ready"]:
            return [("ready", {})]
        return [("post", {"message": f"post {n}"})]

    s0 = Season(c, [agent_model(spec, c) for spec in c.agents], 1)
    holder["ready"] = s0.agents[0].name
    s = run_chat(c, script)
    end = chat_end(s)
    assert end.data["turn_limit"] is False
    assert end.data["ready"] == [holder["ready"]]
    assert end.data["posts"] == {holder["ready"]: 0, s.agents[1].name: 2}
    assert len(records(s, DecisionRecord)) <= 4


def test_first_speaker_is_drawn_per_round_and_logged():
    from undercooked.season import first_speaker

    c = cfg()
    seen = set()
    for seed in range(4):
        for r in (1, 2, 3):
            cs = c.model_copy(update={"seed": seed})
            s = run_chat(cs, lambda agent, text, n: [("ready", {})], r=r)
            start = [e for e in records(s, Event) if e.type == "phase_start"][0]
            first = s.agents[first_speaker(seed, 1, r)].name
            assert start.data == {"first": first}
            assert records(s, DecisionRecord)[0].agent == first
            seen.add(first == s.agents[0].name)
    assert seen == {True, False}
