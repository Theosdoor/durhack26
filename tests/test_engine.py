"""Engine rules: layout, pathfinding, interactions, collisions, cooking, orders, plans, text."""

import pytest

from undercooked.config import AgentSpec, Config, Orders
from undercooked.engine import Game, Kitchen, rules_text
from undercooked.replay import Frame, OrderState


def make_cfg(**kw) -> Config:
    """Scoring "orders" unless given: most of these tests exercise the order machinery
    (test_soups_scoring_* cover the default, scoring "soups")."""
    kw.setdefault("scoring", "orders")
    return Config(name="test", level="forced_coordination", agents=[AgentSpec(model="mockllm/model")] * 2,
                  **kw)


def game(level="forced_coordination", names=("Kilo", "Tango"), **kw) -> Game:
    return Game(Kitchen.load(level), list(names), make_cfg(**kw))


def run(g: Game, n: int) -> list:
    out = []
    for _ in range(n):
        out += g.tick()
    return out


def types(events, agent=None):
    return [e.type for e in events if agent is None or e.agent == agent]


def acts(events):
    """Event types caused by agents (drops kitchen events such as order_new)."""
    return [e.type for e in events if e.agent is not None]


# ------------------------------------------------------------------ kitchen
def test_kitchen_ids_in_reading_order():
    k = Kitchen.load("forced_coordination")
    assert k.objects["pot_1"].pos == (3, 0)
    assert k.objects["pot_2"].pos == (4, 1)
    assert k.objects["onion_crate_1"].pos == (0, 1)
    assert k.objects["onion_crate_2"].pos == (0, 2)
    assert k.objects["dish_rack_1"].pos == (0, 3)
    assert k.objects["hatch_1"].pos == (3, 4)
    assert k.objects["counter_2_2"].pos == (2, 2)
    assert k.starts == {1: (3, 1), 2: (1, 2)}
    assert [o.id for o in k.shared_counters()] == ["counter_2_1", "counter_2_2", "counter_2_3"]
    assert k.map_rows()[1] == "O X P"
    assert len(set(k.room_of.values())) == 2


def test_kitchen_load_by_path():
    from undercooked.config import LEVELS_DIR
    assert Kitchen.load(LEVELS_DIR / "practice.txt").name == "practice"


def test_pathfinding():
    k = Kitchen.load("forced_coordination")
    assert k.path((1, 1), [(1, 3)]) == [(1, 2), (1, 3)]
    assert k.path((1, 1), [(1, 1)]) == []
    assert k.path((1, 1), [(1, 3)], blocked=[(1, 2)]) is None
    assert k.path((1, 1), [(3, 1)]) is None                  # other room
    o = Kitchen.load("open_plan")
    p = o.path((1, 2), [(3, 2)], blocked=[(2, 2)])           # detour around an agent
    assert p is not None and len(p) == 4 and (2, 2) not in p


def test_agent_starts():
    g = game()
    assert g.agent("Kilo").pos == (3, 1) and g.agent("Tango").pos == (1, 2)
    with pytest.raises(ValueError):
        Game(Kitchen.load("practice"), ["A", "B"], make_cfg())   # practice has one start


# ------------------------------------------------------------- interactions
def setup_open(holding=None, pos=(2, 1), **kw):
    """One agent on open_plan, placed by hand."""
    g = game("open_plan", names=("Kilo",), **kw)
    a = g.agent("Kilo")
    a.pos, a.holding = pos, holding
    return g, a


def do(g, a, action):
    assert g.set_plan(a.name, [action]) is None
    ev = run(g, 1)
    return ev


def test_interact_onion_crate():
    g, a = setup_open(pos=(1, 1))
    ev = do(g, a, "interact(onion_crate_1)")
    assert a.holding == "onion" and types(ev, "Kilo") == ["pickup"]
    assert ev[-1].data == {"item": "onion", "at": "0,1"}
    ev = do(g, a, "interact(onion_crate_1)")
    assert a.holding == "onion" and types(ev, "Kilo") == ["fail"]
    assert "hands are full" in ev[-1].data["reason"]
    assert g.last_plan_result("Kilo").startswith("failed: your hands are full")


def test_interact_dish_rack():
    g, a = setup_open(pos=(5, 1))
    do(g, a, "interact(dish_rack_1)")
    assert a.holding == "dish"
    ev = do(g, a, "interact(dish_rack_1)")
    assert types(ev, "Kilo") == ["fail"]


