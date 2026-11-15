"""Everything the agents read that isn't produced by the engine.

The wording lives in the Markdown files of the top-level `prompts/` folder (see
prompts/README.md); this module loads them (`promptfiles.load`), fills in the numbers and picks
which variant or snippet applies to the experimental condition. The engine supplies
`rules_text(cfg)` (kitchen rules) and `Game.state_text(...)` (the per-decision view). This
module adds the board rules, the season structure and the goal (system prompt), and the chat,
debrief and phase-start messages. It says nothing about roles, leadership or strategy.
"""

from __future__ import annotations

import re

from undercooked.board import MAX_POST_CHARS
from undercooked.config import Config
from undercooked.engine import Game, rules_text
from undercooked.engine.text import map_lines
from undercooked.promptfiles import load

GOAL = load("system", "goal").split("\n", 1)[1]    # the sentence under the GOAL heading


def cutoff_notice(seconds: int) -> str:
    """Told at the next decision after a cutoff that cost `seconds` (1 + the post's time)."""
    post = " " + load("messages", "cutoff_notice.post", post_s=seconds - 1) if seconds > 1 else ""
    return load("messages", "cutoff_notice.notice", seconds=seconds, post=post)


def forced_notice(seconds: int) -> str:
    """Told at the next decision after a forced answer, which cost `seconds` more."""
    return load("messages", "forced_notice", seconds=seconds)


def unseen_chat(posts: str) -> str:
    """comms planning_only: chat posts the agent had not seen when the round starts (the
    teammate's last post can come after the agent's last chat turn)."""
    return load("messages", "round_start.unseen_chat", posts=posts)


def chat_turns_each(cfg: Config) -> int:
    """Most turns each agent gets in one chat (season.chat ends the chat after 2x this)."""
    return cfg.chat_max_posts + 2


def _s(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
               8: "eight", 9: "nine"}


def join_names(names: list[str]) -> str:
    """"Tango", "Tango and Lima", "Tango, Lima and Golf"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def us(mates: list[str]) -> str:
    """The team seen from one agent: "you and Tango", "you, Tango and Lima"."""
    return join_names(["you", *mates])


def _mates(mates: str | list[str]) -> list[str]:
    return [mates] if isinstance(mates, str) else list(mates)


def served_result(cfg: Config, served: int, missed: int) -> str:
    """"2 soups served" (scoring soups) or "2 orders served, 1 missed" (scoring orders)."""
    if cfg.scoring == "soups":
        return load("messages", "result.soups", served=_s(served, "soup"))
    return load("messages", "result.orders", served=_s(served, "order"), missed=missed)


PLANNING_ONLY_RULE = load("communication", "planning_only_rule", max_post_chars=MAX_POST_CHARS)


def compaction_prompt(cfg: Config) -> str:
    """The summarising prompt, without posts when the agents cannot message each other at all.
    It keeps a literal `{addendums}`: Inspect fills that in itself."""
    posted = load("messages", "compaction.posted") + " " if cfg.comms != "none" else ""
    results = load("messages", f"compaction.results_{cfg.scoring}")
    return load("messages", "compaction.prompt", posted=posted, results=results,
                addendums="{addendums}")


COMPACTION_PROMPT = compaction_prompt(Config.model_construct(comms="full", scoring="soups"))


def thinking_budget(cfg: Config) -> str:
    """The sentence that tells the agent its reasoning budget (nothing when reasoning is off)."""
    if cfg.decision.reasoning == "off":
        return ""
    return " " + load("system", "thinking_budget")


def _comms_lines(cfg: Config) -> tuple[str, str, str]:
    """System prompt sentences that depend on cfg.comms: what happens between rounds, what a post
    costs, and what a decision may do besides act."""
    d = cfg.decision
    after = "score_only" if cfg.debrief == "score_only" else "private"
    between = load("system",
                   f"between_{'no_chat' if cfg.comms == 'none' else 'chat'}_{after}")
    if cfg.comms == "full":
        posting = load("system", "posting_full", post_words_per_s=d.post_words_per_s)
        extras = load("system", "extras_full")
    else:
        posting = ""
        extras = load("system", "extras_no_post")
    return between, posting, extras


def _comms_section(cfg: Config, mates: list[str]) -> str:
    """The system prompt's section on the board / chat (none: nothing), with its blank line."""
    chat_rules = load("communication", "chat_rules", chat_posts=_s(cfg.chat_max_posts, "post"),
                      chat_turns=_s(chat_turns_each(cfg), "turn"))
    if cfg.comms == "full":
        board_read = load("system", f"board_read_{cfg.board}")
        section = load("communication", "comms.full", us=us(mates), max_post_chars=MAX_POST_CHARS,
                       board_read=board_read, chat_rules=chat_rules)
    elif cfg.comms == "planning_only":
        section = load("communication", "comms.planning_only", planning_only_rule=PLANNING_ONLY_RULE,
                       chat_rules=chat_rules)
    else:
        section = load("communication", "comms.none")
    return section + "\n\n" if section else ""


