"""Render every prompt one agent sees in a short fake season, labelled with its source.

    uv run python -m analysis.render_prompts                 # writes prompts/EXAMPLE.md
    uv run python -m analysis.render_prompts --plain         # same, no markers (EXAMPLE.plain.md)
    uv run python -m analysis.render_prompts --out FILE

How it works: it runs one real (but offline) season through Inspect with the scripted fake models
of tests/kitchen_fake.py, while the prompt loader (`promptfiles.load`) and a few code-made texts
(map drawing, plan results, board posts) are wrapped so that every piece of agent-facing text
carries a marker naming its source. Nothing in the library is changed: the wrappers exist only
inside `labelled()`. The fake model strips the markers before it reads a message, so it behaves as
it would without them.

Markers: `⟦system.md › kitchen_rules⟧ text ⟦/⟧` is the section `kitchen_rules` of
prompts/system.md after its placeholders were filled; `⟦code: engine/text.py › map_lines⟧` is text
drawn by code. Markers nest (a section's placeholder may hold other sections). Any text with no
marker comes from code that is not wrapped (for example engine parse errors, board post wrapper
text typed by an agent, or the join words between pieces).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tempfile
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelAPI

from tests import kitchen_fake as fake  # registers the kitchenfake/... models (offline)
from undercooked import board as board_mod
from undercooked import prompts, promptfiles
from undercooked.config import REPO_ROOT, AgentSpec, Config
from undercooked.engine import text as engine_text
from undercooked.engine.game import Game
from undercooked.season import assign_seats, first_speaker, pick_names
from undercooked.task import make_task

PROMPT_FILES = ("system", "communication", "messages", "state", "tools")
OUT_MARKED = REPO_ROOT / "prompts" / "EXAMPLE.md"
OUT_PLAIN = REPO_ROOT / "prompts" / "EXAMPLE.plain.md"
KEEP_PER_PHASE = 2          # decisions shown in full per phase; later look-alikes are summarised
SHOWN = 0                   # agents[0] is the agent whose conversation is shown

OPEN, CLOSE, END = "⟦", "⟧", "⟦/⟧"
MARK_RE = re.compile(r"⟦(?!/⟧)[^⟧]*⟧|⟦/⟧")
LABEL_RE = re.compile(r"⟦(?!/⟧)([^⟧]*)⟧")


def strip_marks(text: str) -> str:
    return MARK_RE.sub("", text)


def mark(label: str, text: str) -> str:
    return f"{OPEN}{label}{CLOSE}{text}{END}" if text else text


# ---------------------------------------------------------------------------- the config
def example_config() -> Config:
    """Practice on, 2 rounds, full comms and debrief, a low compaction threshold."""
    seed = next(s for s in range(1000)
                if assign_seats(s, 1) == [1, 2] and first_speaker(s, 1, 1) == 0)
    return Config.model_validate(dict(
        name="prompt_example", level="split_room", rounds=2, round_s=60, practice=True,
        practice_s=15, chat_max_posts=2, epochs=1, cook_s=3, seed=seed, comms="full",
        orders={"every_s": 50, "due_s": 45},
        debrief="full", board="pull", compact_at_tokens=COMPACT_AT,
        agents=[AgentSpec(model="kitchenfake/cook"), AgentSpec(model="kitchenfake/passer")]))


COMPACT_AT = 4000


# ----------------------------------------------------------------- labelling the sources
class Recorder:
    """What the wrappers saw during the season."""

    def __init__(self) -> None:
        self.used: Counter[tuple[str, str | None]] = Counter()
        self.tool_sets: list[dict[str, Any]] = []      # tools offered to the shown agent
        self.compaction_prompts: list[str] = []        # summarising prompts sent to it
        self.shown_name: str = ""


def _clean_message(m: Any) -> Any:
    """A copy of a chat message with the markers removed (what the fake model reads)."""
    if isinstance(m.content, str):
        return m.model_copy(update={"content": strip_marks(m.content)})
    parts = [p.model_copy(update={"text": strip_marks(p.text)}) if hasattr(p, "text") else p
             for p in m.content]
    return m.model_copy(update={"content": parts})


def _scripted(rec: Recorder, orig_script: Any) -> Any:
    """The fake models' script, with markers stripped from its input and a few one-off
    behaviours added so that the rarer prompts occur: for the shown agent a cutoff with a post, a
    rejected plan, forced answers, a chat that hits its turn cap, a refused post and a batch of
    odd tool calls; for its teammate a board post and a first chat turn passed."""
    done: set[str] = set()
    forcing: set[str] = set()
    chat2_turns = 0
    replaying: list[bool] = []
    mate_chat2 = 0

    def script(role: str, messages: list, tools: list[str], flags: tuple[str, ...] = ()) -> Any:
        nonlocal chat2_turns, mate_chat2
        clean = [_clean_message(m) for m in messages]
        if replaying or not tools or fake.agent_name(clean) is None:
            return orig_script(role, clean, tools, flags)
        mine = fake.agent_name(clean) == rec.shown_name
        text = fake.last_user(clean)
        first = text.split("\n")[0]
        if text in fake.FORCED:
            if forcing and mine:            # answer the forced request as the plain script would
                forcing.clear()
                replaying.append(True)      # the follow-up re-runs the plain script: no overrides
                try:
                    return fake.follow_up(role, clean, tools, ("overthink",))
                finally:
                    replaying.clear()
            return orig_script(role, clean, tools, flags)
        kind = ("chat" if "ready" in tools else "debrief" if tools == ["note"] else
                "round" if "act" in tools and "Practice" not in text else "practice")

        def once(key: str) -> bool:
            if key in done:
                return False
            done.add(key)
            return True

        if clean[-1].role == "tool":
            if mine and "cutpost" in done and once("cutpost2"):
                return fake.reply("Nothing more to do this second.")     # a post, but no act()
            return orig_script(role, clean, tools, flags)
        if mine:
            if kind == "chat" and "Chat before round 1" in text:
                return fake.reply(calls=[("post", {"message": fake.CHAT_POST}), ("ready", {})])
            if kind == "chat" and "round 2" in text.lower():
                chat2_turns += 1
                if chat2_turns == 1:
                    forcing.add("chat2")
                    return fake.overthought()
                if chat2_turns <= 3:        # the 3rd post is over the cap of 2: refused
                    return fake.reply(calls=[("post", {"message": f"Still here ({chat2_turns})."})])
                return fake.reply("I pass.")
            if kind == "round" and "starts now" in first and once("round1"):
                forcing.add("round1")
                return fake.overthought()   # thought until max_tokens: a forced answer follows
            if kind == "debrief" and once("debrief1"):
                forcing.add("debrief1")
                return fake.overthought()
            if kind == "round" and "starts now" not in first:
                if "unread post" in text and once("readboard"):
                    return fake.reply(calls=[("read_board", {})])
                if "Round 1 of" in text and once("cutpost"):
                    return fake.reply(calls=[("post", {"message": "Dish rack is yours, I am "
                                                       "heading to the pots."})])
                if "Round 2 of" in text and once("oddtools"):
                    return fake.reply(calls=[
                        ("act", {"plan": ["wait(1)"] * 7}), ("act", {"plan": ["wait(1)"]}),
                        ("post", {"message": "   "}), ("post", {"message": "x" * 250}),
                        ("post", {"message": "again"})])
            if kind == "practice" and "Time 0 /" not in text and once("badplan"):
                return fake.reply(calls=[("act", {"plan": [fake.BAD_ACTION]})])
        else:
            if kind == "chat" and "Chat before round 1" in text:
                return fake.reply("Let me look at the kitchen first.")   # passes: the shown agent
            if kind == "chat" and "round 2" in text.lower():             # then gets a turn after
                mate_chat2 += 1                                          # its own ready()
                if mate_chat2 == 1:
                    return fake.reply(calls=[("post", {"message": "Same plan as last round."})])
                return fake.reply("I pass.")
            if (kind == "round" and "starts now" not in first and "Round 1 of" in text
                    and once("matepost")):
                out = orig_script(role, clean, tools, flags)
                calls = [("post", {"message": "Onions are coming over the counter."})]
                calls += [(c.function, c.arguments) for c in out.message.tool_calls or []]
                return fake.reply(calls=calls)
        return orig_script(role, clean, tools, flags)

    return script


@contextlib.contextmanager
def labelled(rec: Recorder) -> Iterator[None]:
    """Wrap the prompt loader and a few code-made texts with source markers (see the module doc)."""
    orig_load = promptfiles.load
    orig_script = fake.script
    # @modelapi replaces the name by a factory; the class itself is a ModelAPI subclass
    API = next(c for c in ModelAPI.__subclasses__() if c.__name__ == "KitchenFake")
    orig_generate = API.generate
    orig_count = API.count_text_tokens
    orig_map = engine_text.map_lines
    orig_last = Game.last_plan_result
    orig_post = board_mod.format_post
    orig_forced = dict(prompts.FORCED_ASKS)

    def load(name: str, section: str | None = None, /, **values: object) -> str:
        text = orig_load(name, section, **values)
        rec.used[(name, section)] += 1
        label = f"{name}.md › {section}" if section else f"{name}.md"
        return mark(label, text)

    def map_lines(game: Game) -> list[str]:
        lines = orig_map(game)
        lines[0] = OPEN + "code: engine/text.py › map_lines" + CLOSE + lines[0]
        lines[-1] += END
        return lines

    def last_plan_result(self: Game, agent: str) -> str:
        text = orig_last(self, agent).strip().rstrip(".")
        return mark("code: engine/game.py › last_plan_result", text)

    def format_post(p: Any) -> str:
        return mark("code: board.py › format_post", orig_post(p))

    async def generate(self: Any, input: list, tools: list, tool_choice: Any, config: Any) -> Any:
        clean = [_clean_message(m) for m in input]
        who = fake.agent_name(clean)
        if who == rec.shown_name:
            if not tools:
                rec.compaction_prompts.append(next(m.text for m in reversed(input)
                                                   if m.role == "user"))
            else:
                rec.tool_sets.append({
                    "after": next(m.text for m in reversed(input) if m.role == "user"),
                    "tools": [{"name": t.name, "description": t.description,
                               "params": {k: v.description for k, v in
                                          (t.parameters.properties or {}).items()}}
                              for t in tools]})
        return await orig_generate(self, input, tools, tool_choice, config)

    async def count_text_tokens(self: Any, text: str) -> int:
        return await orig_count(self, strip_marks(text))   # token counts as if unmarked

    targets = [m for m in list(sys.modules.values())
               if getattr(m, "load", None) is orig_load and m is not promptfiles]
    for m in targets:
        m.load = load
    promptfiles.load = load
    prompts.FORCED_ASKS = {k: load("messages", f"forced.{k}") for k in orig_forced}
    engine_text.map_lines = map_lines
    prompts.map_lines = map_lines
    Game.last_plan_result = last_plan_result
    board_mod.format_post = format_post
    fake.script = _scripted(rec, orig_script)
    API.generate = generate
    API.count_text_tokens = count_text_tokens
    try:
        yield
    finally:
        for m in targets:
            m.load = orig_load
        promptfiles.load = orig_load
        prompts.FORCED_ASKS = orig_forced
        engine_text.map_lines = orig_map
        prompts.map_lines = orig_map
        Game.last_plan_result = orig_last
        board_mod.format_post = orig_post
        fake.script = orig_script
        API.generate = orig_generate
        API.count_text_tokens = orig_count


# ------------------------------------------------------------------------ the season
def run_season(cfg: Config, *, labels: bool) -> tuple[list[dict[str, Any]], Recorder]:
    """The shown agent's conversation (store["conversations"]) and what the wrappers recorded."""
    rec = Recorder()
    rec.shown_name = pick_names(cfg.seed, 1)[SHOWN]
    with tempfile.TemporaryDirectory() as tmp:
        with labelled(rec) if labels else contextlib.nullcontext():
            [log] = inspect_eval(make_task(cfg), model="none/none", log_dir=tmp, display="none")
        assert log.status == "success", log.error
        full = read_eval_log(log.location, resolve_attachments=True)
    conv = full.samples[0].store["conversations"][rec.shown_name]
    return conv, rec


