"""Fixes from the adversarial review: the planning_only last chat post (D1), act's plan shapes
through Inspect's validation (D2b), what counts as a cut-off chat/debrief reply (D2a), forced vs
cutoff after a follow-up that passes (D3), and the forced-answer notice."""

from __future__ import annotations

import asyncio
import itertools
import json

import pytest
from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessageAssistant,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    execute_tools,
    modelapi,
)
from inspect_ai.model._model_call import ModelCall
from inspect_ai.tool import ToolCall

from tests import kitchen_fake as fake
from tests.test_conditions import agents, names_of, users
from tests.test_season import config, run_season
from undercooked import prompts
from undercooked.config import AgentSpec
from undercooked.replay import DecisionRecord, Post
from undercooked.season import FORCED_COST_S, cut_off, first_speaker
from undercooked.tools import Turn, act_tool

# --------------------------------------------------------------- D1: the last chat post
_ids = itertools.count()
LAST = "B here: I take the onions, you plate."
FIRST = "A here: I cook, you pass."


def _out(content: str = "", calls=()) -> ModelOutput:
    tcs = [ToolCall(id=f"lp{next(_ids)}", function=f, arguments=a) for f, a in calls]
    return ModelOutput(model="lastpost", choices=[ChatCompletionChoice(
        message=ChatMessageAssistant(content=content, tool_calls=tcs or None),
        stop_reason="tool_calls" if tcs else "stop")])


@modelapi(name="lastpost")
class LastPost(ModelAPI):
    """From tests of review2/p_lastpost2.py: agent "a" (first speaker) posts and says ready in
    its first chat turn; "b" then posts LAST and says ready, which ends the chat."""

    def __init__(self, model_name, base_url=None, api_key=None, config=GenerateConfig(), **kw):
        super().__init__(model_name, base_url, api_key, [], config)

    async def count_text_tokens(self, text: str) -> int:
        return len(text) // 4 + 1

    async def generate(self, input, tools, tool_choice, config):
        names = [t.name for t in tools]
        if not names:
            out = _out("summary")
        elif "ready" in names:
            msg = FIRST if self.model_name == "a" else LAST
            out = _out("", [("post", {"message": msg}), ("ready", {})])
        elif "act" in names:
            out = _out("", [("act", {"plan": ["wait(4)"]})])
        else:
            out = _out("ok")
        return out, ModelCall.create({}, {})


@pytest.mark.parametrize("comms, board", [("planning_only", "pull"), ("planning_only", "push"),
                                          ("full", "pull"), ("full", "push")])
def test_last_chat_post_reaches_the_first_speaker(tmp_path, comms, board):
    seed = next(s for s in range(100) if first_speaker(s, 1, 1) == 0)
    cfg = config(rounds=1, round_s=10, practice=False, chat_max_posts=2, seed=seed, board=board,
                 comms=comms, agents=[AgentSpec(model="lastpost/a"), AgentSpec(model="lastpost/b")],
                 decision={"flat_s": 2, "post_words_per_s": 10})
    log, [rep] = run_season(cfg, tmp_path)
    a, b = names_of(rep)
    assert [(p.agent, p.text) for p in rep.select(Post, "chat", 1)] == [(a, FIRST), (b, LAST)]
    conv = log.samples[0].store["conversations"][a]
    seen = [m for m in conv if m["role"] in ("user", "tool") and LAST in str(m["content"])]
    if comms == "planning_only" or board == "push":
        # the post itself, in a's first round message
        assert len(seen) == 1 and seen[0]["role"] == "user"
        assert seen[0]["content"].startswith("Round 1 of 1 starts now.")
    else:
        # board pull: a's state says there is an unread post; read_board() would show it
        assert any("Board: 1 unread post." in u for u in users(log, a))
    if comms == "planning_only":
        assert prompts.unseen_chat(f"[chat] {b}: {LAST}") in seen[0]["content"]
        # b wrote it and saw a's post in its chat turn: nothing unseen to repeat
        assert not any("Chat posts you had not seen yet" in u for u in users(log, b))


