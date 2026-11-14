"""Forced answers (budget forcing), the reasoning switch, communication and debrief conditions.

Offline, with the scripted models in tests/kitchen_fake.py: `+overthink` replies with reasoning
only and stops at max_tokens; `+stubborn` does that on the follow-up too.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from inspect_ai.log import read_eval_log

from tests import kitchen_fake as fake
from tests.test_pipeline import comparable, run, season_of, with_pipeline
from tests.test_pipeline import config as chaos_config
from tests.test_season import config, run_season
from undercooked import prompts
from undercooked.cli import main as cli
from undercooked.config import REPO_ROOT, AgentSpec, Config
from undercooked.replay import DecisionRecord, Event, Header, Post, Replay
from undercooked.replay_io import load_replay
from undercooked.season import FORCED_COST_S, FORCED_MAX_TOKENS

GOAL = "Get the highest team score. You won't score highly unless you work together."
BUDGET = re.compile(r"tokens to think|thinking budget", re.I)
# ("ready" alone also means a soup that is ready; the chat tool is ready())
COMMS_WORDS = re.compile(r"\b(board|post\w*|messages?|chat\w*|read_board|ready\(\))", re.I)
# Inspect's compaction summary wrapper (inspect_ai/model/_compaction/summary.py), not a channel
INSPECT_SUMMARY_NOTE = "it is not a message from the user"


def agents(*flags: str) -> list[AgentSpec]:
    tail = "".join(f"+{f}" for f in flags)
    return [AgentSpec(model=f"kitchenfake/cook{tail}"),
            AgentSpec(model=f"kitchenfake/passer{tail}")]


@pytest.fixture
def requests(monkeypatch) -> list[fake.Request]:
    monkeypatch.setattr(fake, "REQUESTS", [])
    return fake.REQUESTS


def names_of(rep: Replay) -> tuple[str, str]:
    return rep.header.agents[0].name, rep.header.agents[1].name


def users(log, name: str) -> list[str]:
    return [m["content"] for m in log.samples[0].store["conversations"][name]
            if m["role"] == "user"]


def play_decisions(rep: Replay) -> list[DecisionRecord]:
    return [d for d in rep.select(DecisionRecord) if d.phase in ("practice", "round")]


# ------------------------------------------------------------------- forced answers
def test_forced_answer_rounds_practice_chat_debrief(tmp_path, requests):
    """Every first reply thinks until max_tokens; the one follow-up (reasoning off, small
    max_tokens) answers. Decisions cost 1 s more; chat and debrief turns are rescued too."""
    cfg = config(agents=agents("overthink"), decision={"flat_s": 2, "post_words_per_s": 10})
    log, [rep] = run_season(cfg, tmp_path)
    cook, passer = names_of(rep)

    # (a) the system prompt states the budget
    assert all("You have about 1000 tokens to think before each decision; decide quickly."
               in r.system for r in requests)

    # (b) practice and round decisions: forced, costed flat_s + 1 (+ the post cost)
    played = play_decisions(rep)
    forced = [d for d in played if d.forced]
    assert len(forced) >= 10 and {d.agent for d in forced} == {cook, passer}
    assert {d.phase for d in forced} == {"practice", "round"}
    for d in played:
        assert not (d.forced and d.cutoff)
        if d.lands_t is None:
            continue
        post = math.ceil(len(d.post.split()) / 10) if d.post else 0
        assert d.lands_t - d.t == (1 if d.cutoff else 2 + FORCED_COST_S * d.forced) + post, d
    assert any(d.forced and d.post for d in forced)          # the cook's round-start post
    for d in forced:
        assert fake.OVERTHOUGHT in d.reasoning and d.plan_sent     # plan: [] if rejected
    events = [e for e in rep.select(Event) if e.type == "forced_answer"]
    assert sorted((e.phase, e.t, e.agent) for e in events if e.phase in ("practice", "round")) \
        == sorted((d.phase, d.t, d.agent) for d in forced)

    # the follow-up: right after the overthought reply, with reasoning off and FORCED_MAX_TOKENS
    act_prompt = "Your thinking budget is used up. Call act now with your plan."
    follow = [r for r in requests if r.last_user in fake.FORCED]
    main = [r for r in requests if r.last_user not in fake.FORCED]
    assert follow and main
    for r in follow:
        assert (r.reasoning_enabled, r.reasoning_tokens, r.max_tokens) \
            == (False, None, FORCED_MAX_TOKENS), r
    for r in main:
        assert r.reasoning_enabled is None and r.reasoning_tokens == 1000, r
    assert sum(r.last_user == act_prompt for r in follow) >= len(forced)
    conv = log.samples[0].store["conversations"][cook]
    i = next(i for i, m in enumerate(conv) if m["role"] == "user" and m["content"] == act_prompt)
    assert conv[i - 1]["role"] == "assistant" and conv[i + 1]["role"] == "assistant"
    assert conv[i - 1]["content"][0]["type"] == "reasoning"   # the reply as Inspect produced it

    # (c) chat and debrief: rescued, not cut off
    chat = rep.select(DecisionRecord, "chat", 1)
    assert chat and all(d.forced and not d.cutoff for d in chat)
    assert [p.text for p in rep.select(Post, "chat", 1)] == [fake.CHAT_POST, "OK, onions coming."]
    debrief = rep.select(DecisionRecord, "debrief", 1)
    assert all(d.forced and d.note for d in debrief)
    assert any(r.last_user.endswith("Post your message and/or call ready() now, or pass.")
               for r in follow)
    assert any(r.last_user.endswith("Update your notepad with note() now, or pass.")
               for r in follow)

    # scorer: practice + round forced answers per agent; chat and debrief apart
    v = log.samples[0].scores["season_score"].value
    for i, name in enumerate((cook, passer)):
        assert v[f"forced_agent{i}"] == sum(d.agent == name for d in forced)
        assert v[f"forced_chat_agent{i}"] == sum(d.agent == name for d in chat)
        assert v[f"forced_debrief_agent{i}"] == 1
        assert v[f"cutoffs_chat_agent{i}"] == v[f"cutoffs_debrief_agent{i}"] == 0
        assert v[f"cutoffs_agent{i}"] == sum(d.agent == name and d.cutoff for d in played)


def test_follow_up_that_fails_is_a_cutoff_in_every_phase(tmp_path, requests):
    cfg = config(agents=agents("overthink", "stubborn"), practice=False, round_s=10,
                 decision={"flat_s": 2, "post_words_per_s": 10})
    log, [rep] = run_season(cfg, tmp_path)
    cook, passer = names_of(rep)
    played = play_decisions(rep)
    assert played and all(d.cutoff and not d.forced for d in played)
    assert all(d.lands_t - d.t == 1 for d in played if d.lands_t is not None)   # as before
    for phase in ("chat", "debrief"):
        recs = rep.select(DecisionRecord, phase, 1)
        assert recs and all(d.cutoff and not d.forced for d in recs)
        cuts = [e for e in rep.select(Event, phase, 1) if e.type == "cutoff"]
        assert sorted((e.t, e.agent) for e in cuts) == sorted((d.t, d.agent) for d in recs)
    # each turn made exactly one follow-up call
    turns = len(played) + len(rep.select(DecisionRecord, "chat")) + \
        len(rep.select(DecisionRecord, "debrief"))
    assert sum(r.last_user in fake.FORCED for r in requests) == turns
    assert not any(e.type == "forced_answer" for e in rep.select(Event))
    v = log.samples[0].scores["season_score"].value
    for i, name in enumerate((cook, passer)):
        assert v[f"cutoffs_chat_agent{i}"] == len(rep.select(DecisionRecord, "chat", 1, name))
        assert v[f"cutoffs_debrief_agent{i}"] == 1
        assert v[f"forced_agent{i}"] == v[f"forced_chat_agent{i}"] == 0


def test_plain_chat_pass_is_not_a_cutoff(season_full):
    """A chat reply without a tool call that stopped normally is a pass, as before."""
    log, rep = season_full
    assert not any(d.cutoff or d.forced for d in rep.select(DecisionRecord, "chat"))
    assert not any(d.cutoff or d.forced for d in rep.select(DecisionRecord, "debrief"))


@pytest.fixture(scope="module")
def season_full(tmp_path_factory):
    full, [rep] = run_season(config(), tmp_path_factory.mktemp("full"))
    return full, rep


def test_full_condition_header_and_metadata(season_full):
    log, rep = season_full
    h = rep.header
    assert (h.reasoning, h.comms, h.debrief) == ("on", "full", "full")
    assert log.samples[0].metadata["condition"] == {"reasoning": "on", "comms": "full",
                                                     "debrief": "full"}
    v = log.samples[0].scores["season_score"].value
    assert v["forced_agent0"] == v["forced_agent1"] == 0


# ------------------------------------------------------------------- reasoning off
def test_reasoning_off(tmp_path, requests):
    cfg = config(agents=agents("overthink"),
                 decision={"flat_s": 0, "post_words_per_s": 10, "reasoning": "off"})
    log, [rep] = run_season(cfg, tmp_path)
    assert requests
    for r in requests:     # every call, follow-ups included: no reasoning params but "disabled"
        assert r.reasoning_enabled is False and r.reasoning_tokens is None, r
        assert not BUDGET.search(r.system) and not BUDGET.search(r.last_user), r
    assert {r.max_tokens for r in requests} == {4000, FORCED_MAX_TOKENS}
    assert any(r.last_user == "Your reply did not call act. Call act now with your plan."
               for r in requests)
    assert any(d.forced for d in play_decisions(rep))
    assert rep.header.reasoning == "off"
    assert log.samples[0].metadata["condition"]["reasoning"] == "off"


def test_reasoning_off_openrouter_request():
    """Inspect's OpenRouter provider: reasoning off sends {"enabled": false} and no budget."""
    from undercooked.providers import NO_REASONING, generate_config, provider_options

    from inspect_ai.model import get_model

    cfg = config(decision={"reasoning": "off"})
    model = get_model("openrouter/google/gemma-4-31b-it", config=generate_config(cfg),
                      provider=provider_options("deepinfra/fp8"), api_key="unused",
                      memoize=False, **NO_REASONING)
    params = model.api.completion_params(model.config, tools=True)
    assert params["extra_body"]["reasoning"] == {"enabled": False}
    assert params["max_tokens"] == 4000
    on = get_model("openrouter/google/gemma-4-31b-it", config=generate_config(config()),
                   provider=provider_options("deepinfra/fp8"), api_key="unused", memoize=False)
    assert on.api.completion_params(on.config, tools=True)["extra_body"]["reasoning"] \
        == {"max_tokens": 1000}