# ----------------------------------------------------------------------- the rendering
def labels_in(text: str) -> list[str]:
    return LABEL_RE.findall(text)


def content_text(msg: dict[str, Any]) -> tuple[str, list[str]]:
    """(visible text, reasoning blocks) of a message dict."""
    c = msg.get("content", "")
    if isinstance(c, str):
        return c, []
    text, reasoning = [], []
    for part in c:
        if part.get("type") == "reasoning":
            reasoning.append(part.get("reasoning") or part.get("summary") or "")
        elif part.get("type") == "text":
            text.append(part["text"])
    return "\n".join(text), reasoning


# what each kind of message is, and when it is sent (one line under its heading)
WHEN = {
    "system prompt": "Once per agent, first message of the conversation. Always kept in front of "
                     "the model, also after a compaction.",
    "season intro": "Once, right after the system prompt (variant by practice / chat).",
    "practice start": "First decision of practice (the state follows the opening sentence).",
    "practice decision": "Every decision in practice: the state at that second.",
    "round start": "First decision of each round (the state follows the opening sentence).",
    "round decision": "Every later decision in a round: the state at that second.",
    "chat first": "First turn of the pre-round chat: the kitchen overview and the orders.",
    "chat later": "Later turns of the same chat.",
    "debrief": "After each round (debrief: full): the result, scores, notepad and a request.",
    "forced": "Forced-answer follow-up: sent when the turn ended without its required answer "
              "(no act(), or a chat/debrief reply cut off).",
    "compaction summary": "Inserted by Inspect when the conversation passes compact_at_tokens; "
                          "the model then sees the summary in place of older messages.",
    "other": "",
}