def identity(me: str, mates: list[str]) -> str:
    team = load("system", "identity.one_mate" if len(mates) == 1 else "identity.many_mates",
                names=join_names(mates))
    return load("system", "identity", me=me, cooks=COUNT_WORDS.get(len(mates) + 1,
                                                                    str(len(mates) + 1)), team=team)


def _bare_prompt(cfg: Config, me: str, mates: list[str]) -> str:
    """rules bare / open: identity, the goal (open: an invitation, no goal) and one line on the
    rounds and their chats. Nothing else: the tool descriptions are the only manual."""
    if cfg.rules == "open":
        aim = load("system", "open.invitation", names=join_names(mates))
    else:
        aim = load("system", "bare.goal")
    key = {"full": "chat_full", "planning_only": "planning_only", "none": "no_chat"}[cfg.comms]
    season = load("system", f"bare.season_{key}", rounds=_s(cfg.rounds, "round"))
    return "\n\n".join([identity(me, mates), aim, season]) + "\n"


def system_prompt(cfg: Config, me: str, mates: str | list[str]) -> str:
    """The system prompt of agent `me`; `mates` are the other agents' names (a str: one mate)."""
    mates = _mates(mates)
    if cfg.rules in ("bare", "open"):
        return _bare_prompt(cfg, me, mates)
    d = cfg.decision
    practice = ""
    if cfg.practice:
        key = "practice_intro_no_comms" if cfg.comms == "none" else "practice_intro"
        practice = (load("system", key, practice_s=cfg.practice_s) + " "
                    + load("system", "practice_finish_note") + " ")
    between, posting, extras = _comms_lines(cfg)
    kw = dict(us=us(mates), practice=practice, rounds=_s(cfg.rounds, "round"),
              round_s=cfg.round_s, between=between, afresh=load("system", f"afresh.{cfg.scoring}"),
              flat=_s(d.flat_s, "second"), posting=posting, extras=extras,
              thinking_budget=thinking_budget(cfg))
    parts = [identity(me, mates), rules_text(cfg).removesuffix("\n"),
             load("system", "season", **kw), load("system", "deciding", **kw),
             _comms_section(cfg, mates).removesuffix("\n\n"), load("system", "notepad"),
             load("system", "goal")]
    return "\n\n".join(x for x in parts if x) + "\n"


def season_intro(cfg: Config) -> str:
    if cfg.practice:
        return load("messages", "season_intro.practice")
    return load("messages", "season_intro." + ("no_chat" if cfg.comms == "none" else "chat"))


def practice_start(cfg: Config) -> str:
    return load("messages", "practice_start." + ("no_comms" if cfg.comms == "none" else "with_comms"),
                practice_s=cfg.practice_s)


def round_start(cfg: Config, round: int, chat_turn_limit: bool = False) -> str:
    text = load("messages", "round_start.base", round=round, rounds=cfg.rounds)
    if chat_turn_limit:
        text += " " + load("messages", "round_start.turn_limit", turns=_s(chat_turns_each(cfg), "turn"))
    return text


def strip_board_line(state: str) -> str:
    """Drop the state's "Board:" line (prompts/state.md: board_unread, board_none)."""
    return "\n".join(line for line in state.split("\n") if not line.startswith("Board:"))


def push_board_text(posts: str | None) -> str:
    """Board part of the state in board: push mode: the new posts (formatted) or none."""
    if posts:
        return load("state", "push_new_posts", posts=posts)
    return load("state", "push_none")


def orders_rules(cfg: Config) -> str:
    o = cfg.orders
    return load("messages", "orders_rules", every_s=o.every_s, max_open=o.max_open, due_s=o.due_s,
                reward=o.reward, penalty=o.penalty, round_s=cfg.round_s)


def kitchen_overview(game: Game, me: str) -> str:
    """Map, start positions, objects and counters, without any clock (for the chat)."""
    k = game.kitchen
    lines = [*map_lines(game), ""]
    for a in game.agents:
        lines.append(load("messages", "kitchen_overview.start_" + ("you" if a.name == me else "mate"),
                          name=a.name, letter=a.letter, x=a.pos[0], y=a.pos[1]))
    objs = [load("messages", "kitchen_overview.object_item", id=o.id, x=o.pos[0], y=o.pos[1])
            for o in k.objects.values() if o.kind != "counter"]
    lines.append(load("messages", "kitchen_overview.objects", objects=", ".join(objs)))
    shared = sorted((o.pos for o in k.shared_counters()) if game.cfg.counters_shown == "shared"
                    else (), key=lambda p: (p[1], p[0]))
    if shared:
        lines.append(load("messages", "kitchen_overview.counters", counters=", ".join(
            load("messages", "kitchen_overview.counter_item", x=x, y=y) for x, y in shared)))
    return "\n".join(lines)


