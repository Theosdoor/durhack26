"""Game state and rules: one `tick()` = one game second.

Per tick: (0) every agent picks its step for this second from its plan (walk one tile, interact,
or stand still); (1) interactions resolve: first every "give" (put an item on a counter, an onion
in a pot, a soup in the hatch), then every "take" (pick up from a counter, take from a crate or
rack, plate a soup), so a same-second handover over a counter works whoever is who;
(2) movement resolves together (same target tile, swaps, or walking into a standing agent: the
movers don't move, `bump`); (3) pots cook; (4) orders expire and arrive (none at or after the
phase's end; scoring "orders" only: under scoring "soups", the default, there are no orders and
every soup served scores cfg.orders.reward). `t` counts seconds since the phase started: events returned by `tick()` carry the
new `t`, the same `t` as the frame taken right after it.

No agent has fixed priority. Whatever still depends on order (step planning, two agents in the
same interaction pass contending for one item, tile or order, and who gives way after a clash)
uses a fresh order each second, drawn from the game's own RNG (`seed`), so a game is
deterministic for a given seed but no list index wins ties systematically.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from undercooked.config import Config
from undercooked.engine.actions import Action, ParseError, parse_action, split_plan
from undercooked.engine.kitchen import DIRS, Kitchen, Pos
from undercooked.replay import AgentState, Event, Frame, OrderState, Phase, PotState

Status = Literal["acting", "deciding", "idle"]
BLOCKED_LIMIT = 3      # consecutive blocked seconds before a plan fails
POT_CAPACITY = 3


@dataclass
class Agent:
    name: str
    letter: str
    pos: Pos
    holding: str | None = None
    plan: list[Action] = field(default_factory=list)
    status: Status = "idle"
    result: str = "none yet"          # "none yet" | "in progress" | "done" | "failed: ..." | "stopped"
    blocked: int = 0                  # consecutive seconds blocked by another agent
    yield_to: str | None = None       # give way one second after a same-tile clash


@dataclass
class Pot:
    id: str
    pos: Pos
    onions: int = 0
    ready_in: int | None = None       # seconds left while cooking
    ready: bool = False

    @property
    def cooking(self) -> bool:
        return self.ready_in is not None

    @property
    def state(self) -> str:
        if self.ready:
            return "ready"
        if self.cooking:
            return "cooking"
        return "filling" if self.onions else "empty"


def _xy(p: Pos) -> str:
    return f"{p[0]},{p[1]}"


class Game:
    """One phase (a practice or a round) of play in one kitchen."""

    def __init__(self, kitchen: Kitchen, agent_names: list[str], cfg: Config, *,
                 phase: Phase = "round", round: int = 1, seed: int | str | None = None):
        """`agent_names[i]` starts at start position i + 1. `seed` seeds the per-second tie
        order (default: from cfg.seed, phase and round)."""
        if not agent_names:
            raise ValueError("need at least one agent")
        if len(set(agent_names)) != len(agent_names):
            raise ValueError(f"agent names must be unique: {agent_names}")
        self.kitchen = kitchen
        self.cfg = cfg
        self.phase: Phase = phase
        self.round = round
        self.t = 0
        self.seed = seed if seed is not None else f"{cfg.seed}|{phase}|{round}"
        self.rng = random.Random(self.seed)
        self.duration = cfg.practice_s if phase == "practice" else cfg.round_s
        self.ended = False                 # ended early (see end())
        self.agents: list[Agent] = []
        for i, name in enumerate(agent_names):
            if i + 1 not in kitchen.starts:
                raise ValueError(f"level {kitchen.name!r} has no start position {i + 1}")
            self.agents.append(Agent(name, name[0].upper(), kitchen.starts[i + 1]))
        self._by_name = {a.name: a for a in self.agents}
        self.pots: dict[str, Pot] = {o.id: Pot(o.id, o.pos) for o in kitchen.of_kind("pot")}
        self.counters: dict[Pos, str] = {}          # only non-empty counters
        self.orders: list[OrderState] = []          # open orders
        self.score = 0
        self.served = 0
        self.missed = 0
        self._next_order_id = 1
        self._pending: list[Event] = []
        self._events: list[Event] = []
        self._just_started: set[str] = set()
        self._rank: dict[str, int] = {}   # this second's tie order (set by tick)
        if self.uses_orders:
            self._new_order()                       # first order at t=0
        self._pending, self._events = self._events, []

    # ================================================================ public API
    @property
    def uses_orders(self) -> bool:
        """scoring "orders": soups fill open orders; "soups": no orders at all."""
        return self.cfg.scoring == "orders"

    @property
    def accepts_soup(self) -> bool:
        """Would the hatch take a soup now? (scoring "orders": only while an order is open)"""
        return not self.uses_orders or bool(self.orders)

    @property
    def over(self) -> bool:
        return self.ended or self.t >= self.duration

    def end(self) -> None:
        """End the phase now (practice: the agent called finish_practice()). No more ticks."""
        self.ended = True

    def agent(self, name: str) -> Agent:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no agent {name!r}; agents are {list(self._by_name)}") from None

    def set_plan(self, agent: str, actions: list[str]) -> str | None:
        """Parse and set a plan (replacing any current plan).

        Returns None on success, or an error string if the plan is unusable (its first action is
        invalid); then no plan is set and the agent's last plan result becomes "failed: <error>".
        If a later action is invalid, the valid actions before it are kept and the plan fails with
        that action's reason when it is reached. Plans longer than cfg.max_plan_len are truncated.
        """
        a = self.agent(agent)
        texts = split_plan(actions)
        if not texts:
            return self._reject(a, f"empty plan: send 1 to {self.cfg.max_plan_len} actions")
        texts = texts[: self.cfg.max_plan_len]
        plan: list[Action] = []
        for text in texts:
            try:
                act = parse_action(text, self.kitchen)
                self._check_reachable(a, act)
            except ParseError as e:
                if not plan:
                    return self._reject(a, str(e))
                plan.append(Action("invalid", text, reason=str(e)))
                break
            plan.append(act)
        a.plan = plan
        a.result = "in progress"
        a.status = "acting"
        a.blocked = 0
        a.yield_to = None
        return None

    def clear_plan(self, agent: str) -> None:
        a = self.agent(agent)
        if a.plan:
            a.result = "stopped"
        a.plan = []

    def set_status(self, agent: str, status: Status) -> None:
        if status not in ("acting", "deciding", "idle"):
            raise ValueError(f"bad status {status!r}")
        self.agent(agent).status = status

    def needs_decision(self) -> list[str]:
        """Agents with no plan left (finished, failed or never set) who aren't deciding."""
        return [a.name for a in self.agents if not a.plan and a.status != "deciding"]

    def last_plan_result(self, agent: str) -> str:
        """ "done", "failed: <reason>", "in progress", "stopped" or "none yet"."""
        return self.agent(agent).result

    def tick(self) -> list[Event]:
        """Advance one game second and return the events of that second."""
        self._events = self._pending
        self._pending = []
        self._just_started: set[str] = set()
        self.t += 1
        start_pos = {a.name: a.pos for a in self.agents}
        order = list(self.agents)            # this second's tie order (see the module doc)
        self.rng.shuffle(order)
        self._rank = {a.name: i for i, a in enumerate(order)}
        # (0) choose steps, in this second's order. Path planning is asymmetric so that two
        # agents never mirror each other's dodges forever: an agent avoids the tiles that earlier
        # agents will occupy this second (and the tile of an earlier agent walking into its own
        # tile, so no swaps), but only those later agents that will certainly stand still.
        steps: dict[str, tuple] = {}
        claims: dict[str, Pos] = {}
        for i, a in enumerate(order):
            avoid: dict[Pos, str] = {}
            for b in order[:i]:
                avoid[claims[b.name]] = b.name
                if claims[b.name] == a.pos:
                    avoid[start_pos[b.name]] = b.name
            for b in order[i + 1:]:
                if self._will_stay(b):
                    avoid[b.pos] = b.name
            step = self._choose_step(a, avoid, start_pos)
            steps[a.name] = step
            claims[a.name] = step[1] if step[0] == "move" else a.pos
        # (1) interactions: all gives, then all takes, each pass in this second's order
        acting = [(a, steps[a.name][1]) for a in order if steps[a.name][0] == "interact"]
        gives = {a.name: self._gives(a, obj_id) for a, obj_id in acting}
        for give_pass in (True, False):
            for a, obj_id in acting:
                if gives[a.name] == give_pass:
                    self._interact(a, obj_id)
        # (2) movement
        self._move(steps, start_pos)
        # (3) pots cook
        for pot in self.pots.values():
            if pot.cooking and pot.id not in self._just_started:
                pot.ready_in -= 1  # type: ignore[operator]
                if pot.ready_in <= 0:
                    pot.ready_in = None
                    pot.ready = True
                    self._emit("cook_done", None, pot=pot.id, at=_xy(pot.pos), onions=pot.onions)
        # (4) orders
        for o in list(self.orders):
            if self.t >= o.due_t:
                self.orders.remove(o)
                self.score -= self.cfg.orders.penalty
                self.missed += 1
                self._emit("order_missed", None, order=o.id, points=-self.cfg.orders.penalty)
        if (self.uses_orders and self.t % self.cfg.orders.every_s == 0
                and self.t < self.duration):
            self._new_order()
        out, self._events = self._events, []
        return out

    def frame(self) -> Frame:
        return Frame(
            phase=self.phase, round=self.round, t=self.t,
            agents=[AgentState(name=a.name, pos=a.pos, holding=a.holding,
                               plan=[x.text() for x in a.plan], status=a.status)
                    for a in self.agents],
            pots=[PotState(id=p.id, pos=p.pos, onions=p.onions, state=p.state,  # type: ignore[arg-type]
                           ready_in=p.ready_in) for p in self.pots.values()],
            counters={_xy(p): item for p, item in sorted(self.counters.items(),
                                                         key=lambda kv: (kv[0][1], kv[0][0]))},
            orders=[o.model_copy() for o in self.orders],
            score=self.score, served=self.served, missed=self.missed,
        )

    def state_text(self, agent: str, *, unread_posts: int, notepad: str | None,
                   last_plan_result: str) -> str:
        from undercooked.engine.text import state_text
        return state_text(self, agent, unread_posts=unread_posts, notepad=notepad,
                          last_plan_result=last_plan_result)

    # ============================================================== internals
    def _emit(self, type_: str, agent: str | None, **data) -> None:
        self._events.append(Event(phase=self.phase, round=self.round, t=self.t, type=type_,
                                  agent=agent, data=data))

    def _reject(self, a: Agent, reason: str) -> str:
        a.plan = []
        a.result = f"failed: {reason}"
        if a.status == "acting":
            a.status = "idle"
        return reason

    def _fail(self, a: Agent, act: Action | None, reason: str) -> None:
        data = {"reason": reason}
        if act is not None:
            data["action"] = act.text()
        self._emit("fail", a.name, **data)
        a.plan = []
        a.result = f"failed: {reason}"
        a.blocked = 0
        a.yield_to = None
        if a.status == "acting":
            a.status = "idle"

    def _pop(self, a: Agent) -> None:
        a.plan.pop(0)
        if not a.plan:
            a.result = "done"
            if a.status == "acting":
                a.status = "idle"

    def _check_reachable(self, a: Agent, act: Action) -> None:
        k = self.kitchen
        room = k.room_of[a.pos]
        if act.target is not None and not k.reachable(a.pos, act.target):
            obj = k.objects[act.target]
            raise ParseError(f"{act.target} at ({obj.pos[0]},{obj.pos[1]}) is not reachable "
                             f"from your room")
        if act.kind_target is not None and not self._reachable_of_kind(a.pos, act.kind_target):
            raise ParseError(f"no {act.kind_target} is reachable from your room")
        if act.kind == "move_to" and act.pos is not None and k.room_of.get(act.pos) != room:
            raise ParseError(f"({act.pos[0]},{act.pos[1]}) is not reachable from your room")

    def _reachable_of_kind(self, pos: Pos, kind: str) -> list[str]:
        return [o.id for o in self.kitchen.of_kind(kind) if self.kitchen.reachable(pos, o.id)]

    def _nearest(self, pos: Pos, kind: str) -> str | None:
        best: tuple[int, int, str] | None = None
        for i, oid in enumerate(self._reachable_of_kind(pos, kind)):
            d = self.kitchen.distance(pos, self.kitchen.access_tiles(oid))
            if d is not None and (best is None or (d, i) < best[:2]):
                best = (d, i, oid)
        return best[2] if best else None

    def _goals(self, act: Action) -> list[Pos]:
        if act.pos is not None:
            return [act.pos]
        return self.kitchen.access_tiles(act.target)  # type: ignore[arg-type]

    def _will_stay(self, a: Agent) -> bool:
        """True if `a` certainly won't walk this second (used before its step is chosen)."""
        if not a.plan or a.yield_to is not None:
            return True
        act = a.plan[0]
        if act.kind in ("wait", "invalid"):
            return True
        if act.kind == "interact" and act.target is not None:
            return a.pos in self._goals(act)
        return False

    def _choose_step(self, a: Agent, avoid: dict[Pos, str], start_pos: dict[str, Pos]) -> tuple:
        """("interact", obj_id) | ("move", tile, act) | ("blocked", by, tile, act) | ("stay",).

        `avoid` maps tiles to plan around -> the agent responsible for them."""
        others = {n: p for n, p in start_pos.items() if n != a.name}
        for _ in range(len(a.plan) + 1):
            if not a.plan:
                return ("stay",)
            act = a.plan[0]
            if act.kind == "invalid":
                self._fail(a, act, act.reason or "invalid action")
                return ("stay",)
            if act.kind == "wait":
                act.seconds -= 1
                if act.seconds <= 0:
                    self._pop(a)
                return ("stay",)
            if a.yield_to is not None:
                a.yield_to = None
                return ("stay",)
            if act.kind == "step":
                dx, dy = DIRS[act.direction]  # type: ignore[index]
                to = (a.pos[0] + dx, a.pos[1] + dy)
                if not self.kitchen.is_floor(to):
                    obj = self.kitchen.by_pos.get(to)
                    what = obj.id if obj else "outside the kitchen"
                    self._fail(a, act, f"cannot move {act.direction} from "
                                       f"({a.pos[0]},{a.pos[1]}): ({to[0]},{to[1]}) is {what}")
                    return ("stay",)
                return ("move", to, act)
            # interact / move_to
            if act.kind_target is not None and act.target is None:
                act.target = self._nearest(a.pos, act.kind_target)
                if act.target is None:
                    self._fail(a, act, f"no {act.kind_target} is reachable from your room")
                    return ("stay",)
            goals = self._goals(act)
            if a.pos in goals:
                if act.kind == "interact":
                    return ("interact", act.target, act)
                self._pop(a)          # already there: costs no time
                continue
            path = self.kitchen.path(a.pos, goals, blocked=avoid)
            if path:
                return ("move", path[0], act)
            free_path = self.kitchen.path(a.pos, goals)
            if free_path is None:
                tgt = act.target or f"({act.pos[0]},{act.pos[1]})"  # type: ignore[index]
                self._fail(a, act, f"{tgt} is not reachable from your room")
                return ("stay",)
            by = next((avoid[p] for p in free_path if p in avoid),
                      next((n for n, p in others.items() if p in free_path), next(iter(others))))
            return ("blocked", by, free_path[0], act)
        return ("stay",)

    def _gives(self, a: Agent, obj_id: str) -> bool:
        """True if this interaction hands an item over (counter put-down, onion into a pot,
        serving); False if it takes one (counter pick-up, crate, rack, plating). Interactions
        that will fail go where their kind would; failing changes nothing."""
        kind = self.kitchen.objects[obj_id].kind
        if kind in ("counter", "bin"):
            return a.holding is not None
        if kind == "pot":
            return a.holding == "onion"
        return kind == "hatch"

    def _interact(self, a: Agent, obj_id: str) -> None:
        act = a.plan[0]
        obj = self.kitchen.objects[obj_id]
        at = _xy(obj.pos)
        h = a.holding
        fail = None
        if obj.kind == "onion_crate":
            if h is None:
                a.holding = "onion"
                self._emit("pickup", a.name, item="onion", at=at)
            else:
                fail = f"your hands are full (holding {_a(h)}); put it down first"
        elif obj.kind == "dish_rack":
            if h is None:
                a.holding = "dish"
                self._emit("pickup", a.name, item="dish", at=at)
            else:
                fail = f"your hands are full (holding {_a(h)}); put it down first"
        elif obj.kind == "counter":
            here = self.counters.get(obj.pos)
            if h is not None and here is None:
                self.counters[obj.pos] = h
                a.holding = None
                self._emit("putdown", a.name, item=h, at=at)
            elif h is None and here is not None:
                del self.counters[obj.pos]
                a.holding = here
                self._emit("pickup", a.name, item=here, at=at)
            elif h is None:
                fail = f"{obj_id} is empty and you are holding nothing"
            else:
                fail = f"{obj_id} already holds {_a(here)}; a counter holds one item"
        elif obj.kind == "bin":
            if h is not None:
                a.holding = None
                self._emit("trash", a.name, item=h, at=at)
            else:
                fail = "the bin is for throwing items away; you are holding nothing"
        elif obj.kind == "pot":
            pot = self.pots[obj_id]
            if h == "onion":
                if pot.ready:
                    fail = f"{obj_id} has soup ready; plate it with a dish first"
                elif pot.cooking:
                    fail = f"{obj_id} is already cooking (ready in {pot.ready_in} s)"
                elif pot.onions >= POT_CAPACITY:
                    fail = f"{obj_id} is full"
                else:
                    pot.onions += 1
                    a.holding = None
                    self._emit("add_to_pot", a.name, item="onion", pot=obj_id, at=at,
                               onions=pot.onions)
                    if pot.onions == POT_CAPACITY:
                        pot.ready_in = self.cfg.cook_s
                        self._just_started.add(obj_id)
                        self._emit("cook_start", a.name, pot=obj_id, at=at, onions=pot.onions)
            elif h == "dish":
                if pot.ready:
                    pot.onions, pot.ready, pot.ready_in = 0, False, None
                    a.holding = "soup"
                    self._emit("plate", a.name, item="soup", pot=obj_id, at=at, onions=0)
                elif pot.cooking:
                    fail = f"{obj_id} is not ready yet (ready in {pot.ready_in} s)"
                else:
                    fail = (f"{obj_id} has nothing to plate ({pot.onions} of {POT_CAPACITY} "
                            f"onions, not cooking)")
            elif h == "soup":
                fail = "you are already holding a soup; serve it at the hatch"
            else:
                fail = f"you are holding nothing; bring an onion or a dish to {obj_id}"
        elif obj.kind == "hatch":
            if h == "soup":
                if not self.uses_orders:
                    a.holding = None
                    self.score += self.cfg.orders.reward
                    self.served += 1
                    self._emit("serve", a.name, item="soup", at=at, points=self.cfg.orders.reward)
                elif not self.orders:
                    fail = "no open order to serve; keep the soup until an order arrives"
                else:
                    order = min(self.orders, key=lambda o: (o.due_t, o.id))
                    self.orders.remove(order)
                    a.holding = None
                    self.score += self.cfg.orders.reward
                    self.served += 1
                    self._emit("serve", a.name, item="soup", at=at, order=order.id,
                               points=self.cfg.orders.reward)
            elif h is None:
                fail = "you are holding nothing; only a soup can be served at the hatch"
            else:
                fail = f"only a soup can be served at the hatch, not {_a(h)}"
        if fail is not None:
            self._fail(a, act, fail)
            return
        a.blocked = 0
        self._pop(a)

    def _move(self, steps: dict[str, tuple], start_pos: dict[str, Pos]) -> None:
        movers: dict[str, Pos] = {n: s[1] for n, s in steps.items() if s[0] == "move"}
        bumped: dict[str, tuple[str, Pos, bool]] = {}  # name -> (by, tile, same_tile_clash)
        while True:
            ends = {n: movers.get(n, start_pos[n]) for n in start_pos}
            clash: dict[str, tuple[str, Pos, bool]] = {}
            for m, to in movers.items():
                for o in start_pos:
                    if o == m:
                        continue
                    swap = o in movers and movers[o] == start_pos[m] and to == start_pos[o]
                    if ends[o] == to or swap:
                        clash[m] = (o, to, o in movers and not swap)
                        break
            if not clash:
                break
            for m, info in clash.items():
                del movers[m]
                bumped[m] = info
        for a in self.agents:
            step = steps[a.name]
            if a.name in movers:
                a.pos = movers[a.name]
                a.blocked = 0
                act = step[2]
                if act.kind == "step" or (act.kind == "move_to" and a.pos in self._goals(act)):
                    self._pop(a)
            elif a.name in bumped or step[0] == "blocked":
                by, tile, same = bumped.get(a.name) or (step[1], step[2], False)
                act = step[-1]
                a.blocked += 1
                self._emit("bump", a.name, at=_xy(tile), reason=f"blocked by {by}",
                           action=act.text())
                if a.blocked >= BLOCKED_LIMIT:
                    self._fail(a, act, f"blocked by {by}")
                elif same and self._rank[a.name] > self._rank[by]:
                    a.yield_to = by   # later in this second's order: gives way next second

    def _new_order(self) -> None:
        if len(self.orders) >= self.cfg.orders.max_open:
            return
        o = OrderState(id=self._next_order_id, created_t=self.t,
                       due_t=self.t + self.cfg.orders.due_s)
        self._next_order_id += 1
        self.orders.append(o)
        self._emit("order_new", None, order=o.id)


def _a(item: str | None) -> str:
    if item is None:
        return "nothing"
    return {"onion": "an onion", "dish": "a dish", "soup": "a soup"}.get(item, f"a {item}")