# ------------------------------------------------------------------- comms conditions
def all_text(requests: list[fake.Request]) -> list[str]:
    return [t for r in requests for t in (r.system, r.last_user, r.tool_text)]


def test_comms_none(tmp_path, requests):
    cfg = config(comms="none", rounds=2, round_s=20, compact_at_tokens=2400)
    log, [rep] = run_season(cfg, tmp_path)
    cook, passer = names_of(rep)
    assert rep.phases() == [("briefing", 0), ("practice", 0), ("round", 1), ("debrief", 1),
                            ("round", 2), ("debrief", 2)]
    assert not rep.select(DecisionRecord, "chat") and not rep.select(Post)
    assert not any(e.phase == "chat" for e in rep.select(Event))
    assert all(set(r.tools) <= {"act", "note"} for r in requests)
    assert any(e.type == "compaction" for e in rep.select(Event))
    # no prompt offers communication (the teammate and the private notepad may be named)
    for name in (cook, passer):
        texts = [*all_text(requests), *users(log, name)]
        for t in texts:
            t = t.replace(INSPECT_SUMMARY_NOTE, "")
            assert not COMMS_WORDS.search(t), COMMS_WORDS.search(t).group(0) + " in: " + t
    system = requests[0].system
    assert GOAL in system and "YOUR NOTEPAD" in system
    assert (rep.header.comms, log.samples[0].metadata["condition"]["comms"]) == ("none", "none")
    assert rep.frames("round", 2)[-1].t == 20
    assert all(d.note for d in rep.select(DecisionRecord, "debrief"))


