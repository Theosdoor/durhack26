"""One season = one Inspect sample: briefing, practice, then chat -> round -> debrief per round.

Every replay record is logged with `transcript().info(rec.model_dump(mode="json"),
source="kitchen")`, always from the season's game loop (never from inside a concurrent decision),
so the log order is deterministic: Header first, then Events, Frames, Posts and DecisionRecords as
they happen (in pipelined play, in the order the sequential loop would log them).

Simulated time (cfg.decision.mode == "simulated"): the game clock stops while the models think.
Each game second t:
  1. decisions whose landing time is t land: the plan is set (game.set_plan), the post appears on
     the board at t, the notepad is updated;
  2. every agent in game.needs_decision() starts a decision (status "deciding"; it stands still);
     decisions that start in the same second run concurrently and are awaited before the clock
     moves on. A decision lands at t + flat_s + ceil(words posted / post_words_per_s); a reply
     with no act() (a "cutoff") lands at t + 1 (+ the post cost, if it posted);
  3. decisions with zero cost land (only possible when flat_s = 0);
  4. game.tick(); its events and the frame after it are logged.
So a decision that starts at t and lands at L keeps the agent standing still for exactly L - t
seconds. A plan that set_plan rejects is rejected in the act() tool result at once (checked on
a copy of the game as it was when the decision started), the decision still lands at its normal
time, and the agent decides again in that same second.

Pipelining (cfg.decision.pipeline, the default) gives exactly the same games, records and record
order as the loop above (`_play_sequential`, kept as the reference), but does not hold the clock
while a model call is in flight unless the game needs that call's outcome. See `_run_game`.
"""

from __future__ import annotations

import asyncio
import copy
import math
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from inspect_ai.log import transcript
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageUser,
    CompactionSummary,
    ContentReasoning,
    ModelOutput,
    Compact,
    compaction,
    execute_tools,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolDef
from inspect_ai.util import collect, span

from undercooked import prompts
from undercooked.board import Board, format_posts
from undercooked.config import NATO_NAMES, Config
from undercooked.engine import Game, Kitchen
from undercooked.providers import AgentModel, agent_model, served_providers
from undercooked.replay import AgentInfo, DecisionRecord, Event, Header, Phase, Post
from undercooked.tools import Turn, act_tool, note_tool, post_tool, read_board_tool, ready_tool

STORE_KEY = "undercooked"          # season summary in the sample store (read by the scorer)
ROUND_EXTRA_GENERATIONS = 2        # generations allowed after one without act()


def pick_names(seed: int, epoch: int, n: int = 2) -> list[str]:
    """Distinct NATO names for a season; agents[i] gets names[i]."""
    return random.Random(seed * 1000 + epoch).sample(NATO_NAMES, n)


@dataclass
class Agent:
    index: int
    name: str
    am: AgentModel
    messages: list[ChatMessage]
    compact: Compact
    notepad: str | None = None
    posts: int = 0
    read_board: int = 0
    cutoffs: int = 0
    decisions: int = 0              # round decisions (not practice, chat or debrief)
    compactions: int = 0
    last_cutoff: bool = False       # previous decision had no act(): say so in the next state

    @property
    def letter(self) -> str:
        return self.name[0].upper()


@dataclass
class Gen:
    """Aggregate of the generations in one decision / turn."""

    reasoning: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    served_by: list[str] = field(default_factory=list)
    compactions: int = 0
    generations: int = 0

    def add(self, output: ModelOutput, served_by: str | None) -> None:
        self.generations += 1
        msg = output.message
        if isinstance(msg.content, list):
            for c in msg.content:
                if isinstance(c, ContentReasoning):
                    r = c.summary if c.redacted else c.reasoning
                    if r:
                        self.reasoning.append(r)
        if msg.text:
            self.text.append(msg.text)
        u = output.usage
        if u is not None:
            self.input_tokens += u.input_tokens
            self.output_tokens += u.output_tokens
            if u.reasoning_tokens is not None:
                self.reasoning_tokens = (self.reasoning_tokens or 0) + u.reasoning_tokens
        if served_by and served_by not in self.served_by:
            self.served_by.append(served_by)

    def record(self, **kw: Any) -> DecisionRecord:
        return DecisionRecord(
            reasoning="\n\n".join(self.reasoning) or None, text="\n\n".join(self.text) or None,
            input_tokens=self.input_tokens if self.generations else None,
            output_tokens=self.output_tokens if self.generations else None,
            reasoning_tokens=self.reasoning_tokens,
            served_by=",".join(self.served_by) or None, **kw)


