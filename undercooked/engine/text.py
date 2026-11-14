"""Text the LLM agents read: the per-decision state view and the static rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from undercooked.config import Config
from undercooked.engine.actions import WAIT_MAX, WAIT_MIN
from undercooked.engine.game import BLOCKED_LIMIT
from undercooked.promptfiles import load

if TYPE_CHECKING:
    from undercooked.engine.game import Game

_ITEMS = {None: "nothing", "onion": "onion", "dish": "dish", "soup": "soup"}


def _item(holding: str | None) -> str:
    return load("state", f"item.{_ITEMS[holding]}") if holding in _ITEMS else str(holding)


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
        what = load("state", "pot.ready")
    elif p.cooking:
        what = load("state", "pot.cooking", ready_in=p.ready_in)
    elif p.onions == 0:
        what = load("state", "pot.empty")
    else:
        what = load("state", "pot.onions", onions=_plural(p.onions, "onion"))
    return load("state", "pot", id=p.id, x=p.pos[0], y=p.pos[1], what=what)


def state_text(game: "Game", agent: str, *, unread_posts: int, notepad: str | None,
               last_plan_result: str) -> str:
    me = game.agent(agent)
    k = game.kitchen
    if game.phase == "practice":
        where = load("state", "where_practice")
    else:
        where = load("state", "where_round", round=game.round, rounds=game.cfg.rounds)
    lines = [load("state", "header", t=game.t, duration=game.duration, score=game.score,
                  where=where)]
    if game.orders:
        # rules minimal / minimal_hint: the order line names no dish (prompts/state.md)
        item = "order_item_minimal" if game.cfg.rules.startswith("minimal") else "order_item"
        orders = ", ".join(load("state", item, due_t=o.due_t)
                           for o in sorted(game.orders, key=lambda o: o.due_t))
        lines.append(load("state", "orders", orders=orders))
    else:
        lines.append(load("state", "orders_none"))
    lines += ["", *map_lines(game), ""]
    result = last_plan_result.strip().rstrip(".")
    lines.append(load("state", "you", name=me.name, letter=me.letter, x=me.pos[0], y=me.pos[1],
                      item=_item(me.holding), result=result))
    for a in game.agents:
        if a is not me:
            lines.append(load("state", "teammate", name=a.name, letter=a.letter, x=a.pos[0],
                              y=a.pos[1], item=_item(a.holding)))
    if game.pots:
        lines.append(" ".join(_pot_text(p) for p in game.pots.values()))
    shown = {o.pos for o in k.shared_counters()} | set(game.counters)
    if shown:
        parts = [load("state", "counter", x=x, y=y,
                      what=game.counters.get((x, y), load("state", "counter.empty")))
                 for (x, y) in sorted(shown, key=lambda p: (p[1], p[0]))]
        lines.append(load("state", "counters", counters=", ".join(parts)))
    else:
        lines.append(load("state", "counters_none"))
    lines.append(load("state", "board_unread", unread=_plural(unread_posts, "unread post"))
                 if unread_posts else load("state", "board_none"))
    if notepad is not None:
        lines.append(load("state", "notepad", notepad=notepad))
    return "\n".join(lines)


def rules_text(cfg: Config) -> str:
    """Plain-English rules, controls, map key and interact table for the system prompt (the
    kitchen_rules ... plans sections of prompts/system.md).

    cfg.rules other than "full": the recipe and orders paragraphs of kitchen_rules (all but its
    first paragraph, which says how time, walking and interacting work) and the interact table
    are replaced by the section rules_<cfg.rules>; map_key, actions and plans stay."""
    o = cfg.orders
    kw = dict(cook_s=cfg.cook_s, every_s=o.every_s, max_open=o.max_open, due_s=o.due_s,
              reward=o.reward, penalty=o.penalty, max_plan_len=cfg.max_plan_len,
              wait_min=WAIT_MIN, wait_max=WAIT_MAX, blocked_limit=BLOCKED_LIMIT)
    if cfg.rules == "full":
        keys = ("kitchen_rules", "map_key", "actions", "interact_table", "plans")
        return "\n\n".join(load("system", k, **kw) for k in keys) + "\n"
    moving = load("system", "kitchen_rules", **kw).split("\n\n")[0]
    parts = [moving, load("system", f"rules_{cfg.rules}")]
    parts += [load("system", k, **kw) for k in ("map_key", "actions", "plans")]
    return "\n\n".join(parts) + "\n"
