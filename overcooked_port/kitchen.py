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
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld, OvercookedState, Recipe
from overcooked_ai_py.planning.planners import MotionPlanner

from overcooked_port.replay import AgentState, Event, Frame, Phase, PotState

# name -> (what you must hold before, description)
ACTIONS: dict[str, tuple[str, str]] = {
    "get_onion": ("nothing", "pick up an onion from the onion crate or a counter"),
    "get_dish": ("nothing", "pick up a dish from the dish rack or a counter"),
    "put_in_pot": ("onion", "put the onion you hold into a pot that is not full"),
    "start_cooking": ("nothing", "start cooking a pot that has onions in it (3 onions = soup)"),
    "plate_soup": ("dish", "scoop a cooked soup from a pot onto your dish (waits by the pot "
                           "if it is still cooking)"),
    "serve": ("soup", "deliver the soup you hold at the serving hatch (+20 for a 3-onion soup)"),
    "place_on_counter": ("anything", "put what you hold on the nearest empty counter that your "
                                     "teammate can also reach"),
    "pick_up_from_counter": ("nothing", "pick up the nearest item on a counter you can reach"),
    "move_to": ("anything", "walk to the floor tile (x, y), e.g. to get out of your "
                            "teammate's way"),
    "wait": ("anything", "stand still for n steps (1-10)"),
}
SIGNATURE = {"move_to": "move_to(x, y)", "wait": "wait(n)"}
WAIT_RE = re.compile(r"^wait\((\d+)\)$")
MOVE_RE = re.compile(r"^move_to\(\(?(\d+),(\d+)\)?\)$")
STUCK_STEPS = 8          # an action blocked by the teammate this many steps in a row fails


def parse_action(s: str) -> tuple[str, int | tuple[int, int]] | None:
    """'get_onion' -> ('get_onion', 0); 'wait(3)' -> ('wait', 3);
    'move_to(2, 3)' -> ('move_to', (2, 3)); unknown -> None."""
    s = s.strip().lower().replace(" ", "")
    if s.endswith("()"):
        s = s[:-2]
    if m := WAIT_RE.match(s):
        return ("wait", max(1, min(10, int(m.group(1)))))
    if s == "wait":
        return ("wait", 1)
    if m := MOVE_RE.match(s):
        return ("move_to", (int(m.group(1)), int(m.group(2))))
    return (s, 0) if s in ACTIONS and s != "move_to" else None


@dataclass
class Player:
    name: str
    index: int                            # Overcooked player index
    plan: list[str] = field(default_factory=list)   # remaining actions, current first
    waited: int = 0                       # steps done of the current wait(n)
    stuck: int = 0                        # steps without progress on the current action
    status: str = "idle"


