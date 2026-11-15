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

from undercooked.config import REPO_ROOT, AgentSpec, Config, parse_agents
from undercooked.season import DISCOVERY, STORE_KEY, season_solver


def load_config(path: str | Path, **overrides: Any) -> Config:
    """Load a YAML config (relative paths: cwd first, then the repo root) and apply overrides
    that are not None (rounds, round_s, practice, epochs, ...)."""
    p = Path(path)
    if not p.exists() and not p.is_absolute() and (REPO_ROOT / p).exists():
        p = REPO_ROOT / p
    cfg = Config.load(p)
    updates = {k: v for k, v in overrides.items() if v is not None}
    return Config.model_validate({**cfg.model_dump(), **updates}) if updates else cfg


def override_agents(cfg: Config, agents: str | None = None, model: str | None = None,
                    provider: str | None = None) -> Config:
    """--agents "m1@p1,m2@p2" replaces the agent list (mixed teams); --model MODEL [--provider
    TAG] sets every listed agent to that model and provider (self-play; "MODEL!reasoning" or a
    provider ending "!reasoning" marks reasoning as mandatory). n_agents and level_overrides
    still decide how many agents a season has (the list is cycled)."""
    if agents is not None and model is not None:
        raise ValueError("give --agents or --model, not both")
    if agents is not None:
        specs = parse_agents(agents)
    elif model is not None:
        one = AgentSpec.parse(model + (f"@{provider}" if provider else ""))
        specs = [one] * len(cfg.agents)
    elif provider is not None:
        raise ValueError("--provider needs --model")
    else:
        return cfg
    return Config.model_validate({**cfg.model_dump(), "agents": [s.model_dump() for s in specs]})


def season_values(s: dict[str, Any]) -> dict[str, float]:
    rounds = s["rounds"]
    value: dict[str, float] = {
        "total": sum(r["score"] for r in rounds),
        **{f"round_{i + 1}": r["score"] for i, r in enumerate(rounds)},
        "served": sum(r["served"] for r in rounds),
        "missed": sum(r["missed"] for r in rounds),        # always 0 under scoring "soups"
    }
    # cutoffs, forced, decisions: round decisions only; *_practice, *_chat, *_debrief (the
    # private turns), *_reflection: those phases. (Logs from before this split counted practice
    # in cutoffs and forced.) One key per agent: _agent0 .. _agent{N-1}.
    for key in ("posts", "read_board", "cutoffs", "forced", "decisions", "cutoffs_practice",
                "forced_practice", "decisions_practice", "cutoffs_chat", "forced_chat",
                "cutoffs_debrief", "forced_debrief", "practice_finished", "cutoffs_reflection"):
        for i, n in enumerate(s.get(key, [])):
            value[f"{key}_agent{i}"] = n
    # reflection question 5: each agent's predicted score with full instructions (NaN if it
    # gave none, or there was no reflection); compare with "total"
    for i, p in enumerate(s.get("predicted_score", [])):
        value[f"predicted_score_agent{i}"] = float("nan") if p is None else p
    # discovery: when each agent FIRST put an onion in a pot, started a pot cooking (added its
    # 3rd onion), plated and served: round (0 = practice) and game second; NaN if never (Inspect
    # leaves NaN out of the metrics)
    for i, firsts in enumerate(s.get("firsts", [])):
        for kind in DISCOVERY.values():
            hit = firsts.get(kind)
            for part in ("round", "t"):
                value[f"first_{kind}_{part}_agent{i}"] = float("nan") if hit is None else hit[part]
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
                debrief: str | None = None, levels: str | list[str] | None = None,
                rules: str | None = None, scoring: str | None = None,
                reflection: bool | None = None, agents: str | None = None,
                agent_model: str | None = None, agent_provider: str | None = None) -> Task:
    """`levels`: a list, or names joined by commas ("cramped_room,split_room"); replaces the
    config's level / levels. `agents` ("m1@p1,m2@p2"), `agent_model` and `agent_provider`: see
    override_agents."""
    if isinstance(levels, str):
        levels = [x.strip() for x in levels.split(",") if x.strip()]
    cfg = load_config(config, rounds=rounds, round_s=round_s, practice=practice, epochs=epochs,
                      comms=comms, debrief=debrief, levels=levels, rules=rules, scoring=scoring,
                      reflection=reflection)
    cfg = override_agents(cfg, agents, agent_model, agent_provider)
    if pipeline is not None:
        cfg = cfg.model_copy(update={"decision": cfg.decision.model_copy(
            update={"pipeline": pipeline})})
    if reasoning is not None:                        # validated like the YAML field
        cfg = Config.model_validate({**cfg.model_dump(), "decision": {
            **cfg.decision.model_dump(), "reasoning": reasoning}})
    return make_task(cfg)