def test_comms_planning_only(tmp_path, requests):
    cfg = config(comms="planning_only")
    log, [rep] = run_season(cfg, tmp_path)
    cook, passer = names_of(rep)
    assert [d.agent for d in rep.select(DecisionRecord, "chat", 1)] == [cook, passer, cook]
    assert [p.phase for p in rep.select(Post)] == ["chat", "chat"]
    rounds = [r for r in requests if "act" in r.tools]
    assert rounds and all(set(r.tools) == {"act", "note"} for r in rounds)
    chats = [r for r in requests if "ready" in r.tools]
    assert chats and all(set(r.tools) == {"post", "ready"} for r in chats)
    states = [u for u in users(log, passer) if re.search(r"^Time \d+ /", u, re.M)]
    assert states and not any("unread" in s or re.search(r"^Board:", s, re.M) for s in states)
    system = requests[0].system
    assert prompts.PLANNING_ONLY_RULE in system and GOAL in system
    assert "read_board" not in system and "words you post" not in system
    assert "In the chat before a round you take turns." in system
    assert rep.header.comms == "planning_only"


@pytest.mark.parametrize("comms", ["full", "planning_only", "none"])
@pytest.mark.parametrize("reasoning", ["on", "off"])
def test_goal_sentence_in_every_condition(comms, reasoning):
    from undercooked.prompts import system_prompt

    cfg = config(comms=comms, decision={"reasoning": reasoning})
    text = system_prompt(cfg, "Kilo", "Tango")
    assert text.count(GOAL) == 1 and text.rstrip().endswith(GOAL)
    assert bool(BUDGET.search(text)) == (reasoning == "on")


# ------------------------------------------------------------------- debrief score_only
def score_lines(cfg: Config, rep: Replay) -> list[str]:
    ends = [e for e in rep.select(Event, "round") if e.type == "phase_end"]
    return [prompts.round_result(cfg, e.round, e.data["score"], e.data["served"],
                                 e.data["missed"]) for e in ends]