@dataclass
class Pending:
    """A finished decision waiting for its landing time."""

    agent: Agent
    game: Game
    turn: Turn
    record: DecisionRecord
    lands_t: int
    gen: Gen
    messages: int = 0               # len(agent.messages) when the decision finished


@dataclass
class _Flight:
    """A pipelined decision: its model call runs as a task; `result` is set once resolved."""

    agent: Agent
    g: int                          # index of its game in play()'s `games`
    i: int                          # index of the agent in its game (needs_decision order)
    t0: int                         # game second it started
    task: asyncio.Task[Pending]
    result: Pending | None = None

    @property
    def key(self) -> tuple[int, int, int]:
        """Order of the sequential loop's `pending` dict: start second, then starter order."""
        return (self.t0, self.g, self.i)

    @property
    def side_key(self) -> tuple[int, ...]:
        return (self.t0, _SIDE, self.g, self.i, 0)


# Sections of one game second, in the order the sequential loop logs them (see _LogBuffer).
_LAND, _START, _SIDE, _LAND_SAME, _TICK, _UNLANDED = 1, 2, 3, 4, 5, 6
_END = 10 ** 9                      # "second" of the never-landed records, after the last tick


class _LogBuffer:
    """Kitchen records of a pipelined play(), written in exactly the sequential loop's order.

    Each record is logged under a sort key that says where the sequential loop logs it:
      (-1, 0, g)                    the t = 0 frame of game g
      (t, _LAND, t0, g, i)          a decision started at t0 < t lands at t (pending-dict order)
      (t, _START, g, i)             decision_start at t (starter order)
      (t, _SIDE, g, i)              compaction / read_board / cutoff events of that decision
      (t, _LAND_SAME, t, g, i)      a decision started at t lands at t (only when flat_s <= 0)
      (t, _TICK, g)                 the events of game g's tick at t, then its frame
      (_END, _UNLANDED, t0, g, i)   records of decisions that never landed
    plus a sequence number within the key. A record goes to the transcript once nothing with a
    smaller key can still appear: every game has moved past it (`frontier`) and every decision
    that started before it has resolved (`open` side blocks)."""

    DONE: tuple[float] = (math.inf,)

    def __init__(self, emit: Callable[[dict[str, Any]], None], games: int):
        self._emit = emit
        self._items: list[tuple[tuple[int, ...], dict[str, Any]]] = []
        self.frontier: list[tuple[float, ...]] = [(0,)] * games
        self.open: set[tuple[int, ...]] = set()
        self._key: tuple[int, ...] | None = None
        self._seq = 0

    @contextmanager
    def at(self, key: tuple[int, ...]) -> Iterator[None]:
        self._key, self._seq = key, 0
        try:
            yield
        finally:
            self._key = None

    def add(self, data: dict[str, Any]) -> None:
        if self._key is None:
            raise RuntimeError("kitchen record logged outside a keyed section of the pipeline")
        self._items.append((self._key + (self._seq,), data))
        self._seq += 1

    def flush(self) -> None:
        mark = min([*self.frontier, *self.open], default=self.DONE)
        self._items.sort(key=lambda kv: kv[0])
        n = 0
        while n < len(self._items) and self._items[n][0] < mark:
            self._emit(self._items[n][1])
            n += 1
        del self._items[:n]

    @property
    def empty(self) -> bool:
        return not self._items and not self.open


