"""No seat or list index carries a built-in advantage (the review's seat confound, R1).

- the engine resolves gives before takes, so a same-second handover works whoever is who;
- remaining ties (same-pass contention, step planning, who gives way) use a per-second order
  from the game's seeded RNG, so over many seeds neither agent index wins them systematically;
- seats (start positions) and each chat's first speaker are seeded draws per season / round.
"""

from __future__ import annotations

import pytest

from undercooked.config import AgentSpec, Config
from undercooked.engine import Game, Kitchen
from undercooked.season import assign_seats, first_speaker

SEEDS = range(400)


def cfg(**kw) -> Config:
    return Config(name="fair", level="forced_coordination", agents=[AgentSpec(model="mockllm/model")] * 2,
                  **kw)


def game(level: str, names: list[str], seed: int | str | None = None) -> Game:
    return Game(Kitchen.load(level), names, cfg(), seed=seed)


def share_of_index0(outcomes: list[int]) -> float:
    """Fraction of contests won by agent index 0 (outcomes: winner index per seed)."""
    return outcomes.count(0) / len(outcomes)


# ------------------------------------------------------------------ handover (R1b)
@pytest.mark.parametrize("putter_index", [0, 1])
def test_same_second_handover_works_in_both_index_orders(putter_index):
    """forced_coordination: one agent puts an onion on counter_2_1 while the other picks it up, in the
    same second. Before the fix the pick-up failed whenever the picker came first in the list."""
    for seed in range(50):                     # every tie order the RNG might draw
        names = ["Kilo", "Tango"]
        g = game("forced_coordination", names, seed)
        putter, picker = names[putter_index], names[1 - putter_index]
        g.agent(putter).pos, g.agent(picker).pos = (1, 1), (3, 1)   # either side of (2,1)
        g.agent(putter).holding = "onion"
        assert g.set_plan(putter, ["interact(counter_2_1)"]) is None
        assert g.set_plan(picker, ["interact(counter_2_1)"]) is None
        ev = g.tick()
        assert [(e.type, e.agent) for e in ev if e.agent] == [("putdown", putter),
                                                              ("pickup", picker)], seed
        assert g.agent(picker).holding == "onion" and g.agent(putter).holding is None
        assert g.counters == {}


# ------------------------------------------------------------------ ties (R1b)
def counter_pickup_winner(seed: int) -> int:
    """Both empty-handed, both take the one onion on counter_2_1 in the same second."""
    names = ["Kilo", "Tango"]
    g = game("forced_coordination", names, seed)
    g.agent("Kilo").pos, g.agent("Tango").pos = (3, 1), (1, 1)
    g.counters[(2, 1)] = "onion"
    for n in names:
        g.set_plan(n, ["interact(counter_2_1)"])
    g.tick()
    [winner] = [i for i, n in enumerate(names) if g.agent(n).holding == "onion"]
    return winner


def counter_putdown_winner(seed: int) -> int:
    """Both hold an onion and put it on the empty counter_2_1 in the same second."""
    names = ["Kilo", "Tango"]
    g = game("forced_coordination", names, seed)
    g.agent("Kilo").pos, g.agent("Tango").pos = (3, 1), (1, 1)
    for n in names:
        g.agent(n).holding = "onion"
        g.set_plan(n, ["interact(counter_2_1)"])
    g.tick()
    [winner] = [i for i, n in enumerate(names) if g.agent(n).holding is None]
    return winner


def same_target_winner(seed: int) -> int:
    """open_plan: both move_to the free tile (2,2) between them. Step planning is asymmetric
    (the later agent plans around the earlier one's claim), so the first in the second's
    order steps in and the other waits."""
    names = ["Kilo", "Tango"]
    g = game("open_plan", names, seed)
    g.agent("Kilo").pos, g.agent("Tango").pos = (1, 2), (3, 2)
    for n in names:
        g.set_plan(n, ["move_to(2, 2)"])
    g.tick()
    [winner] = [i for i, n in enumerate(names) if g.agent(n).pos == (2, 2)]
    return winner


