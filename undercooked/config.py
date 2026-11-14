"""Run config: one YAML file per experiment (see configs/). Shared contract for all modules."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# NATO alphabet minus "Alpha" (it reads as "leader", a confound for hierarchy) and minus the
# names whose first letter is a map symbol (Delta=D dish rack, Oscar=O onion crate, Papa=P pot,
# Sierra=S hatch, Xray=X counter): an agent shows on the map as its first letter.
MAP_SYMBOLS = set("XODPS")
NATO_NAMES = [
    "Bravo", "Charlie", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliett",
    "Kilo", "Lima", "Mike", "November", "Quebec", "Romeo",
    "Tango", "Uniform", "Victor", "Whiskey", "Yankee", "Zulu",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Orders(_Strict):
    every_s: int = 30      # a new order arrives every N seconds (first at t=0)
    due_s: int = 60        # each order is due N seconds after it arrives
    max_open: int = 3      # no new order while this many are open
    reward: int = 20       # points for serving an open order
    penalty: int = 10      # points lost when an order expires


class Decision(_Strict):
    mode: Literal["simulated", "realtime"] = "simulated"
    flat_s: int = 2                 # game seconds every decision costs
    post_words_per_s: int = 10      # extra game seconds = ceil(words posted / this)
    reasoning_tokens: int = 1000    # GenerateConfig.reasoning_tokens
    max_tokens: int = 4000          # GenerateConfig.max_tokens: hard cap; DeepSeek on Baidu overshoots reasoning_tokens
    pipeline: bool = True           # overlap model calls across game seconds (same outcomes; season.py)
    reasoning: Literal["on", "off"] = "on"   # "off": every call asks for no reasoning (providers.py)

    @field_validator("mode")
    @classmethod
    def _only_simulated(cls, mode: str) -> str:
        if mode != "simulated":     # kept in the schema for later; season.py has no realtime loop
            raise ValueError("realtime mode is not implemented; use mode: simulated")
        return mode


class Providers(_Strict):
    pick: Literal["cheapest"] = "cheapest"
    min_quant: Literal["fp8", "bf16", "fp16"] = "fp8"


class AgentSpec(_Strict):
    model: str                          # e.g. openrouter/deepseek/deepseek-v4.1-flash
    provider: str | None = None         # OpenRouter endpoint tag, e.g. "baidu/fp8"; None = auto-pick


class LevelOverride(_Strict):
    """Settings that differ for one level of a multi-level run (Config.level_overrides). Only
    the fields given change: `orders: {every_s: 20}` keeps the run's other order settings."""

    orders: Orders | None = None
    round_s: int | None = None


class Config(_Strict):
    name: str
    level: str | None = None            # file stem in levels/ (a single-level run)
    # several levels per run: one Inspect sample (one season) per level, each repeated `epochs`
    # times; when set, it replaces `level`
    levels: list[str] | None = None
    level_overrides: dict[str, LevelOverride] = {}    # per-level orders / round_s
    rounds: int = 3
    round_s: int = 180
    practice: bool = True
    practice_level: str = "practice"
    practice_s: int = 60
    chat_max_posts: int = 6
    orders: Orders = Field(default_factory=Orders)
    cook_s: int = 20
    decision: Decision = Field(default_factory=Decision)
    max_plan_len: int = 5
    epochs: int = 10                    # seasons per run (Inspect epochs)
    seed: int = 0
    names: Literal["nato_random"] = "nato_random"
    board: Literal["pull", "push"] = "pull"
    # who can talk when: "full" = planning chat + board in rounds; "planning_only" = planning chat
    # only (no board in practice or rounds); "none" = no chat and no board at all
    comms: Literal["full", "planning_only", "none"] = "full"
    # "full" = a debrief turn after each round; "score_only" = no debrief turn: the round's result
    # opens the agent's next message (note() still works in practice and rounds)
    debrief: Literal["full", "score_only"] = "full"
    # how much of the kitchen the system prompt explains: "full" = recipe, orders and the
    # interact table; "recipe" = what scores, not how the objects work; "minimal" = only that
    # delivering at the hatch scores; "minimal_hint" = minimal + "try everything in practice"
    rules: Literal["full", "recipe", "minimal", "minimal_hint"] = "full"
    compact_at_tokens: int = 32000
    providers: Providers = Field(default_factory=Providers)
    agents: list[AgentSpec]

    @model_validator(mode="after")
    def _check_levels(self) -> "Config":
        if self.levels is not None:
            if not self.levels:
                raise ValueError("levels is empty; give at least one level (or use level)")
            if len(set(self.levels)) != len(self.levels):
                raise ValueError(f"levels has duplicates: {self.levels}")
        elif self.level is None:
            raise ValueError("set level (one level) or levels (several)")
        # an override for a level this run doesn't play is fine (--levels may pick a subset),
        # but not one for a level that doesn't exist (a typo would silently do nothing)
        unknown = sorted(k for k in self.level_overrides if not (LEVELS_DIR / f"{k}.txt").exists())
        if unknown:
            raise ValueError(f"level_overrides for unknown levels {unknown} "
                             f"(no levels/<name>.txt)")
        return self

    def run_levels(self) -> list[str]:
        """The levels this run plays: `levels`, or [`level`]."""
        return list(self.levels) if self.levels is not None else [self.level]  # type: ignore[list-item]

    def for_level(self, level: str) -> "Config":
        """The config one season plays: `level` set, its level_overrides applied (only the
        fields the override gives), `levels` and `level_overrides` kept as they were."""
        if level not in self.run_levels():
            raise ValueError(f"{level!r} is not one of this run's levels {self.run_levels()}")
        update: dict = {"level": level}
        ov = self.level_overrides.get(level)
        if ov is not None:
            if ov.orders is not None:
                update["orders"] = self.orders.model_copy(
                    update=ov.orders.model_dump(exclude_unset=True))
            if ov.round_s is not None:
                update["round_s"] = ov.round_s
        return self.model_copy(update=update)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


REPO_ROOT = Path(__file__).resolve().parent.parent
LEVELS_DIR = REPO_ROOT / "levels"