def test_interact_counter_put_and_take():
    g, a = setup_open(holding="onion", pos=(1, 1))
    ev = do(g, a, "interact(counter_1_0)")
    assert a.holding is None and g.counters == {(1, 0): "onion"}
    assert ev[-1].type == "putdown" and ev[-1].data == {"item": "onion", "at": "1,0"}
    a.holding = "dish"
    ev = do(g, a, "interact(counter_1_0)")
    assert ev[-1].type == "fail" and "already holds an onion" in ev[-1].data["reason"]
    a.holding = None
    ev = do(g, a, "interact(counter_1_0)")
    assert a.holding == "onion" and g.counters == {} and ev[-1].type == "pickup"
    a.holding = None
    ev = do(g, a, "interact(counter_1_0)")
    assert ev[-1].type == "fail" and "is empty and you are holding nothing" in ev[-1].data["reason"]


def test_interact_pot_onions_cook_and_plate():
    g, a = setup_open(holding="onion")
    ev = do(g, a, "interact(pot_1)")
    assert g.pots["pot_1"].onions == 1 and a.holding is None
    assert ev[-1].type == "add_to_pot" and ev[-1].data["onions"] == 1
    g.pots["pot_1"].onions = 2
    a.holding = "onion"
    ev = do(g, a, "interact(pot_1)")
    assert types(ev, "Kilo") == ["add_to_pot", "cook_start"]
    assert g.frame().pots[0].state == "cooking" and g.frame().pots[0].ready_in == g.cfg.cook_s
    a.holding = "onion"                                          # onion into a cooking pot
    ev = do(g, a, "interact(pot_1)")
    assert ev[-1].type == "fail" and "already cooking" in ev[-1].data["reason"]
    a.holding = "dish"                                           # dish too early
    ev = do(g, a, "interact(pot_1)")
    assert ev[-1].type == "fail" and "not ready yet" in ev[-1].data["reason"]
    a.holding = None                                             # empty hands
    ev = do(g, a, "interact(pot_1)")
    assert ev[-1].type == "fail" and "holding nothing" in ev[-1].data["reason"]


def test_cooking_countdown_and_plate():
    g, a = setup_open(holding="onion")
    g.pots["pot_1"].onions = 2
    do(g, a, "interact(pot_1)")            # 3rd onion goes in during this tick
    start_t = g.t
    pot = g.pots["pot_1"]
    assert pot.ready_in == 20
    ev = run(g, 1)
    assert pot.ready_in == 19
    ev = run(g, 18)
    assert pot.ready_in == 1 and "cook_done" not in types(ev)
    ev = run(g, 1)
    assert pot.ready and pot.ready_in is None and g.t == start_t + 20
    assert types(ev) == ["cook_done"] and ev[0].data["pot"] == "pot_1"
    assert g.frame().pots[0].state == "ready"
    a.holding = "onion"                                          # pot with soup refuses onions
    assert do(g, a, "interact(pot_1)")[-1].type == "fail"
    a.holding = "soup"
    assert do(g, a, "interact(pot_1)")[-1].type == "fail"
    a.holding = "dish"
    ev = do(g, a, "interact(pot_1)")
    assert a.holding == "soup" and ev[-1].type == "plate"
    assert pot.onions == 0 and not pot.ready and g.frame().pots[0].state == "empty"


def test_onion_into_partial_pot_does_not_cook():
    g, a = setup_open(holding="onion")
    do(g, a, "interact(pot_1)")
    run(g, 30)
    assert g.pots["pot_1"].onions == 1 and not g.pots["pot_1"].cooking


def test_interact_hatch_serves_earliest_due():
    g, a = setup_open(holding="soup", pos=(5, 3))
    g.orders.append(OrderState(id=99, created_t=-50, due_t=10))
    ev = do(g, a, "interact(hatch_1)")
    assert a.holding is None and g.score == 20 and g.served == 1
    assert ev[-1].type == "serve" and ev[-1].data == {
        "item": "soup", "at": "6,3", "order": 99, "points": 20}
    assert [o.id for o in g.orders] == [1]
    a.holding = "dish"
    assert "only a soup" in do(g, a, "interact(hatch_1)")[-1].data["reason"]
    a.holding = None
    assert do(g, a, "interact(hatch_1)")[-1].type == "fail"
    g.orders.clear()
    a.holding = "soup"
    ev = do(g, a, "interact(hatch_1)")
    assert ev[-1].type == "fail" and "no open order" in ev[-1].data["reason"]
    assert a.holding == "soup" and g.score == 20


