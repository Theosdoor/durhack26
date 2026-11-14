"""The agents' tools: act, post, read_board, note, ready.

Tools are built per decision (or chat/debrief turn) as Inspect `ToolDef`s over closures, so each
call writes into that decision's `Turn` record and the season's state; they run through Inspect's
`execute_tools`, so every call shows as a ToolEvent in Inspect View. Nothing here touches the game
clock: the season applies a decision's plan, post and note when the decision lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from inspect_ai.tool import ToolDef

from undercooked.board import MAX_POST_CHARS
from undercooked.engine.actions import split_plan


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
            return "Ignored: you already sent a plan in this decision."
        if isinstance(plan, str):
            plan = [plan]
        plan = [str(p) for p in plan]
        turn.plan = plan
        turn.plan_error = check(plan)
        if turn.plan_error is not None:
            return (f"Plan rejected: {turn.plan_error}. You stand still and decide again when "
                    f"this decision lands.")
        n = len(split_plan(plan))            # what the engine counts: ";", "," and newlines split
        extra = (f" Your plan has {n} actions. Only the first {max_len} actions are kept."
                 if n > max_len else "")
        return f"Plan accepted. It starts when this decision lands.{extra}"

    return ToolDef(
        act, name="act",
        description=(f"Set your plan: a list of 1 to {max_len} actions done in order, e.g. "
                     f'["interact(onion_crate)", "interact(pot_1)"]. Every decision must end '
                     f"with act. A new plan replaces your old one."),
        parameters={"plan": f"1 to {max_len} actions, each one string such as "
                            f'"interact(pot_1)", "move_to(2, 3)" or "wait(3)".'},
    )


def post_tool(turn: Turn, cap_error: Callable[[], str | None] | None = None,
              lands_later: bool = True) -> ToolDef:
    async def post(message: str) -> str:
        if turn.post is not None:
            return "Ignored: one post per turn."
        if cap_error is not None and (err := cap_error()) is not None:
            return f"Not posted: {err}"
        message = " ".join(str(message).split())
        if not message:
            return "Not posted: the message is empty."
        cut = len(message) > MAX_POST_CHARS
        turn.post = message[:MAX_POST_CHARS]
        when = " It appears on the board when this decision lands." if lands_later else ""
        return ("Posted" + (f" (cut to {MAX_POST_CHARS} characters)" if cut else "") + "." + when)

    return ToolDef(
        post, name="post",
        description=(f"Post a message (at most {MAX_POST_CHARS} characters) on the board you "
                     f"share with your teammate. One post per turn."),
        parameters={"message": "The message."},
    )


def read_board_tool(turn: Turn, read: Callable[[], str]) -> ToolDef:
    async def read_board() -> str:
        turn.read_board += 1
        return read()

    return ToolDef(
        read_board, name="read_board",
        description=("Read the board: every post since the chat before this round, newest last. "
                     "Free: takes no game time. Marks the posts as read."),
        parameters={},
    )


def note_tool(turn: Turn) -> ToolDef:
    async def note(text: str) -> str:
        turn.note = str(text)
        return "Notepad updated."

    return ToolDef(
        note, name="note",
        description="Replace the text of your private notepad (only you can see it).",
        parameters={"text": "The new notepad text."},
    )


def ready_tool(turn: Turn) -> ToolDef:
    async def ready() -> str:
        turn.ready = True
        return "Noted: you are ready to start the round."

    return ToolDef(
        ready, name="ready",
        description="Say that you are ready to start the round (chat only).",
        parameters={},
    )
