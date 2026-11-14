"""Inspect task: one Sample = one season; `epochs` repeats it.

    uv run undercooked run configs/split_room.yaml
    uv run inspect eval undercooked/task.py -T config=configs/split_room.yaml

The agents use their own models (config `agents`, see providers.py). The task-level model is not
used for anything: leave it unset (Inspect then uses `none/none`, which fails loudly if anything
ever calls it) or pass any model, e.g. `--model mockllm/model`. The agents' models are recorded
in the task metadata, the Header records and the sample metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState

from undercooked.config import REPO_ROOT, Config
from undercooked.season import STORE_KEY, season_solver


def load_config(path: str | Path, **overrides: Any) -> Config:
    """Load a YAML config (relative paths: cwd first, then the repo root) and apply overrides
    that are not None (rounds, round_s, practice, epochs, ...)."""
    p = Path(path)
    if not p.exists() and not p.is_absolute() and (REPO_ROOT / p).exists():
        p = REPO_ROOT / p
    cfg = Config.load(p)
    updates = {k: v for k, v in overrides.items() if v is not None}
    return Config.model_validate({**cfg.model_dump(), **updates}) if updates else cfg


def season_values(s: dict[str, Any]) -> dict[str, float]:
    rounds = s["rounds"]
    value: dict[str, float] = {
        "total": sum(r["score"] for r in rounds),
        **{f"round_{i + 1}": r["score"] for i, r in enumerate(rounds)},
        "served": sum(r["served"] for r in rounds),
        "missed": sum(r["missed"] for r in rounds),
    }
    for key in ("posts", "read_board", "cutoffs", "decisions"):
        for i, n in enumerate(s[key]):
            value[f"{key}_agent{i}"] = n
    return value


def season_explanation(s: dict[str, Any]) -> str:
    v = season_values(s)
    names = s["agents"]
    rounds = ", ".join(f"r{i + 1} {r['score']}" for i, r in enumerate(s["rounds"]))

    def per(key: str) -> str:
        return " / ".join(f"{n} {c}" for n, c in zip(names, s[key]))

    return (f"{' & '.join(f'{n} ({m})' for n, m in zip(names, s['models']))}: team score "
            f"{v['total']:g} ({rounds}); served {v['served']:g}, missed {v['missed']:g}. "
            f"Posts {per('posts')}; read_board {per('read_board')}; cutoffs {per('cutoffs')}; "
            f"round decisions {per('decisions')}.")


@scorer(metrics={"*": [mean(), stderr()]})
def season_score():
    async def score(state: TaskState, target: Target) -> Score:
        s = state.store.get(STORE_KEY)
        if s is None:
            raise RuntimeError("season summary missing from the sample store")
        return Score(value=season_values(s), explanation=season_explanation(s),
                     metadata={"agents": s["agents"], "providers": s["providers"]})

    return score


def make_task(cfg: Config) -> Task:
    return Task(
        dataset=[Sample(id=cfg.name, input=f"Undercooked season ({cfg.name}, level {cfg.level})")],
        solver=season_solver(cfg),
        scorer=season_score(),
        epochs=cfg.epochs,
        name="undercooked",
        metadata={"config": cfg.model_dump(mode="json"),
                  "agents": [a.model_dump(mode="json") for a in cfg.agents]},
    )


@task
def undercooked(config: str = "configs/split_room.yaml", rounds: int | None = None,
                round_s: int | None = None, practice: bool | None = None,
                epochs: int | None = None, pipeline: bool | None = None) -> Task:
    cfg = load_config(config, rounds=rounds, round_s=round_s, practice=practice, epochs=epochs)
    if pipeline is not None:
        cfg = cfg.model_copy(update={"decision": cfg.decision.model_copy(
            update={"pipeline": pipeline})})
    return make_task(cfg)
