"""Render every agent-facing text over a grid of inputs.

`render_all()` is what tests/test_prompt_files.py compares with tests/fixtures/prompts_golden.json.
After an INTENTIONAL prompt edit, regenerate the golden file:

    uv run python -m tests.make_prompt_golden            # writes tests/fixtures/prompts_golden.json
    uv run python -m tests.make_prompt_golden OUT.json   # or anywhere else
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from undercooked import prompts
from undercooked.board import Board
from undercooked.config import AgentSpec, Config
from undercooked.engine import Game, Kitchen, rules_text
from undercooked.providers import agent_model
from undercooked.replay import Post
from undercooked.season import Season
from undercooked.tools import (Turn, act_tool, finish_practice_tool, note_tool, post_tool,
                               read_board_tool, ready_tool)

GOLDEN = Path(__file__).parent / "fixtures" / "prompts_golden.json"


def cfg(**kw: Any) -> Config:
    base: dict[str, Any] = dict(name="t", level="forced_coordination", rounds=3, round_s=180, practice=True,
                                agents=[AgentSpec(model="mockllm/model")] * 2)
    return Config.model_validate({**base, **kw})


def _grid() -> list[tuple[str, Config]]:
    out = []
    for comms, reasoning, debrief, practice in itertools.product(
            ("full", "planning_only", "none"), ("on", "off"), ("full", "score_only"),
            (True, False)):
        out.append((f"{comms}|{reasoning}|{debrief}|practice={practice}",
                    cfg(comms=comms, debrief=debrief, practice=practice,
                        decision={"reasoning": reasoning})))
    out.append(("push", cfg(board="push")))
    out.append(("one_round_one_post", cfg(rounds=1, chat_max_posts=1, practice_s=30,
                                          decision={"flat_s": 1, "reasoning_tokens": 500})))
    out.append(("odd_numbers", cfg(rounds=2, chat_max_posts=2, cook_s=15, max_plan_len=3,
                                   orders={"every_s": 20, "due_s": 50, "max_open": 2,
                                           "reward": 30, "penalty": 5})))
    return out


def _call(tool, *a, **kw) -> str:
    return asyncio.run(tool.tool(*a, **kw))


def _desc(d) -> list:
    return [d.name, d.description, d.parameters.model_dump_json(exclude_none=True)]


def _tool_texts() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for max_len in (5, 3):
        t = Turn()
        d = act_tool(t, lambda p: None, max_len)
        out[f"act.desc.{max_len}"] = _desc(d)
        out[f"act.accepted.{max_len}"] = _call(d, ["interact(pot_1)"])
        out[f"act.ignored.{max_len}"] = _call(d, ["wait(1)"])
        t = Turn()
        d = act_tool(t, lambda p: None, max_len)
        out[f"act.extra.{max_len}"] = _call(d, ["wait(1)"] * (max_len + 2))
        t = Turn()
        d = act_tool(t, lambda p: "bad action 'x'", max_len)
        out[f"act.rejected.{max_len}"] = _call(d, ["x"])
    for lands_later in (True, False):
        k = f"post.{lands_later}"
        t = Turn()
        p = post_tool(t, lands_later=lands_later)
        out[k + ".desc"] = _desc(p)
        out[k + ".empty"] = _call(p, "   ")
        out[k + ".ok"] = _call(p, "hello  there")
        out[k + ".ignored"] = _call(p, "again")
        t = Turn()
        p = post_tool(t, lands_later=lands_later)
        out[k + ".cut"] = _call(p, "x" * 500)
        t = Turn()
        p = post_tool(t, cap_error=lambda: "you have used all 2 posts for this chat",
                      lands_later=lands_later)
        out[k + ".cap"] = _call(p, "hi")
    t = Turn()
    r = read_board_tool(t, lambda: "BOARD")
    out["read_board"] = [*_desc(r), _call(r)]
    n = note_tool(t)
    out["note"] = [*_desc(n), _call(n, "x")]
    y = ready_tool(t)
    out["ready"] = [*_desc(y), _call(y)]
    f = finish_practice_tool(t)
    out["finish_practice"] = [*_desc(f), _call(f)]
    return out


def _state_texts() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for board_mode, comms, phase in (("pull", "full", "round"), ("push", "full", "round"),
                                     ("pull", "none", "round"), ("pull", "planning_only", "round"),
                                     ("pull", "full", "practice")):
        c = cfg(board=board_mode, comms=comms)
        s = Season(c, [agent_model(AgentSpec(model="mockllm/model"), c)] * 2, 1)
        a = s.agents[0]
        a.notepad = "np"
        kitchen = Kitchen.load("practice" if phase == "practice" else c.level)
        g = Game(kitchen, [x.name for x in s.agents] if phase == "round" else [a.name], c,
                 phase=phase, round=0 if phase == "practice" else 2)
        for _ in range(3):
            g.tick()
        board = Board([x.name for x in s.agents])
        for i in range(2):
            board.add(Post(phase="round", round=g.round, t=i, agent=s.agents[1].name,
                           text=("first", "second")[i]))
        for label, b in (("none", None), ("posts", board), ("read", board)):
            if label == "read":
                board.read_all(a.name, g.round)
            out[f"{board_mode}|{comms}|{phase}|{label}"] = s.state_text(g, a, b)
        a.notepad = None
        out[f"{board_mode}|{comms}|{phase}|nonotepad"] = s.state_text(g, a, None)
    c = cfg()
    g = Game(Kitchen.load("forced_coordination"), ["Kilo", "Tango"], c, phase="round", round=1)
    for n in (0, 1, 2):
        out[f"engine.unread{n}"] = g.state_text("Kilo", unread_posts=n, notepad="n",
                                                last_plan_result="failed: blocked by Tango.")
    return out


def render_all() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, c in _grid():
        o = out[label] = {}
        o["system"] = prompts.system_prompt(c, "Kilo", "Tango")
        o["system_swapped"] = prompts.system_prompt(c, "Tango", "Kilo")
        o["season_intro"] = prompts.season_intro(c)
        o["practice_start"] = prompts.practice_start(c)
        o["round_start"] = prompts.round_start(c, 2)
        o["round_start_limit"] = prompts.round_start(c, 3, chat_turn_limit=True)
        o["orders_rules"] = prompts.orders_rules(c)
        o["rules_text"] = rules_text(c)
        o["thinking_budget"] = prompts.thinking_budget(c)
        o["compaction"] = prompts.compaction_prompt(c)
        o["compaction_filled"] = prompts.compaction_prompt(c).format(addendums="\n\nEXTRA")
        o["chat_turns_each"] = prompts.chat_turns_each(c)
        o["forced"] = {k: prompts.forced_prompt(c, k) for k in ("act", "chat", "debrief")}
        o["round_result"] = [prompts.round_result(c, 1, 20, 1, 0),
                             prompts.round_result(c, 2, -10, 2, 3),
                             prompts.round_result(c, 3, 0, 0, 0)]
        o["debrief"] = [prompts.debrief(c, 1, 20, 1, 0, [20], None),
                        prompts.debrief(c, 2, 30, 2, 1, [20, 30], 'my "note"'),
                        prompts.debrief(c, 3, -10, 0, 1, [20, 30, -10], "")]
        g = Game(Kitchen.load(c.level), ["Kilo", "Tango"], c)
        o["kitchen_overview"] = prompts.kitchen_overview(g, "Kilo")
        o["chat_turn"] = [
            prompts.chat_turn(c, 1, first_turn=True, overview="OVERVIEW", new_posts=None,
                              posts_left=6, ready=False, notepad=None),
            prompts.chat_turn(c, 2, first_turn=True, overview="OVERVIEW",
                              new_posts="[chat] Tango: hi", posts_left=1, ready=True,
                              notepad="my note"),
            prompts.chat_turn(c, 2, first_turn=False, overview="OVERVIEW", new_posts=None,
                              posts_left=0, ready=True, notepad="ignored"),
            prompts.chat_turn(c, 3, first_turn=False, overview="OVERVIEW",
                              new_posts="[chat] a: 1\n[chat] b: 2", posts_left=3, ready=False,
                              notepad=None),
        ]
    out["cutoff_notice"] = {str(n): prompts.cutoff_notice(n) for n in (1, 2, 4, 11)}
    out["forced_notice"] = {str(n): prompts.forced_notice(n) for n in (1, 2)}
    out["unseen_chat"] = prompts.unseen_chat("[chat] Tango: hi\n[chat] Tango: go")
    out["constants"] = {"GOAL": prompts.GOAL, "COMPACTION_PROMPT": prompts.COMPACTION_PROMPT,
                        "PLANNING_ONLY_RULE": prompts.PLANNING_ONLY_RULE,
                        "FORCED_ASKS": prompts.FORCED_ASKS}
    out["tools"] = _tool_texts()
    out["state_text"] = _state_texts()
    return out


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else GOLDEN
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(render_all(), indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
