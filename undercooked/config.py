"""Run config: one YAML file per experiment (see configs/). Shared contract for all modules."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

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


class Providers(_Strict):
    pick: Literal["cheapest"] = "cheapest"
    min_quant: Literal["fp8", "bf16", "fp16"] = "fp8"


class AgentSpec(_Strict):
    model: str                          # e.g. openrouter/deepseek/deepseek-v4.1-flash
    provider: str | None = None         # OpenRouter endpoint tag, e.g. "baidu/fp8"; None = auto-pick


class Config(_Strict):
    name: str
    level: str                          # file stem in levels/
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
    compact_at_tokens: int = 32000
    providers: Providers = Field(default_factory=Providers)
    agents: list[AgentSpec]

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


REPO_ROOT = Path(__file__).resolve().parent.parent
LEVELS_DIR = REPO_ROOT / "levels"
