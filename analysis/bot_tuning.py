"""Level tuning with scripted bots: `uv run python analysis/bot_tuning.py [config.yaml]`.

Prints score / served / missed for the solo bot on practice, a lone solo bot and the bot pair on
split_room, and 1 vs 2 solo bots on open_plan (the design wants 2 bots to score about double).
"""

from __future__ import annotations

import sys
from collections import Counter

from undercooked.bots import SoloBot, run, split_room_pair
from undercooked.config import AgentSpec, Config, Orders
from undercooked.engine import Game, Kitchen

NAMES = ["Bravo", "Kilo"]


def play(cfg: Config, level: str, n: int, *, pair: bool = False, phase: str = "round",
         split_pots: bool = False) -> str:
    game = Game(Kitchen.load(level), NAMES[:n], cfg, phase=phase,  # type: ignore[arg-type]
                round=0 if phase == "practice" else 1)
    pots = list(game.pots)
    if pair:
        bots = split_room_pair(game)
    elif split_pots:  # bot i only uses pot i+1
        bots = {x: SoloBot(x, pots=[pots[i % len(pots)]]) for i, x in enumerate(NAMES[:n])}
    else:
        bots = {x: SoloBot(x) for x in NAMES[:n]}
    events = run(game, bots)
    first = next((e.t for e in events if e.type == "serve"), None)
    kinds = Counter(e.type for e in events)
    return (f"score={game.score} served={game.served} missed={game.missed} "
            f"first_serve_t={first} fails={kinds['fail']} bumps={kinds['bump']}")


def main() -> None:
    if len(sys.argv) > 1:
        cfg = Config.load(sys.argv[1])
    else:
        cfg = Config(name="tuning", level="open_plan", agents=[AgentSpec(model="bot")] * 2)
    print(f"orders={cfg.orders.model_dump()} cook_s={cfg.cook_s} round_s={cfg.round_s}")
    print(f"practice  1 solo  ({cfg.practice_s} s):", play(cfg, "practice", 1, phase="practice"))
    print(f"split_room 1 solo ({cfg.round_s} s):", play(cfg, "split_room", 1))
    print(f"split_room pair   ({cfg.round_s} s):", play(cfg, "split_room", 2, pair=True))
    print(f"open_plan 1 solo  ({cfg.round_s} s):", play(cfg, "open_plan", 1))
    print(f"open_plan 2 solo  ({cfg.round_s} s):", play(cfg, "open_plan", 2))
    print(f"open_plan 2 solo, one pot each:", play(cfg, "open_plan", 2, split_pots=True))
    for every in (20, 15, 12, 10):
        alt = cfg.model_copy(update={"orders": Orders(**{**cfg.orders.model_dump(),
                                                         "every_s": every})})
        print(f"  every_s={every}: 1 solo:", play(alt, "open_plan", 1))
        print(f"  every_s={every}: 2 solo:", play(alt, "open_plan", 2))
        print(f"  every_s={every}: 2 solo, one pot each:",
              play(alt, "open_plan", 2, split_pots=True))
        print(f"  every_s={every}: split_room pair:", play(alt, "split_room", 2, pair=True))


if __name__ == "__main__":
    main()
