"""Scripted, deterministic bots for testing and level tuning.

Bots read the game state directly but act only through `Game.set_plan` with action strings, the
same API the LLM agents use. Each `decide` returns a short plan (1-2 actions); the bot is asked
again when the plan ends or fails.

    game = Game(Kitchen.load("open_plan"), ["Bravo", "Kilo"], cfg)
    bots = {"Bravo": SoloBot("Bravo"), "Kilo": SoloBot("Kilo")}
    run(game, bots)              # plays until game.over; returns all events
"""

from __future__ import annotations

from typing import Protocol

from undercooked.engine.actions import WAIT_MAX
from undercooked.engine.game import POT_CAPACITY, Game, Pot
from undercooked.engine.kitchen import Pos
from undercooked.replay import Event

IDLE = ["wait(1)"]
DISH_LEAD_S = 4   # fetch a dish when a pot will be ready within this many seconds


class Bot(Protocol):
    name: str

    def decide(self, game: Game) -> list[str]: ...


# ------------------------------------------------------------------ helpers
def _reachable(game: Game, name: str, obj_id: str) -> bool:
    return game.kitchen.reachable(game.agent(name).pos, obj_id)


def _dist(game: Game, name: str, obj_id: str) -> int:
    d = game.kitchen.distance(game.agent(name).pos, game.kitchen.access_tiles(obj_id))
    return 10_000 if d is None else d


def _nearest(game: Game, name: str, ids: list[str]) -> str | None:
    ids = [i for i in ids if _reachable(game, name, i)]
    return min(ids, key=lambda i: (_dist(game, name, i), i)) if ids else None


def _pots(game: Game, name: str, only: list[str] | None = None) -> list[Pot]:
    return [p for p in game.pots.values()
            if _reachable(game, name, p.id) and (only is None or p.id in only)]


def _fillable(p: Pot) -> bool:
    return not p.cooking and not p.ready and p.onions < POT_CAPACITY


def _counter_id(pos: Pos) -> str:
    return f"counter_{pos[0]}_{pos[1]}"


def _empty_counter(game: Game, name: str, among: list[Pos] | None = None) -> str | None:
    if among is None:
        among = [o.pos for o in game.kitchen.usable_counters()]
    free = [_counter_id(p) for p in among if p not in game.counters]
    return _nearest(game, name, free)


def _counter_with(game: Game, name: str, item: str, among: list[Pos]) -> str | None:
    return _nearest(game, name, [_counter_id(p) for p in among if game.counters.get(p) == item])


def _kind_reachable(game: Game, name: str, kind: str) -> str | None:
    return _nearest(game, name, [o.id for o in game.kitchen.of_kind(kind)])


def _pot_to_fill(game: Game, name: str, only: list[str] | None = None) -> Pot | None:
    """Fill one pot at a time: the fillable pot with the most onions, then the nearest."""
    cands = [p for p in _pots(game, name, only) if _fillable(p)]
    return min(cands, key=lambda p: (-p.onions, _dist(game, name, p.id), p.id)) if cands else None


def _pot_to_plate(game: Game, name: str, lead: int = 0,
                  only: list[str] | None = None) -> Pot | None:
    """A ready pot, else the cooking pot that is ready soonest (within `lead` seconds)."""
    ready = [p for p in _pots(game, name, only) if p.ready]
    if ready:
        return min(ready, key=lambda p: (_dist(game, name, p.id), p.id))
    cooking = [p for p in _pots(game, name, only) if p.cooking and p.ready_in <= lead]  # type: ignore[operator]
    return min(cooking, key=lambda p: (p.ready_in, p.id)) if cooking else None


def _step_aside(game: Game, name: str) -> list[str] | None:
    """After a "blocked by" failure, step to a free neighbouring tile, preferring tiles that are
    not next to any object (so we stop standing where the other agent needs to be)."""
    me = game.agent(name)
    if not me.result.startswith("failed: blocked by"):
        return None
    k = game.kitchen
    taken = {a.pos for a in game.agents if a.name != name}
    free = [p for p in k.floor_neighbours(me.pos) if p not in taken]
    if not free:
        return None
    busy = {p for o in k.objects.values() if o.kind not in ("counter", "bin") for p in k.access_tiles(o.id)}
    x, y = min(free, key=lambda p: (p in busy, p[1] < me.pos[1], p))
    return [f"move_to({x}, {y})"]


