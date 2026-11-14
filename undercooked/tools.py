"""The agents' tools: act, post, read_board, note, ready.

Tools are built per decision (or chat/debrief turn) as Inspect `ToolDef`s over closures, so each
call writes into that decision's `Turn` record and the season's state; they run through Inspect's
`execute_tools`, so every call shows as a ToolEvent in Inspect View. Nothing here touches the game
clock: the season applies a decision's plan, post and note when the decision lands.
The texts the tools show and return are in prompts/tools.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from inspect_ai.tool import ToolDef

from undercooked.board import MAX_POST_CHARS
from undercooked.engine.actions import split_plan
from undercooked.promptfiles import load


@dataclass
class Turn:
    """What one decision (or chat / debrief turn) did, filled in by the tools."""

    plan: list[str] | None = None        # act(plan) as sent
    plan_error: str | None = None        # why the plan was rejected (None = accepted)
    kept: list[str] | None = None        # the plan that will run: split into actions, cut to
                                         # max_plan_len, up to the first invalid one; [] if rejected
    post: str | None = None
    note: str | None = None
    read_board: int = 0                  # read_board() calls
    ready: bool = False


def act_tool(turn: Turn, check: Callable[[list[str]], str | None], max_len: int) -> ToolDef:
    async def act(plan: list[str]) -> str:
        if turn.plan is not None:
            return load("tools", "act.ignored")
        if isinstance(plan, str):
            plan = [plan]
        plan = [str(p) for p in plan]
        turn.plan = plan
        turn.plan_error = check(plan)
        if turn.plan_error is not None:
            return load("tools", "act.rejected", error=turn.plan_error)
        n = len(split_plan(plan))            # what the engine counts: ";", "," and newlines split
        extra = (" " + load("tools", "act.too_long", n=n, max_len=max_len)
                 if n > max_len else "")
        return load("tools", "act.accepted") + extra

    return ToolDef(
        act, name="act",
        description=load("tools", "act.description", max_len=max_len),
        parameters={"plan": load("tools", "act.plan_param", max_len=max_len)},
    )


def post_tool(turn: Turn, cap_error: Callable[[], str | None] | None = None,
              lands_later: bool = True) -> ToolDef:
    async def post(message: str) -> str:
        if turn.post is not None:
            return load("tools", "post.ignored")
        if cap_error is not None and (err := cap_error()) is not None:
            return load("tools", "post.not_posted", reason=err)
        message = " ".join(str(message).split())
        if not message:
            return load("tools", "post.empty")
        cut = len(message) > MAX_POST_CHARS
        turn.post = message[:MAX_POST_CHARS]
        text = (load("tools", "post.posted_cut", max_post_chars=MAX_POST_CHARS) if cut
                else load("tools", "post.posted"))
        return text + (" " + load("tools", "post.lands_later") if lands_later else "")

    return ToolDef(
        post, name="post",
        description=load("tools", "post.description", max_post_chars=MAX_POST_CHARS),
        parameters={"message": load("tools", "post.message_param")},
    )


def post_cap_error(cap: int) -> str:
    """Why a post is refused once `cap` posts are used up in a chat."""
    return load("tools", "post.cap_reached", cap=cap)


def board_empty_text() -> str:
    """What read_board() answers when there are no posts."""
    return load("tools", "read_board.empty")


def read_board_tool(turn: Turn, read: Callable[[], str]) -> ToolDef:
    async def read_board() -> str:
        turn.read_board += 1
        return read()

    return ToolDef(
        read_board, name="read_board",
        description=load("tools", "read_board.description"),
        parameters={},
    )


def note_tool(turn: Turn) -> ToolDef:
    async def note(text: str) -> str:
        turn.note = str(text)
        return load("tools", "note.result")

    return ToolDef(
        note, name="note",
        description=load("tools", "note.description"),
        parameters={"text": load("tools", "note.text_param")},
    )


def ready_tool(turn: Turn) -> ToolDef:
    async def ready() -> str:
        turn.ready = True
        return load("tools", "ready.result")

    return ToolDef(
        ready, name="ready",
        description=load("tools", "ready.description"),
        parameters={},
    )