def classify(msg: dict[str, Any]) -> tuple[str, str]:
    """(kind, heading text) of a user message from the sections it contains."""
    text, _ = content_text(msg)
    plain = strip_marks(text)
    labs = labels_in(text)
    n = re.search(r"[Rr]ound (\d+) of", plain)
    rnd = n.group(1) if n else "?"
    t = re.search(r"Time (\d+) /", plain)
    when = f" (t={t.group(1)})" if t else ""
    if (msg.get("metadata") or {}).get("summary"):
        return "compaction summary", "compaction summary"
    has = lambda s: any(s in lab for lab in labs)       # noqa: E731
    if has("› forced."):
        return "forced", "forced-answer request"
    if has("› season_intro."):
        return "season intro", "season intro"
    if has("› debrief.result"):
        return "debrief", f"debrief, round {rnd}"
    if has("› chat_turn.first"):
        return "chat first", f"chat before round {rnd}, first turn"
    if has("› chat_turn.later"):
        r = re.search(r"round (\d+)", plain)
        return "chat later", f"chat before round {r.group(1) if r else '?'}, later turn"
    cut = ", after a cutoff" if has("› cutoff_notice.notice") else ""
    if has("› practice_start."):
        return "practice start", "practice start"
    if has("› round_start.base"):
        return "round start", f"round {rnd} start"
    if has("› where_practice"):
        return "practice decision", f"practice decision{when}{cut}"
    if has("› where_round"):
        return "round decision", f"round {rnd} decision{when}{cut}"
    return "other", "message"