def _plate(game: Game, name: str, pot: Pot) -> list[str]:
    """Holding a dish: plate `pot` now, or go next to it and wait exactly until it is ready
    (a pot with ready_in = k is ready after k ticks; the interaction comes in the next one)."""
    if pot.ready:
        return [f"interact({pot.id})"]
    if _dist(game, name, pot.id) > 0:
        return [f"move_to({pot.id})"]
    wait = min(pot.ready_in, WAIT_MAX)  # type: ignore[type-var]
    return [f"wait({wait})"] + ([f"interact({pot.id})"] if wait == pot.ready_in else [])


def _idle(game: Game, name: str) -> list[str]:
    """Nothing to do: if standing next to a pot, crate, rack or hatch (where the teammate may need
    to stand), walk to the nearest free floor tile that isn't; else wait a second."""
    me = game.agent(name)
    k = game.kitchen
    pot_tiles = {p for o in k.of_kind("pot") for p in k.access_tiles(o.id)}
    busy = {p for o in k.objects.values() if o.kind not in ("counter", "bin") for p in k.access_tiles(o.id)}

    def badness(p: Pos) -> tuple[bool, bool]:      # pot tiles are the most contended
        return (p in pot_tiles, p in busy)

    if badness(me.pos) == (False, False):
        return IDLE
    taken = {a.pos for a in game.agents if a.name != name}
    room = k.room_of[me.pos]
    better = [p for p in k.floor if k.room_of[p] == room and p not in taken
              and badness(p) < badness(me.pos)]
    for level in sorted({badness(p) for p in better}):
        path = k.path(me.pos, [p for p in better if badness(p) == level], blocked=taken)
        if path:
            x, y = path[-1]
            return [f"move_to({x}, {y})"]
    return IDLE


# --------------------------------------------------------------------- bots
class SoloBot:
    """Does everything itself with whatever it can reach: onions -> pot -> dish -> hatch.

    `pots` optionally restricts the bot to those pot ids (e.g. two bots splitting the pots)."""

    def __init__(self, name: str, pots: list[str] | None = None):
        self.name = name
        self.pots = pots

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        hatch = _kind_reachable(game, self.name, "hatch")
        if me.holding == "soup":
            return [f"interact({hatch})"] if hatch and game.accepts_soup else IDLE
        if me.holding == "dish":
            pot = _pot_to_plate(game, self.name, lead=10_000, only=self.pots)
            if pot is None:
                spot = _empty_counter(game, self.name)
                return [f"interact({spot})"] if spot else IDLE
            if pot.ready:
                return [f"interact({pot.id})"]
            return _plate(game, self.name, pot)
        if me.holding == "onion":
            pot = _pot_to_fill(game, self.name, self.pots)
            if pot is not None:
                return [f"interact({pot.id})"]
            spot = _empty_counter(game, self.name)
            return [f"interact({spot})"] if spot else IDLE
        # empty hands
        crate = _kind_reachable(game, self.name, "onion_crate")
        rack = _kind_reachable(game, self.name, "dish_rack")
        plate = _pot_to_plate(game, self.name, lead=DISH_LEAD_S, only=self.pots)
        if plate is not None and rack and hatch:
            return [f"interact({rack})", f"interact({plate.id})"]
        fill = _pot_to_fill(game, self.name, self.pots)
        if fill is not None and crate:
            return [f"interact({crate})", f"interact({fill.id})"]
        if rack and hatch and any(p.cooking for p in _pots(game, self.name, self.pots)):
            return [f"interact({rack})"]
        return IDLE


def _shared(game: Game) -> list[Pos]:
    return [o.pos for o in game.kitchen.shared_counters()]


def _own_counter(game: Game, name: str) -> str | None:
    """A free counter to park an item on: one only this agent's room touches, else any."""
    shared = set(_shared(game))
    own = [o.pos for o in game.kitchen.usable_counters() if o.pos not in shared]
    return _empty_counter(game, name, own) or _empty_counter(game, name)


def _mate_reaches(game: Game, name: str, kind: str) -> bool:
    """Can any other agent reach an object of this kind?"""
    return any(_kind_reachable(game, a.name, kind) for a in game.agents if a.name != name)