# -------------------------------------------------------------- movement
def test_interact_walks_then_acts():
    g = game()
    # Tango at (1,2) -> nearest onion crate is onion_crate_2 at (0,2), already adjacent
    assert g.set_plan("Tango", ["interact(onion_crate)", "interact(counter_2_1)"]) is None
    ev = run(g, 1)
    assert ev[-1].type == "pickup" and ev[-1].data["at"] == "0,2" and ev[-1].t == 1
    ev = run(g, 1)      # walk to (1,1)
    assert g.agent("Tango").pos == (1, 1) and types(ev, "Tango") == []
    ev = run(g, 1)
    assert ev[-1].type == "putdown" and ev[-1].data["at"] == "2,1" and ev[-1].t == 3
    assert g.last_plan_result("Tango") == "done" and g.agent("Tango").status == "idle"


def test_move_to_and_wait_timing():
    g = game()
    assert g.set_plan("Tango", ["move_to(1, 3)", "wait(3)", "move_to(1, 1)"]) is None
    run(g, 1)
    assert g.agent("Tango").pos == (1, 3)
    run(g, 3)
    assert g.agent("Tango").pos == (1, 3) and g.frame().agents[1].plan == ["move_to(1, 1)"]
    run(g, 1)
    assert g.agent("Tango").pos == (1, 2)


def test_zero_cost_move_to_then_next_action_same_tick():
    g = game()
    assert g.set_plan("Tango", ["move_to(1, 2)", "move_to(1, 3)"]) is None
    run(g, 1)
    assert g.agent("Tango").pos == (1, 3)


def place(g, **positions):
    for name, pos in positions.items():
        g.agent(name).pos = pos


def test_collision_same_tile():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(1, 2), Tango=(3, 2))
    g.set_plan("Kilo", ["move(right)"])
    g.set_plan("Tango", ["move(left)"])
    ev = run(g, 1)
    assert g.agent("Kilo").pos == (1, 2) and g.agent("Tango").pos == (3, 2)
    assert acts(ev) == ["bump", "bump"]
    assert {e.data["reason"] for e in ev if e.agent} == {"blocked by Kilo", "blocked by Tango"}


def test_collision_swap():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(1, 2), Tango=(2, 2))
    g.set_plan("Kilo", ["move(right)"])
    g.set_plan("Tango", ["move(left)"])
    ev = run(g, 1)
    assert g.agent("Kilo").pos == (1, 2) and g.agent("Tango").pos == (2, 2)
    assert acts(ev) == ["bump", "bump"]


def test_collision_into_standing_agent_and_following():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(1, 2), Tango=(2, 2))
    g.set_plan("Kilo", ["move(right)"])
    g.set_plan("Tango", ["wait(5)"])
    ev = run(g, 1)
    assert g.agent("Kilo").pos == (1, 2) and acts(ev) == ["bump"]
    assert ev[-1].agent == "Kilo" and ev[-1].data["at"] == "2,2"
    g.set_plan("Kilo", ["move(right)"])        # following: the leader moves away first
    g.set_plan("Tango", ["move(right)"])
    run(g, 1)
    assert g.agent("Kilo").pos == (2, 2) and g.agent("Tango").pos == (3, 2)


def test_path_replans_around_other_agent():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(1, 2), Tango=(2, 2))
    g.set_plan("Tango", ["wait(10)"])
    g.set_plan("Kilo", ["move_to(3, 2)"])
    ev = run(g, 4)
    assert g.agent("Kilo").pos == (3, 2) and "bump" not in types(ev)


def test_blocked_three_seconds_fails_plan():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(2, 2), Tango=(2, 1))       # Tango stands on pot_1's only access tile
    g.agent("Kilo").holding = "onion"
    g.set_plan("Tango", ["wait(10)"])
    g.set_plan("Kilo", ["interact(pot_1)"])
    ev = run(g, 3)
    assert types(ev, "Kilo") == ["bump", "bump", "bump", "fail"]
    assert ev[-1].data["reason"] == "blocked by Tango"
    assert g.last_plan_result("Kilo") == "failed: blocked by Tango"
    assert g.needs_decision() == ["Kilo"]


def test_head_on_clash_resolves_by_giving_way():
    g = game("open_plan", names=("Kilo", "Tango"))
    place(g, Kilo=(1, 2), Tango=(3, 2))
    g.set_plan("Kilo", ["move_to(5, 2)"])
    g.set_plan("Tango", ["move_to(1, 1)"])
    ev = run(g, 8)
    assert g.agent("Kilo").pos == (5, 2) and g.agent("Tango").pos == (1, 1)
    assert "fail" not in types(ev)


