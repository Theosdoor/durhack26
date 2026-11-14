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
from inspect_ai.scorer import Score, Target, grouped, mean, scorer, stderr
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
    # cutoffs, forced, decisions: round decisions only; *_practice, *_chat, *_debrief: those
    # phases. (Logs from before this split counted practice in cutoffs and forced.)
    for key in ("posts", "read_board", "cutoffs", "forced", "decisions", "cutoffs_practice",
                "forced_practice", "decisions_practice", "cutoffs_chat", "forced_chat",
                "cutoffs_debrief", "forced_debrief", "practice_finished"):
        for i, n in enumerate(s[key]):
            value[f"{key}_agent{i}"] = n
    return value


def season_explanation(s: dict[str, Any]) -> str:
    v = season_values(s)
    names = s["agents"]
    rounds = ", ".join(f"r{i + 1} {r['score']}" for i, r in enumerate(s["rounds"]))

    def per(key: str) -> str:
        return " / ".join(f"{n} {c}" for n, c in zip(names, s[key]))

    team = " & ".join(f"{n} ({m})" for n, m in zip(names, s["models"]))
    return (f"{s.get('level', '?')}: {team}: team score "
            f"{v['total']:g} ({rounds}); served {v['served']:g}, missed {v['missed']:g}. "
            f"Posts {per('posts')}; read_board {per('read_board')}; cutoffs {per('cutoffs')}; "
            f"round decisions {per('decisions')}.")


def _by_level() -> list:
    """mean() per level (sample metadata "level"); one value per level, named `grouped_<level>`
    in the log's metrics."""
    return [mean(), stderr(), grouped(mean(), "level", all=False)]


@scorer(metrics={"total": _by_level(), "served": _by_level(), "missed": _by_level(),
                 "*": [mean(), stderr()]})
def season_score():
    async def score(state: TaskState, target: Target) -> Score:
        s = state.store.get(STORE_KEY)
        if s is None:
            raise RuntimeError("season summary missing from the sample store")
        return Score(value=season_values(s), explanation=season_explanation(s),
                     metadata={"agents": s["agents"], "providers": s["providers"]})

    return score


def dataset(cfg: Config) -> list[Sample]:
    """One sample per level (sample id = the level when `levels` is set, else the config name);
    the season reads its level from the sample metadata."""
    if cfg.levels is None:
        return [Sample(id=cfg.name, input=f"Undercooked season ({cfg.name}, level {cfg.level})",
                       metadata={"level": cfg.level})]
    return [Sample(id=level, input=f"Undercooked season ({cfg.name}, level {level})",
                   metadata={"level": level}) for level in cfg.levels]


def make_task(cfg: Config) -> Task:
    return Task(
        dataset=dataset(cfg),
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
                epochs: int | None = None, pipeline: bool | None = None,
                reasoning: str | None = None, comms: str | None = None,
                debrief: str | None = None, levels: str | list[str] | None = None) -> Task:
    """`levels`: a list, or names joined by commas ("cramped_room,split_room"); replaces the
    config's level / levels."""
    if isinstance(levels, str):
        levels = [x.strip() for x in levels.split(",") if x.strip()]
    cfg = load_config(config, rounds=rounds, round_s=round_s, practice=practice, epochs=epochs,
                      comms=comms, debrief=debrief, levels=levels)
    if pipeline is not None:
        cfg = cfg.model_copy(update={"decision": cfg.decision.model_copy(
            update={"pipeline": pipeline})})
    if reasoning is not None:                        # validated like the YAML field
        cfg = Config.model_validate({**cfg.model_dump(), "decision": {
            **cfg.decision.model_dump(), "reasoning": reasoning}})
    return make_task(cfg)
