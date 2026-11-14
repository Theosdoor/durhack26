"""Replay records: the contract between the game (writer) and the viewer/analysis (readers).

During a season the runner logs every record with
    transcript().info(record.model_dump(mode="json"), source="kitchen")
so it lands in the Inspect .eval log. `export` (added later) turns an .eval sample back into a
`Replay`. The viewer only ever reads `Replay` objects, so it can be built from the fixture
tests/fixtures/replay_min.json before the engine exists.

Coordinates are (x, y): x = column, y = row, origin top-left, matching levels/*.txt.

A phase is identified by the pair (phase, round):
- briefing and practice: round = 0.
- chat: round = the round it plans for (the chat before round 2 has round = 2).
- round: round = 1..N.
- debrief: round = the round just played (the debrief after round 2 has round = 2).
Time `t`: in practice and rounds, whole game seconds since the phase started. In briefing,
chat and debrief (no game clock), `t` is the turn index within the phase (0, 1, 2, ...).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Phase = Literal["briefing", "practice", "chat", "round", "debrief"]
Pos = tuple[int, int]


class AgentInfo(BaseModel):
    name: str                      # NATO name, e.g. "Kilo"
    letter: str                    # map letter = first letter of name
    model: str                     # e.g. openrouter/deepseek/deepseek-v4.1-flash
    provider: str | None = None    # pinned OpenRouter endpoint tag, e.g. "baidu/fp8"
    seat: int = 1                  # start position (level digit) this season; seeded per season


class Header(BaseModel):
    kind: Literal["header"] = "header"
    config_name: str
    level: str
    map: list[str]                 # level rows, start digits replaced by floor (" ")
    agents: list[AgentInfo]
    seed: int
    epoch: int = 1
    round_s: int
    rounds: int
    cook_s: int = 20               # seconds a full pot cooks (config.cook_s); viewers draw progress from it
    practice_s: int | None = None  # practice phase length, if practice ran
    # experimental condition (config.decision.reasoning, config.comms, config.debrief);
    # the defaults are what older logs ran with
    reasoning: Literal["on", "off"] = "on"
    comms: Literal["full", "planning_only", "none"] = "full"
    debrief: Literal["full", "score_only"] = "full"
    rules: Literal["full", "recipe", "minimal", "minimal_hint"] = "full"   # config.rules


class AgentState(BaseModel):
    name: str
    pos: Pos
    holding: str | None = None     # "onion" | "dish" | "soup" | None
    plan: list[str] = []           # remaining actions, as strings
    status: Literal["acting", "deciding", "idle"] = "idle"


class PotState(BaseModel):
    id: str                        # "pot_1", numbered in reading order
    pos: Pos
    onions: int = 0
    state: Literal["empty", "filling", "cooking", "ready"] = "empty"
    ready_in: int | None = None    # seconds left while cooking


class OrderState(BaseModel):
    id: int
    dish: str = "onion_soup"
    created_t: int
    due_t: int


class Frame(BaseModel):
    """Full kitchen snapshot, logged once per game second (after that second resolves)."""

    kind: Literal["frame"] = "frame"
    phase: Phase
    round: int                     # 0 for practice
    t: int
    agents: list[AgentState]       # in practice: just the one agent playing
    pots: list[PotState]
    counters: dict[str, str]       # "x,y" -> item, only non-empty counters
    orders: list[OrderState]       # open orders
    score: int
    served: int = 0                # orders served so far this round
    missed: int = 0                # orders expired so far this round


class Event(BaseModel):
    """Something that happened. `type` values:
    pickup, putdown, add_to_pot, cook_start, cook_done, plate, serve, fail (action failed),
    bump (blocked by another agent), order_new, order_missed, decision_start, decision_land,
    cutoff (reply had no act), read_board, note, compaction, phase_start, phase_end,
    forced_answer (no act at first, then act on the one follow-up call; see season.py),
    practice_finished (the agent's finish_practice() landed: its practice game ends at `t`).

    Keys used in `data` (include those that apply): item ("onion"|"dish"|"soup"),
    at ("x,y" of the tile acted on), pot (pot id), onions (count after the action),
    order (order id), points (score change, +reward or -penalty), reason (why it failed),
    action (the plan step as a string), duration_s (phase_end only), plan (decision_land: the
    plan that runs, as in DecisionRecord.plan), started_t (decision_land), first (chat
    phase_start: name of the agent who speaks first), turn_limit (chat phase_end: True if the
    turn cap ended the chat), ready and posts (chat phase_end), score, served and missed (round
    phase_end)."""

    kind: Literal["event"] = "event"
    phase: Phase
    round: int
    t: int
    type: str
    agent: str | None = None
    data: dict = {}


class Post(BaseModel):
    """A message on the shared board."""

    kind: Literal["post"] = "post"
    phase: Phase
    round: int
    t: int
    agent: str
    text: str


class DecisionRecord(BaseModel):
    """One model decision by one agent (a decision may include reading the board).

    `post` is what the agent posted in this decision, whether or not it reached the board: a
    decision still in flight when its phase ends never lands (`landed` False), so its post,
    plan and note never take effect. The board is the Post records, not DecisionRecord.post."""

    kind: Literal["decision"] = "decision"
    phase: Phase
    round: int
    t: int                         # game time the decision started
    lands_t: int | None = None     # game time it took effect (None outside rounds)
    agent: str
    reasoning: str | None = None   # model's reasoning text, if the provider returns it
    text: str | None = None        # model's visible text, if any
    plan: list[str] = []           # the plan that runs: act(plan) split into actions (";", ",",
                                   # newlines), cut to max_plan_len, up to the first invalid
                                   # action; [] if rejected or no act()
    plan_sent: list[str] = []      # act(plan) exactly as the model sent it
    post: str | None = None
    note: str | None = None
    read_board: bool = False
    ready: bool = False            # chat phase: agent called ready()
    # cutoff / forced. Practice and rounds: cutoff = no act() (nor finish_practice()) in the
    # decision, even after the forced-answer follow-up; forced = only the follow-up acted.
    # Chat and debrief: a turn needs the follow-up when its reply had no tool call and did not
    # stop normally (season.cut_off). forced = the follow-up then called a tool; a follow-up with
    # no tool call (still cut off, or a pass) leaves the turn a cutoff, not forced. A reply with no
    # tool call that stopped normally is a deliberate pass: neither.
    cutoff: bool = False
    landed: bool = True            # False: still in flight when the phase ended (no effect)
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    served_by: str | None = None   # provider OpenRouter reports for this call
    forced: bool = False           # answered only on the forced-answer follow-up call (above)
    finish_practice: bool = False  # called finish_practice() (practice only)


Record = Annotated[Union[Header, Frame, Event, Post, DecisionRecord], Field(discriminator="kind")]


class Replay(BaseModel):
    header: Header
    records: list[Record]          # in log order (frames, events, posts, decisions interleaved)

    def select(self, kind: type | None = None, phase: Phase | None = None,
               round: int | None = None, agent: str | None = None) -> list:
        """Records filtered by type, phase, round and agent (any filter may be None)."""
        return [
            r for r in self.records
            if (kind is None or isinstance(r, kind))
            and (phase is None or r.phase == phase)
            and (round is None or r.round == round)
            and (agent is None or getattr(r, "agent", None) == agent)
        ]

    def frames(self, phase: Phase | None = None, round: int | None = None) -> list[Frame]:
        return self.select(Frame, phase, round)

    def phases(self) -> list[tuple[str, int]]:
        """(phase, round) pairs in the order they first appear."""
        seen: list[tuple[str, int]] = []
        for r in self.records:
            key = (r.phase, r.round)
            if key not in seen:
                seen.append(key)
        return seen
