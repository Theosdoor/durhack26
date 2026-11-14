"""Golden file of everything the agents read: proves prompt edits/refactors are intentional.

    uv run python -m tests.golden_prompts            # check against tests/golden/prompts.json
    uv run python -m tests.golden_prompts --update   # regenerate after an intentional edit

`render()` plays short seasons with a deterministic recording model on every layout in both
rules modes (its replies rotate through posting, double posts, ready, passing, invalid plans,
cutoffs, targeted waits that fail, notes), and records every message and tool description the
agents get. It also renders the rules, map/state text and action-check errors directly.
"""

from __future__ import annotations

import asyncio
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from inspect_ai.model import (ChatMessageAssistant, ModelAPI, ModelOutput,  # noqa: E402
                              get_model, modelapi)
from inspect_ai.model._model_output import ChatCompletionChoice  # noqa: E402
from inspect_ai.tool import ToolCall  # noqa: E402

from overcooked_port.kitchen import Kitchen, rules_text  # noqa: E402
from overcooked_port.season import AgentSpec, Config, Season, system_prompt  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "prompts.json"
LAYOUTS = ["forced_coordination", "asymmetric_advantages", "coordination_ring",
           "counter_circuit_o_1order"]
TOOLS_SEEN: dict[str, dict] = {}


def _out(calls: list[tuple[str, dict]], text: str = "") -> ModelOutput:
    msg = ChatMessageAssistant(content=text, model="golden", tool_calls=[
        ToolCall(id=f"c{i}", function=f, arguments=a) for i, (f, a) in enumerate(calls)] or None)
    return ModelOutput(model="golden", choices=[ChatCompletionChoice(message=msg)])


@modelapi(name="goldencook")
class GoldenCook(ModelAPI):
    def __init__(self, model_name: str, base_url=None, api_key=None, config=None, **kw):
        super().__init__(model_name, base_url, api_key, [], config)
        self.minimal = "minimal" in model_name
        self.n = 0

    async def generate(self, input, tools, tool_choice, config):
        for t in tools:
            TOOLS_SEEN[json.dumps(t.model_dump(mode="json"), sort_keys=True)] = \
                t.model_dump(mode="json")
        if input[-1].role == "tool":
            return _out([], "ok")
        text = input[-1].text
        self.n += 1
        k = self.n
        if "Posts left" in text:                                   # chat turn
            return [_out([("ready", {})]),
                    _out([("post", {"message": f"plan {k}"}), ("post", {"message": "again"})]),
                    _out([], "pass")][k % 3]
        if "note(text)" in text:                                   # debrief
            return _out([("note", {"text": f"note {k}"})]) if k % 2 else _out([], "no note")
        if self.minimal:
            plans = [["interact(0, 1)"], ["get_onion"], ["interact(3, 4)", "wait(2)"],
                     ["interact(pot_1)"], ["move_to(9, 9)"]]
        else:
            plans = [["get_onion", "put_in_pot(pot_1)"], ["interact(0, 1)"],
                     ["pick_up_from_counter(2, 2)"], ["serve"], ["put_in_pot(pot_9)"],
                     ["move_to(1, 1)", "wait(3)"]]
        if k % 7 == 0:
            return _out([], "thinking, no act")                     # cutoff
        calls = [("act", {"plan": plans[k % len(plans)]})]
        if k % 4 == 0:
            calls = [("read_board", {}), ("post", {"message": f"mid {k}"})] + calls
        return _out(calls)


def _season(layout: str, rules: str) -> dict:
    cfg = Config(layout=layout, rules=rules, rounds=2, round_steps=24, seed=1,
                 agents=[AgentSpec(model=f"goldencook/{layout}-{rules}-a"),
                         AgentSpec(model=f"goldencook/{layout}-{rules}-b")])
    models = [get_model(a.model) for a in cfg.agents]
    season = Season(cfg, models, epoch=1)
    asyncio.run(season.run())
    out = {}
    for a in season.agents:
        out[a.name] = [{"role": m.role, "text": m.text,
                        **({"tools": [(c.function, c.arguments) for c in m.tool_calls]}
                           if getattr(m, "tool_calls", None) else {})}
                       for m in a.messages]
    return out


def render() -> dict:
    TOOLS_SEEN.clear()
    data: dict = {"seasons": {}, "direct": {}}
    for layout in LAYOUTS:
        for rules in ("full", "minimal"):
            data["seasons"][f"{layout}/{rules}"] = _season(layout, rules)
            k = Kitchen(layout, ["Kilo", "Lima"], mode=rules)
            d = data["direct"][f"{layout}/{rules}"] = {}
            d["rules"] = rules_text(k)
            d["system"] = system_prompt(Config(layout=layout, rules=rules), k, "Kilo", "Lima")
            d["checks"] = {f"{p.name}:{a}": k.check(p, a) for p in k.players for a in [
                "get_onion", "get_dish", "put_in_pot", "put_in_pot(pot_1)", "start_cooking",
                "plate_soup", "serve", "place_on_counter", "pick_up_from_counter(2, 2)",
                "interact(0, 1)", "interact(pot_2)", "interact(9, 9)", "move_to(0, 0)",
                "move_to(1, 1)", "wait(3)", "dance", "put_in_pot(pot_9)", "get_onion(3, 4)"]}
            states = [k.state_text(k.players[0])]
            plan = (["interact(0, 1)", "interact(pot_1)"] if rules == "minimal" else
                    ["get_onion", "put_in_pot"])
            for p in k.players:
                k.set_plan(p, plan * 3 + (["interact(pot_1)"] if rules == "minimal"
                                          else ["start_cooking"]))
            for _ in range(30):
                k.step("round", 1)
                if k.t % 6 == 0:
                    states.append(k.state_text(k.players[1]))
            d["states"] = states
            d["last_fail"] = [p.last_fail for p in k.players]
    data["tools"] = sorted(TOOLS_SEEN.values(), key=lambda t: json.dumps(t, sort_keys=True))
    return data


def main() -> int:
    data = render()
    if "--update" in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN}")
        return 0
    want = json.loads(GOLDEN.read_text())
    if want == json.loads(json.dumps(data)):
        print("prompts match the golden file")
        return 0
    print("prompts differ from the golden file; run with --update if intended")
    return 1


if __name__ == "__main__":
    sys.exit(main())