# --------------------------------------------------------------- D2b: act's plan shapes
@pytest.mark.parametrize("plan, expect", [
    (["interact(pot_1)", "wait(2)"], ["interact(pot_1)", "wait(2)"]),
    ("interact(pot_1)", ["interact(pot_1)"]),
    (json.dumps(["interact(pot_1)", "wait(2)"]), ["interact(pot_1)", "wait(2)"]),
    ("interact(pot_1); wait(2)", ["interact(pot_1); wait(2)"]),     # the engine splits it
])
def test_act_accepts_a_list_or_a_string_through_inspect(plan, expect):
    """Inspect validates tool arguments against the JSON schema before the tool runs; a string
    plan used to fail there with "is not of type 'array'"."""
    turn = Turn()
    tool = act_tool(turn, lambda p: None, 5)
    msg = ChatMessageAssistant(content="", tool_calls=[
        ToolCall(id="c1", function="act", arguments={"plan": plan})])
    result = asyncio.run(execute_tools([msg], [tool]))
    [tool_msg] = result.messages
    assert tool_msg.error is None, tool_msg.error
    assert tool_msg.text.startswith("Plan accepted.")
    assert turn.plan == expect


def test_act_schema_is_list_or_string():
    params = act_tool(Turn(), lambda p: None, 5).parameters.model_dump(exclude_none=True)
    assert params["properties"]["plan"]["anyOf"] == [
        {"type": "array", "items": {"type": "string"}}, {"type": "string"}]


# --------------------------------------------------------------- D2a: cut-off replies
@pytest.mark.parametrize("stop, calls, cut", [
    ("stop", False, False),                 # a deliberate pass
    ("tool_calls", True, False),
    ("max_tokens", False, True),
    ("model_length", False, True),
    ("unknown", False, True),               # the case the review found
    ("content_filter", False, True),
    ("max_tokens", True, False),            # it did call a tool
])
def test_cut_off(stop, calls, cut):
    tcs = [ToolCall(id="x", function="ready", arguments={})] if calls else None
    out = ModelOutput(model="m", choices=[ChatCompletionChoice(
        message=ChatMessageAssistant(content="", tool_calls=tcs), stop_reason=stop)])
    assert cut_off(out) is cut


# --------------------------------------------------------------- D3: a follow-up that passes
def test_follow_up_that_passes_is_a_cutoff_not_forced(tmp_path):
    cfg = config(agents=agents("overthink", "pass"), practice=False, round_s=10,
                 decision={"flat_s": 2, "post_words_per_s": 10})
    log, [rep] = run_season(cfg, tmp_path)
    for phase in ("chat", "debrief"):
        recs = rep.select(DecisionRecord, phase, 1)
        assert recs and all(d.cutoff and not d.forced for d in recs), phase
    v = log.samples[0].scores["season_score"].value
    for i, name in enumerate(names_of(rep)):
        assert v[f"forced_chat_agent{i}"] == v[f"forced_debrief_agent{i}"] == 0
        assert v[f"cutoffs_chat_agent{i}"] == len(rep.select(DecisionRecord, "chat", 1, name))
        assert v[f"cutoffs_debrief_agent{i}"] == 1
    assert not any(d.forced for d in rep.select(DecisionRecord))


# --------------------------------------------------------------- the forced-answer notice
def test_forced_answer_cost_is_told(tmp_path):
    cfg = config(agents=agents("overthink"), practice=False, round_s=12,
                 decision={"flat_s": 2, "post_words_per_s": 10})
    log, [rep] = run_season(cfg, tmp_path)
    notice = prompts.forced_notice(FORCED_COST_S)
    assert notice == "Your last decision only called act(plan) after a reminder, so it took 1 s longer."
    for name in names_of(rep):
        played = sorted(rep.select(DecisionRecord, "round", 1, name), key=lambda d: d.t)
        forced_before = sum(d.forced for d in played[:-1])     # each is told at the next decision
        assert forced_before > 0
        assert sum(u.startswith(notice) or f"\n{notice}" in u
                   for u in users(log, name)) == forced_before