class Season:
    def __init__(self, cfg: Config, models: list[AgentModel], epoch: int):
        if len(models) != 2:
            raise ValueError(f"a season needs exactly 2 agents, got {len(models)}")
        self.cfg = cfg
        self.epoch = epoch
        names = pick_names(cfg.seed, epoch, len(models))
        self.agents: list[Agent] = []
        for i, (name, am) in enumerate(zip(names, models)):
            mate = names[1 - i]
            system = ChatMessageSystem(content=prompts.system_prompt(cfg, name, mate))
            intro = ChatMessageUser(content=prompts.season_intro(cfg), source="input")
            strategy = CompactionSummary(threshold=cfg.compact_at_tokens,
                                         prompt=prompts.COMPACTION_PROMPT)
            handler = compaction(strategy, prefix=[system, intro], model=am.model)
            self.agents.append(Agent(i, name, am, [system, intro], handler))
        self.by_name = {a.name: a for a in self.agents}
        self.board = Board(names)
        self.rounds: list[dict[str, int]] = []
        self._buf: _LogBuffer | None = None          # set while a pipelined play() runs

    # ------------------------------------------------------------------ logging
    @staticmethod
    def _emit(data: dict[str, Any]) -> None:
        transcript().info(data, source="kitchen")

    def log(self, rec: Any) -> None:
        data = rec.model_dump(mode="json")
        if self._buf is not None:
            self._buf.add(data)
        else:
            self._emit(data)

    def event(self, phase: Phase, round: int, t: int, type_: str, agent: str | None = None,
              **data: Any) -> None:
        self.log(Event(phase=phase, round=round, t=t, type=type_, agent=agent, data=data))

    # ---------------------------------------------------------------- the season
    async def run(self) -> dict[str, Any]:
        cfg = self.cfg
        self.log(Header(
            config_name=cfg.name, level=cfg.level, map=Kitchen.load(cfg.level).map_rows(),
            agents=[AgentInfo(name=a.name, letter=a.letter, model=a.am.spec.model,
                              provider=a.am.provider) for a in self.agents],
            seed=cfg.seed, epoch=self.epoch, round_s=cfg.round_s, rounds=cfg.rounds,
            cook_s=cfg.cook_s, practice_s=cfg.practice_s if cfg.practice else None))
        clock = time.monotonic()
        self.event("briefing", 0, 0, "phase_start")
        self.event("briefing", 0, 0, "phase_end", duration_s=round(time.monotonic() - clock, 1))
        if cfg.practice:
            async with span("practice"):
                await self.practice()
        for r in range(1, cfg.rounds + 1):
            async with span(f"chat-{r}"):
                await self.chat(r)
            async with span(f"round-{r}"):
                game = await self.round(r)
            self.rounds.append({"score": game.score, "served": game.served,
                                "missed": game.missed})
            async with span(f"debrief-{r}"):
                await self.debrief(r)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {
            "agents": [a.name for a in self.agents],
            "models": [a.am.spec.model for a in self.agents],
            "providers": [a.am.provider for a in self.agents],
            "rounds": self.rounds,
            "posts": [a.posts for a in self.agents],
            "read_board": [a.read_board for a in self.agents],
            "cutoffs": [a.cutoffs for a in self.agents],
            "decisions": [a.decisions for a in self.agents],
            "compactions": [a.compactions for a in self.agents],
        }

    # ------------------------------------------------------------------ model I/O
    async def model_turn(self, a: Agent, text: str, tools: list[ToolDef], max_gens: int,
                         done: Callable[[], bool]) -> Gen:
        """Append `text` as a user message, then generate (and run tools) until `done()`, a reply
        without tool calls, or `max_gens` generations.

        Compaction runs once, at the start of the turn and before the new user message, so the
        model always sees the current state verbatim (a summary would otherwise swallow it)."""
        gen = Gen()
        base, summary = await a.compact.compact_input(a.messages)
        if summary is not None:
            a.messages.append(summary)
            gen.compactions += 1
        start = len(a.messages)
        a.messages.append(ChatMessageUser(content=text))
        for _ in range(max_gens):
            model_input = base + a.messages[start:]
            with served_providers() as served:
                output = await a.am.model.generate(model_input, tools=tools, tool_choice="auto")
            await a.compact.record_output(model_input, output)
            a.messages.append(output.message)
            gen.add(output, served[-1] if served else None)
            if output.message.tool_calls:
                result = await execute_tools(a.messages, tools)
                a.messages.extend(result.messages)
            if done() or not output.message.tool_calls:
                break
        return gen

    def _log_side_events(self, phase: Phase, round: int, t: int, a: Agent, gen: Gen,
                         turn: Turn, messages: int | None = None) -> None:
        for _ in range(gen.compactions):
            a.compactions += 1
            self.event(phase, round, t, "compaction", a.name,
                       messages=len(a.messages) if messages is None else messages)
        for _ in range(turn.read_board):
            self.event(phase, round, t, "read_board", a.name)

    # ------------------------------------------------------------------ decisions
    def state_text(self, game: Game, a: Agent, board: Board | None) -> str:
        last = game.last_plan_result(a.name)
        if board is None:    # practice: no board at all
            return prompts.strip_board_line(game.state_text(
                a.name, unread_posts=0, notepad=a.notepad, last_plan_result=last))
        if self.cfg.board == "pull":
            return game.state_text(a.name, unread_posts=len(board.unread(a.name, game.round)),
                                   notepad=a.notepad, last_plan_result=last)
        new = board.mark_read(a.name, game.round)
        text = prompts.strip_board_line(game.state_text(
            a.name, unread_posts=0, notepad=a.notepad, last_plan_result=last))
        return text + ("\nNew posts on the board:\n" + format_posts(new) if new
                       else "\nBoard: no new posts.")

    def prepare(self, game: Game, a: Agent, board: Board | None, intro: str | None
                ) -> tuple[Turn, list[ToolDef], str]:
        """Everything a decision reads from the season, taken now (at its start second): the
        state text (with notepad and unread count), and snapshots of the game and the board for
        its tools, whose model call may still be running when the game and board have moved on."""
        turn = Turn()
        seen = copy.deepcopy(game)                  # the game as this decision sees it

        def check(plan: list[str]) -> str | None:   # dry run on a copy: same result as later
            return copy.deepcopy(seen).set_plan(a.name, plan)

        tools = [act_tool(turn, check, self.cfg.max_plan_len)]
        if board is not None:
            upto, rnd = len(board.posts), game.round
            tools += [post_tool(turn),
                      read_board_tool(turn, lambda: format_posts(
                          board.read_all(a.name, rnd, upto=upto), "The board is empty."))]
        tools.append(note_tool(turn))
        text = self.state_text(game, a, board)
        if a.last_cutoff:
            text = f"{prompts.CUTOFF_NOTICE}\n\n{text}"
        if intro:
            text = f"{intro}\n\n{text}"
        return turn, tools, text

    async def decide(self, game: Game, a: Agent, board: Board | None, intro: str | None
                     ) -> Pending:
        t0 = game.t
        turn, tools, text = self.prepare(game, a, board, intro)
        return await self.think(game, a, t0, turn, tools, text)

    async def think(self, game: Game, a: Agent, t0: int, turn: Turn, tools: list[ToolDef],
                    text: str) -> Pending:
        """The model call(s) of a decision prepared at second t0, and when it lands."""
        async with span(f"agent-{a.name}"):
            gen = await self.model_turn(a, text, tools, 1 + ROUND_EXTRA_GENERATIONS,
                                        lambda: turn.plan is not None)
        cutoff = turn.plan is None
        d = self.cfg.decision
        words = len(turn.post.split()) if turn.post else 0
        cost = (1 if cutoff else d.flat_s) + math.ceil(words / d.post_words_per_s)
        record = gen.record(phase=game.phase, round=game.round, t=t0, agent=a.name,
                            plan=turn.plan or [], post=turn.post, note=turn.note,
                            read_board=turn.read_board > 0, cutoff=cutoff)
        return Pending(a, game, turn, record, t0 + cost, gen, len(a.messages))

    def land(self, p: Pending, board: Board | None) -> None:
        game, a, turn = p.game, p.agent, p.turn
        t = game.t
        error = None
        if turn.plan is not None:
            error = game.set_plan(a.name, turn.plan)
        if turn.plan is None or error is not None:
            game.set_status(a.name, "idle")       # decides again this second
        data: dict[str, Any] = {"plan": turn.plan or [], "started_t": p.record.t}
        if error is not None:
            data["reason"] = error
        self.event(game.phase, game.round, t, "decision_land", a.name, **data)
        if turn.post is not None and board is not None:
            post = Post(phase=game.phase, round=game.round, t=t, agent=a.name, text=turn.post)
            board.add(post)
            a.posts += 1
            self.log(post)
        if turn.note is not None:
            a.notepad = turn.note
            self.event(game.phase, game.round, t, "note", a.name)
        p.record.lands_t = t
        self.log(p.record)

    async def play(self, games: list[tuple[Game, list[Agent]]], board: Board | None,
                   intro: str) -> None:
        """Run the games (one per practising agent, or the one round game) to the end."""
        d = self.cfg.decision
        if (d.pipeline and d.post_words_per_s >= 1 and (len(games) == 1 or board is None)
                and len({g.duration for g, _ in games}) == 1):
            await self._play_pipelined(games, board, intro)
        else:
            await self._play_sequential(games, board, intro)

    async def _play_sequential(self, games: list[tuple[Game, list[Agent]]], board: Board | None,
                               intro: str) -> None:
        """The reference loop: games in lockstep; the clock waits for every model call."""
        pending: dict[str, Pending] = {}
        started: set[str] = set()
        for game, agents in games:
            self.log(game.frame())                    # t = 0
            for a in agents:
                a.last_cutoff = False

        def land_due() -> None:
            for name, p in list(pending.items()):
                if p.lands_t <= p.game.t:
                    del pending[name]
                    self.land(p, board)

        while not all(g.over for g, _ in games):
            land_due()
            starters: list[tuple[Game, Agent]] = []
            for game, agents in games:
                if game.over:
                    continue
                for name in game.needs_decision():
                    if name in pending:
                        continue
                    a = self.by_name[name]
                    game.set_status(name, "deciding")
                    self.event(game.phase, game.round, game.t, "decision_start", name)
                    starters.append((game, a))
            results = await collect(*(
                self.decide(g, a, board, None if a.name in started else intro)
                for g, a in starters))
            for p in results:
                a, game = p.agent, p.game
                started.add(a.name)
                if game.phase == "round":
                    a.decisions += 1
                a.read_board += p.turn.read_board
                self._log_side_events(game.phase, game.round, p.record.t, a, p.gen, p.turn,
                                      p.messages)
                a.last_cutoff = p.record.cutoff
                if p.record.cutoff:
                    a.cutoffs += 1
                    self.event(game.phase, game.round, p.record.t, "cutoff", a.name)
                pending[a.name] = p
            land_due()
            for game, _ in games:
                if game.over:
                    continue
                for ev in game.tick():
                    self.log(ev)
                self.log(game.frame())
        for p in pending.values():                     # never landed: the phase ended first
            self.log(p.record)

    # ------------------------------------------------------------------ pipelining
    async def _play_pipelined(self, games: list[tuple[Game, list[Agent]]], board: Board | None,
                              intro: str) -> None:
        """Same games, records and record order as `_play_sequential`, with model calls that
        overlap across game seconds. Each game runs in its own runner (`_run_game`; practice
        games share nothing, so one never waits for the other's model calls) and every kitchen
        record goes through a `_LogBuffer` that writes them in the sequential loop's order."""
        buf = _LogBuffer(self._emit, len(games))
        started: set[str] = set()
        flights: list[_Flight] = []
        runners: list[asyncio.Task[None]] = []
        self._buf = buf
        try:
            for g, (game, agents) in enumerate(games):
                with buf.at((-1, 0, g, 0, 0)):
                    self.log(game.frame())                # t = 0
                for a in agents:
                    a.last_cutoff = False
            runners = [asyncio.create_task(self._run_game(g, game, board, intro, started,
                                                          buf, flights))
                       for g, (game, _) in enumerate(games)]
            done, _ = await asyncio.wait(runners, return_when=asyncio.FIRST_EXCEPTION)
            for r in done:
                r.result()                                # re-raise the first failure
            buf.flush()
            if not buf.empty:
                raise RuntimeError("pipelined play ended with kitchen records still buffered")
        finally:
            self._buf = None
            stray = [x for x in [*runners, *(f.task for f in flights)] if not x.done()]
            for x in stray:                               # only after a failure: no orphan calls
                x.cancel()
            if stray:
                await asyncio.gather(*stray, return_exceptions=True)

    async def _run_game(self, g: int, game: Game, board: Board | None, intro: str,
                        started: set[str], buf: _LogBuffer, flights: list[_Flight]) -> None:
        """One game, second by second, as in `_play_sequential`, except that each decision's
        model call runs as a task that is awaited only when the game needs its outcome:

        - A decision started at t0 lands no earlier than t0 + min(1, flat_s) (a cutoff costs 1 s,
          anything else flat_s + its post cost). The tick at t waits for every decision with
          t0 + min(1, flat_s) <= t and lands those that land at t, so the game never runs past a
          landing it doesn't know about.
        - A landing with a post costs at least one second more (a post has at least one word,
          and post_words_per_s >= 1), so every decision that could post by t was resolved
          before the tick at t - 1, and the board is complete when decisions start at t.
        - A post is the only way a landing at t changes what a decision starting at t reads: a
          landing sets the lander's plan, status and notepad, and the state text shows the
          teammate's position and holding, which only the tick changes (tests/test_pipeline.py
          guards this). So a decision that is still running at t can land at t (a cutoff, or a
          plan that set_plan accepts or rejects) after the decisions starting at t have started:
          it lands then, before the tick, and if its agent has no plan it starts deciding again
          at t, as in the sequential loop.
        Landings of different agents in one second touch different agents, and the board only
        through posts, which have all landed, in order, before anyone starts; so a landing
        applied later in its second leaves the same state, and _LogBuffer restores the order
        of the records."""
        d = self.cfg.decision
        min_cost = min(1, d.flat_s)
        post_min = min_cost + 1
        index = {x.name: i for i, x in enumerate(game.agents)}
        pending: dict[str, _Flight] = {}

        def launch(names: list[str]) -> None:
            t = game.t
            for name in names:
                game.set_status(name, "deciding")
                with buf.at((t, _START, g, index[name], 0)):
                    self.event(game.phase, game.round, t, "decision_start", name)
            for name in names:
                a = self.by_name[name]
                turn, tools, text = self.prepare(game, a, board, None if name in started else intro)
                started.add(name)
                task = asyncio.create_task(self.think(game, a, t, turn, tools, text))
                f = _Flight(a, g, index[name], t, task)
                buf.open.add(f.side_key)
                pending[name] = f
                flights.append(f)

        async def resolve(fs: list[_Flight]) -> None:
            fs = [f for f in fs if f.result is None]
            if not fs:
                return
            done, _ = await asyncio.wait([f.task for f in fs],
                                         return_when=asyncio.FIRST_EXCEPTION)
            for x in done:
                x.result()                          # re-raise a failed call
            for f in sorted(fs, key=lambda f: f.key):
                p = f.result = f.task.result()
                a = f.agent
                if game.phase == "round":
                    a.decisions += 1
                a.read_board += p.turn.read_board
                with buf.at(f.side_key):
                    self._log_side_events(game.phase, game.round, f.t0, a, p.gen, p.turn,
                                          p.messages)
                    a.last_cutoff = p.record.cutoff
                    if p.record.cutoff:
                        a.cutoffs += 1
                        self.event(game.phase, game.round, f.t0, "cutoff", a.name)
                buf.open.discard(f.side_key)

        def land_resolved(restart: bool) -> None:
            t = game.t
            for f in sorted(pending.values(), key=lambda f: f.key):
                if f.result is None or f.result.lands_t > t:
                    continue
                del pending[f.agent.name]
                late = f.t0 < t
                with buf.at((t, _LAND if late else _LAND_SAME, f.t0, g, f.i)):
                    self.land(f.result, board)
                if restart and late and f.agent.name in game.needs_decision():
                    launch([f.agent.name])

        while not game.over:
            t = game.t
            buf.frontier[g] = (t,)
            if any(f.result is None and t >= f.t0 + post_min for f in pending.values()):
                raise RuntimeError("pipeline: a decision that could post by now is unresolved")
            land_resolved(restart=False)            # starters below pick these agents up
            launch([n for n in game.needs_decision() if n not in pending])
            while due := [f for f in pending.values()
                          if f.result is None and t >= f.t0 + min_cost]:
                await resolve(due)
                land_resolved(restart=True)
            with buf.at((t, _TICK, g, 0, 0)):
                for ev in game.tick():
                    self.log(ev)
                self.log(game.frame())
            buf.frontier[g] = (t + 1,)
            buf.flush()
        buf.frontier[g] = (_END,)
        await resolve(list(pending.values()))
        for f in sorted(pending.values(), key=lambda f: f.key):   # never landed
            with buf.at((_END, _UNLANDED, f.t0, g, f.i)):
                self.log(f.task.result().record)
        buf.frontier[g] = _LogBuffer.DONE
        buf.flush()

    # ------------------------------------------------------------------ phases
    async def practice(self) -> None:
        clock = time.monotonic()
        self.event("practice", 0, 0, "phase_start")
        kitchen = Kitchen.load(self.cfg.practice_level)
        games = [(Game(kitchen, [a.name], self.cfg, phase="practice", round=0), [a])
                 for a in self.agents]
        await self.play(games, None, prompts.practice_start(self.cfg))
        self.event("practice", 0, games[0][0].t, "phase_end",
                   duration_s=round(time.monotonic() - clock, 1))

    async def round(self, r: int) -> Game:
        clock = time.monotonic()
        self.event("round", r, 0, "phase_start")
        game = Game(Kitchen.load(self.cfg.level), [a.name for a in self.agents], self.cfg,
                    phase="round", round=r)
        await self.play([(game, self.agents)], self.board, prompts.round_start(self.cfg, r))
        self.event("round", r, game.t, "phase_end", duration_s=round(time.monotonic() - clock, 1),
                   score=game.score, served=game.served, missed=game.missed)
        return game

    async def chat(self, r: int) -> None:
        cfg = self.cfg
        clock = time.monotonic()
        self.event("chat", r, 0, "phase_start")
        names = [a.name for a in self.agents]
        preview = Game(Kitchen.load(cfg.level), names, cfg, phase="round", round=r)
        cap = cfg.chat_max_posts
        used = {n: 0 for n in names}
        ready = {n: False for n in names}
        had_turn: set[str] = set()
        order = [0, 1] if r % 2 == 1 else [1, 0]
        max_turns = 2 * (cap + 2)
        turn_i = 0

        def finished(n: str) -> bool:
            return ready[n] or used[n] >= cap

        k = 0
        while not all(finished(n) for n in names) and k < max_turns:
            a = self.agents[order[k % 2]]
            k += 1
            if ready[a.name] and used[a.name] >= cap:
                continue                                  # nothing left to do: skip the turn
            turn = Turn()
            new = self.board.mark_read(a.name, r)
            text = prompts.chat_turn(
                cfg, r, first_turn=a.name not in had_turn,
                overview=prompts.kitchen_overview(preview, a.name),
                new_posts=format_posts(new) if new else None,
                posts_left=cap - used[a.name], ready=ready[a.name], notepad=a.notepad)
            had_turn.add(a.name)
            tools = [post_tool(turn, cap_error=lambda n=a.name: (
                         f"you have used all {cap} posts for this chat" if used[n] >= cap else None),
                     lands_later=False),
                     ready_tool(turn)]
            async with span(f"agent-{a.name}"):
                gen = await self.model_turn(a, text, tools, 1, lambda: True)
            self._log_side_events("chat", r, turn_i, a, gen, turn)
            if turn.post is not None:
                used[a.name] += 1
                a.posts += 1
                post = Post(phase="chat", round=r, t=turn_i, agent=a.name, text=turn.post)
                self.board.add(post)
                self.log(post)
            if turn.ready:
                ready[a.name] = True
            self.log(gen.record(phase="chat", round=r, t=turn_i, agent=a.name, post=turn.post,
                                ready=turn.ready))
            turn_i += 1
        self.event("chat", r, turn_i, "phase_end", duration_s=round(time.monotonic() - clock, 1),
                   ready=[n for n in names if ready[n]], posts={n: used[n] for n in names})

    async def debrief(self, r: int) -> None:
        clock = time.monotonic()
        self.event("debrief", r, 0, "phase_start")
        res = self.rounds[r - 1]
        history = [x["score"] for x in self.rounds]

        async def one(a: Agent) -> tuple[Turn, Gen]:
            turn = Turn()
            text = prompts.debrief(self.cfg, r, res["score"], res["served"], res["missed"],
                                   history, a.notepad)
            async with span(f"agent-{a.name}"):
                gen = await self.model_turn(a, text, [note_tool(turn)], 1, lambda: True)
            return turn, gen

        results = await collect(*(one(a) for a in self.agents))
        for a, (turn, gen) in zip(self.agents, results):
            t = a.index
            self._log_side_events("debrief", r, t, a, gen, turn)
            if turn.note is not None:
                a.notepad = turn.note
                self.event("debrief", r, t, "note", a.name)
            self.log(gen.record(phase="debrief", round=r, t=t, agent=a.name, note=turn.note))
        self.event("debrief", r, len(self.agents), "phase_end",
                   duration_s=round(time.monotonic() - clock, 1))


def _conversation(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json", exclude_none=True) for m in messages]


@solver
def season_solver(cfg: Config) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        models = [agent_model(spec, cfg) for spec in cfg.agents]
        providers = [{"agent_index": i, "model": m.spec.model, "pin": m.provider,
                      "endpoints": m.endpoints, "endpoints_error": m.endpoints_error}
                     for i, m in enumerate(models)]
        transcript().info({"providers": providers}, source="providers")    # every endpoint
        state.metadata["providers"] = [                                      # the pinned one
            {"agent_index": p["agent_index"], "model": p["model"], "pin": p["pin"],
             "endpoint": next((e for e in p["endpoints"] or [] if e.get("tag") == p["pin"]), None),
             "endpoints_error": p["endpoints_error"]} for p in providers]
        season = Season(cfg, models, state.epoch)
        summary = await season.run()
        state.store.set(STORE_KEY, summary)
        state.store.set("conversations",
                        {a.name: _conversation(a.messages) for a in season.agents})
        total = sum(x["score"] for x in summary["rounds"])
        state.output = ModelOutput.from_content(
            "undercooked", f"Season over: team score {total} over {len(summary['rounds'])} rounds.")
        state.completed = True
        return state

    return solve
