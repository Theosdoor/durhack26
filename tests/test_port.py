import warnings

warnings.filterwarnings("ignore")

from inspect_ai import eval as inspect_eval  # noqa: E402
from inspect_ai.event import InfoEvent  # noqa: E402
from inspect_ai.model import get_model  # noqa: E402

import tests.fake_model  # noqa: F401,E402  (registers fakecook)
from overcooked_port.kitchen import Kitchen, parse_action  # noqa: E402
from overcooked_port.replay import Event, Header, Replay  # noqa: E402
from overcooked_port.season import AgentSpec, Config  # noqa: E402
from overcooked_port.task import make_task  # noqa: E402


def test_parse_action():
    assert parse_action("get_onion") == ("get_onion", None)
    assert parse_action("get_onion()") == ("get_onion", None)
    assert parse_action("wait(3)") == ("wait", 3)
    assert parse_action("wait(99)") == ("wait", 10)
    assert parse_action("put_in_pot(pot_2)") == ("put_in_pot", "pot_2")
    assert parse_action("pick_up_from_counter(3, 2)") == ("pick_up_from_counter", (3, 2))
    assert parse_action("interact((0,1))") == ("interact", (0, 1))
    assert parse_action("interact") is None          # interact needs a target
    assert parse_action("move_to(pot_1)") is None
    assert parse_action("fly") is None


def test_scripted_soup_scores():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"])
    right, left = k.players
    assert k.shared_counters == [(2, 1), (2, 2), (2, 3)]
    assert k.check(right, "get_onion") is None          # nothing there yet: it will wait
    k.set_plan(left, ["get_onion", "place_on_counter"] * 3 + ["get_dish", "place_on_counter"])
    script = ["get_onion", "put_in_pot"] * 3 + ["start_cooking", "get_dish", "plate_soup", "serve"]
    for _ in range(60):
        if not right.plan and script and k.check(right, script[0]) is None:
            k.set_plan(right, [script.pop(0)])
        k.step("round", 1)
    assert k.score == 20 and k.served == 1


def test_preconditions():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"])
    right, left = k.players
    assert "needs" in k.check(left, "serve")
    assert "unknown" in k.check(left, "dance")
    assert k.check(left, "get_onion") is None
    assert "no pot_9" in k.check(left, "start_cooking(pot_9)")
    assert "not something you can" in k.check(left, "pick_up_from_counter(0, 1)")
    assert "can't reach" in k.check(left, "start_cooking(pot_1)")   # pots are on the right
    assert "unknown" in k.check(left, "interact(0, 1)")             # not in the full vocabulary


def test_named_pot_is_used():
    # asymmetric_advantages: pot_1 (4,2) and pot_2 (4,3); untargeted put_in_pot would pick the
    # fuller pot_1, the named one must go to pot_2
    k = Kitchen("asymmetric_advantages", ["Kilo", "Lima"])
    p = k.players[0]
    k.set_plan(p, ["get_onion", "put_in_pot(pot_1)", "get_onion", "put_in_pot(pot_2)"])
    for _ in range(30):
        k.step("round", 1)
    assert k._pot((4, 2)).onions == 1 and k._pot((4, 3)).onions == 1


def test_waits_for_item_then_picks_it_up():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"])
    right, left = k.players
    k.set_plan(right, ["pick_up_from_counter(2, 1)"])          # nothing there yet
    k.set_plan(left, ["get_onion", "place_on_counter(2, 1)"])
    for _ in range(8):
        k.step("round", 1)
    assert k.holding(right) == "onion" and right.last_fail is None


def test_waiting_gives_up_with_a_reason():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"])
    right, _ = k.players
    k.set_plan(right, ["pick_up_from_counter(2, 2)"])
    for _ in range(15):
        k.step("round", 1)
    assert not right.plan and "nothing to do there for 10 steps" in right.last_fail


def test_minimal_mode_soup_with_interact_only():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"], mode="minimal")
    right, left = k.players
    assert "unknown" in k.check(left, "get_onion")             # named actions are not offered
    k.set_plan(left, ["interact(0, 1)", "interact(2, 1)"] * 3 + ["interact(0, 3)",
                                                                  "interact(2, 2)"])
    k.set_plan(right, ["interact(2, 1)", "interact(pot_1)"] * 3 + [
        "interact(pot_1)", "interact(2, 2)", "interact(pot_1)", "interact(3, 4)"])
    for _ in range(80):
        k.step("round", 1)
    assert k.score == 20 and right.last_fail is None and left.last_fail is None