class SplitPasserBot:
    """Split levels, the room with the onion crates and no pot. Keeps the pass-through counters
    stocked with onions (and a dish, if the cook has no dish rack), and serves any soup the cook
    passes back if this room has the hatch (split_room); otherwise the cook serves
    (forced_coordination)."""

    def __init__(self, name: str, max_onions: int = 2):
        self.name = name
        self.max_onions = max_onions

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        shared = _shared(game)
        hatch = _kind_reachable(game, self.name, "hatch")
        if me.holding == "soup" and hatch:
            return [f"interact({hatch})"] if game.accepts_soup else IDLE
        if me.holding is not None:
            spot = _empty_counter(game, self.name, shared)
            return [f"interact({spot})"] if spot else IDLE
        soup = _counter_with(game, self.name, "soup", shared)
        if soup and hatch:
            return [f"interact({soup})"] + ([f"interact({hatch})"] if game.accepts_soup else [])
        onions = sum(1 for p in shared if game.counters.get(p) == "onion")
        dishes = sum(1 for p in shared if game.counters.get(p) == "dish")
        free = sum(1 for p in shared if p not in game.counters)
        if free == 0:
            return IDLE
        pots = list(game.pots.values())
        onions_in_pots = sum(p.onions for p in pots if _fillable(p))
        soon = any(p.ready or p.cooking for p in pots) or onions_in_pots + onions >= POT_CAPACITY
        crate = _kind_reachable(game, self.name, "onion_crate")
        rack = _kind_reachable(game, self.name, "dish_rack")
        spot = _empty_counter(game, self.name, shared)
        needs_dish = rack and not _mate_reaches(game, self.name, "dish_rack")
        if needs_dish and dishes == 0 and soon:
            return [f"interact({rack})", f"interact({spot})"]
        # keep one counter free (for the dish, or for the soup coming back)
        if onions < self.max_onions and (free > 1 or dishes > 0) and crate:
            return [f"interact({crate})", f"interact({spot})"]
        return IDLE


class SplitCookBot:
    """Split levels, the room with the pots: takes onions off the pass-through counters, cooks,
    plates with a dish from its own rack (split_room) or from the counters (forced_coordination),
    then serves if it can reach a hatch, else puts the soup on a counter for the passer."""

    def __init__(self, name: str):
        self.name = name

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        shared = _shared(game)
        hatch = _kind_reachable(game, self.name, "hatch")
        if me.holding == "soup":
            if hatch:
                return [f"interact({hatch})"] if game.accepts_soup else IDLE
            spot = _empty_counter(game, self.name, shared)
            return [f"interact({spot})"] if spot else IDLE
        if me.holding == "dish":
            pot = _pot_to_plate(game, self.name, lead=10_000)
            if pot is None:
                spot = _own_counter(game, self.name)
                return [f"interact({spot})"] if spot else IDLE
            return _plate(game, self.name, pot)
        if me.holding == "onion":
            pot = _pot_to_fill(game, self.name)
            if pot is not None:
                return [f"interact({pot.id})"]
            spot = _own_counter(game, self.name)
            return [f"interact({spot})"] if spot else IDLE
        plate = _pot_to_plate(game, self.name, lead=DISH_LEAD_S)
        dish = (_kind_reachable(game, self.name, "dish_rack")
                or _counter_with(game, self.name, "dish", shared))
        if plate is not None and dish:
            return [f"interact({dish})", f"interact({plate.id})"]
        fill = _pot_to_fill(game, self.name)
        onion = _counter_with(game, self.name, "onion", shared)
        if fill is not None and onion:
            return [f"interact({onion})", f"interact({fill.id})"]
        return IDLE


def self_sufficient(game: Game, name: str) -> bool:
    """Can this agent make and serve a soup alone (crate, pot, rack and hatch in reach)?"""
    return all(_kind_reachable(game, name, k) for k in ("onion_crate", "pot", "dish_rack", "hatch"))


def split_room_pair(game: Game) -> dict[str, Bot]:
    """Cook for whoever can reach a pot, passer for whoever can't (split levels)."""
    bots: dict[str, Bot] = {}
    for a in game.agents:
        can_cook = any(game.kitchen.reachable(a.pos, p) for p in game.pots)
        bots[a.name] = SplitCookBot(a.name) if can_cook else SplitPasserBot(a.name)
    return bots


class FillerBot:
    """Cooperative pair on a level where both agents can do everything: this one only fetches
    onions and fills pots (one pot at a time); its partner (PlaterBot) plates and serves."""

    def __init__(self, name: str):
        self.name = name

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        if me.holding == "onion":
            pot = _pot_to_fill(game, self.name)
            return [f"interact({pot.id})"] if pot is not None else _idle(game, self.name)
        if me.holding is not None:                   # not its job: park it
            spot = _own_counter(game, self.name)
            return [f"interact({spot})"] if spot else _idle(game, self.name)
        crate = _kind_reachable(game, self.name, "onion_crate")
        fill = _pot_to_fill(game, self.name)
        if fill is not None and crate:
            return [f"interact({crate})", f"interact({fill.id})"]
        return _idle(game, self.name)


