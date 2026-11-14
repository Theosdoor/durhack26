"""A scripted, offline model API for season tests: `kitchenfake/cook` and `kitchenfake/passer`.

The scripts read only what a real agent reads (the conversation and the offered tools) and play
split_room: the passer (left room) puts onions and then a dish on counter (2,1); the cook (right
room) takes them, cooks in pot_1 and serves. Along the way they post, read the board (pull mode),
write notes and, once, reply without act() (a cutoff). Responses report provider "FakeCloud" in
the ModelCall, the way OpenRouter reports its served provider.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessage,
    ChatMessageAssistant,
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


def reply(content: str = "", calls: list[tuple[str, dict[str, Any]]] = ()) -> ModelOutput:
    tool_calls = [ToolCall(id=f"call_{next(_ids)}", function=f, arguments=a) for f, a in calls]
    msg = ChatMessageAssistant(content=content, tool_calls=tool_calls or None,
                               model="kitchenfake", source="generate")
    return ModelOutput(model="kitchenfake", choices=[ChatCompletionChoice(
        message=msg, stop_reason="tool_calls" if calls else "stop")])


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


def script(role: str, messages: list[ChatMessage], tools: list[str]) -> ModelOutput:
    if not tools:                                           # compaction summary request
        return reply("Summary: I am a cook; we pass onions and a dish over counter (2,1).")
    text = last_user(messages)
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

    async def count_text_tokens(self, text: str) -> int:   # no tiktoken: stays offline
        return len(text) // 4 + 1

    async def generate(self, input: list[ChatMessage], tools: list[ToolInfo],
                       tool_choice: ToolChoice, config: GenerateConfig
                       ) -> tuple[ModelOutput, ModelCall]:
        assert tool_choice == "auto" or not tools, tool_choice
        out = script(self.model_name, input, [t.name for t in tools])
        n_in = await self.count_tokens(input)
        out.usage = ModelUsage(input_tokens=n_in, output_tokens=20, total_tokens=n_in + 20,
                               reasoning_tokens=5)
        response = {"provider": SERVED_BY, "content": out.completion}
        self.on_response(response)          # as OpenAI-compatible APIs do with the raw response
        call = ModelCall.create({"model": self.model_name, "n_messages": len(input)}, response)
        return out, call

    def on_response(self, response: dict[str, Any]) -> None:
        pass
