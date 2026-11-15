"""Run config: one YAML file per experiment (see configs/). Shared contract for all modules."""

from __future__ import annotations

import re
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
    every_s: int = 30      # a new order arrives every N seconds (first at t=0)   [scoring: orders]
    due_s: int = 60        # each order is due N seconds after it arrives          [scoring: orders]
    max_open: int = 3      # no new order while this many are open                [scoring: orders]
    reward: int = 20       # points per soup served (scoring orders: per open order it fills)
    penalty: int = 10      # points lost when an order expires                    [scoring: orders]


class Decision(_Strict):
    mode: Literal["simulated", "realtime"] = "simulated"
    flat_s: int = 2                 # game seconds every decision costs
    post_words_per_s: int = 10      # extra game seconds = ceil(words posted / this)
    reasoning_tokens: int = 1000    # GenerateConfig.reasoning_tokens
    max_tokens: int = 2000          # GenerateConfig.max_tokens; a reply cut off here gets the forced answer
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
    model: str                          # e.g. openrouter/deepseek/deepseek-v4.1-flash, or google/gemini-2.5-flash
    provider: str | None = None         # OpenRouter endpoint tag, e.g. "baidu/fp8"; None = auto-pick
    # False for endpoints that refuse to turn reasoning off ("Reasoning is mandatory", e.g.
    # z-ai/glm-5.3-flash@morph/fp8): its forced-answer follow-up keeps reasoning on (max_tokens
    # providers.MANDATORY_MAX_TOKENS), and decision.reasoning "off" leaves its reasoning on
    reasoning_off: bool = True

    @model_validator(mode="after")
    def _no_provider_for_google(self) -> "AgentSpec":
        if self.model.startswith("google/") and self.provider:
            raise ValueError(f"{self.model}: provider pins are OpenRouter endpoints; a google/ "
                             f"model (Gemini API) has none")
        return self

    @classmethod
    def parse(cls, text: str) -> "AgentSpec":
        """"model@provider", "model" or "model@provider!reasoning" (reasoning_off False). The
        model is a full Inspect model name, e.g. openrouter/deepseek/deepseek-v4.1-flash."""
        text = text.strip()
        mandatory = "!reasoning" in text.lower()   # after the model or after the provider
        text = re.sub("!reasoning", "", text, flags=re.I)
        model, _, provider = text.partition("@")
        if not model:
            raise ValueError(f"no model in agent spec {text!r}")
        return cls(model=model.strip(), provider=provider.strip() or None,
                   reasoning_off=not mandatory)


def parse_agents(text: str) -> list[AgentSpec]:
    """--agents "m1@p1,m2@p2" (each part as AgentSpec.parse)."""
    specs = [AgentSpec.parse(x) for x in text.split(",") if x.strip()]
    if not specs:
        raise ValueError(f"no agents in {text!r}")
    return specs


class LevelOverride(_Strict):
    """Settings that differ for one level of a multi-level run (Config.level_overrides). Only
    the fields given change: `orders: {every_s: 20}` keeps the run's other order settings."""

    orders: Orders | None = None
    round_s: int | None = None
    n_agents: int | None = None         # agents in this level's seasons (see Config.n_agents)


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
    # "private" = after each round (the last one too) each agent gets one private turn: it sees
    # the round's score and the scores so far and may call note(text) or pass; then the chat for
    # the next round starts. Logged with phase "debrief". "full" is this flow's older name and
    # loads as "private". "score_only" = no private turn: the round's result opens the agent's
    # next message (note() still works in practice and rounds)
    debrief: Literal["private", "full", "score_only"] = "private"
    # after the last round (and its private turn) each agent answers the reflection questions
    # (prompts/messages.md reflection.prompt) in one free-text reply, logged with phase "reflection"
    reflection: bool = True
    # how much the system prompt explains: "full" = everything needed for the best play (recipe,
    # scoring, interact table, plans, time costs, season, notepad); "bare" = identity, the goal
    # sentence and one line on rounds and chats (the tool descriptions are the only manual);
    # "open" = bare without a goal: "Do whatever you like" (the system prompt mentions no score
    # or points, but the game does: the state shows "Score N", the private turn and round result
    # say "Team score this round: X (N soups served)", failure messages name onions, dishes, pots
    # and the hatch; the reflection asks whether they tried to raise it). Report it as "no goal
    # in the system prompt, score feedback shown", not as goal-free.
    # Older values, kept so old configs and logs load: "recipe" = what scores, not how the objects
    # work; "minimal" = only that delivering at the hatch scores; "minimal_hint" = minimal + "try
    # everything in practice"
    rules: Literal["full", "bare", "open", "recipe", "minimal", "minimal_hint"] = "full"
    # "soups" = every soup served at a hatch scores orders.reward: no orders, nothing expires.
    # "orders" = a soup fills the open order due soonest; an expired order costs orders.penalty
    scoring: Literal["soups", "orders"] = "soups"
    compact_at_tokens: int = 32000
    providers: Providers = Field(default_factory=Providers)
    agents: list[AgentSpec]
    # agents per season: None = len(agents). More than len(agents) cycles through the list
    # (agents [A] and n_agents 3 = self-play of three A; [A, B] and 3 = A, B, A). The level needs
    # start positions 1..n. Per level: level_overrides.<level>.n_agents
    n_agents: int | None = None

    @field_validator("debrief", mode="before")
    @classmethod
    def _debrief_alias(cls, v: object) -> object:
        return "private" if v == "full" else v      # the older name of the same flow

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
        if self.agents:
            for level in self.run_levels():
                n = self.agents_for(level)
                if n < len(self.agents):
                    raise ValueError(f"{level}: n_agents {n} is less than the "
                                     f"{len(self.agents)} agents listed")
                starts = level_starts(level)
                if starts is not None and not set(range(1, n + 1)) <= starts:
                    raise ValueError(f"level {level} has start positions {sorted(starts)}; "
                                     f"{n} agents need 1..{n}")
        return self

    def agents_for(self, level: str | None) -> int:
        """Agents per season on `level`: its n_agents override, else n_agents, else len(agents)."""
        ov = self.level_overrides.get(level) if level is not None else None
        if ov is not None and ov.n_agents is not None:
            return ov.n_agents
        return self.n_agents if self.n_agents is not None else len(self.agents)

    def season_agents(self) -> list[AgentSpec]:
        """The agent specs of one season (of `level`): `agents` cycled to agents_for(level)."""
        n = self.agents_for(self.level)
        return [self.agents[i % len(self.agents)] for i in range(n)]

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
            if ov.n_agents is not None:
                update["n_agents"] = ov.n_agents
        return self.model_copy(update=update)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


REPO_ROOT = Path(__file__).resolve().parent.parent
LEVELS_DIR = REPO_ROOT / "levels"


def level_starts(level: str) -> set[int] | None:
    """The start digits in levels/<level>.txt (None if there is no such file)."""
    p = LEVELS_DIR / f"{level}.txt"
    if not p.exists():
        return None
    return {int(c) for c in p.read_text() if c.isdigit() and c != "0"}