def head_on_winner(seed: int) -> int:
    """open_plan: both step into (2,2) in the same second (a clash: neither moves, and one
    gives way next second). The winner is the one standing on (2,2) after 2 seconds."""
    names = ["Kilo", "Tango"]
    g = game("open_plan", names, seed)
    g.agent("Kilo").pos, g.agent("Tango").pos = (1, 2), (3, 2)
    g.set_plan("Kilo", ["move(right)"])
    g.set_plan("Tango", ["move(left)"])
    g.tick()
    assert (g.agent("Kilo").pos, g.agent("Tango").pos) == ((1, 2), (3, 2))   # bumped
    g.tick()
    [winner] = [i for i, n in enumerate(names) if g.agent(n).pos == (2, 2)]
    return winner


CONTESTS = {
    "counter pick-up (same take pass)": counter_pickup_winner,
    "counter put-down (same give pass)": counter_putdown_winner,
    "step planning (same target tile)": same_target_winner,
    "giving way after a clash": head_on_winner,
}


@pytest.mark.parametrize("name", list(CONTESTS))
def test_no_index_wins_contested_ties_systematically(name):
    """Over 400 seeds each agent index wins about half of the ties (binomial sd 2.5 points;
    the bounds are 4 sd). Before the fix index 0 won every one of them."""
    contest = CONTESTS[name]
    winners = [contest(seed) for seed in SEEDS]
    share = share_of_index0(winners)
    assert 0.4 < share < 0.6, f"{name}: index 0 won {share:.0%}"
    assert winners == [contest(seed) for seed in SEEDS]        # deterministic per seed


def test_tie_order_is_deterministic_per_seed_and_defaults_from_config():
    def play(seed):
        g = game("open_plan", ["Kilo", "Tango"], seed)
        g.agent("Kilo").pos, g.agent("Tango").pos = (1, 2), (3, 2)
        out = []
        for _ in range(30):
            for n in g.needs_decision():
                g.set_plan(n, ["move_to(2, 2)", "move_to(5, 1)", "move_to(1, 3)"])
            out += [(e.t, e.type, e.agent) for e in g.tick()]
        return out, [a.pos for a in g.agents]

    assert play(7) == play(7)
    assert play(None) == play(None)
    assert len({str(play(s)) for s in range(10)}) > 1


# ------------------------------------------------------------------ seats and speakers
def test_seat_assignment_is_seeded_and_varies():
    for seed in range(5):
        for epoch in range(1, 30):
            seats = assign_seats(seed, epoch)
            assert sorted(seats) == [1, 2] and seats == assign_seats(seed, epoch)
    across_epochs = [assign_seats(0, e)[0] for e in range(1, 41)]
    assert set(across_epochs) == {1, 2}
    grid = [assign_seats(s, e)[0] for s in range(20) for e in range(1, 21)]
    assert 0.4 < grid.count(1) / len(grid) < 0.6           # agents[0] starts at 1 about half


def test_first_speaker_is_seeded_and_varies_across_rounds_and_seeds():
    assert first_speaker(3, 2, 1) == first_speaker(3, 2, 1)
    assert {first_speaker(0, 1, r) for r in range(1, 11)} == {0, 1}     # across rounds
    assert {first_speaker(s, 1, 1) for s in range(10)} == {0, 1}        # across seeds
    grid = [first_speaker(s, e, r) for s in range(10) for e in range(1, 6) for r in range(1, 9)]
    assert 0.4 < grid.count(0) / len(grid) < 0.6


def test_season_seats_place_agents_at_their_start_digits():
    from undercooked.providers import agent_model
    from undercooked.season import Season

    c = cfg(seed=0)
    for epoch in range(1, 9):
        s = Season(c, [agent_model(spec, c) for spec in c.agents], epoch)
        assert [a.seat for a in s.agents] == assign_seats(0, epoch)
        g = Game(Kitchen.load("forced_coordination"), s.seated, c)
        for a in s.agents:
            assert g.agent(a.name).pos == g.kitchen.starts[a.seat]
