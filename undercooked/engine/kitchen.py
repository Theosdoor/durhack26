"""Static kitchen layout: tiles, objects (with ids), start positions, rooms and pathfinding.

Level files (levels/*.txt): `X` counter, ` ` floor, `O` onion crate, `D` dish rack, `P` pot,
`S` serving hatch, `B` bin (trash), `1`..`9` start positions (floor). Coordinates are (x=column, y=row).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from undercooked.config import LEVELS_DIR

Pos = tuple[int, int]

KIND_BY_CHAR = {"X": "counter", "O": "onion_crate", "D": "dish_rack", "P": "pot", "S": "hatch", "B": "bin"}
KINDS = ("onion_crate", "dish_rack", "pot", "hatch", "bin", "counter")
# Neighbour order is fixed so that pathfinding is deterministic: up, right, down, left.
DIRS: dict[str, Pos] = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}


@dataclass(frozen=True)
class Obj:
    """A non-floor tile the agents can interact with."""

    id: str     # "pot_1", "onion_crate_2", "counter_2_1", ...
    kind: str   # one of KINDS
    pos: Pos


class Kitchen:
    """Immutable level layout. Build with `Kitchen.load("forced_coordination")`."""

    def __init__(self, name: str, rows: list[str], bins: bool = False):
        if not rows:
            raise ValueError(f"level {name!r} is empty")
        self.name = name
        if not bins:    # config bins: false: the old layouts, every bin is a plain counter
            rows = [r.replace("B", "X") for r in rows]
        self.width = max(len(r) for r in rows)
        self.height = len(rows)
        # Pad ragged rows with counters so the kitchen is always enclosed.
        self.rows = [r.ljust(self.width, "X") for r in rows]
        self.floor: frozenset[Pos] = frozenset()
        self.starts: dict[int, Pos] = {}
        objs: list[Obj] = []
        counts: dict[str, int] = {}
        floor: set[Pos] = set()
        for y, row in enumerate(self.rows):
            for x, ch in enumerate(row):
                if ch == " ":
                    floor.add((x, y))
                elif ch.isdigit() and ch != "0":
                    floor.add((x, y))
                    self.starts[int(ch)] = (x, y)
                elif ch in KIND_BY_CHAR:
                    kind = KIND_BY_CHAR[ch]
                    if kind == "counter":
                        oid = f"counter_{x}_{y}"
                    else:
                        counts[kind] = counts.get(kind, 0) + 1
                        oid = f"{kind}_{counts[kind]}"
                    objs.append(Obj(oid, kind, (x, y)))
                else:
                    raise ValueError(f"level {name!r}: unknown tile {ch!r} at ({x},{y})")
        self.floor = frozenset(floor)
        self.objects: dict[str, Obj] = {o.id: o for o in objs}   # reading order
        self.by_pos: dict[Pos, Obj] = {o.pos: o for o in objs}
        # Rooms = connected components of floor. Agents can never leave their room.
        self.room_of: dict[Pos, int] = {}
        for p in sorted(self.floor, key=lambda q: (q[1], q[0])):
            if p in self.room_of:
                continue
            rid = len(set(self.room_of.values()))
            for q in self._flood(p):
                self.room_of[q] = rid

    # ----------------------------------------------------------------- loading
    @classmethod
    def load(cls, level: str | Path, bins: bool = False) -> "Kitchen":
        """Load `levels/<level>.txt` (or any path to a level file). `bins=False` (the default, as
        Config.bins) turns every `B` into a counter."""
        p = Path(level)
        if not (p.suffix == ".txt" or p.exists()):
            p = LEVELS_DIR / f"{level}.txt"
        text = p.read_text()
        rows = [r.rstrip("\n") for r in text.split("\n")]
        while rows and not rows[-1].strip():
            rows.pop()
        return cls(p.stem, rows, bins)

    # ------------------------------------------------------------------ basics
    def map_rows(self) -> list[str]:
        """Level rows with start digits replaced by floor (" ")."""
        return ["".join(" " if c.isdigit() else c for c in r) for r in self.rows]

    def is_floor(self, p: Pos) -> bool:
        return p in self.floor

    def of_kind(self, kind: str) -> list[Obj]:
        return [o for o in self.objects.values() if o.kind == kind]

    def floor_neighbours(self, p: Pos) -> list[Pos]:
        x, y = p
        return [(x + dx, y + dy) for dx, dy in DIRS.values() if (x + dx, y + dy) in self.floor]

    def access_tiles(self, obj_id: str) -> list[Pos]:
        """Floor tiles orthogonally adjacent to an object (where an agent can interact with it)."""
        return self.floor_neighbours(self.objects[obj_id].pos)

    def rooms_touching(self, obj_id: str) -> set[int]:
        return {self.room_of[p] for p in self.access_tiles(obj_id)}

    def reachable(self, from_pos: Pos, obj_id: str) -> bool:
        """Can an agent standing at `from_pos` get next to `obj_id` (ignoring other agents)?"""
        return self.room_of.get(from_pos) in self.rooms_touching(obj_id)

    def usable_counters(self) -> list[Obj]:
        """Counters next to at least one floor tile (the rest are just walls)."""
        return [o for o in self.of_kind("counter") if self.access_tiles(o.id)]

    def shared_counters(self) -> list[Obj]:
        """Counters reachable from two or more rooms: the only way to pass items between rooms."""
        return [o for o in self.of_kind("counter") if len(self.rooms_touching(o.id)) >= 2]

    # ------------------------------------------------------------- pathfinding
    def _flood(self, start: Pos) -> set[Pos]:
        seen = {start}
        q = deque([start])
        while q:
            for n in self.floor_neighbours(q.popleft()):
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return seen

    def path(self, start: Pos, goals: Iterable[Pos], blocked: Iterable[Pos] = ()) -> list[Pos] | None:
        """Shortest floor path from `start` to any goal, avoiding `blocked` tiles.

        Returns the tiles after `start` (empty list if already on a goal), or None if no path.
        Deterministic: neighbours are explored up, right, down, left.
        """
        goal_set = set(goals) - set(blocked)
        if start in set(goals):
            return []
        blocked_set = set(blocked)
        prev: dict[Pos, Pos | None] = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur in goal_set:
                out = []
                while cur != start:
                    out.append(cur)
                    cur = prev[cur]  # type: ignore[assignment]
                return out[::-1]
            for n in self.floor_neighbours(cur):
                if n not in prev and n not in blocked_set:
                    prev[n] = cur
                    q.append(n)
        return None

    def distance(self, start: Pos, goals: Iterable[Pos]) -> int | None:
        p = self.path(start, goals)
        return None if p is None else len(p)