# ------------------------------------------------------------------ orders
def test_orders_arrive_expire_and_score():
    g = game(names=("Kilo",))
    assert [(o.id, o.created_t, o.due_t) for o in g.orders] == [(1, 0, 60)]
    ev = g.tick()
    assert [(e.type, e.t, e.data) for e in ev] == [("order_new", 0, {"order": 1})]
    ev = run(g, 29)
    assert g.t == 30 and [e.type for e in ev] == ["order_new"] and ev[0].t == 30
    assert [(o.id, o.due_t) for o in g.orders] == [(1, 60), (2, 90)]
    ev = run(g, 30)
    assert g.t == 60
    assert [(e.type, e.data) for e in ev] == [
        ("order_missed", {"order": 1, "points": -10}), ("order_new", {"order": 3})]
    assert g.score == -10 and g.missed == 1 and [o.id for o in g.orders] == [2, 3]


def test_orders_respect_max_open():
    g = game(names=("Kilo",), orders=Orders(every_s=10, due_s=100, max_open=2))
    run(g, 25)
    assert [o.id for o in g.orders] == [1, 2]     # the t=20 arrival was skipped
    f = g.frame()
    assert isinstance(f, Frame) and f.t == 25 and len(f.orders) == 2


def test_soups_scoring_has_no_orders_and_every_soup_scores():
    g = game(names=("Kilo",), scoring="soups")
    assert g.orders == [] and not g.uses_orders and g.accepts_soup
    ev = run(g, 200)
    assert not [e for e in ev if e.type.startswith("order")] and g.score == 0 and g.missed == 0
    assert g.frame().orders == []
    text = g.state_text("Kilo", unread_posts=0, notepad=None, last_plan_result="none yet")
    assert "Orders" not in text and "order" not in text.lower()


def test_soups_scoring_serves_without_an_order():
    g, a = setup_open(holding="soup", pos=(5, 3), scoring="soups")
    ev = do(g, a, "interact(hatch_1)")
    serve = [e for e in ev if e.type == "serve"]
    assert serve and serve[0].data == {"item": "soup", "at": "6,3", "points": 20}
    assert (g.score, g.served, g.missed, a.holding) == (20, 1, 0, None)
    ev = do(g, a, "wait(1)")
    a.holding = "soup"
    ev = do(g, a, "interact(hatch_1)")
    assert (g.score, g.served) == (40, 2)


def test_serve_on_due_second_counts():
    g, a = setup_open(holding="soup", pos=(5, 3))
    g.orders[:] = [OrderState(id=1, created_t=0, due_t=1)]
    ev = do(g, a, "interact(hatch_1)")
    assert acts(ev) == ["serve"] and g.missed == 0 and "order_missed" not in types(ev)


# ------------------------------------------------------------------- plans
def test_plan_unreachable_target():
    g = game()
    err = g.set_plan("Tango", ["interact(pot_1)"])
    assert err == "pot_1 at (3,0) is not reachable from your room"
    assert g.last_plan_result("Tango") == f"failed: {err}"
    assert g.set_plan("Tango", ["interact(counter_0_0)"]) == \
        "counter_0_0 at (0,0) is not reachable from your room"
    assert g.set_plan("Tango", ["interact(pot)"]) == "no pot is reachable from your room"
    assert g.set_plan("Tango", ["move_to(3, 2)"]) == "(3,2) is not reachable from your room"
    assert "Tango" in g.needs_decision()


def test_plan_errors_and_partial_plans():
    g = game()
    assert g.set_plan("Tango", []).startswith("empty plan")
    assert "could not parse" in g.set_plan("Tango", ["@@@"])
    # valid prefix runs, then the bad step fails the plan with its reason
    assert g.set_plan("Tango", ["move_to(1, 3)", "interact(pot_9)"]) is None
    ev = run(g, 2)
    assert g.agent("Tango").pos == (1, 3)
    assert ev[-1].type == "fail" and "pot_9 does not exist" in ev[-1].data["reason"]


def test_plan_truncated_to_max_len():
    g = game(max_plan_len=2)
    assert g.set_plan("Tango", ["wait(1)"] * 5) is None
    assert len(g.frame().agents[1].plan) == 2


