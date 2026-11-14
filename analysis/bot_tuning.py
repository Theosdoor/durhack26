"""Level tuning with scripted bots: `uv run python analysis/bot_tuning.py [config.yaml]`.

For every level, prints score / served / missed of one bot alone (seat 1) and of two bots
(`bots.team`: the split pair on split levels, one SoloBot each elsewhere; on shared levels also
two SoloBots sharing every pot), for each orders.every_s in EVERY_S, over SEEDS tie-order seeds
(mean). The level's chosen every_s (configs/all_levels_nocomms.yaml) should let two bots clearly
beat one without maxing out: the design wants coordination to pay.
"""

from __future__ import annotations

import sys
from statistics import mean

from undercooked.bots import SoloBot, coop_pair, run, team
from undercooked.config import AgentSpec, Config, Orders
from undercooked.engine import Game, Kitchen

NAMES = ["Bravo", "Kilo"]
LEVELS = ["forced_coordination", "split_room", "cramped_room", "asymmetric_advantages",
          "coordination_ring", "counter_circuit"]
EVERY_S = (30, 20, 15)
SEEDS = tuple(range(10))


def play(cfg: Config, level: str, who: str, seed: int) -> tuple[int, int, int]:
    """One round of `level`: who = "1 bot", "2 bots" (bots.team) or "2 shared" (SoloBots that
    share every pot). Returns (score, served, missed)."""
    n = 1 if who == "1 bot" else 2
    game = Game(Kitchen.load(level), NAMES[:n], cfg, seed=f"tuning|{level}|{seed}")
    if who == "1 bot":
        bots = {NAMES[0]: SoloBot(NAMES[0])}
    elif who == "2 shared":
        bots = coop_pair(game, split_pots=False)
    else:
        bots = team(game)
    run(game, bots)
    return game.score, game.served, game.missed


def cell(cfg: Config, level: str, who: str) -> tuple[float, float, float]:
    res = [play(cfg, level, who, s) for s in SEEDS]
    return tuple(mean(r[i] for r in res) for i in range(3))  # type: ignore[return-value]


def with_every(cfg: Config, every: int) -> Config:
    return cfg.model_copy(update={"orders": Orders(**{**cfg.orders.model_dump(),
                                                      "every_s": every})})


def table(cfg: Config, levels: list[str] = LEVELS) -> list[dict]:
    rows = []
    for level in levels:
        shared = len(set(Kitchen.load(level).room_of.values())) == 1
        whos = ["1 bot", "2 bots"] + (["2 shared"] if shared else [])
        for every in EVERY_S:
            alt = with_every(cfg, every)
            rows.append({"level": level, "every_s": every,
                         **{w: cell(alt, level, w) for w in whos}})
    return rows


def fmt(c: tuple[float, float, float] | None) -> str:
    return "" if c is None else f"{c[0]:6.1f} {c[1]:4.1f} {c[2]:4.1f}"


def main() -> None:
    if len(sys.argv) > 1:
        cfg = Config.load(sys.argv[1])
    else:
        cfg = Config(name="tuning", level="forced_coordination",
                     agents=[AgentSpec(model="bot")] * 2)
    o = cfg.orders
    print(f"round_s={cfg.round_s} cook_s={cfg.cook_s} due_s={o.due_s} max_open={o.max_open} "
          f"reward={o.reward} penalty={o.penalty}; mean over tie seeds {list(SEEDS)}")
    print(f"{'level':22s} {'every':>5s}  {'1 bot: score served missed':>27s}  "
          f"{'2 bots: score served missed':>28s}  {'2 shared-pot SoloBots':>22s}")
    for r in table(cfg):
        print(f"{r['level']:22s} {r['every_s']:5d}  {fmt(r['1 bot']):>27s}  "
              f"{fmt(r['2 bots']):>28s}  {fmt(r.get('2 shared')):>22s}")


if __name__ == "__main__":
    main()
