"""One season = one Inspect sample: (chat -> round -> debrief) x rounds, on Overcooked-AI.

Every replay record (overcooked_port/replay.py, copied from Undercooked) is logged with
`transcript().info(rec.model_dump(mode="json"), source="kitchen")`, Header first.

Time: one round = `round_steps` Overcooked steps. The clock stops while models think: at each
step, every agent with no plan decides (concurrently), then the step runs. Deciding costs no
game time. Each agent keeps one conversation for the whole season.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from inspect_ai.log import transcript
from inspect_ai.model import (ChatMessage, ChatMessageSystem, ChatMessageUser, ContentReasoning,
                              GenerateConfig, Model, ModelOutput, execute_tools, get_model)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolDef
from inspect_ai.util import collect, span
from pydantic import BaseModel

from overcooked_port.kitchen import Kitchen, Player, rules_text
from overcooked_port.replay import AgentInfo, DecisionRecord, Event, Header, Phase, Post

GOAL = "Get the highest team score. You won't score highly unless you work together."
NATO = ["Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliett",
        "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo", "Sierra",
        "Tango", "Uniform", "Victor", "Whiskey", "Xray", "Yankee", "Zulu"]
MAX_POST_CHARS = 200
STORE_KEY = "overcooked_port"


class AgentSpec(BaseModel):
    model: str
    provider: str | None = None          # OpenRouter endpoint tag, e.g. "baidu/fp8"


class Config(BaseModel):
    name: str = "forced_coordination_pilot"
    layout: str = "forced_coordination"
    rounds: int = 3
    round_steps: int = 100
    max_plan_len: int = 5
    chat_max_posts: int = 4               # per agent per chat
    seed: int = 0
    reasoning_tokens: int = 1000
    max_tokens: int = 4000
    agents: list[AgentSpec] = [
        AgentSpec(model="openrouter/deepseek/deepseek-v4.1-flash", provider="baidu/fp8"),
        AgentSpec(model="openrouter/deepseek/deepseek-v4.1-flash", provider="baidu/fp8")]


# ---------------------------------------------------------------- models and served provider
_served: ContextVar[list[str] | None] = ContextVar("served", default=None)


def _track(model: Model) -> Model:
    """Record the raw response's "provider" field (OpenRouter's served endpoint) per call."""
    api = model.api
    if getattr(api, "_port_tracked", False) or not hasattr(api, "on_response"):
        return model
    original = api.on_response

    def on_response(response: dict[str, Any]) -> None:
        box = _served.get()
        if box is not None and isinstance(response, dict) and \
                isinstance(response.get("provider"), str):
            box.append(response["provider"])
        original(response)

    api.on_response = on_response
    api._port_tracked = True
    return model


@contextmanager
def served() -> Iterator[list[str]]:
    box: list[str] = []
    tok = _served.set(box)
    try:
        yield box
    finally:
        _served.reset(tok)


def make_model(spec: AgentSpec, cfg: Config) -> Model:
    config = GenerateConfig(reasoning_tokens=cfg.reasoning_tokens, max_tokens=cfg.max_tokens)
    if spec.provider is None:
        return _track(get_model(spec.model, config=config))
    return _track(get_model(spec.model, config=config, provider={
        "only": [spec.provider], "allow_fallbacks": False, "require_parameters": True,
        "data_collection": "deny"}))


# ---------------------------------------------------------------- per-turn state and tools
@dataclass
class Turn:
    plan: list[str] | None = None
    plan_error: str | None = None
    post: str | None = None
    note: str | None = None
    read_board: int = 0
    ready: bool = False


def tools_for(turn: Turn, *, act: Callable[[list[str]], str | None] | None = None,
              post: bool = False, read: Callable[[], str] | None = None, note: bool = False,
              ready: bool = False, max_len: int = 5) -> list[ToolDef]:
    out: list[ToolDef] = []
    if act is not None:
        async def act_fn(plan: list[str]) -> str:
            if isinstance(plan, str):
                plan = [plan]
            plan = [str(p) for p in plan][:max_len]
            err = act(plan)
            turn.plan, turn.plan_error = plan, err
            if err:
                return f"Plan rejected: {err}. Call act(plan) again with a valid plan."
            return "Plan accepted. It starts now."
        out.append(ToolDef(act_fn, name="act", description=(
            f"Set your plan: 1 to {max_len} actions done in order, e.g. "
            '["get_onion", "place_on_counter"]. A new plan replaces the old one. Every round '
            "decision must call act."),
            parameters={"plan": f"1 to {max_len} action names, e.g. \"put_in_pot\" or "
                                "\"wait(3)\"."}))
    if post:
        async def post_fn(message: str) -> str:
            if turn.post is not None:
                return "Ignored: one post per turn."
            message = " ".join(str(message).split())[:MAX_POST_CHARS]
            if not message:
                return "Not posted: empty message."
            turn.post = message
            return "Posted."
        out.append(ToolDef(post_fn, name="post", description=(
            f"Post a message (at most {MAX_POST_CHARS} characters) on the board you share "
            "with your teammate. One post per turn."), parameters={"message": "The message."}))
    if read is not None:
        async def read_fn() -> str:
            turn.read_board += 1
            return read()
        out.append(ToolDef(read_fn, name="read_board", description=(
            "Read the board: every post since the chat before this round, newest last. Free: "
            "takes no game time."), parameters={}))
    if note:
        async def note_fn(text: str) -> str:
            turn.note = str(text)
            return "Notepad updated."
        out.append(ToolDef(note_fn, name="note", description=(
            "Replace the text of your private notepad (only you can see it)."),
            parameters={"text": "The new notepad text."}))
    if ready:
        async def ready_fn() -> str:
            turn.ready = True
            return "Noted: you are ready to start the round."
        out.append(ToolDef(ready_fn, name="ready", description=(
            "Say you are ready to start the round (chat only)."), parameters={}))
    return out


@dataclass
class Gen:
    reasoning: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    served_by: list[str] = field(default_factory=list)
    n: int = 0

    def add(self, out: ModelOutput, by: list[str]) -> None:
        self.n += 1
        msg = out.message
        if isinstance(msg.content, list):
            for c in msg.content:
                if isinstance(c, ContentReasoning):
                    r = c.summary if c.redacted else c.reasoning
                    if r:
                        self.reasoning.append(r)
        if msg.text:
            self.text.append(msg.text)
        if out.usage:
            self.input_tokens += out.usage.input_tokens
            self.output_tokens += out.usage.output_tokens
            if out.usage.reasoning_tokens is not None:
                self.reasoning_tokens = (self.reasoning_tokens or 0) + out.usage.reasoning_tokens
        for b in by:
            if b not in self.served_by:
                self.served_by.append(b)

    def record(self, **kw: Any) -> DecisionRecord:
        return DecisionRecord(
            reasoning="\n\n".join(self.reasoning) or None, text="\n\n".join(self.text) or None,
            input_tokens=self.input_tokens if self.n else None,
            output_tokens=self.output_tokens if self.n else None,
            reasoning_tokens=self.reasoning_tokens,
            served_by=",".join(self.served_by) or None, **kw)


@dataclass
class Agent:
    index: int                 # config index (which model)
    name: str
    model: Model
    spec: AgentSpec
    messages: list[ChatMessage]
    notepad: str | None = None
    posts: int = 0
    read_board: int = 0
    cutoffs: int = 0
    decisions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------- prompts
def system_prompt(cfg: Config, k: Kitchen, me: str, mate: str) -> str:
    return f"""\
