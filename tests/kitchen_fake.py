"""A scripted, offline model API for season tests: `kitchenfake/cook` and `kitchenfake/passer`.

The scripts read only what a real agent reads (the conversation and the offered tools) and play
split_room: the passer (left room) puts onions and then a dish on counter (2,1); the cook (right
room) takes them, cooks in pot_1 and serves. Along the way they post, read the board (pull mode),
write notes and, once, reply without act() (a cutoff). Responses report provider "FakeCloud" in
the ModelCall, the way OpenRouter reports its served provider.

Flavours, added to the model name with "+" (e.g. `kitchenfake/cook+chaos+tagged`):
- chaos: decisions vary with a hash of the state text: posts of 1-25 words (1-3 s), cutoffs
  with and without a post, notes, rejected plans, waits of 1-4 s, so landing times vary;
- tagged: the served provider is "Cloud-<agent name>" (from the system prompt), not FakeCloud.
- overthink: every turn's first reply (practice and round decisions, chat, debrief) is all
  reasoning and stops at max_tokens with no tool call; the forced-answer follow-up then answers
  as the plain script would have;
- stubborn (with overthink): the follow-up stops at max_tokens too, so every turn is a cutoff;
- rescue (with chaos): a decision without act() is rescued by the follow-up for half the states.
Without overthink, stubborn or rescue a follow-up gets a reply with no tool call, as before.
`DELAY` = (seed, lo, hi) makes every call sleep a pseudo-random time in [lo, hi] s, drawn from a
hash of the seed and the call's input (so the same call sleeps the same in every run, but
completion order differs from start order). `CALLS` records the wall-clock intervals (`Call`)
of the practice and round decision calls when `RECORD` is on. `REQUESTS`, when a list, gets a
`Request` per call: what the model was asked and with which reasoning settings.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import random
import re
import time
from typing import Any, NamedTuple

from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessage,
    ChatMessageAssistant,
    ContentReasoning,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)
from inspect_ai.model._model_call import ModelCall
from inspect_ai.tool import ToolCall, ToolChoice, ToolInfo

SERVED_BY = "FakeCloud"
COOK_ROUND_POST = "Onions on (2,1) please"           # 5 words
CHAT_POST = "I am at the pots. Put onions then a dish on (2,1) please."
NOTE = "Onions and the dish go on (2,1)."
BAD_ACTION = "interact(pot_9)"
_ids = itertools.count()

DELAY: tuple[int, float, float] | None = None      # (seed, lo, hi) seconds per call
RECORD = False


class Call(NamedTuple):
    """One generate call of a practice or round decision, with its wall-clock interval."""

    agent: str
    phase: str                 # "Practice" or "Round r of N"
    t: int | None              # game second the decision started
    start: float
    end: float


CALLS: list[Call] = []


class Request(NamedTuple):
    """One generate call as the fake API received it."""

    model: str                     # e.g. "cook+overthink"
    reasoning_enabled: bool | None  # the model arg (None: not given)
    reasoning_tokens: int | None   # from the merged GenerateConfig
    max_tokens: int | None
    tools: list[str]
    tool_text: str                 # the offered tools' descriptions, as JSON
    system: str
    last_user: str


REQUESTS: list[Request] | None = None
OVERTHOUGHT = "Hmm. Let me weigh every option once more, step by step"


def forced_prompts() -> set[str]:
    """Every user message of a forced-answer follow-up."""
    from undercooked import prompts
    from undercooked.config import Config

    out = set()
    for reasoning in ("on", "off"):
        cfg = Config.model_validate({"name": "x", "level": "x", "agents": [],
                                     "decision": {"reasoning": reasoning}})
        out |= {prompts.forced_prompt(cfg, k) for k in prompts.FORCED_ASKS}
    return out


FORCED = forced_prompts()


def reply(content: str = "", calls: list[tuple[str, dict[str, Any]]] = ()) -> ModelOutput:
    tool_calls = [ToolCall(id=f"call_{next(_ids)}", function=f, arguments=a) for f, a in calls]
    msg = ChatMessageAssistant(content=content, tool_calls=tool_calls or None,
                               model="kitchenfake", source="generate")
    return ModelOutput(model="kitchenfake", choices=[ChatCompletionChoice(
        message=msg, stop_reason="tool_calls" if calls else "stop")])


def overthought() -> ModelOutput:
    """A reply that thought until max_tokens: reasoning only, no text, no tool call."""
    msg = ChatMessageAssistant(content=[ContentReasoning(reasoning=OVERTHOUGHT)],
                               model="kitchenfake", source="generate")
    return ModelOutput(model="kitchenfake", choices=[ChatCompletionChoice(
        message=msg, stop_reason="max_tokens")])


def follow_up(role: str, messages: list[ChatMessage], tools: list[str],
              flags: tuple[str, ...]) -> ModelOutput:
    """The reply to a forced-answer follow-up (see the module doc)."""
    if "stubborn" in flags:
        return overthought()
    k = max(i for i, m in enumerate(messages[:-1]) if m.role == "user")   # the turn's message
    if "overthink" in flags:                    # answer as the plain script would have
        plain = tuple(f for f in flags if f != "overthink")
        return script(role, messages[:k + 1], tools, plain)
    state = messages[k].text
    if "rescue" in flags and "act" in tools and _hash(state) % 2 == 0:
        if "Practice" in state:
            plan = [f"wait({1 + _hash(state) // 2 % 3})"]
        else:
            plan = cook_plan(state) if role == "cook" else passer_plan(state)
        return reply(calls=[("act", {"plan": plan})])
    return reply("I still need to think.")


def last_user(messages: list[ChatMessage]) -> str:
    return next(m.text for m in reversed(messages) if m.role == "user")


def _me(state: str) -> str:
    return re.search(r"^You \(.*?\): at \(\d+,\d+\), holding (\w+(?: \w+)?)\.", state, re.M).group(1)


def _mate(state: str) -> str:
    m = re.search(r"^(?!You )\w+ \(\w\): at \(\d+,\d+\), holding (\w+(?: \w+)?)\.", state, re.M)
    return m.group(1) if m else "nothing"


def _pot1(state: str) -> str:
    return re.search(r"pot_1 \(\d+,\d+\): ([^.]*)\.", state).group(1)


def _counters(state: str) -> dict[str, str]:
    m = re.search(r"^Counters: (.*)\.$", state, re.M)
    out = {}
    for part in m.group(1).split(", ") if m else []:
        xy, item = part.split(" ", 1)
        out["counter_" + xy.strip("()").replace(",", "_")] = item
    return out


def _pot_busy(pot: str) -> bool:
    return pot.startswith("cooking") or "ready to plate" in pot


def _pot_onions(pot: str) -> int:
    if _pot_busy(pot):
        return 3
    m = re.match(r"(\d+) onion", pot)
    return int(m.group(1)) if m else 0


def cook_plan(state: str) -> list[str]:
    me, pot, counters = _me(state), _pot1(state), _counters(state)
    if me == "a soup":
        return ["interact(hatch_1)"]
    if me == "a dish":
        return ["interact(pot_1)"] if "ready to plate" in pot else ["wait(1)"]
    if me == "an onion":
        return ["interact(pot_1)"]
    want = "dish" if _pot_busy(pot) else "onion"
    spot = next((c for c, item in counters.items() if item == want), None)
    if spot is None:
        return ["wait(1)"]
    return [f"interact({spot})", "interact(pot_1)"] if want == "onion" else [f"interact({spot})"]


def passer_plan(state: str) -> list[str]:
    """Onions go on (2,1), one at a time; then one dish there too."""
    me, mate, pot, counters = _me(state), _mate(state), _pot1(state), _counters(state)
    spot = {"an onion": "counter_2_1", "a dish": "counter_2_1"}.get(me)
    if spot is not None:
        return [f"interact({spot})"] if counters.get(spot) == "empty" else ["wait(1)"]
    onions = _pot_onions(pot) + sum(v == "onion" for v in counters.values()) + (mate == "an onion")
    dishes = sum(v == "dish" for v in counters.values()) + (mate in ("a dish", "a soup"))
    served = not re.search(r"Score 0\b", state)
    if onions < 3:
        if counters.get("counter_2_1") != "empty":
            return ["wait(1)"]
        return ["interact(onion_crate)", "interact(counter_2_1)"]
    if dishes == 0 and not served:
        return ["interact(dish_rack)", "interact(counter_2_1)"]
    return ["wait(2)"]


def _hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def agent_name(messages: list[ChatMessage]) -> str | None:
    m = re.search(r"You are (\w+),", messages[0].text) if messages else None
    return m.group(1) if m else None


def game_second(text: str) -> tuple[int | None, str]:
    m = re.search(r"^Time (\d+) / \d+ s · Score -?\d+ · (.*)$", text, re.M)
    return (int(m.group(1)), m.group(2)) if m else (None, "")


def chaos(role: str, messages: list[ChatMessage], tools: list[str], text: str) -> ModelOutput:
    """A practice or round decision that varies with the state text (see the module doc)."""
    h = _hash(role + text)
    kind = h % 20
    since = 0                                    # generations so far in this decision
    for m in reversed(messages):
        if m.role == "user":
            break
        since += m.role == "assistant"
    if kind >= 18:
        plan = [BAD_ACTION]                                          # rejected
    elif "Practice" in text:
        plan = [f"wait({1 + h // 20 % 4})"]
    else:
        plan = cook_plan(text) if role == "cook" else passer_plan(text)
    words = " ".join(f"w{j}" for j in range(1 + h // 80 % 25))      # 1-25 words: 1-3 s
    can_post = "post" in tools
    if since == 0:
        if kind < 3:
            return reply("Let me think about this first.")           # cutoff, no post
        if kind < 5 and can_post:
            return reply(calls=[("post", {"message": words})])       # a cutoff with a post
        if kind < 8 and "read_board" in tools and "unread post" in text:
            return reply(calls=[("read_board", {})])
        calls: list[tuple[str, dict[str, Any]]] = []
        if can_post and kind < 12:
            calls.append(("post", {"message": words}))
        if kind % 3 == 0:
            calls.append(("note", {"text": f"note {h % 1000}"}))
        calls.append(("act", {"plan": plan}))
        return reply(calls=calls)
    if kind < 5:
        return reply("That is all for now.")
    return reply(calls=[("act", {"plan": plan})])


def script(role: str, messages: list[ChatMessage], tools: list[str],
           flags: tuple[str, ...] = ()) -> ModelOutput:
    if not tools:                                           # compaction summary request
        return reply("Summary: I am a cook; we pass onions and a dish over counter (2,1).")
    text = last_user(messages)
    if text in FORCED:
        return follow_up(role, messages, tools, flags)
    if "overthink" in flags and messages[-1].role == "user":
        return overthought()
    if "ready" in tools:                                    # chat turn
        if role == "cook":
            if "Posts left: 2" in text:
                return reply(calls=[("post", {"message": CHAT_POST})])
            return reply(calls=[("ready", {})])
        if "Posts left: 2" in text:
            return reply(calls=[("post", {"message": "OK, onions coming."}), ("ready", {})])
        return reply("I pass.")
    if tools == ["note"]:                                   # debrief
        return reply(calls=[("note", {"text": f"{role}: {NOTE}"})])
    first_line = text.split("\n")[0]
    if "chaos" in flags and "act" in tools:
        return chaos(role, messages, tools, text)
    if "Practice" in text and "act" in tools:
        if role == "cook" and "Time 0 /" in text:
            return reply("Hmm, let me think about the practice kitchen first.")   # cutoff
        if role == "passer" and "Last plan: none yet" in text:
            return reply(calls=[("act", {"plan": [BAD_ACTION]})])                # rejected
        return reply(calls=[("act", {"plan": ["wait(5)"]})])
    # a round decision
    after_tool = messages[-1].role == "tool"
    if role == "passer" and re.search(r"^Board: \d+ unread post", text, re.M) and not after_tool:
        return reply(calls=[("read_board", {})])
    calls: list[tuple[str, dict[str, Any]]] = []
    if "starts now" in first_line:
        if role == "cook":
            calls.append(("post", {"message": COOK_ROUND_POST}))
        else:
            calls.append(("note", {"text": NOTE}))
    plan = cook_plan(text) if role == "cook" else passer_plan(text)
    calls.append(("act", {"plan": plan}))
    return reply(calls=calls)


@modelapi(name="kitchenfake")
class KitchenFake(ModelAPI):
    def __init__(self, model_name: str, base_url: str | None = None, api_key: str | None = None,
                 config: GenerateConfig = GenerateConfig(), **model_args: Any) -> None:
        super().__init__(model_name, base_url, api_key, [], config)
        self.model_args = model_args

    async def count_text_tokens(self, text: str) -> int:   # no tiktoken: stays offline
        return len(text) // 4 + 1

    async def generate(self, input: list[ChatMessage], tools: list[ToolInfo],
                       tool_choice: ToolChoice, config: GenerateConfig
                       ) -> tuple[ModelOutput, ModelCall]:
        assert tool_choice == "auto" or not tools, tool_choice
        role, *flags = self.model_name.split("+")
        names = [t.name for t in tools]
        if REQUESTS is not None:
            REQUESTS.append(Request(
                self.model_name, self.model_args.get("reasoning_enabled"), config.reasoning_tokens,
                config.max_tokens, names, "\n".join(t.model_dump_json() for t in tools),
                input[0].text if input else "",
                last_user(input) if any(m.role == "user" for m in input) else ""))
        start = time.monotonic()
        delay = 0.0
        if DELAY is not None:
            seed, lo, hi = DELAY
            key = f"{seed}|{self.model_name}|{len(input)}|{input[-1].text if input else ''}"
            delay = random.Random(_hash(key)).uniform(lo, hi)
            await asyncio.sleep(delay / 2)
        out = script(role, input, names, tuple(flags))
        n_in = await self.count_tokens(input)
        out.usage = ModelUsage(input_tokens=n_in, output_tokens=20, total_tokens=n_in + 20,
                               reasoning_tokens=5)
        served = f"Cloud-{agent_name(input)}" if "tagged" in flags else SERVED_BY
        response = {"provider": served, "content": out.completion}
        self.on_response(response)          # as OpenAI-compatible APIs do with the raw response
        if delay:
            await asyncio.sleep(delay / 2)  # other calls get their responses in between
        if RECORD and "act" in names:      # a follow-up counts with its decision's state
            text = next(m.text for m in reversed(input)
                        if m.role == "user" and m.text not in FORCED)
            t, phase = game_second(text)
            CALLS.append(Call(agent_name(input) or "?", phase, t,
                              start, time.monotonic()))
        call = ModelCall.create({"model": self.model_name, "n_messages": len(input)}, response)
        return out, call

    def on_response(self, response: dict[str, Any]) -> None:
        pass