def chat_turn(cfg: Config, round: int, *, first_turn: bool, overview: str, new_posts: str | None,
              posts_left: int, ready: bool, notepad: str | None) -> str:
    lines: list[str] = []
    if first_turn:
        # rules other than "full": the order rules are for the agents to find out, so not here;
        # scoring "soups": there are no orders
        rules = orders_rules(cfg) if cfg.rules == "full" and cfg.scoring == "orders" else ""
        first = load("communication", "chat_turn.first", round=round, rounds=cfg.rounds,
                     overview=overview, orders_rules=rules)
        lines += [first if rules else first.rstrip("\n"), ""]
        if notepad is not None:
            lines.append(load("communication", "chat_turn.notepad", notepad=notepad))
    else:
        lines.append(load("communication", "chat_turn.later", round=round))
    lines.append(load("communication", "chat_turn.new_posts", posts=new_posts) if new_posts
                 else load("communication", "chat_turn.no_new_posts"))
    status = load("communication", "chat_turn.ready_prefix") + " " if ready else ""
    lines.append(status + load("communication", "chat_turn.status", posts_left=posts_left))
    return "\n".join(lines)


def debrief(cfg: Config, round: int, score: int, served: int, missed: int,
            history: list[int], notepad: str | None) -> str:
    """The private turn after round `round` (debrief "private")."""
    lines = [load("messages", "debrief.result", round=round, rounds=cfg.rounds, score=score,
                  result=served_result(cfg, served, missed))]
    if len(history) > 1:
        scores = ", ".join(load("messages", "debrief.score_item", n=i + 1, score=s)
                           for i, s in enumerate(history))
        lines.append(load("messages", "debrief.scores_so_far", scores=scores))
    lines.append(load("messages", "debrief.notepad", notepad=notepad) if notepad is not None
                 else load("messages", "debrief.notepad_empty"))
    lines.append(load("messages", "debrief.instruction"))
    if round >= cfg.rounds:
        lines.append(load("messages", "debrief.last"))
    elif cfg.comms != "none":
        lines.append(load("messages", "debrief.next_chat", round=round + 1))
    else:
        lines.append(load("messages", "debrief.next_round", round=round + 1))
    return "\n".join(lines)


def reflection(cfg: Config, history: list[int]) -> str:
    """The final reflection question list (one free-text reply, no tools)."""
    scores = ", ".join(load("messages", "debrief.score_item", n=i + 1, score=s)
                       for i, s in enumerate(history))
    lines = [load("messages", "reflection.intro", scores=scores, total=sum(history)),
             load("messages", "reflection.questions")]
    if cfg.rules == "open":
        lines.append(load("messages", "reflection.open_question"))
    return "\n".join(lines)


_PREDICTED = re.compile(r"PREDICTED_SCORE\W*?(-?\d[\d,]*(?:\.\d+)?)", re.I)


def predicted_score(text: str | None) -> float | None:
    """The number on the last "PREDICTED_SCORE: <number>" line of a reflection (None if none)."""
    hits = _PREDICTED.findall(text or "")
    return float(hits[-1].replace(",", "")) if hits else None   # "1,200" -> 1200


# ------------------------------------------------------------- experimental conditions
# Thinking budget (cfg.decision.reasoning) and who can talk when (cfg.comms). Kept apart from
# the text above so that the full-communication prompt reads as it always has.

# The forced-answer follow-up (season.Season.forced_answer): what the turn still needs to do
FORCED_ASKS = {kind: load("messages", f"forced.{kind}") for kind in ("act", "chat", "debrief")}


def forced_prompt(cfg: Config, kind: str) -> str:
    """The user message of the forced-answer follow-up call; kind is act, chat or debrief."""
    why = (load("messages", "forced_why.thinking_budget_used_up") if cfg.decision.reasoning == "on"
           else load("messages", f"forced_why.{kind}_no_reasoning"))
    return f"{why} {FORCED_ASKS[kind]}"


def round_result(cfg: Config, round: int, score: int, served: int, missed: int) -> str:
    """debrief "score_only": the round's result, put before the agent's next message."""
    return load("messages", "round_result", round=round, rounds=cfg.rounds, score=score,
                result=served_result(cfg, served, missed))
