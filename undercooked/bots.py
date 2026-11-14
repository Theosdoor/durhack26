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
    busy = {p for o in k.objects.values() if o.kind != "counter" for p in k.access_tiles(o.id)}
    x, y = min(free, key=lambda p: (p in busy, p[1] < me.pos[1], p))
    return [f"move_to({x}, {y})"]


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
            return [f"interact({hatch})"] if hatch and game.orders else IDLE
        if me.holding == "dish":
            pot = _pot_to_plate(game, self.name, lead=10_000, only=self.pots)
            if pot is None:
                spot = _empty_counter(game, self.name)
                return [f"interact({spot})"] if spot else IDLE
            if pot.ready:
                return [f"interact({pot.id})"]
            return [f"move_to({pot.id})", f"wait({min(pot.ready_in, 5)})"]  # type: ignore[type-var]
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


class SplitPasserBot:
    """Split levels, onion room: keeps the middle counters stocked with onions and a dish."""

    def __init__(self, name: str, max_onions: int = 2):
        self.name = name
        self.max_onions = max_onions

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        shared = [o.pos for o in game.kitchen.shared_counters()]
        if me.holding is not None:
            spot = _empty_counter(game, self.name, shared)
            return [f"interact({spot})"] if spot else IDLE
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
        if dishes == 0 and soon and rack:
            return [f"interact({rack})", f"interact({spot})"]
        # keep one counter free for the dish
        if onions < self.max_onions and (free > 1 or dishes > 0) and crate:
            return [f"interact({crate})", f"interact({spot})"]
        return IDLE


class SplitCookBot:
    """Split levels, pot room: takes onions and dishes off the middle counters, cooks, serves."""

    def __init__(self, name: str):
        self.name = name

    def decide(self, game: Game) -> list[str]:
        unstick = _step_aside(game, self.name)
        if unstick:
            return unstick
        me = game.agent(self.name)
        shared = [o.pos for o in game.kitchen.shared_counters()]
        hatch = _kind_reachable(game, self.name, "hatch")
        if me.holding == "soup":
            return [f"interact({hatch})"] if hatch and game.orders else IDLE
        if me.holding == "dish":
            pot = _pot_to_plate(game, self.name, lead=10_000)
            if pot is None:
                spot = _empty_counter(game, self.name, shared)
                return [f"interact({spot})"] if spot else IDLE
            return [f"interact({pot.id})"] if pot.ready else [f"wait({min(pot.ready_in, 5)})"]  # type: ignore[type-var]
        if me.holding == "onion":
            pot = _pot_to_fill(game, self.name)
            if pot is not None:
                return [f"interact({pot.id})"]
            spot = _empty_counter(game, self.name, shared)
            return [f"interact({spot})"] if spot else IDLE
        plate = _pot_to_plate(game, self.name, lead=DISH_LEAD_S)
        dish = _counter_with(game, self.name, "dish", shared)
        if plate is not None and dish:
            return [f"interact({dish})", f"interact({plate.id})"]
        fill = _pot_to_fill(game, self.name)
        onion = _counter_with(game, self.name, "onion", shared)
        if fill is not None and onion:
            return [f"interact({onion})", f"interact({fill.id})"]
        return IDLE


def split_room_pair(game: Game) -> dict[str, Bot]:
    """Cook for whoever can reach a pot, passer for whoever can reach an onion crate."""
    bots: dict[str, Bot] = {}
    for a in game.agents:
        can_cook = any(game.kitchen.reachable(a.pos, p) for p in game.pots)
        bots[a.name] = SplitCookBot(a.name) if can_cook else SplitPasserBot(a.name)
    return bots


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