def test_season_log_validates(tmp_path):
    cfg = Config(rounds=3, round_steps=70, agents=[AgentSpec(model="fakecook/a"),
                                                   AgentSpec(model="fakecook/b")])
    models = [get_model("fakecook/a"), get_model("fakecook/b")]
    [log] = inspect_eval(make_task(cfg, epochs=1, models=models), model="mockllm/model",
                         log_dir=str(tmp_path), display="none")
    assert log.status == "success", log.error
    s = log.samples[0]
    data = [e.data for e in s.events if isinstance(e, InfoEvent) and e.source == "kitchen"]
    rep = Replay.model_validate({"header": Header.model_validate(data[0]).model_dump(),
                                 "records": data[1:]})
    phases = rep.phases()
    for r in (1, 2, 3):
        assert ("chat", r) in phases and ("round", r) in phases and ("debrief", r) in phases
    assert any(e.type == "cutoff" for e in rep.select(Event, phase="round"))
    assert any(e.type == "serve" for e in rep.select(Event, phase="round"))
    assert rep.select(phase="chat")
    summary = s.store["overcooked_port"]
    assert len(summary["rounds"]) == 3 and summary["rounds"][0]["score"] == 20
    assert summary["cutoffs"] == [1, 1]
    assert sorted(summary["start_positions"].values()) == [1, 2]


def test_move_to():
    assert parse_action("move_to(2, 3)") == ("move_to", (2, 3))
    assert parse_action("move_to((2,3))") == ("move_to", (2, 3))
    k = Kitchen("coordination_ring", ["Kilo", "Lima"])
    p = k.players[0]
    assert "not a floor tile" in k.check(p, "move_to(0, 0)")
    k.set_plan(p, ["move_to(3, 3)"])
    for _ in range(10):
        k.step("round", 1)
    assert k.state.players[0].position == (3, 3) and not p.plan


def test_routes_around_idle_teammate():
    # coordination_ring: the idle teammate stands in the ring; the onion run goes the other way
    k = Kitchen("coordination_ring", ["Kilo", "Lima"])
    p = k.players[0]
    k.set_plan(p, ["get_onion", "put_in_pot"])
    for _ in range(20):
        k.step("round", 1)
    assert k._pot((3, 0)).onions + k._pot((4, 1)).onions == 1


def test_blocked_names_the_teammate():
    # coordination_ring: the dish rack (0,2) is reachable only from (1,2), where Lima starts
    k = Kitchen("coordination_ring", ["Kilo", "Lima"])
    k.set_plan(k.players[0], ["get_dish"])
    fails = []
    for _ in range(15):
        fails += [e for e in k.step("round", 1) if e.type == "fail"]
    assert fails and "Lima at (1,2)" in fails[0].data["reason"]


import pytest  # noqa: E402


@pytest.mark.parametrize("layout", ["asymmetric_advantages", "coordination_ring",
                                    "counter_circuit_o_1order"])
def test_season_runs_on_layout(tmp_path, layout):
    cfg = Config(layout=layout, rounds=1, round_steps=30,
                 agents=[AgentSpec(model="fakecook/a"), AgentSpec(model="fakecook/b")])
    models = [get_model("fakecook/a"), get_model("fakecook/b")]
    [log] = inspect_eval(make_task(cfg, models=models), model="mockllm/model",
                         log_dir=str(tmp_path), display="none")
    assert log.status == "success", log.error
    data = [e.data for e in log.samples[0].events
            if isinstance(e, InfoEvent) and e.source == "kitchen"]
    rep = Replay.model_validate({"header": Header.model_validate(data[0]).model_dump(),
                                 "records": data[1:]})
    assert rep.header.level == layout and ("round", 1) in rep.phases()


def test_season_runs_in_minimal_mode(tmp_path):
    cfg = Config(layout="coordination_ring", rules="minimal", rounds=1, round_steps=20,
                 agents=[AgentSpec(model="fakecook/a"), AgentSpec(model="fakecook/b")])
    models = [get_model("fakecook/a"), get_model("fakecook/b")]
    [log] = inspect_eval(make_task(cfg, models=models), model="mockllm/model",
                         log_dir=str(tmp_path), display="none")
    assert log.status == "success", log.error
    assert len(log.samples[0].store["overcooked_port"]["rounds"]) == 1
    from inspect_ai.log import read_eval_log
    full = read_eval_log(log.location, resolve_attachments=True)
    msgs = [e for e in full.samples[0].events if getattr(e, "event", "") == "model"]
    prompt = msgs[0].input[0].text
    assert "interact(target)" in prompt and "3 onions" not in prompt


def test_prompts_match_golden():
    from tests.golden_prompts import main
    assert main() == 0, "prompts changed: run `uv run python -m tests.golden_prompts --update`"


def test_prompt_loader(tmp_path, monkeypatch):
    from overcooked_port import prompts
    (tmp_path / "x.md").write_text("# notes\nignored\n## a\nHi {name}, {{ok}}\n\n# more notes\n"
                                   "## b\n trailing space \n")
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    prompts._cache.clear()
    assert prompts.render("x", "a", name="Kilo") == "Hi Kilo, {ok}"
    assert prompts.render("x", "b") == " trailing space "
    with pytest.raises(prompts.PromptError, match=r"x.md ## a: no value for placeholder \{name\}"):
        prompts.render("x", "a")
    with pytest.raises(prompts.PromptError, match="no section '## c'"):
        prompts.render("x", "c")
    import os
    import time
    (tmp_path / "x.md").write_text("## a\nBye {name}\n")         # edits apply at once
    os.utime(tmp_path / "x.md", (time.time() + 5, time.time() + 5))
    assert prompts.render("x", "a", name="Kilo") == "Bye Kilo"
    prompts._cache.clear()
