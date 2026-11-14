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
    assert parse_action("get_onion") == ("get_onion", 0)
    assert parse_action("wait(3)") == ("wait", 3)
    assert parse_action("wait(99)") == ("wait", 10)
    assert parse_action("fly") is None


def test_scripted_soup_scores():
    k = Kitchen("forced_coordination", ["Kilo", "Lima"])
    right, left = k.players
    assert k.shared_counters == [(2, 1), (2, 2), (2, 3)]
    assert k.check(right, "get_onion") is not None      # nothing on the counters yet
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