def test_move_step_into_counter_fails():
    g = game()
    g.set_plan("Tango", ["move(right)"])
    ev = run(g, 1)
    assert ev[-1].type == "fail"
    assert ev[-1].data["reason"] == "cannot move right from (1,2): (2,2) is counter_2_2"


def test_needs_decision_and_status():
    g = game()
    assert g.needs_decision() == ["Kilo", "Tango"]
    g.set_status("Kilo", "deciding")
    assert g.needs_decision() == ["Tango"]
    g.set_plan("Tango", ["wait(2)"])
    assert g.agent("Tango").status == "acting" and g.needs_decision() == []
    run(g, 2)
    assert g.needs_decision() == ["Tango"] and g.agent("Tango").status == "idle"
    g.set_plan("Tango", ["wait(5)"])
    g.clear_plan("Tango")
    assert g.last_plan_result("Tango") == "stopped" and g.needs_decision() == ["Tango"]
    with pytest.raises(ValueError):
        g.set_status("Tango", "napping")  # type: ignore[arg-type]


def test_events_carry_phase_round_and_frame_fields():
    g = Game(Kitchen.load("practice"), ["Kilo"], make_cfg(), phase="practice", round=0)
    assert g.duration == 60
    g.set_plan("Kilo", ["interact(onion_crate)"])
    ev = g.tick()
    assert all(e.phase == "practice" and e.round == 0 for e in ev)
    f = g.frame()
    assert f.phase == "practice" and f.round == 0 and f.t == 1
    assert f.agents[0].holding == "onion" and f.agents[0].status == "idle"
    assert f.pots[0].id == "pot_1" and f.pots[0].state == "empty"


def test_deterministic():
    from undercooked.bots import run as run_bots, split_room_pair

    def once():
        g = game()
        return [e.model_dump() for e in run_bots(g, split_room_pair(g), 120)]
    assert once() == once()


# -------------------------------------------------------------------- text
EXPECTED = """\
Time 37 / 180 s · Score 20 · Round 2 of 3
Orders: onion soup (due 52 s), onion soup (due 70 s)

  01234
0 XXXPX
1 O XKP
2 OTX X
3 D X X
4 XXXSX

You (Tango, T): at (1,2), holding nothing. Last plan: done.
Kilo (K): at (3,1), holding an onion.
pot_1 (3,0): 2 onions, not cooking. pot_2 (4,1): cooking, ready in 12 s.
Counters: (2,1) onion, (2,2) empty, (2,3) empty.
Board: 1 unread post.
Your notepad: "..."\
"""


def test_state_text_forced_coordination_matches_spec():
    g = Game(Kitchen.load("forced_coordination"), ["Kilo", "Tango"], make_cfg(counters_shown="shared"), round=2)
    g.t, g.score = 37, 20
    g.orders[:] = [OrderState(id=3, created_t=10, due_t=70), OrderState(id=2, created_t=-8,
                                                                         due_t=52)]
    g.agent("Kilo").holding = "onion"
    g.pots["pot_1"].onions = 2
    g.pots["pot_2"].onions, g.pots["pot_2"].ready_in = 3, 12
    g.counters[(2, 1)] = "onion"
    text = g.state_text("Tango", unread_posts=1, notepad="...", last_plan_result="done")
    assert text == EXPECTED


def test_state_text_variants():
    g = Game(Kitchen.load("practice"), ["Kilo"], make_cfg(), phase="practice", round=0)
    g.pots["pot_1"].onions, g.pots["pot_1"].ready = 3, True
    text = g.state_text("Kilo", unread_posts=0, notepad=None,
                        last_plan_result="failed: blocked by Tango")
    assert text.splitlines()[0] == "Time 0 / 60 s · Score 0 · Practice"
    assert "Last plan: failed: blocked by Tango." in text
    assert "pot_1 (1,0): soup ready to plate." in text
    assert "Counters: all empty." in text and "Board: no unread posts." in text
    assert "notepad" not in text
    g.orders.clear()
    assert "Orders: none open." in g.state_text("Kilo", unread_posts=2, notepad="",
                                                last_plan_result="done")


def test_rules_text():
    cfg = make_cfg(cook_s=25)
    text = rules_text(cfg)
    for s in ("25 s", "+20 points", "-10 points", "interact(<id>)",
              "move_to(x, y)", "wait(<seconds>)", "onion_crate_1", "counter_2_1", "1 to 5"):
        assert s in text, s
    low = text.lower()
    assert "role" not in low and "strateg" not in low