def group_of(kind: str, heading: str) -> str:
    """The phase a message belongs to (for the H2 sections and for truncation)."""
    if kind in ("system prompt", "season intro"):
        return "Season start"
    if kind in ("practice start", "practice decision"):
        return "Practice"
    m = re.search(r"(?:round|before round|round) (\d+)", heading)
    r = m.group(1) if m else "?"
    if kind in ("chat first", "chat later"):
        return f"Round {r}: chat"
    if kind == "debrief":
        return f"Round {r}: debrief"
    if kind in ("round start", "round decision"):
        return f"Round {r}: play"
    return ""                  # e.g. the compaction summary: stays in the current phase


def fence(text: str, lang: str = "text") -> str:
    fence_ = "`" * max(3, max((len(x) for x in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence_}{lang}\n{text}\n{fence_}"


def render_assistant(msg: dict[str, Any]) -> tuple[str, str]:
    text, reasoning = content_text(msg)
    calls = msg.get("tool_calls") or []
    parts: list[str] = []
    for r in reasoning:
        parts.append(f"[reasoning] {r}")
    if text.strip():
        parts.append(text)
    for c in calls:
        args = json.dumps(c["arguments"], ensure_ascii=False)
        parts.append(f"tool call: {c['function']}({args})")
    names = ", ".join(c["function"] for c in calls)
    head = f"assistant: tool calls ({names})" if calls else "assistant: text reply, no tool call"
    if not calls and not text.strip() and reasoning:
        head = "assistant: reasoning only, cut off at max_tokens (no tool call)"
    return head, "\n".join(parts)


Turn = list[tuple[int, dict[str, Any]]]


def segment(conv: list[dict[str, Any]]) -> list[Turn]:
    """Split into the system message, then one segment per turn (a user message, its replies and
    tool results, and any forced follow-up that belongs to it)."""
    segs: list[Turn] = []
    for i, m in enumerate(conv, 1):
        starts = m["role"] == "system"
        if m["role"] == "user":
            starts = classify(m)[0] != "forced"
        if starts or not segs:
            segs.append([])
        segs[-1].append((i, m))
    return segs


def render_body(m: dict[str, Any], plain: bool) -> tuple[str, str]:
    """(heading text, fenced body) of one message."""
    role = m["role"]
    if role == "assistant":
        head, body = render_assistant(m)
    elif role == "tool":
        text, _ = content_text(m)
        fn = m.get("function") or "tool"
        err = " (error)" if m.get("error") else ""
        head, body = f"tool result: {fn}{err}", text
    else:
        text, _ = content_text(m)
        kind, label = classify(m) if role == "user" else ("system prompt", "system prompt")
        head, body = f"{role}: {label}", text
    return head, strip_marks(body) if plain else body


def render_conversation(conv: list[dict[str, Any]], plain: bool) -> tuple[list[str], set[str]]:
    out: list[str] = []
    shown_labels: set[str] = set()
    seen_in_group: Counter[str] = Counter()
    skipped: list[tuple[int, int]] = []          # (first msg no, last msg no) of omitted turns
    current_group = None

    def flush_skipped() -> None:
        nonlocal skipped
        if skipped:
            n = len(skipped)
            out.append(f"*({n} more decision{'s' if n != 1 else ''} like this, messages "
                       f"{skipped[0][0]}-{skipped[-1][1]} omitted; they use the same sections as "
                       f"the ones above.)*\n")
            skipped = []

    for seg in segment(conv):
        first = seg[0][1]
        kind, label = (classify(first) if first["role"] == "user" else
                       ("system prompt", "system prompt"))
        group = group_of(kind, label) or current_group or "Season start"
        labs = {lab for _, m in seg for lab in labels_in(content_text(m)[0])}
        for _, m in seg:                                   # tool calls are text too
            labs |= set()
        if group != current_group:
            flush_skipped()
            out.append(f"## {group}\n")
            current_group = group
        seen_in_group[group] += 1
        novel = bool(labs - shown_labels)
        decision_like = kind in ("practice decision", "round decision", "chat later")
        if decision_like and seen_in_group[group] > KEEP_PER_PHASE and not novel:
            skipped.append((seg[0][0], seg[-1][0]))
            continue
        flush_skipped()
        shown_labels |= labs
        for idx, m in seg:
            head, body = render_body(m, plain)
            out.append(f"### {idx}. {head}\n")
            kind_m = classify(m)[0] if m["role"] == "user" else (
                "system prompt" if m["role"] == "system" else "")
            when = WHEN.get(kind_m, "")
            if when:
                out.append(f"*{when}*\n")
            out.append(fence(body) + "\n")
    flush_skipped()
    return out, shown_labels


def render_tools(rec: Recorder, plain: bool) -> list[str]:
    """The tool descriptions the model was offered, once per distinct set."""
    out = ["## Tools offered to the agent\n",
           "Tool names, descriptions and parameter descriptions are sent with each call (outside "
           "the message list). Each distinct set is shown once, with the message it was first "
           "offered at.\n"]
    seen: set[str] = set()
    for ts in rec.tool_sets:
        sig = json.dumps(ts["tools"], sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        kind, label = classify({"role": "user", "content": ts["after"]})
        names = ", ".join(t["name"] for t in ts["tools"])
        out.append(f"### Tools: {names}\n")
        out.append(f"*First offered with: {label}.*\n")
        lines = []
        for t in ts["tools"]:
            lines.append(f"{t['name']}: {t['description']}")
            for k, v in t["params"].items():
                lines.append(f"    param {k}: {v}")
        body = "\n".join(lines)
        out.append(fence(strip_marks(body) if plain else body) + "\n")
    return out


# ------------------------------------------------------------ sections never used
# "file.md › section": when it appears (shown only for sections the season did not use)
TRIGGERS: dict[str, str] = {
    "communication.md › comms.planning_only": "comms: planning_only (system prompt)",
    "communication.md › comms.none": "comms: none (deliberately empty)",
    "communication.md › planning_only_rule": "comms: planning_only (inside comms.planning_only)",
    "communication.md › chat_turn.notepad": "first chat turn when the agent has a notepad "
                                            "(called note() earlier)",
    "communication.md › chat_turn.new_posts": "a chat turn after teammate posts arrived",
    "communication.md › chat_turn.no_new_posts": "a chat turn with no new posts from the teammate",
    "communication.md › chat_turn.ready_prefix": "a chat turn after the agent called ready()",
    "messages.md › season_intro.chat": "practice: off, comms not none",
    "messages.md › season_intro.no_chat": "practice: off, comms: none",
    "messages.md › season_intro.practice": "practice: on",
    "messages.md › practice_start.no_comms": "practice on and comms: none",
    "messages.md › practice_start.with_comms": "practice on and comms not none",
    "messages.md › round_start.turn_limit": "the round's chat ended by hitting its turn cap",
    "messages.md › cutoff_notice.post": "after a cutoff that also posted (costs more than 1 s)",
    "messages.md › forced_why.thinking_budget_used_up": "reasoning: on, a turn needed a forced "
                                                        "answer",
    "messages.md › forced_why.act_no_reasoning": "reasoning: off, a decision without act()",
    "messages.md › forced_why.chat_no_reasoning": "reasoning: off, a chat reply cut off",
    "messages.md › forced_why.debrief_no_reasoning": "reasoning: off, a debrief reply cut off",
    "messages.md › forced.act": "forced answer after a decision without act()",
    "messages.md › forced.chat": "forced answer after a chat reply cut off at max_tokens",
    "messages.md › forced.debrief": "forced answer after a debrief reply cut off at max_tokens",
    "messages.md › round_result": "debrief: score_only (opens the next message)",
    "messages.md › debrief.scores_so_far": "debrief: full, from round 2 on",
    "messages.md › debrief.score_item": "debrief: full, from round 2 on",
    "messages.md › debrief.notepad": "debrief: full, agent has a notepad",
    "messages.md › debrief.notepad_empty": "debrief: full, agent has no notepad",
    "messages.md › compaction.posted": "compaction when comms is not none",
    "messages.md › compaction.prompt": "when the conversation passes compact_at_tokens",
    "state.md › push_new_posts": "board: push, decision with new teammate posts",
    "state.md › push_none": "board: push, decision with no new posts",
    "state.md › orders_none": "a decision when no order is open",
    "state.md › board_unread": "board: pull, comms full, unread teammate posts",
    "state.md › board_none": "board: pull, comms full, nothing unread",
    "state.md › notepad": "a decision when the agent has a notepad",
    "state.md › counters_none": "a kitchen with no shared counters",
    "state.md › item.soup": "an agent holding soup",
    "state.md › item.dish": "an agent holding a dish",
    "state.md › item.onion": "an agent holding an onion",
    "state.md › pot.ready": "a pot that finished cooking",
    "state.md › pot.cooking": "a pot cooking",
    "state.md › pot.onions": "a pot with 1 or 2 onions",
    "state.md › pot.empty": "an empty pot",
    "tools.md › act.too_long": "act() with more actions than max_plan_len",
    "tools.md › act.ignored": "a second act() call in the same turn",
    "tools.md › act.rejected": "an act() plan the engine rejects",
    "tools.md › act.accepted": "an act() plan accepted",
    "tools.md › post.ignored": "a second post() call in the same turn",
    "tools.md › post.not_posted": "post() refused (e.g. cap reached)",
    "tools.md › post.empty": "post() with empty text",
    "tools.md › post.cap_reached": "post() after the chat's post cap was used",
    "tools.md › post.posted_cut": "post() longer than max_post_chars",
    "tools.md › post.posted": "post() accepted",
    "tools.md › post.lands_later": "post() in a round (lands with the decision)",
    "tools.md › read_board.empty": "read_board() with no posts",
    "system.md › rules_recipe": "rules: recipe (replaces the recipe, orders and interact table)",
    "system.md › rules_minimal": "rules: minimal (replaces the recipe, orders and interact table)",
    "system.md › rules_minimal_hint": "rules: minimal_hint (minimal plus a nudge to experiment)",
    "messages.md › round_start.unseen_chat": "comms: planning_only, chat posts the agent has not seen yet",
    "state.md › order_item_minimal": "rules not full: an order line that names no dish",
    "tools.md › finish_practice.result": "practice: an agent calls finish_practice()",
    "tools.md › act.description_no_recipe": "rules not full: act's example plan without the recipe",
    "system.md › practice_intro": "practice: on, comms not none",
    "system.md › practice_intro_no_comms": "practice: on, comms: none",
    "system.md › between_chat_debrief": "comms not none, debrief: full",
    "system.md › between_chat_score_only": "comms not none, debrief: score_only",
    "system.md › between_no_chat_debrief": "comms: none, debrief: full",
    "system.md › between_no_chat_score_only": "comms: none, debrief: score_only",
    "system.md › posting_full": "comms: full",
    "system.md › extras_full": "comms: full",
    "system.md › extras_no_post": "comms: planning_only or none",
    "system.md › board_read_pull": "comms: full, board: pull",
    "system.md › board_read_push": "comms: full, board: push",
    "system.md › thinking_budget": "reasoning: on",
}


def all_sections() -> list[tuple[str, str]]:
    return [(name, sec) for name in PROMPT_FILES for sec in promptfiles.sections(name)]


def section_label(name: str, sec: str) -> str:
    return f"{name}.md › {sec}"


def trigger(name: str, sec: str) -> str:
    return TRIGGERS.get(section_label(name, sec), "(always used)")


def render_unused(markers: set[str], rec: Recorder) -> list[str]:
    seen = {lab for lab in markers}
    rows = [(n, s) for n, s in all_sections() if section_label(n, s) not in seen]
    out = ["## Sections that never appear in this example\n",
           "Sections of `prompts/*.md` that no marker above names, with the condition that "
           "makes them appear. Change the config in `analysis/render_prompts.py` (or run another "
           "season) to see them.\n",
           "| Section | Appears when |", "|---|---|"]
    for n, s in rows:
        out.append(f"| `{section_label(n, s)}` | {trigger(n, s)} |")
    return out + [""]


# ----------------------------------------------------------------------------- the file
HEADER = (
    "This file is generated by `analysis/render_prompts.py`: it runs one short offline season "
    "(fake models, no network; practice on, 2 rounds, comms full, debrief full, compaction at "
    "{compact} tokens) and prints, in order, every message in the conversation of {name} "
    "(the first agent in the config), plus the tool descriptions. {marks} A few turns are "
    "scripted (a cutoff, a rejected plan, forced answers, a refused post, odd tool calls) so that "
    "the rarer prompts appear, and look-alike decisions after the first {keep} of each phase are "
    "summarised. Regenerate with `uv run python -m analysis.render_prompts` (add `--plain` for a "
    "version without markers); the file is stale after any edit to `prompts/` or to the code that "
    "builds prompts.")

MARKS = ("Source markers: `⟦system.md › kitchen_rules⟧ ... ⟦/⟧` is that section of "
         "`prompts/system.md` with its placeholders filled; `⟦code: ...⟧` is text drawn by code; "
         "markers nest; text with no marker is glue or an unlabelled code path (engine parse "
         "errors, the agents' own words).")


def render(cfg: Config, conv: list[dict[str, Any]], rec: Recorder, plain: bool) -> str:
    body, shown = render_conversation(conv, plain)
    all_marked = {lab for m in conv for lab in labels_in(content_text(m)[0])}
    for ts in rec.tool_sets:
        for t in ts["tools"]:
            all_marked |= set(labels_in(t["description"]))
            for v in t["params"].values():
                all_marked |= set(labels_in(v))
    for p in rec.compaction_prompts:
        all_marked |= set(labels_in(p))
    marker_labels = {lab for lab in all_marked}
    head = textwrap.fill(HEADER.format(
        compact=cfg.compact_at_tokens, name=rec.shown_name, shown=SHOWN, keep=KEEP_PER_PHASE,
        marks="No markers are shown (plain mode)." if plain else MARKS), 100)
    head = "# Example: every prompt one agent sees in one season\n\n" + head + "\n"
    parts = [head, "# Conversation\n", *body]
    if rec.compaction_prompts:
        parts += ["## Compaction prompt (sent to the model to write the summary)\n",
                  "*Not in the conversation: Inspect sends this as one extra user message "
                  "after the older messages, in a separate call; the reply becomes the "
                  "compaction summary message above.*\n",
                  fence(strip_marks(rec.compaction_prompts[0]) if plain
                        else rec.compaction_prompts[0]) + "\n"]
    parts += render_tools(rec, plain)
    parts += render_unused(marker_labels | shown, rec)
    return "\n".join(parts)


def build(plain: bool) -> tuple[str, Recorder, list[dict[str, Any]]]:
    cfg = example_config()
    conv, rec = run_season(cfg, labels=True)
    return render(cfg, conv, rec, plain), rec, conv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plain", action="store_true", help="no source markers")
    ap.add_argument("--out", type=Path, help="output file")
    args = ap.parse_args(argv)
    text, _, _ = build(args.plain)
    out = args.out or (OUT_PLAIN if args.plain else OUT_MARKED)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({text.count(chr(10)) + 1} lines)")


if __name__ == "__main__":
    main()
