"""Overcooked-AI kitchen with high-level actions.

The rules, state and step function are Overcooked-AI's (`OvercookedGridworld`). We add only:
- a high-level action vocabulary the LLM agents choose from (get_onion, put_in_pot, ...);
- per-step execution of those actions with Overcooked-AI's `MotionPlanner`: every step, each
  agent with an action re-plans from where it stands to the nearest valid motion goal for that
  action and takes the first low-level move (re-planning each step absorbs being blocked);
- conversion of Overcooked state into the Undercooked replay records (Frame, Event).

Coordinates are Overcooked's (x, y) = (column, row), origin top-left, as in replay.py.
One game step = one replay "second" (`t`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from overcooked_ai_py.mdp.actions import Action
from overcooked_ai_py.mdp.overcooked_mdp import (EVENT_TYPES, OvercookedGridworld, OvercookedState,
                                                Recipe)
from overcooked_ai_py.planning.planners import MotionPlanner

from overcooked_port.replay import AgentState, Event, Frame, Phase, PotState

# name -> (what you must hold before, terrain letters it can target, description)
ACTIONS: dict[str, tuple[str, str, str]] = {
    "get_onion": ("nothing", "OX", "pick up an onion from the onion crate (or a counter)"),
    "get_dish": ("nothing", "DX", "pick up a dish from the dish rack (or a counter)"),
    "put_in_pot": ("onion", "P", "put the onion you hold into a pot that is not full"),
    "start_cooking": ("nothing", "P", "start cooking a pot that has onions in it"),
    "plate_soup": ("dish", "P", "scoop a cooked soup from a pot onto your dish (waits by the "
                                "pot while it cooks)"),
    "serve": ("soup", "S", "deliver the soup you hold at the serving hatch"),
    "place_on_counter": ("anything", "X", "put what you hold on an empty counter"),
    "pick_up_from_counter": ("nothing", "X", "pick up an item from a counter"),
    "interact": ("anything", "XODPS", "walk to the target and interact with it"),
    "move_to": ("anything", "", "walk to the floor tile (x, y), e.g. to get out of your "
                                "teammate's way"),
    "wait": ("anything", "", "stand still for n steps (1-10)"),
}
# which actions each rules mode offers
VOCAB = {"full": [a for a in ACTIONS if a != "interact"],
         "minimal": ["interact", "move_to", "wait"]}
SIGNATURE = {"move_to": "move_to(x, y)", "wait": "wait(n)", "interact": "interact(target)"}
CALL_RE = re.compile(r"^([a-z_]+)(?:\((.*)\))?$")
POT_RE = re.compile(r"^pot_?(\d+)$")
XY_RE = re.compile(r"^\(?(\d+),(\d+)\)?$")
STUCK_STEPS = 8          # an action blocked by the teammate this many steps in a row fails
WAIT_LIMIT = 10          # an action with nothing to do yet waits this many steps, then fails


def parse_action(s: str) -> tuple[str, int | tuple[int, int] | str | None] | None:
    """'get_onion' -> ('get_onion', None); 'wait(3)' -> ('wait', 3);
    'move_to(2, 3)' / 'pick_up_from_counter(2, 3)' -> (name, (2, 3));
    'put_in_pot(pot_2)' -> ('put_in_pot', 'pot_2'); malformed or unknown -> None."""
    s = s.strip().lower().replace(" ", "").replace('"', "").replace("'", "")
    m = CALL_RE.match(s)
    if m is None or m.group(1) not in ACTIONS:
        return None
    name, raw = m.group(1), (m.group(2) or "")
    if name == "wait":
        if raw and not raw.isdigit():
            return None
        return ("wait", max(1, min(10, int(raw or 1))))
    if not raw:
        return None if name in ("move_to", "interact") else (name, None)
    if xy := XY_RE.match(raw):
        return (name, (int(xy.group(1)), int(xy.group(2))))
    if (pot := POT_RE.match(raw)) and name != "move_to":
        return (name, f"pot_{pot.group(1)}")
    return None


@dataclass
class Player:
    name: str
    index: int                            # Overcooked player index
    plan: list[str] = field(default_factory=list)   # remaining actions, current first
    waited: int = 0                       # steps done of the current wait(n)
    stuck: int = 0                        # steps blocked by the teammate in a row
    idle: int = 0                         # steps the current action had nothing to do
    last_fail: str | None = None          # why the last plan stopped early (shown once)
    status: str = "idle"


class Kitchen:
    def __init__(self, layout: str, names: list[str], mode: str = "full"):
        """`names[i]` plays Overcooked player i (start position i+1 in the layout).
        `mode`: "full" (named actions) or "minimal" (interact / move_to / wait)."""
        self.layout = layout
        self.mode = mode
        self.mdp = OvercookedGridworld.from_layout_name(layout)
        self.cook_time = Recipe(("onion",) * 3).time    # Overcooked default: 20 steps
        counters = self.mdp.get_counter_locations()
        self.mp = MotionPlanner(self.mdp, counter_goals=counters)
        self.state: OvercookedState = self.mdp.get_standard_start_state()
        self.players = [Player(n, i) for i, n in enumerate(names)]
        self.t = 0
        self.score = 0
        self.served = 0
        pots = sorted(self.mdp.get_pot_locations(), key=lambda p: (p[1], p[0]))
        self.pot_ids = {p: f"pot_{i + 1}" for i, p in enumerate(pots)}
        self.pot_by_id = {v: k for k, v in self.pot_ids.items()}
        # counters both players can reach: where items are passed between the two halves
        self.shared_counters = sorted(
            [c for c in counters if all(self._reachable(p, [c]) for p in self.players)],
            key=lambda p: (p[1], p[0]))

    # ------------------------------------------------------------------ geometry
    def _pos_or(self, p: Player):
        return self.state.players[p.index].pos_and_or

    def _goals(self, p: Player, targets: list) -> list:
        start = self._pos_or(p)
        out = []
        for pos in targets:
            for g in self.mp.motion_goals_for_pos.get(pos, []):
                if self.mp.is_valid_motion_start_goal_pair(start, g):
                    out.append((g, pos))
        return out

    def _reachable(self, p: Player, targets: list) -> bool:
        return bool(self._goals(p, targets))

    def _terrain(self, pos) -> str:
        return self.mdp.get_terrain_type_at_pos(pos)

    def map_rows(self) -> list[str]:
        """Terrain rows (no players), Overcooked letters: X counter, O onions, D dishes, P pot,
        S serving hatch, ' ' floor."""
        return ["".join(row) for row in self.mdp.terrain_mtx]

    # ------------------------------------------------------------------ actions
    def holding(self, p: Player) -> str | None:
        obj = self.state.players[p.index].held_object
        return obj.name if obj is not None else None

    def _resolve(self, name: str, arg) -> tuple | str:
        """The target tile of an action argument, or why it is not a valid target."""
        if isinstance(arg, str):
            if arg not in self.pot_by_id:
                return f"there is no {arg} (pots: {', '.join(self.pot_by_id)})"
            return self.pot_by_id[arg]
        x, y = arg
        rows = self.mdp.terrain_mtx
        if not (0 <= y < len(rows) and 0 <= x < len(rows[0])):
            return f"({x},{y}) is outside the kitchen"
        if self._terrain(arg) not in ACTIONS[name][1]:
            return f"({x},{y}) is not something you can {name}"
        return arg

    def _candidates(self, p: Player, name: str, arg) -> list:
        """Tiles the action may use (ready now or not): the named target, or every tile of the
        right kind. Untargeted place_on_counter prefers counters your teammate can reach."""
        if arg is not None:
            return [self._resolve(name, arg)]
        letters = ACTIONS[name][1]
        tiles = [(x, y) for y, row in enumerate(self.mdp.terrain_mtx)
                 for x, c in enumerate(row) if c in letters]
        if name == "place_on_counter" and self.shared_counters:
            tiles = list(self.shared_counters)
        return tiles

    def _ready(self, p: Player, name: str, pos) -> bool:
        """Can `name` do its thing at `pos` right now?"""
        m, s = self.mdp, self.state
        kind = self._terrain(pos)
        obj = s.get_object(pos) if s.has_object(pos) else None
        if name == "interact":
            return self._would_change(p, pos)
        if name in ("get_onion", "get_dish"):
            item = "onion" if name == "get_onion" else "dish"
            return kind != "X" or (obj is not None and obj.name == item)
        if name == "put_in_pot":
            return obj is None or not obj.is_full and not obj.is_cooking and not obj.is_ready
        if name == "start_cooking":
            return m.soup_to_be_cooked_at_location(s, pos)
        if name == "plate_soup":
            return obj is not None and (obj.is_ready or obj.is_cooking)
        if name == "serve":
            return True
        if name == "place_on_counter":
            return obj is None
        if name == "pick_up_from_counter":
            return obj is not None
        return False

    def _would_change(self, p: Player, pos) -> bool:
        """Would interacting with `pos` (from a tile next to it) change anything? Simulated
        with Overcooked's own interaction rules on a copy of the state."""
        goals = self._goals(p, [pos])
        if not goals:
            return False
        sim = self.state.deepcopy()
        (gpos, gor), _ = goals[0]
        sim.players[p.index].update_pos_and_or(gpos, gor)
        joint = [Action.STAY] * len(self.players)
        joint[p.index] = Action.INTERACT
        before = self._signature(sim)
        events = {e: [False] * len(self.players) for e in EVENT_TYPES}
        self.mdp.resolve_interacts(sim, joint, events)
        return self._signature(sim) != before

    @staticmethod
    def _signature(state) -> tuple:
        held = tuple(pl.held_object.name if pl.held_object else None for pl in state.players)
        objs = tuple(sorted(
            (pos, o.name, len(getattr(o, "ingredients", ())), getattr(o, "is_cooking", False))
            for pos, o in state.objects.items()))
        return held, objs

    def check(self, p: Player, action: str) -> str | None:
        """Why `action` can't be planned for `p` now (None = it can). Only things that can't
        change while you wait are checked: the action, what you hold, the target, reach."""
        parsed = parse_action(action)
        vocab = VOCAB[self.mode]
        if parsed is None or parsed[0] not in vocab:
            return (f"unknown action {action!r}; use one of "
                    + ", ".join(SIGNATURE.get(a, a) for a in vocab))
        name, arg = parsed
        need = ACTIONS[name][0]
        held = self.holding(p)
        if need == "nothing" and held is not None:
            return f"{name} needs empty hands, but you hold {held}"
        if need not in ("nothing", "anything") and held != need:
            return f"{name} needs you to hold {need}, but you hold {held or 'nothing'}"
        if name == "wait":
            return None
        if name == "move_to":
            if arg not in self.mdp.get_valid_player_positions():
                return f"move_to{arg}: not a floor tile"
            if not self.mp.positions_are_connected(self._pos_or(p), (arg, (0, -1))):
                return f"move_to{arg}: you can't get there from your side of the kitchen"
            return None
        tiles = self._candidates(p, name, arg)
        if tiles and isinstance(tiles[0], str):
            return f"{action}: {tiles[0]}"
        if not self._goals(p, tiles):
            where = "there" if arg is not None else "anywhere to do that"
            return f"{action}: you can't reach {where} from your side of the kitchen"
        return None

    def set_plan(self, p: Player, plan: list[str]) -> str | None:
        """Replace p's plan. Only the first action is checked now (later ones may become
        possible as the kitchen changes); returns why the first one was rejected."""
        p.plan, p.waited, p.stuck, p.idle = [], 0, 0, 0
        if not plan:
            return "empty plan"
        err = self.check(p, plan[0])
        if err is None:
            p.plan = [a.strip() for a in plan]
            p.status = "acting"
        return err

    def needs_decision(self) -> list[Player]:
        return [p for p in self.players if not p.plan]

    def _priority(self, name: str, target) -> int:
        """Lower = preferred when no target is named: fuller pots first for put_in_pot, ready
        pots first for plating, crate/rack before counters (don't take back a pass)."""
        if name == "put_in_pot":
            return -self._pot(target).onions
        if name == "plate_soup":
            return 0 if self._pot(target).state == "ready" else 1
        if name in ("get_onion", "get_dish"):
            return 1 if self._terrain(target) == "X" else 0
        return 0

    def _fail(self, p: Player, events: list[Event], phase: Phase, round: int, reason: str):
        events.append(Event(phase=phase, round=round, t=self.t, type="fail", agent=p.name,
                            data={"action": p.plan[0], "reason": reason}))
        p.last_fail = f"step {self.t}: {reason}"
        p.plan, p.stuck, p.idle = [], 0, 0
        return Action.STAY

    def _low_level(self, p: Player, events: list[Event], phase: Phase, round: int):
        """Overcooked action for p this step; pops finished actions, logs fails."""
        if not p.plan:
            return Action.STAY
        action = p.plan[0]
        name, arg = parse_action(action) or ("?", None)
        if name == "wait":
            p.waited += 1
            if p.waited >= arg:
                p.plan.pop(0)
                p.waited = 0
            return Action.STAY
        err = self.check(p, action)
        if err is None and p.stuck > STUCK_STEPS:
            mate = [q for q in self.players if q is not p][0]
            x, y = self.state.players[mate.index].position
            err = (f"{action}: {mate.name} at ({x},{y}) blocked your way for {STUCK_STEPS} "
                   f"steps. Ask them to move, go another way, or move_to somewhere else")
        if err is None and p.idle > WAIT_LIMIT:
            err = f"{action}: nothing to do {'there' if arg is not None else 'yet'} " \
                  f"for {WAIT_LIMIT} steps"
        if err is not None:
            return self._fail(p, events, phase, round, err)
        start = self._pos_or(p)
        if name == "move_to":
            if start[0] == arg:
                p.plan.pop(0)
                p.stuck = 0
                return Action.STAY
            route = self._route(p, [(arg, None)])
        else:
            tiles = self._candidates(p, name, arg)
            ready = [t for t in tiles if self._ready(p, name, t)]
            if not ready:                     # nothing to do yet: wait (by the target if named)
                cooking = any(self._pot(t).state == "cooking" for t in tiles if t in self.pot_ids)
                if not (self.holding(p) == "dish" and cooking):   # waiting to plate: no limit
                    p.idle += 1
                if arg is None:
                    return Action.STAY
                route = self._route(p, self._goals(p, tiles), name)
                if route is None or route[0] == [Action.INTERACT]:
                    return Action.STAY
                return route[0][0]
            route = self._route(p, self._goals(p, ready), name)
        if route is None:                    # every way there is blocked by the teammate
            p.stuck += 1
            return Action.STAY
        actions, target = route
        if actions == [Action.INTERACT]:
            if name == "plate_soup" and not self.mdp.soup_ready_at_location(self.state, target):
                return Action.STAY            # wait by the cooking pot (no time limit)
            p.plan.pop(0)
            p.stuck = p.idle = 0
            return Action.INTERACT
        if p.stuck and (self.t + p.index) % 2:   # break symmetric collisions: alternate who
            p.stuck += 1                         # yields while both keep bumping
            return Action.STAY
        return actions[0]

    def _route(self, p: Player, goals: list, name: str = "move_to"):
        """Shortest (actions, target) to any goal, walking around the teammate's tile.

        `goals`: [((pos, orientation), target)] motion goals, or [(pos, None)] for move_to.
        Uses Overcooked's MotionPlanner to turn the position path into actions."""
        start = self._pos_or(p)
        others = {self.state.players[q.index].position for q in self.players if q is not p}
        floor = set(self.mdp.get_valid_player_positions())
        prev = {start[0]: None}
        frontier = [start[0]]
        while frontier:                       # BFS over floor tiles, teammate = wall
            nxt = []
            for pos in frontier:
                for d in ((0, -1), (0, 1), (1, 0), (-1, 0)):
                    q = (pos[0] + d[0], pos[1] + d[1])
                    if q in floor and q not in others and q not in prev:
                        prev[q] = pos
                        nxt.append(q)
            frontier = nxt

        def path_to(pos):
            out = []
            while pos != start[0]:
                out.append(pos)
                pos = prev[pos]
            return out[::-1]

        best = None
        for goal, target in goals:
            gpos = goal if target is None else goal[0]
            if gpos not in prev:
                continue
            path = path_to(gpos)
            if target is None:                # move_to: no facing, no interact
                acts = [Action.determine_action_for_change_in_pos(a, b)
                        for a, b in zip([start[0]] + path, path)]
            else:
                acts, _, _ = self.mp.action_plan_from_positions(path, start, goal)
            key = (self._priority(name, target) if target is not None else 0, len(acts))
            if acts and (best is None or key < best[0]):
                best = (key, acts, target)
        return None if best is None else (best[1], best[2])

    # ------------------------------------------------------------------ step
    def step(self, phase: Phase, round: int) -> list[Event]:
        """Advance one game step. Returns the events of this step (t = the step just played)."""
        events: list[Event] = []
        before = self._snapshot()
        joint = tuple(self._low_level(p, events, phase, round) for p in self.players)
        prev = self.state
        self.state, info = self.mdp.get_state_transition(prev, joint)
        for p, act in zip(self.players, joint):   # bumped into each other: neither moved
            if act in Action.MOTION_ACTIONS and act != Action.STAY:
                ps = prev.players[p.index]
                wanted = self.mdp._move_if_direction(ps.position, ps.orientation, act)[0]
                if wanted != ps.position:
                    moved = self.state.players[p.index].position != ps.position
                    p.stuck = 0 if moved else p.stuck + 1
        reward = sum(info["sparse_reward_by_agent"])
        self.score += reward
        events += self._diff_events(before, joint, info, phase, round, prev)
        for p in self.players:
            p.status = "acting" if p.plan else "idle"
        self.t += 1
        return events

    def _snapshot(self) -> dict:
        return {"pots": {pos: self._pot(pos) for pos in self.pot_ids},
                "holding": [self.holding(p) for p in self.players]}

    def _diff_events(self, before, joint, info, phase, round, prev) -> list[Event]:
        ev: list[Event] = []
        e = info["event_infos"]
        for p, act in zip(self.players, joint):
            i = p.index
            if act != Action.INTERACT:
                continue
            pos, o = prev.players[i].position, prev.players[i].orientation
            at = Action.move_in_direction(pos, o)
            at_s = f"{at[0]},{at[1]}"
            held_before, held_after = before["holding"][i], self.holding(p)
            base = dict(phase=phase, round=round, t=self.t, agent=p.name)
            pot_id = self.pot_ids.get(at)
            if e["potting_onion"][i]:
                ev.append(Event(**base, type="add_to_pot", data={
                    "item": "onion", "at": at_s, "pot": pot_id,
                    "onions": self._pot(at).onions}))
            elif pot_id and before["pots"][at].state != "cooking" and \
                    self._pot(at).state == "cooking":
                ev.append(Event(**base, type="cook_start", data={"at": at_s, "pot": pot_id}))
            elif held_before == "soup" and held_after is None and \
                    self.mdp.get_terrain_type_at_pos(at) == "S":
                pts = info["sparse_reward_by_agent"][i]
                self.served += 1 if pts > 0 else 0
                ev.append(Event(**base, type="serve", data={"item": "soup", "at": at_s,
                                                           "points": pts}))
            elif held_before == "dish" and held_after == "soup":
                ev.append(Event(**base, type="plate", data={"at": at_s, "pot": pot_id}))
            elif held_before is None and held_after is not None:
                ev.append(Event(**base, type="pickup", data={"item": held_after, "at": at_s}))
            elif held_before is not None and held_after is None:
                ev.append(Event(**base, type="putdown", data={"item": held_before, "at": at_s}))
        for pos, pid in self.pot_ids.items():
            if before["pots"][pos].state == "cooking" and self._pot(pos).state == "ready":
                ev.append(Event(phase=phase, round=round, t=self.t, type="cook_done",
                                data={"at": f"{pos[0]},{pos[1]}", "pot": pid}))
        return ev

    # ------------------------------------------------------------------ views
    def _pot(self, pos) -> PotState:
        pid = self.pot_ids[pos]
        if not self.state.has_object(pos):
            return PotState(id=pid, pos=pos)
        soup = self.state.get_object(pos)
        n = len(soup.ingredients)
        if soup.is_ready:
            return PotState(id=pid, pos=pos, onions=n, state="ready")
        if soup.is_cooking:
            return PotState(id=pid, pos=pos, onions=n, state="cooking",
                            ready_in=soup.cook_time_remaining)
        return PotState(id=pid, pos=pos, onions=n, state="filling" if n else "empty")

    def counters(self) -> dict[str, str]:
        return {f"{x},{y}": o.name for (x, y), o in self.state.objects.items()
                if self.mdp.get_terrain_type_at_pos((x, y)) == "X"}

    def frame(self, phase: Phase, round: int) -> Frame:
        agents = [AgentState(name=p.name, pos=self.state.players[p.index].position,
                             holding=self.holding(p), plan=list(p.plan), status=p.status)
                  for p in self.players]
        return Frame(phase=phase, round=round, t=self.t, agents=agents,
                     pots=[self._pot(pos) for pos in self.pot_ids], counters=self.counters(),
                     orders=[], score=self.score, served=self.served)

    def map_text(self, me: str | None = None) -> str:
        """The map with player letters; legend below."""
        rows = [list(r) for r in self.map_rows()]
        for p in self.players:
            x, y = self.state.players[p.index].position
            rows[y][x] = p.name[0]
        w = len(rows[0])
        lines = ["   " + "".join(str(x) for x in range(w))]
        lines += [f"{y:2d} " + "".join(r) for y, r in enumerate(rows)]
        lines.append("Legend: X counter, O onion crate, D dish rack, P pot, S serving hatch, "
                     "space floor, " + ", ".join(f"{p.name[0]} {p.name}" for p in self.players)
                     + ". Coordinates are (x, y) = (column, row).")
        return "\n".join(lines)

    def state_text(self, me: Player) -> str:
        lines = [f"Step {self.t}. Team score {self.score} ({self.served} soups served).",
                 self.map_text()]
        for p in self.players:
            x, y = self.state.players[p.index].position
            who = "You" if p is me else p.name
            lines.append(f"{who} ({p.name[0]}) at ({x},{y}), holding {self.holding(p) or 'nothing'}.")
        for pos, pid in self.pot_ids.items():
            ps = self._pot(pos)
            extra = f", ready in {ps.ready_in}" if ps.ready_in is not None else ""
            if ps.state == "filling" and self.mode == "full":
                extra += " (not cooking: needs start_cooking)"
            lines.append(f"{pid} at ({pos[0]},{pos[1]}): {ps.onions} onions, {ps.state}{extra}.")
        c = self.counters()
        lines.append("Items on counters: " + (", ".join(f"{v} at ({k})" for k, v in c.items())
                                              if c else "none") + ".")
        return "\n".join(lines)


