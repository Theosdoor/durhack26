"""One season = one Inspect sample: briefing, practice, then chat -> round -> debrief per round.

Every replay record is logged with `transcript().info(rec.model_dump(mode="json"),
source="kitchen")`, always from the season's main loop (never from inside a concurrent decision),
so the log order is deterministic: Header first, then Events, Frames, Posts and DecisionRecords as
they happen.

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
a copy of the game, which is exact: the deciding agent cannot move), the decision still lands
at its normal time, and the agent decides again in that same second.
"""

from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

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

    # ------------------------------------------------------------------ logging
    @staticmethod
    def log(rec: Any) -> None:
        transcript().info(rec.model_dump(mode="json"), source="kitchen")

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
                         turn: Turn) -> None:
        for _ in range(gen.compactions):
            a.compactions += 1
            self.event(phase, round, t, "compaction", a.name, messages=len(a.messages))
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

    async def decide(self, game: Game, a: Agent, board: Board | None, intro: str | None
                     ) -> Pending:
        t0 = game.t
        turn = Turn()

        def check(plan: list[str]) -> str | None:   # dry run on a copy: same result as later
            return copy.deepcopy(game).set_plan(a.name, plan)

        tools = [act_tool(turn, check, self.cfg.max_plan_len)]
        if board is not None:
            tools += [post_tool(turn),
                      read_board_tool(turn, lambda: format_posts(
                          board.read_all(a.name, game.round), "The board is empty."))]
        tools.append(note_tool(turn))
        text = self.state_text(game, a, board)
        if a.last_cutoff:
            text = f"{prompts.CUTOFF_NOTICE}\n\n{text}"
        if intro:
            text = f"{intro}\n\n{text}"
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
        return Pending(a, game, turn, record, t0 + cost, gen)

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
        """Run games in lockstep (one per practising agent, or the one round game)."""
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
                self._log_side_events(game.phase, game.round, p.record.t, a, p.gen, p.turn)
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
