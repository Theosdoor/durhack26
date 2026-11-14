"""Text the LLM agents read: the per-decision state view and the static rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from undercooked.config import Config
from undercooked.engine.actions import WAIT_MAX, WAIT_MIN
from undercooked.engine.game import BLOCKED_LIMIT

if TYPE_CHECKING:
    from undercooked.engine.game import Game

_ITEM = {None: "nothing", "onion": "an onion", "dish": "a dish", "soup": "a soup"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def map_lines(game: "Game") -> list[str]:
    """The map with a column header, row labels and agents shown by their letters."""
    k = game.kitchen
    rows = [list(r) for r in k.map_rows()]
    for a in game.agents:
        x, y = a.pos
        rows[y][x] = a.letter
    lw = len(str(k.height - 1))
    out = [" " * (lw + 1) + "".join(str(x % 10) for x in range(k.width))]
    out += [f"{y:>{lw}} " + "".join(r) for y, r in enumerate(rows)]
    return out


def _pot_text(p) -> str:
    if p.ready:
        what = "soup ready to plate"
    elif p.cooking:
        what = f"cooking, ready in {p.ready_in} s"
    elif p.onions == 0:
        what = "empty"
    else:
        what = f"{_plural(p.onions, 'onion')}, not cooking"
    return f"{p.id} ({p.pos[0]},{p.pos[1]}): {what}."


def state_text(game: "Game", agent: str, *, unread_posts: int, notepad: str | None,
               last_plan_result: str) -> str:
    me = game.agent(agent)
    k = game.kitchen
    if game.phase == "practice":
        where = "Practice"
    else:
        where = f"Round {game.round} of {game.cfg.rounds}"
    lines = [f"Time {game.t} / {game.duration} s · Score {game.score} · {where}"]
    if game.orders:
        lines.append("Orders: " + ", ".join(f"onion soup (due {o.due_t} s)"
                                            for o in sorted(game.orders, key=lambda o: o.due_t)))
    else:
        lines.append("Orders: none open.")
    lines += ["", *map_lines(game), ""]
    result = last_plan_result.strip().rstrip(".")
    lines.append(f"You ({me.name}, {me.letter}): at ({me.pos[0]},{me.pos[1]}), "
                 f"holding {_ITEM.get(me.holding, me.holding)}. Last plan: {result}.")
    for a in game.agents:
        if a is not me:
            lines.append(f"{a.name} ({a.letter}): at ({a.pos[0]},{a.pos[1]}), "
                         f"holding {_ITEM.get(a.holding, a.holding)}.")
    if game.pots:
        lines.append(" ".join(_pot_text(p) for p in game.pots.values()))
    shown = {o.pos for o in k.shared_counters()} | set(game.counters)
    if shown:
        parts = [f"({x},{y}) {game.counters.get((x, y), 'empty')}"
                 for (x, y) in sorted(shown, key=lambda p: (p[1], p[0]))]
        lines.append("Counters: " + ", ".join(parts) + ".")
    else:
        lines.append("Counters: all empty.")
    lines.append(f"Board: {_plural(unread_posts, 'unread post')}." if unread_posts
                 else "Board: no unread posts.")
    if notepad is not None:
        lines.append(f'Your notepad: "{notepad}"')
    return "\n".join(lines)


def rules_text(cfg: Config) -> str:
    """Plain-English rules, controls, map key and interact table for the system prompt."""
    o = cfg.orders
    return f"""\
HOW THE KITCHEN WORKS
Time runs in game seconds. In one second you either walk one tile (up, down, left or right; no
diagonals) or do one interaction. You can only walk on floor tiles; counters, crates, racks,
pots and the hatch block movement, and so do other players.

Recipe (the only dish): onion soup. Put 3 onions in a pot; it starts cooking by itself when the
3rd onion goes in and is ready {cfg.cook_s} s later. Take a dish to the ready pot to plate the
soup, then carry the soup to the serving hatch. You can hold one item at a time: an onion, a
dish or a soup. Onions and dishes never run out.

Orders: the first order arrives at the start, then a new one every {o.every_s} s (no new order
while {o.max_open} are open). Each order is due {o.due_s} s after it arrives. Serving a soup
fills the open order that is due soonest: +{o.reward} points. An order not served by its due
time expires: -{o.penalty} points. A soup served with no open order is refused.

MAP KEY
X counter (a wall; any counter next to the floor can hold one item)   ' ' floor
O onion crate   D dish rack   P pot   S serving hatch
Capital letters on the floor are players (the first letter of their name).
Coordinates are (x, y): x is the column (left to right), y is the row (top to bottom),
both starting at 0, as labelled around the map.

ACTIONS (your plan is a list of 1 to {cfg.max_plan_len} of these, done in order)
interact(<id>)    walk next to the object (shortest path), then interact with it (1 s).
move_to(x, y)     walk to that floor tile.
move_to(<id>)     walk next to that object without interacting.
wait(<seconds>)   stand still for {WAIT_MIN} to {WAIT_MAX} s.
Object ids are numbered in reading order (row by row, left to right): onion_crate_1,
dish_rack_1, pot_1, pot_2, hatch_1, ... Counters are named by position: counter_2_1 is the
counter at (2,1). An unnumbered id (onion_crate, dish_rack, pot, hatch) means the nearest one
you can reach. You interact from any floor tile beside the object; facing does not matter.

INTERACT TABLE (anything else does nothing and your plan fails, with the reason)
onion crate + empty hands  -> you take an onion
dish rack   + empty hands  -> you take a dish
counter     + holding item -> you put it on the counter (if the counter is empty)
counter     + empty hands  -> you pick up the item on the counter
pot         + onion        -> the onion goes in (max 3; not while cooking or ready)
pot         + dish         -> if the soup is ready, you now hold the soup and the pot is empty
hatch       + soup         -> served: fills the open order due soonest

PLANS
Your plan runs step by step until it is done or fails. If another player is in your way you
wait for them; if you are blocked for {BLOCKED_LIMIT} seconds in a row the plan fails ("blocked by <name>").
A plan also fails if an action cannot be understood, its target does not exist or cannot be
reached from where you are, or an interaction does nothing. A new plan replaces the old one.
"""
