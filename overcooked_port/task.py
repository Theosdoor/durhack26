"""Inspect task: one Sample = one season; `epochs` repeats it.

    uv run inspect eval overcooked_port/task.py -T rounds=3 -T round_steps=60
    uv run inspect eval overcooked_port/task.py -T mixed=true      # DeepSeek + Gemma
    uv run inspect eval overcooked_port/task.py -T layout=coordination_ring

Layouts (the four classic ones, onion soup only): forced_coordination, asymmetric_advantages,
coordination_ring, counter_circuit (= Overcooked-AI's counter_circuit_o_1order). Any other
onion-only layout name in overcooked_ai_py/data/layouts also works.

The task-level model is unused (each agent has its own pinned OpenRouter model); leave it unset.
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState

from overcooked_port.season import STORE_KEY, AgentSpec, Config, season_solver

DEEPSEEK = AgentSpec(model="openrouter/deepseek/deepseek-v4.1-flash", provider="baidu/fp8")
GEMMA = AgentSpec(model="openrouter/google/gemma-4-31b-it", provider="deepinfra/fp8")


@scorer(metrics={"*": [mean(), stderr()]})
def season_score():
    async def score(state: TaskState, target: Target) -> Score:
        s = state.store.get(STORE_KEY)
        v = {"total": sum(r["score"] for r in s["rounds"]),
             **{f"round_{i + 1}": r["score"] for i, r in enumerate(s["rounds"])}}
        for key in ("posts", "cutoffs", "decisions"):
            for i, n in enumerate(s[key]):
                v[f"{key}_agent{i}"] = n
        return Score(value=v, explanation=str(s), metadata=s)

    return score


def make_task(cfg: Config, epochs: int = 1, models=None) -> Task:
    return Task(dataset=[Sample(id=cfg.name, input=f"Overcooked-AI season ({cfg.layout})")],
                solver=season_solver(cfg, models), scorer=season_score(), epochs=epochs,
                name="overcooked_port", metadata={"config": cfg.model_dump(mode="json")})


# the names used in the Overcooked-AI demo -> layout files (the demo's Counter Circuit is the
# onion-only variant; the file called counter_circuit has tomatoes, which we don't support)
LAYOUTS = {"counter_circuit": "counter_circuit_o_1order"}


@task
def overcooked_port(rounds: int = 3, round_steps: int = 100, epochs: int = 1, seed: int = 0,
                    mixed: bool = False, layout: str = "forced_coordination") -> Task:
    agents = [DEEPSEEK, GEMMA] if mixed else [DEEPSEEK, DEEPSEEK]
    cfg = Config(rounds=rounds, round_steps=round_steps, seed=seed, agents=agents,
                 layout=LAYOUTS.get(layout, layout),
                 name=f"{layout}_{'mixed' if mixed else 'deepseek'}")
    return make_task(cfg, epochs)
