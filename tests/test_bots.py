"""Scripted bots driving the engine through set_plan, end to end."""

from undercooked.bots import SoloBot, run, split_room_pair, SplitCookBot, SplitPasserBot
from undercooked.config import AgentSpec, Config
from undercooked.engine import Game, Kitchen

CFG = Config(name="test", level="forced_coordination", agents=[AgentSpec(model="mockllm/model")] * 2)


def test_split_room_pair_serves_within_a_round():
    g = Game(Kitchen.load("forced_coordination"), ["Kilo", "Tango"], CFG)
    bots = split_room_pair(g)
    assert isinstance(bots["Kilo"], SplitCookBot) and isinstance(bots["Tango"], SplitPasserBot)
    events = run(g, bots, 180)
    assert g.t == 180 and g.served >= 1
    assert any(e.type == "serve" for e in events)
    # items really crossed the middle counters
    assert any(e.type == "putdown" and e.agent == "Tango" for e in events)
    assert any(e.type == "pickup" and e.agent == "Kilo" and e.data["at"].startswith("2,")
               for e in events)


def test_single_solo_bot_cannot_score_on_forced_coordination():
    g = Game(Kitchen.load("forced_coordination"), ["Kilo"], CFG)
    run(g, {"Kilo": SoloBot("Kilo")}, 180)
    assert g.served == 0 and g.score < 0


def test_solo_bot_serves_on_practice_within_60s():
    g = Game(Kitchen.load("practice"), ["Kilo"], CFG, phase="practice", round=0)
    events = run(g, {"Kilo": SoloBot("Kilo")})
    assert g.t == 60 and g.served >= 1
    assert next(e.t for e in events if e.type == "serve") <= 60


def test_two_solo_bots_on_open_plan_score():
    g = Game(Kitchen.load("open_plan"), ["Kilo", "Tango"], CFG)
    run(g, {n: SoloBot(n) for n in ("Kilo", "Tango")}, 180)
    assert g.served >= 1