class Kitchen:
    def __init__(self, layout: str, names: list[str]):
        """`names[i]` plays Overcooked player i (start position i+1 in the layout)."""
        self.layout = layout
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

    def map_rows(self) -> list[str]:
        """Terrain rows (no players), Overcooked letters: X counter, O onions, D dishes, P pot,
        S serving hatch, ' ' floor."""
        return ["".join(row) for row in self.mdp.terrain_mtx]

    # ------------------------------------------------------------------ actions
    def holding(self, p: Player) -> str | None:
        obj = self.state.players[p.index].held_object
        return obj.name if obj is not None else None

    def _targets(self, p: Player, name: str) -> list:
        """Terrain positions the action can be done at, right now."""
        m, s = self.mdp, self.state
        objs = m.get_counter_objects_dict(s)
        pots = m.get_pot_states(s)
        if name == "get_onion":
            return m.get_onion_dispenser_locations() + objs["onion"]
        if name == "get_dish":
            return m.get_dish_dispenser_locations() + objs["dish"]
        if name == "put_in_pot":
            return pots["empty"] + m.get_partially_full_pots(pots)
        if name == "start_cooking":
            return m.get_partially_full_pots(pots) + m.get_full_but_not_cooking_pots(pots)
        if name == "plate_soup":
            return pots["ready"] + pots["cooking"]
        if name == "serve":
            return m.get_serving_locations()
        if name == "place_on_counter":
            empty = set(m.get_empty_counter_locations(s))
            return [c for c in self.shared_counters if c in empty]
        if name == "pick_up_from_counter":
            return [pos for pos in s.objects if m.get_terrain_type_at_pos(pos) == "X"]
        return []

    def check(self, p: Player, action: str) -> str | None:
        """Why `action` can't start now for `p` (None = it can)."""
        parsed = parse_action(action)
        if parsed is None:
            return (f"unknown action {action!r}; use one of "
                    + ", ".join(SIGNATURE.get(a, a) for a in ACTIONS))
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
        if not self._targets(p, name):
            return f"{name}: nothing to do that with right now"
        if not self._goals(p, self._targets(p, name)):
            return f"{name}: you can't reach anywhere to do that from your side of the kitchen"
        return None

    def set_plan(self, p: Player, plan: list[str]) -> str | None:
        """Replace p's plan. Only the first action is checked now (later ones may become
        possible as the kitchen changes); returns why the first one was rejected."""
        p.plan, p.waited, p.stuck = [], 0, 0
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
        """Lower = preferred: fuller pots first for put_in_pot, ready pots first for plating."""
        if name == "put_in_pot":
            return -self._pot(target).onions
        if name == "plate_soup":
            return 0 if self._pot(target).state == "ready" else 1
        if name in ("get_onion", "get_dish"):   # crate/rack before counters (don't take back
            return 1 if self.mdp.get_terrain_type_at_pos(target) == "X" else 0   # a pass)
        return 0

    def _low_level(self, p: Player, events: list[Event], phase: Phase, round: int):
        """Overcooked action for p this step; pops finished actions, logs fails."""
        if not p.plan:
            return Action.STAY
        name, arg = parse_action(p.plan[0]) or ("?", 0)
        if name == "wait":
            p.waited += 1
            if p.waited >= arg:
                p.plan.pop(0)
                p.waited = 0
            return Action.STAY
        err = self.check(p, p.plan[0])
        if err is None and p.stuck > STUCK_STEPS:
            mate = [q for q in self.players if q is not p][0]
            x, y = self.state.players[mate.index].position
            err = (f"{name}: {mate.name} at ({x},{y}) blocked your way for {STUCK_STEPS} "
                   f"steps. Ask them to move, go another way, or move_to somewhere else")
        if err is not None:
            events.append(Event(phase=phase, round=round, t=self.t, type="fail",
                                agent=p.name, data={"action": p.plan[0], "reason": err}))
            p.plan, p.stuck = [], 0
            return Action.STAY
        start = self._pos_or(p)
        if name == "move_to":
            if start[0] == arg:
                p.plan.pop(0)
                p.stuck = 0
                return Action.STAY
            route = self._route(p, [(arg, None)])
        else:
            goals = self._goals(p, self._targets(p, name))
            route = self._route(p, [(g, t) for g, t in goals], name)
        if route is None:                    # every way there is blocked by the teammate
            p.stuck += 1
            return Action.STAY
        actions, target = route
        if actions == [Action.INTERACT]:
            if name == "plate_soup" and not self.mdp.soup_ready_at_location(self.state, target):
                return Action.STAY            # wait by the cooking pot (not "stuck")
            p.plan.pop(0)
            p.stuck = 0
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
            if ps.state == "filling":
                extra += " (not cooking: needs start_cooking)"
            lines.append(f"{pid} at ({pos[0]},{pos[1]}): {ps.onions} onions, {ps.state}{extra}.")
        c = self.counters()
        lines.append("Items on counters: " + (", ".join(f"{v} at ({k})" for k, v in c.items())
                                              if c else "none") + ".")
        return "\n".join(lines)


def rules_text(k: Kitchen) -> str:
    acts = "\n".join(f"- {SIGNATURE.get(a, a)}: {d} [hold: {h}]"
                     for a, (h, d) in ACTIONS.items())
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
Cooks can't walk through each other. Walking goes around your teammate if there is another \
way; if there isn't, you wait, and after {STUCK_STEPS} blocked steps the action fails.

ACTIONS
You choose high-level actions; walking there is automatic (shortest path, one tile per step) \
and the action finishes when you interact. Actions:
{acts}
"""
