"""Everything the agents read that isn't produced by the engine.

The engine supplies `rules_text(cfg)` (kitchen rules) and `Game.state_text(...)` (the per-decision
view). This module adds the board rules, the season structure and the goal (system prompt), and
the chat, debrief and phase-start messages. It says nothing about roles, leadership or strategy.
"""

from __future__ import annotations

from undercooked.board import MAX_POST_CHARS
from undercooked.config import Config
from undercooked.engine import Game, rules_text
from undercooked.engine.text import map_lines

GOAL = "Get the highest team score. You won't score highly unless you work together."

CUTOFF_NOTICE = ("Your last reply did not call act(plan), so no plan was set and you stood "
                 "still for 1 s.")

COMPACTION_PROMPT = (
    "Summarise this conversation so far so that you can keep playing the game from your summary "
    "alone. Include what happened in each phase and round (scores, orders served and missed), "
    "what you and your teammate posted, what you tried and how it worked, and where things stand "
    "now. Write in the first person. At most 400 words.{addendums}"
)


def _s(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def system_prompt(cfg: Config, me: str, mate: str) -> str:
    d = cfg.decision
    if cfg.board == "pull":
        board_read = ("During a round your state shows how many posts you have not read yet; "
                      "call read_board() to read them.")
    else:
        board_read = "During a round, new posts are shown to you with your state."
    no_board = "" if cfg.comms == "none" else "no board, and "
    practice = (f"First you practise alone in a small practice kitchen for {cfg.practice_s} s "
                f"({no_board}the score does not count). " if cfg.practice else "")
    between, posting, extras = _comms_lines(cfg)
    return _comms_sections(cfg, f"""\
You are {me}, one of two cooks in a kitchen game. Your teammate is {mate}.

{rules_text(cfg)}
THE SEASON
{practice}Then you and {mate} play {_s(cfg.rounds, 'round')} together in the same kitchen. Each \
round lasts {cfg.round_s} s and starts afresh (empty pots, new orders, everyone back at the start). \
{between}

DECIDING
During practice and rounds, whenever you have no plan (at the start, or when your plan is done \
or has failed) you decide: call act(plan) with your next plan. Every decision must end with \
act(plan). While you decide you stand still, and deciding costs game time: {_s(d.flat_s, 'second')} \
per decision{posting}. {extras}{thinking_budget(cfg)}

THE BOARD
A message board shared by you and {mate}. post(message) puts a message of at most \
{MAX_POST_CHARS} characters on it. {board_read} read_board() is free: it takes no game time.
In the chat before a round you take turns. On your turn you may call post(message) and/or ready() \
(you are ready to start the round), or reply without calling a tool to pass. You have up to \
{_s(cfg.chat_max_posts, 'post')} per chat. The chat ends when you have both called ready() or \
both used all your posts.

YOUR NOTEPAD
A private note that only you can see. It is shown to you with your state at every decision and \
kept for the whole season. note(text) replaces it.

GOAL
{GOAL}
""")


def season_intro(cfg: Config) -> str:
    parts = ["The season starts now."]
    if cfg.practice:
        parts.append("First: practice.")
    elif cfg.comms == "none":
        parts.append("First: round 1.")
    else:
        parts.append("First: the chat before round 1.")
    return " ".join(parts)


def practice_start(cfg: Config) -> str:
    if cfg.comms == "none":
        return f"Practice: you are alone in the practice kitchen for {cfg.practice_s} s."
    return (f"Practice: you are alone in the practice kitchen for {cfg.practice_s} s. "
            f"The board is not available.")


def round_start(cfg: Config, round: int) -> str:
    return f"Round {round} of {cfg.rounds} starts now."


def strip_board_line(state: str) -> str:
    return "\n".join(line for line in state.split("\n") if not line.startswith("Board:"))


def orders_rules(cfg: Config) -> str:
    o = cfg.orders
    return (f"Orders: the first arrives at the start, then one every {o.every_s} s (at most "
            f"{o.max_open} open). Each is due {o.due_s} s after it arrives: +{o.reward} if served, "
            f"-{o.penalty} if it expires. The round lasts {cfg.round_s} s.")


def kitchen_overview(game: Game, me: str) -> str:
    """Map, start positions, objects and counters, without any clock (for the chat)."""
    k = game.kitchen
    lines = [*map_lines(game), ""]
    for a in game.agents:
        if a.name == me:
            lines.append(f"You ({a.name}, {a.letter}) start at ({a.pos[0]},{a.pos[1]}).")
        else:
            lines.append(f"{a.name} ({a.letter}) starts at ({a.pos[0]},{a.pos[1]}).")
    objs = [f"{o.id} ({o.pos[0]},{o.pos[1]})" for o in k.objects.values() if o.kind != "counter"]
    lines.append("Objects: " + ", ".join(objs) + ".")
    shared = sorted((o.pos for o in k.shared_counters()), key=lambda p: (p[1], p[0]))
    if shared:
        lines.append("Counters listed in your state: "
                     + ", ".join(f"({x},{y})" for x, y in shared) + ".")
    return "\n".join(lines)


def chat_turn(cfg: Config, round: int, *, first_turn: bool, overview: str, new_posts: str | None,
              posts_left: int, ready: bool, notepad: str | None) -> str:
    lines: list[str] = []
    if first_turn:
        lines += [f"Chat before round {round} of {cfg.rounds}.", f"The kitchen for round {round}:",
                  overview, orders_rules(cfg), ""]
        if notepad is not None:
            lines.append(f'Your notepad: "{notepad}"')
    else:
        lines.append(f"Your turn in the chat before round {round}.")
    lines.append("New posts on the board:\n" + new_posts if new_posts else "No new posts.")
    status = "You have said you are ready. " if ready else ""
    lines.append(f"{status}Posts left: {posts_left}. Call post(message) and/or ready(), "
                 f"or reply without a tool call to pass.")
    return "\n".join(lines)


def debrief(cfg: Config, round: int, score: int, served: int, missed: int,
            history: list[int], notepad: str | None) -> str:
    lines = [f"Round {round} of {cfg.rounds} is over. Team score this round: {score} "
             f"({_s(served, 'order')} served, {missed} missed)."]
    if len(history) > 1:
        lines.append("Scores so far: " + ", ".join(f"round {i + 1}: {s}"
                                                  for i, s in enumerate(history)) + ".")
    pad = f'"{notepad}"' if notepad is not None else "(empty)"
    lines.append(f"Your notepad: {pad}")
    lines.append("You may call note(text) to rewrite your notepad, or reply without a tool call.")
    return "\n".join(lines)


# ------------------------------------------------------------- experimental conditions
# Thinking budget (cfg.decision.reasoning) and who can talk when (cfg.comms). Kept apart from
# the text above so that the full-communication prompt reads as it always has.

# The forced-answer follow-up (season.Season.forced_answer): what the turn still needs to do
FORCED_ASKS = {
    "act": "Call act now with your plan.",
    "chat": "Post your message and/or call ready() now, or pass.",
    "debrief": "Update your notepad with note() now, or pass.",
}
FORCED_WHY = "Your thinking budget is used up."
# reasoning off: no thinking budget to speak of
FORCED_WHY_NO_REASONING = {"act": "Your reply did not call act.",
                           "chat": "Your reply was cut off.", "debrief": "Your reply was cut off."}


def thinking_budget(cfg: Config) -> str:
    """The sentence that tells the agent its reasoning budget (nothing when reasoning is off)."""
    if cfg.decision.reasoning == "off":
        return ""
    return (f" You have about {cfg.decision.reasoning_tokens} tokens to think before each "
            f"decision; decide quickly.")


def forced_prompt(cfg: Config, kind: str) -> str:
    """The user message of the forced-answer follow-up call; kind is act, chat or debrief."""
    why = FORCED_WHY if cfg.decision.reasoning == "on" else FORCED_WHY_NO_REASONING[kind]
    return f"{why} {FORCED_ASKS[kind]}"


def round_result(cfg: Config, round: int, score: int, served: int, missed: int) -> str:
    """debrief "score_only": the round's result, put before the agent's next message."""
    return (f"Round {round} of {cfg.rounds} is over. Team score: {score} ({_s(served, 'order')} "
            f"served, {missed} missed).")


def _comms_lines(cfg: Config) -> tuple[str, str, str]:
    """System prompt sentences that depend on cfg.comms: what happens between rounds, what a post
    costs, and what a decision may do besides act."""
    d = cfg.decision
    after = ("after each round there is a debrief where you see the round's score"
             if cfg.debrief == "full" else "after each round you are told the round's score")
    if cfg.comms == "none":
        between = f"{after[0].upper()}{after[1:]}."
    else:
        between = f"Before each round there is a chat on the board; {after}."
    if cfg.comms == "full":
        posting = f", plus 1 s for every {d.post_words_per_s} words you post (rounded up)"
        extras = ("Your plan, post and note take effect when your decision lands. In a decision "
                  "you may also call post(message), read_board() and note(text).")
    else:
        posting = ""
        extras = ("Your plan and note take effect when your decision lands. In a decision you "
                  "may also call note(text).")
    return between, posting, extras


PLANNING_ONLY_RULE = (
    f"You can exchange messages with your teammate only in the chat before each round: "
    f"post(message) puts a message of at most {MAX_POST_CHARS} characters on a board you share. "
    f"There is no board during practice or rounds.")


def _comms_sections(cfg: Config, prompt: str) -> str:
    """The system prompt's THE BOARD section for cfg.comms. full: as written. planning_only: a
    THE CHAT section, whose first line (the board during rounds) becomes PLANNING_ONLY_RULE and
    whose chat rules (every later line) stay as written. none: no section at all."""
    if cfg.comms == "full":
        return prompt
    start, end = "\nTHE BOARD\n", "\n\nYOUR NOTEPAD\n"
    if start not in prompt or end not in prompt.split(start, 1)[1]:
        raise ValueError("system prompt: THE BOARD section not found")
    head, rest = prompt.split(start, 1)
    board, tail = rest.split(end, 1)
    if cfg.comms == "none":
        return f"{head}{end[1:]}{tail}"
    chat_rules = board.split("\n", 1)[1] if "\n" in board else ""
    section = "\n".join(x for x in (PLANNING_ONLY_RULE, chat_rules) if x)
    return f"{head}\nTHE CHAT\n{section}{end}{tail}"


def compaction_prompt(cfg: Config) -> str:
    """COMPACTION_PROMPT, without posts when the agents cannot message each other at all."""
    if cfg.comms != "none":
        return COMPACTION_PROMPT
    posted = "what you and your teammate posted, "
    if posted not in COMPACTION_PROMPT:
        raise ValueError("COMPACTION_PROMPT no longer mentions posts: update compaction_prompt")
    return COMPACTION_PROMPT.replace(posted, "")