def rules_text(k: Kitchen) -> str:
    """The game rules the agents get. "full": recipe, scoring, timing and named actions.
    "minimal": only the interface (interact / move_to / wait); the game is to be discovered."""
    walking = (f"Cooks can't walk through each other. Walking goes around your teammate if there "
               f"is another way; if there isn't, you wait, and after {STUCK_STEPS} blocked steps "
               f"the action fails.")
    if k.mode == "minimal":
        return f"""\
THE KITCHEN
Your team scores points by delivering things at the serving hatch. How the kitchen works is \
for you to find out by playing.
{walking}

ACTIONS
- interact(target): walk to the target and interact with it. The target is a pot \
(e.g. interact(pot_1)) or the (x, y) of any non-floor tile, e.g. interact(0, 1). What happens \
depends on what is there and what you hold. If nothing would happen yet, you wait there for up \
to {WAIT_LIMIT} steps until something can; then the action fails.
- move_to(x, y): walk to the floor tile (x, y), e.g. to get out of your teammate's way.
- wait(n): stand still for n steps (1-10).
Walking is automatic: shortest path, one tile per step.
"""
    acts = "\n".join(f"- {SIGNATURE.get(a, a)}: {ACTIONS[a][2]} [hold: {ACTIONS[a][0]}]"
                     for a in VOCAB["full"])
    shared = ", ".join(f"({x},{y})" for x, y in k.shared_counters)
    if not k.shared_counters:
        counters = "No counter is reachable by both cooks: you can't pass items to each other."
    else:
        counters = f"Counters both cooks can reach (for passing items): {shared}."
    return f"""\
THE KITCHEN
Onion soup = 3 onions in a pot; then someone with empty hands must start_cooking; it cooks for \
{k.cook_time} steps; then plate it with a dish (plate_soup) and serve it at the serving hatch: \
+20 points per soup. Soups with fewer than 3 onions score 0. You can hold one item at a time.
{counters}
{walking}

ACTIONS
You choose high-level actions; walking there is automatic (shortest path, one tile per step) \
and the action finishes when you interact. Actions:
{acts}
Every action except move_to and wait takes an optional target: a pot for pot actions \
(put_in_pot(pot_2)) or the (x, y) of a tile (pick_up_from_counter(3, 2)). Without a target you \
use the nearest suitable one (put_in_pot: the fullest pot that isn't full). If there is nothing \
to do yet (e.g. no item on the counter), you wait (by the target, if you named one) for up to \
{WAIT_LIMIT} steps; then the action fails.
"""