@pytest.mark.parametrize("comms", ["full", "none"])
def test_debrief_score_only(tmp_path, requests, comms):
    cfg = config(debrief="score_only", comms=comms, rounds=2, round_s=15)
    log, [rep] = run_season(cfg, tmp_path)
    assert not any(r.tools == ["note"] for r in requests)          # no debrief calls
    assert not rep.select(DecisionRecord, "debrief")
    assert not any(e.phase == "debrief" for e in rep.select(Event))
    lines = score_lines(cfg, rep)
    assert len(lines) == 2
    for name in names_of(rep):
        us = users(log, name)
        for line in lines:
            assert sum(u.count(line) for u in us) == 1, (name, line)
        # round 1's result opens the next message (chat turn, or round 2's first decision)
        nxt = next(u for u in us if lines[0] in u)
        assert nxt.startswith(lines[0])
        assert ("Chat before round 2" in nxt) == (comms == "full")
        assert ("Round 2 of 2 starts now." in nxt) == (comms == "none")
        assert us[-1] == lines[1]                     # season end: in the conversation, no call
    system = requests[0].system
    assert "after each round you are told the round's score" in system.lower()
    assert "debrief" not in system
    assert rep.header.debrief == "score_only"
    assert log.samples[0].metadata["condition"]["debrief"] == "score_only"


# ------------------------------------------------------------------- pipeline equivalence
EQUIV = [  # condition overrides, agents' flags
    ({"comms": "planning_only"}, ("chaos",)),
    ({"comms": "none"}, ("chaos",)),
    ({"decision": {"flat_s": 2, "post_words_per_s": 10, "reasoning": "off"}}, ("chaos",)),
    ({"debrief": "score_only", "comms": "none"}, ("chaos",)),
    ({}, ("chaos", "rescue")),                                       # forced answers
    ({"decision": {"flat_s": 1, "post_words_per_s": 10}}, ("chaos", "rescue")),
]


@pytest.mark.parametrize("over, flags", EQUIV)
def test_pipeline_matches_sequential_in_conditions(tmp_path, monkeypatch, over, flags):
    monkeypatch.setattr(fake, "DELAY", (21, 0.0, 0.03))
    cfg = chaos_config(rounds=2, round_s=20, practice_s=12, agents=agents(*flags), **over)
    out = {}
    for on in (False, True):
        log, _ = run(with_pipeline(cfg, on), tmp_path / str(on))
        [rep] = load_replay(log.location)
        out[on] = (comparable(rep), season_of(log, 1))
    assert out[True] == out[False]
    rep = Replay.model_validate(out[False][0])
    decisions = play_decisions(rep)
    if "rescue" in flags:
        assert any(d.forced for d in decisions) and any(d.cutoff for d in decisions)
    if over.get("comms") in ("planning_only", "none"):
        assert not any(d.post for d in decisions)


# ------------------------------------------------------------------- CLI and configs
def test_cli_condition_flags(tmp_path):
    cfg = config(practice=False, round_s=4, rounds=1)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    res = CliRunner().invoke(cli, ["run", str(path), "--epochs", "1", "--log-dir",
                                   str(tmp_path / "logs"), "--reasoning", "off", "--comms",
                                   "none", "--debrief", "score_only"])
    assert res.exit_code == 0, res.output
    log_path = res.output.strip().splitlines()[-1]
    [rep] = load_replay(log_path)
    assert (rep.header.reasoning, rep.header.comms, rep.header.debrief) \
        == ("off", "none", "score_only")
    meta = read_eval_log(log_path, header_only=True).eval.metadata["config"]
    assert meta["decision"]["reasoning"] == "off" and meta["comms"] == "none"
    bad = CliRunner().invoke(cli, ["run", str(path), "--comms", "some"])
    assert bad.exit_code != 0


@pytest.mark.parametrize("name, changed", [
    ("split_room_noreason", {"decision": {"reasoning": "off"}}),
    ("split_room_planning_only", {"comms": "planning_only"}),
    ("split_room_nocomms", {"comms": "none"}),
    ("split_room_debrief_scoreonly", {"debrief": "score_only"}),
])
def test_ready_made_configs(name, changed):
    base = Config.load(REPO_ROOT / "configs" / "split_room.yaml").model_dump()
    cfg = Config.load(REPO_ROOT / "configs" / f"{name}.yaml").model_dump()
    assert cfg["name"] == name

    def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        return {**a, **{k: merge(a[k], v) if isinstance(v, dict) else v for k, v in b.items()}}

    assert cfg == merge(base, {"name": name, **changed})


def test_header_defaults_read_old_logs():
    old = {"kind": "header", "config_name": "x", "level": "split_room", "map": [], "agents": [],
           "seed": 0, "round_s": 10, "rounds": 1}
    h = Header.model_validate(old)
    assert (h.reasoning, h.comms, h.debrief) == ("on", "full", "full")
    assert not DecisionRecord(phase="round", round=1, t=0, agent="Kilo").forced