class PlaterBot:
    """FillerBot's partner: fetches a dish as soon as a pot is cooking (or ready), waits at the
    pot, plates and serves. With nothing to plate it fills pots too if `helps_fill`."""

    def __init__(self, name: str, helps_fill: bool = False):
        self.name = name
        self.helps_fill = helps_fill

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        hatch = _kind_reachable(game, self.name, "hatch")
        if me.holding == "soup":
            return [f"interact({hatch})"] if hatch and game.accepts_soup else _idle(game, self.name)
        if me.holding == "dish":
            pot = _pot_to_plate(game, self.name, lead=10_000)
            if pot is None:
                return _idle(game, self.name)        # keep the dish for the next pot
            if pot.ready:
                return [f"interact({pot.id})"]
            if pot.ready_in > _dist(game, self.name, pot.id) + 1:  # type: ignore[operator]
                return _idle(game, self.name)        # don't stand at the pot meanwhile
            return _plate(game, self.name, pot)
        if me.holding == "onion":
            pot = _pot_to_fill(game, self.name)
            if pot is not None:
                return [f"interact({pot.id})"]
            spot = _own_counter(game, self.name)     # free its hands for a dish
            return [f"interact({spot})"] if spot else _idle(game, self.name)
        rack = _kind_reachable(game, self.name, "dish_rack")
        if rack and _pot_to_plate(game, self.name, lead=10_000) is not None:
            return [f"interact({rack})"]
        crate = _kind_reachable(game, self.name, "onion_crate")
        fill = _pot_to_fill(game, self.name)
        carried = sum(a.holding == "onion" for a in game.agents if a.name != self.name)
        if (self.helps_fill and fill is not None and crate
                and POT_CAPACITY - fill.onions - carried >= 2):   # the filler brings one
            return [f"interact({crate})", f"interact({fill.id})"]
        return _idle(game, self.name)


def coop_pair(game: Game, split_pots: bool = True) -> dict[str, Bot]:
    """Shared-room levels, two SoloBots: with `split_pots` and at least as many pots as agents,
    agent i (in seat order) cooks only in pot i+1, so they don't fight over one pot; otherwise
    they share the pots."""
    pots = list(game.pots)
    names = [a.name for a in game.agents]
    if split_pots and len(pots) >= len(names):
        return {n: SoloBot(n, pots=[pots[i]]) for i, n in enumerate(names)}
    return {n: SoloBot(n) for n in names}


def _onion_trip(game: Game, name: str) -> int:
    """Seconds from this agent's start to a crate, then to the nearest pot from that crate."""
    k = game.kitchen
    crate = _kind_reachable(game, name, "onion_crate")
    if crate is None:
        return 10_000
    best = 10_000
    for tile in k.access_tiles(crate):
        if k.room_of.get(tile) != k.room_of.get(game.agent(name).pos):
            continue
        to_pot = min((d for p in game.pots if (d := k.distance(tile, k.access_tiles(p)))
                      is not None), default=10_000)
        best = min(best, _dist(game, name, crate) + to_pot)
    return best


def role_pair(game: Game, helps_fill: bool = False) -> dict[str, Bot]:
    """Shared-room levels: a FillerBot (whoever has the shorter crate-to-pot trip; seat order
    breaks ties) and a PlaterBot."""
    names = [a.name for a in game.agents]
    filler = min(names, key=lambda n: (_onion_trip(game, n), names.index(n)))
    return {n: FillerBot(n) if n == filler else PlaterBot(n, helps_fill) for n in names}


def team(game: Game) -> dict[str, Bot]:
    """The best scripted pair we have for any level (analysis/bot_tuning.py compares them):
    - someone can't make a soup alone (forced_coordination, split_room): the split pair;
    - one self-sufficient room each, sharing the pots (asymmetric_advantages): a filler and a
      plater (the filler is the one with the onions next to the pots);
    - one room with a pot each (coordination_ring, counter_circuit): a SoloBot per pot;
    - one room, one pot (cramped_room): a filler and a plater who also fills."""
    if not all(self_sufficient(game, a.name) for a in game.agents):
        return split_room_pair(game)
    rooms = {game.kitchen.room_of[a.pos] for a in game.agents}
    if len(rooms) > 1:
        return role_pair(game)
    if len(game.pots) >= len(game.agents):
        return coop_pair(game)
    return role_pair(game, helps_fill=True)


def run(game: Game, bots: dict[str, Bot], seconds: int | None = None) -> list[Event]:
    """Play `seconds` (default: until the phase is over). Bots decide instantly."""
    end = game.duration if seconds is None else game.t + seconds
    events: list[Event] = []
    while game.t < end:
        for name in game.needs_decision():
            if name in bots:
                err = game.set_plan(name, bots[name].decide(game))
                if err is not None:
                    game.set_plan(name, IDLE)
        events += game.tick()
    return events