You are {me}, one of two cooks in a kitchen game. Your teammate is {mate}.

{rules_text(k)}
THE SEASON
You and {mate} play {cfg.rounds} rounds in the same kitchen. Each round lasts \
{cfg.round_steps} steps and starts afresh (empty pots, everyone back at the start). Before each \
round there is a chat on the board; after each round there is a debrief where you see the score.

DECIDING
During a round, whenever you have no plan (at the start, or when your plan is done or has \
failed) you decide: call act(plan) with up to {cfg.max_plan_len} actions. The game waits while \
you decide. In a decision you may also call post(message) and read_board().

THE BOARD
A message board shared by you and {mate}. post(message) puts a message of at most \
{MAX_POST_CHARS} characters on it. During a round your state shows how many posts you have not \
read; read_board() reads them and is free. In the chat before a round you take turns: on your \
turn you may call post(message) and/or ready(), or reply without a tool call to pass. The chat \
ends when you have both called ready() or used all {cfg.chat_max_posts} posts.

YOUR NOTEPAD
A private note only you can see, shown to you at every decision and kept all season. \
note(text) replaces it.

GOAL
{GOAL}
"""


class Board:
    def __init__(self) -> None:
        self.posts: list[Post] = []
        self.read_upto: dict[str, int] = {}

    def add(self, p: Post) -> None:
        self.posts.append(p)

    def scope(self, round: int) -> list[Post]:
        return [p for p in self.posts if p.round == round]

    def unread(self, agent: str, round: int) -> list[Post]:
        seen = self.read_upto.get(agent, 0)
        return [p for p in self.posts[seen:] if p.round == round and p.agent != agent]

    def mark_read(self, agent: str) -> None:
        self.read_upto[agent] = len(self.posts)


def fmt_posts(posts: list[Post]) -> str:
    return "\n".join(f"[{'chat' if p.phase == 'chat' else f'step {p.t}'}] {p.agent}: {p.text}"
                     for p in posts) or "(no posts)"


# ---------------------------------------------------------------- season
class Season:
    def __init__(self, cfg: Config, models: list[Model], epoch: int = 1):
        self.cfg = cfg
        self.epoch = epoch
        rng = random.Random(cfg.seed * 1000 + epoch)
        names = rng.sample(NATO, 2)
        # fairness: which agent gets which start position, who speaks first in chat
        self.start_order = rng.sample([0, 1], 2)        # start_order[player_idx] = agent idx
        self.first_speaker = rng.randrange(2)
        self.agents = [Agent(i, names[i], models[i], cfg.agents[i], []) for i in range(2)]
        self.board = Board()
        self.rounds: list[dict[str, Any]] = []

    def kitchen(self) -> Kitchen:
        return Kitchen(self.cfg.layout, [self.agents[i].name for i in self.start_order])

    def log(self, rec: BaseModel) -> None:
        transcript().info(rec.model_dump(mode="json"), source="kitchen")

    def event(self, phase: Phase, round: int, t: int, type_: str, agent: str | None = None,
              **data: Any) -> None:
        self.log(Event(phase=phase, round=round, t=t, type=type_, agent=agent, data=data))

    async def run(self) -> dict[str, Any]:
        k = self.kitchen()
        self.log(Header(
            config_name=self.cfg.name, level=self.cfg.layout, map=k.map_rows(),
            agents=[AgentInfo(name=a.name, letter=a.name[0], model=a.spec.model,
                              provider=a.spec.provider) for a in self.agents],
            seed=self.cfg.seed, epoch=self.epoch, round_s=self.cfg.round_steps,
            rounds=self.cfg.rounds, cook_s=k.cook_time))
        for a in self.agents:
            mate = self.agents[1 - a.index].name
            a.messages.append(ChatMessageSystem(content=system_prompt(self.cfg, k, a.name, mate)))
        for r in range(1, self.cfg.rounds + 1):
            await self.chat(r)
            await self.round(r)
            await self.debrief(r)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        A = self.agents
        return {"agents": [a.name for a in A], "models": [a.spec.model for a in A],
                "providers": [a.spec.provider for a in A], "rounds": self.rounds,
                "start_positions": {self.agents[ai].name: pi + 1
                                    for pi, ai in enumerate(self.start_order)},
                "first_speaker": A[self.first_speaker].name,
                **{key: [getattr(a, key) for a in A] for key in
                   ("posts", "read_board", "cutoffs", "decisions", "input_tokens",
                    "output_tokens")}}

    async def model_turn(self, a: Agent, text: str, tools: list[ToolDef], max_gens: int,
                         done: Callable[[], bool]) -> Gen:
        gen = Gen()
        a.messages.append(ChatMessageUser(content=text))
        for _ in range(max_gens):
            with served() as by:
                out = await a.model.generate(a.messages, tools=tools, tool_choice="auto")
            a.messages.append(out.message)
            gen.add(out, by)
            if out.message.tool_calls:
                res = await execute_tools(a.messages, tools)
                a.messages.extend(res.messages)
            if done() or not out.message.tool_calls:
                break
        a.input_tokens += gen.input_tokens
        a.output_tokens += gen.output_tokens
        return gen

    def _post(self, a: Agent, phase: Phase, r: int, t: int, text: str | None) -> None:
        if text is None:
            return
        a.posts += 1
        p = Post(phase=phase, round=r, t=t, agent=a.name, text=text)
        self.board.add(p)
        self.log(p)

    # ------------------------------------------------------------ chat
    async def chat(self, r: int) -> None:
        cfg, clock = self.cfg, time.monotonic()
        self.event("chat", r, 0, "phase_start")
        k = self.kitchen()
        used = {a.name: 0 for a in self.agents}
        ready = {a.name: False for a in self.agents}
        had_turn: set[str] = set()
        order = [self.first_speaker, 1 - self.first_speaker]
        turn_i = 0

        def finished(a: Agent) -> bool:
            return ready[a.name] or used[a.name] >= cfg.chat_max_posts

        while not all(finished(a) for a in self.agents) and turn_i < 2 * (cfg.chat_max_posts + 2):
            a = self.agents[order[turn_i % 2]]
            new = self.board.unread(a.name, r)
            self.board.mark_read(a.name)
            lines = []
            if a.name not in had_turn:
                had_turn.add(a.name)
                scores = ", ".join(f"round {i + 1}: {x['score']}" for i, x in enumerate(self.rounds))
                lines += [f"Chat before round {r} of {cfg.rounds}." +
                          (f" Scores so far: {scores}." if scores else ""),
                          "The kitchen at the start of the round:", k.state_text(
                              k.players[[p.name for p in k.players].index(a.name)]),
                          f'Your notepad: "{a.notepad}"' if a.notepad else "Your notepad is empty."]
            else:
                lines.append(f"Your turn in the chat before round {r}.")
            lines.append("New posts on the board:\n" + fmt_posts(new) if new else "No new posts.")
            lines.append(("You have said you are ready. " if ready[a.name] else "") +
                         f"Posts left: {cfg.chat_max_posts - used[a.name]}. Call post(message) "
                         "and/or ready(), or reply without a tool call to pass.")
            turn = Turn()
            tools = tools_for(turn, post=used[a.name] < cfg.chat_max_posts, ready=True)
            async with span(f"agent-{a.name}"):
                gen = await self.model_turn(a, "\n".join(lines), tools, 2,
                                            lambda: turn.ready or turn.post is not None)
            if turn.post is not None:
                used[a.name] += 1
            self._post(a, "chat", r, turn_i, turn.post)
            ready[a.name] = ready[a.name] or turn.ready
            self.log(gen.record(phase="chat", round=r, t=turn_i, agent=a.name, post=turn.post,
                                ready=turn.ready))
            turn_i += 1
        self.event("chat", r, turn_i, "phase_end", duration_s=round(time.monotonic() - clock, 1),
                   ready=[n for n, v in ready.items() if v], posts=used)

    # ------------------------------------------------------------ round
    async def decide(self, k: Kitchen, p: Player, a: Agent, r: int, intro: str | None) -> None:
        cfg = self.cfg
        unread = self.board.unread(a.name, r)
        lines = ([intro] if intro else []) + [k.state_text(p)]
        lines.append(f"Unread posts on the board: {len(unread)}." if unread else
                     "No unread posts.")
        if a.notepad:
            lines.append(f'Your notepad: "{a.notepad}"')
        lines.append("You have no plan. Decide: call act(plan).")
        turn = Turn()

        def read() -> str:
            txt = fmt_posts(self.board.scope(r))
            self.board.mark_read(a.name)
            return txt

        def act(plan: list[str]) -> str | None:
            # dry-run the first action on the live kitchen (nothing moves while we decide)
            return k.check(p, plan[0]) if plan else "empty plan"

        tools = tools_for(turn, act=act, post=True, read=read, max_len=cfg.max_plan_len)
        t = k.t
        cut = 0
        async with span(f"agent-{a.name}"):
            gen = await self.model_turn(a, "\n".join(lines), tools, 3,
                                        lambda: turn.plan is not None and not turn.plan_error)
            while turn.plan is None and cut < 2:          # cutoff: no act at all -> re-ask
                cut += 1
                a.cutoffs += 1
                self.event("round", r, t, "cutoff", a.name)
                self.log(gen.record(phase="round", round=r, t=t, lands_t=t, agent=a.name,
                                    post=turn.post, read_board=turn.read_board > 0, cutoff=True))
                self._post(a, "round", r, t, turn.post)
                turn.post, turn.read_board = None, 0
                gen = await self.model_turn(
                    a, "Your last reply did not call act(plan), so you have no plan. "
                       "Call act(plan) now.", tools, 2,
                    lambda: turn.plan is not None and not turn.plan_error)
        a.decisions += 1
        for _ in range(turn.read_board):
            self.event("round", r, t, "read_board", a.name)
        self._post(a, "round", r, t, turn.post)
        if turn.plan is None or turn.plan_error:
            plan = ["wait(1)"]                 # gave up: stand still one step
            if turn.plan is None:
                a.cutoffs += 1
                self.event("round", r, t, "cutoff", a.name)
        else:
            plan = turn.plan
        k.set_plan(p, plan)
        self.log(gen.record(phase="round", round=r, t=t, lands_t=t, agent=a.name, plan=plan,
                            post=turn.post, read_board=turn.read_board > 0,
                            cutoff=turn.plan is None))

    async def round(self, r: int) -> None:
        cfg, clock = self.cfg, time.monotonic()
        k = self.kitchen()
        by_name = {a.name: a for a in self.agents}
        self.event("round", r, 0, "phase_start")
        self.log(k.frame("round", r))
        started: set[str] = set()
        while k.t < cfg.round_steps:
            need = k.needs_decision()
            if need:
                for p in need:
                    p.status = "deciding"
                intros = {p.name: (f"Round {r} of {cfg.rounds} starts now."
                                   if p.name not in started else None) for p in need}
                started.update(intros)
                await collect(*(self.decide(k, p, by_name[p.name], r, intros[p.name])
                                for p in need))
            for e in k.step("round", r):
                self.log(e)
            self.log(k.frame("round", r))
        self.rounds.append({"score": k.score, "served": k.served})
        self.event("round", r, k.t, "phase_end", duration_s=round(time.monotonic() - clock, 1),
                   score=k.score, served=k.served)

    # ------------------------------------------------------------ debrief
    async def debrief(self, r: int) -> None:
        clock = time.monotonic()
        self.event("debrief", r, 0, "phase_start")
        res = self.rounds[-1]
        hist = ", ".join(f"round {i + 1}: {x['score']}" for i, x in enumerate(self.rounds))

        async def one(a: Agent) -> tuple[Turn, Gen]:
            turn = Turn()
            text = (f"Round {r} of {self.cfg.rounds} is over. Team score this round: "
                    f"{res['score']} ({res['served']} soups served). Scores so far: {hist}.\n"
                    f'Your notepad: {chr(34) + a.notepad + chr(34) if a.notepad else "(empty)"}\n'
                    "You may call note(text) to rewrite your notepad, or reply without a tool "
                    "call.")
            async with span(f"agent-{a.name}"):
                gen = await self.model_turn(a, text, tools_for(turn, note=True), 1, lambda: True)
            return turn, gen

        results = await collect(*(one(a) for a in self.agents))
        for a, (turn, gen) in zip(self.agents, results):
            if turn.note is not None:
                a.notepad = turn.note
                self.event("debrief", r, a.index, "note", a.name)
            self.log(gen.record(phase="debrief", round=r, t=a.index, agent=a.name,
                                note=turn.note))
        self.event("debrief", r, 2, "phase_end", duration_s=round(time.monotonic() - clock, 1))


@solver
def season_solver(cfg: Config, models: list[Model] | None = None) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        ms = models or [make_model(s, cfg) for s in cfg.agents]
        season = Season(cfg, ms, state.epoch)
        summary = await season.run()
        state.store.set(STORE_KEY, summary)
        total = sum(x["score"] for x in summary["rounds"])
        state.output = ModelOutput.from_content(
            "overcooked_port", f"Season over: team score {total} "
                               f"({', '.join(str(x['score']) for x in summary['rounds'])}).")
        state.completed = True
        return state

    return solve
