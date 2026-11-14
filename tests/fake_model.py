"""A scripted, offline model: reads the latest user message and replies with tool calls.

Left-side cook (x < 2) ferries onions then a dish to the counters; right-side cook pots,
cooks, plates and serves. Its first round decision is a cutoff (text, no act) to exercise the
re-ask path. Registered as `fakecook/<anything>`.
"""

from __future__ import annotations

import re

from inspect_ai.model import ChatMessageAssistant, ModelAPI, ModelOutput, modelapi
from inspect_ai.model._model_output import ChatCompletionChoice, ModelUsage
from inspect_ai.tool import ToolCall


def _out(calls: list[tuple[str, dict]], text: str = "") -> ModelOutput:
    msg = ChatMessageAssistant(content=text, model="fakecook", tool_calls=[
        ToolCall(id=f"c{i}", function=f, arguments=a) for i, (f, a) in enumerate(calls)] or None)
    return ModelOutput(model="fakecook", choices=[ChatCompletionChoice(message=msg)],
                       usage=ModelUsage(input_tokens=100, output_tokens=10, total_tokens=110))


@modelapi(name="fakecook")
class FakeCook(ModelAPI):
    def __init__(self, model_name: str, base_url=None, api_key=None, config=None, **kw):
        super().__init__(model_name, base_url, api_key, [], config)
        self.cut_done = False

    async def generate(self, input, tools, tool_choice, config):
        last = input[-1]
        if last.role == "tool":           # after tool results: stop
            return _out([], "ok")
        text = last.text
        if "chat" in text.lower() and "Posts left" in text:
            return _out([("post", {"message": "I'll pass onions, you pot them."}),
                         ("ready", {})])
        if "is over" in text:
            return _out([("note", {"text": "pass onions via counters"})])
        if "act(plan)" in text:
            if not self.cut_done:
                self.cut_done = True
                return _out([], "thinking out loud, no tool")
            if "You (" not in text:          # a re-ask after a cutoff: use the last state
                text = next(m.text for m in reversed(input)
                            if m.role == "user" and "You (" in m.text)
            m = re.search(r"You \(\w\) at \((\d),(\d)\), holding (\w+)", text)
            x, held = int(m.group(1)), m.group(3)
            if x < 2:                        # every 4th trip carries a dish
                self.trips = getattr(self, "trips", 0) + 1
                plan = (["get_dish", "place_on_counter"] if self.trips % 4 == 0
                        else ["get_onion", "place_on_counter"])
                if held != "nothing":
                    plan = ["place_on_counter"]
            else:
                if held == "onion":
                    plan = ["put_in_pot"]
                elif held == "dish":
                    plan = ["plate_soup"]
                elif held == "soup":
                    plan = ["serve"]
                elif "3 onions, filling" in text:
                    plan = ["start_cooking"]
                elif "dish at" in text and ("cooking" in text or "ready" in text):
                    plan = ["get_dish"]
                elif "onion at (2," in text:
                    plan = ["get_onion"]
                else:
                    plan = ["wait(2)"]
            calls = [("act", {"plan": plan})]
            if "Unread posts on the board: " in text:
                calls.insert(0, ("read_board", {}))
            return _out(calls)
        return _out([], "pass")
