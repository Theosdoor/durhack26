"""Forgiving parser for plan actions written by LLMs.

Canonical forms:  interact(<target>)   move_to(x, y)   move_to(<target>)   wait(<seconds>)
Also accepted (real model output): interact(onion_crate), interact(pot 1), interact("pot_1"),
interact(counter(2,2)), interact(counter_2_2), interact((2,2)), move_to((2,2)), move_to(x=2, y=2),
move_to 2,2, wait(3s), wait, pick_up(onion_crate), serve(hatch), go_to(pot_1), move(left), up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from undercooked.engine.kitchen import DIRS, KINDS, Kitchen, Pos

WAIT_MIN, WAIT_MAX = 1, 30

HELP = "use interact(<id>), move_to(x, y), move_to(<id>) or wait(<seconds>)"

_VERBS = {
    "interact": "interact", "interactwith": "interact", "use": "interact", "pickup": "interact",
    "pick": "interact", "putdown": "interact", "put": "interact", "place": "interact",
    "drop": "interact", "take": "interact", "grab": "interact", "get": "interact",
    "add": "interact", "plate": "interact", "serve": "interact", "deliver": "interact",
    "fetch": "interact", "collect": "interact", "cook": "interact", "fill": "interact",
    "moveto": "move_to", "goto": "move_to", "walkto": "move_to", "go": "move_to",
    "walk": "move_to", "move": "move_to", "navigate": "move_to", "navigateto": "move_to",
    "wait": "wait", "sleep": "wait", "idle": "wait", "stay": "wait", "pause": "wait",
    "noop": "wait", "rest": "wait",
}
_DIR_WORDS = {
    "up": "up", "north": "up", "n": "up", "u": "up",
    "down": "down", "south": "down", "s": "down", "d": "down",
    "left": "left", "west": "left", "w": "left", "l": "left",
    "right": "right", "east": "right", "e": "right", "r": "right",
}
_KIND_ALIASES = {
    "onion_crate": [
        "onion_crate", "onions_crate", "onion", "onions", "crate", "onion_box", "onion_bin",
        "onion_dispenser", "onion_station", "onion_supply", "onion_pile", "onion_source",
    ],
    "dish_rack": [
        "dish_rack", "dishes_rack", "dish", "dishes", "rack", "dish_station", "plate", "plates",
        "plate_rack", "bowl", "bowls", "dish_dispenser", "dish_pile", "dish_source",
    ],
    "pot": ["pot", "pots", "stove", "cooker", "cooking_pot", "oven"],
    "hatch": [
        "hatch", "serving_hatch", "serve", "serving", "serving_window", "window", "delivery",
        "serving_station", "service_hatch", "serving_counter", "pass", "server", "delivery_hatch",
    ],
    "counter": ["counter", "counters", "countertop", "counter_top", "table"],
}
KIND_OF_ALIAS = {a: k for k, aliases in _KIND_ALIASES.items() for a in aliases}


@dataclass
class Action:
    """One parsed plan step. `target` is an object id; `kind_target` an unnumbered kind that is
    resolved to the nearest reachable object when the step starts."""

    kind: str                       # "interact" | "move_to" | "wait" | "step" | "invalid"
    raw: str                        # what the agent wrote
    target: str | None = None       # object id, e.g. "pot_1"
    kind_target: str | None = None  # e.g. "pot" (nearest one)
    pos: Pos | None = None          # move_to a floor tile
    seconds: int = 0                # wait: seconds left
    direction: str | None = None    # step: up/right/down/left
    reason: str | None = None       # invalid: why
    started: bool = field(default=False, repr=False)

    def text(self) -> str:
        """Canonical string, as shown in frames."""
        if self.kind == "wait":
            return f"wait({self.seconds})"
        if self.kind == "step":
            return f"move({self.direction})"
        if self.kind == "invalid":
            return self.raw
        tgt = self.target or self.kind_target
        if tgt is None and self.pos is not None:
            return f"{self.kind}({self.pos[0]}, {self.pos[1]})"
        return f"{self.kind}({tgt})"


class ParseError(ValueError):
    pass


def split_plan(actions: list[str] | str) -> list[str]:
    """Flatten a plan: elements may themselves hold several actions separated by `;`, newlines
    or top-level commas (`interact(onion_crate), interact(pot_1)`)."""
    items = [actions] if isinstance(actions, str) else list(actions)
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            item = str(item)
        for chunk in re.split(r"[;\n]", item):
            parts = _split_top_level_commas(chunk)
            if len(parts) > 1 and all(re.match(r"\s*[A-Za-z]", p) for p in parts):
                out.extend(parts)
            else:
                out.append(chunk)
    return [s.strip() for s in out if _clean(s)]


def _split_top_level_commas(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _clean(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^(?:[-*•]|\d+[.):])\s+", "", s)          # list bullets / numbering
    s = s.strip("`'\" \t;,.")
    return s.lower()


def parse_action(text: str, kitchen: Kitchen) -> Action:
    """Parse one action string. Raises ParseError with a reason an LLM can act on."""
    raw = text.strip()
    s = _clean(raw)
    if not s:
        raise ParseError(f"empty action: {HELP}")
    s = re.sub(r"^(pick|put)[\s_\-]+(up|down)\b\s*", r"\1\2 ", s).strip()
    m = re.fullmatch(r"([a-z][a-z_ \-]*?)\s*\((.*)\)", s) or re.fullmatch(
        r"([a-z][a-z_\-]*)(?:\s+(.*))?", s)
    if not m:
        try:  # a bare target such as "pot_2" or "(2,2)" means interact with it
            return _target_action("interact", raw, s, kitchen)
        except ParseError:
            raise ParseError(f"could not parse {raw!r}: {HELP}") from None
    verb_word, arg = m.group(1), (m.group(2) or "").strip()
    verb_key = re.sub(r"[\s_\-]", "", verb_word)
    if verb_key in _DIR_WORDS and not arg:
        return Action("step", raw, direction=_DIR_WORDS[verb_key])
    verb = _VERBS.get(verb_key)
    if verb is None:
        # e.g. "onion_crate_1" alone: treat a bare target as interact(target).
        try:
            return _target_action("interact", raw, s, kitchen)
        except ParseError:
            raise ParseError(f"unknown action {verb_word!r} in {raw!r}: {HELP}") from None
    if verb == "wait":
        return Action("wait", raw, seconds=_parse_seconds(arg, raw))
    if verb == "move_to" and _DIR_WORDS.get(arg.strip("'\" ")) and len(arg.strip("'\" ")) > 1:
        return Action("step", raw, direction=_DIR_WORDS[arg.strip("'\" ")])
    if not arg:
        raise ParseError(f"{raw!r} needs a target, e.g. {verb}(pot_1) or {verb}(2, 2)")
    return _target_action(verb, raw, arg, kitchen)


def _parse_seconds(arg: str, raw: str) -> int:
    a = re.sub(r"^(?:seconds|secs|sec|s|t|time|duration|n)\s*=\s*", "", arg.strip("'\" "))
    if not a:
        return 1
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)?", a)
    if not m:
        raise ParseError(f"wait needs a number of seconds ({WAIT_MIN}..{WAIT_MAX}), got {raw!r}")
    return max(WAIT_MIN, min(WAIT_MAX, round(float(m.group(1)))))


def _target_action(verb: str, raw: str, arg: str, kitchen: Kitchen) -> Action:
    a = arg.strip().strip("'\"` ")
    # x=2, y=2
    m = re.fullmatch(r"x\s*[=:]\s*(\d+)\s*,\s*y\s*[=:]\s*(\d+)", a)
    if m:
        return _pos_action(verb, raw, (int(m.group(1)), int(m.group(2))), None, kitchen)
    a = re.sub(r"^(?:target|id|object|obj|name|to|at|station|location|loc|tile|pos|position)"
               r"\s*[=:]\s*", "", a).strip("'\"` ")
    a = re.sub(r"^(?:(?:with|at|on|to|into|in|from|the)\s+)+", "", a)
    # (2,2) / 2,2 / [2, 2]
    m = re.fullmatch(r"[(\[]?\s*(\d+)\s*,\s*(\d+)\s*[)\]]?", a)
    if m:
        return _pos_action(verb, raw, (int(m.group(1)), int(m.group(2))), None, kitchen)
    # counter(2,2) / counter_2_2 / counter 2 2 / pot(3,0) / counter at (2,2)
    m = re.fullmatch(r"([a-z][a-z_ \-]*?)[\s_\-]*(?:at)?\s*[(\[]\s*(\d+)\s*[,_ ]\s*(\d+)\s*[)\]]", a) \
        or re.fullmatch(r"([a-z][a-z_ \-]*?)[\s_\-]+(\d+)[\s_\-,]+(\d+)", a)
    if m:
        kind = KIND_OF_ALIAS.get(_norm(m.group(1)))
        if kind is None:
            raise ParseError(f"unknown target {arg!r} in {raw!r}")
        return _pos_action(verb, raw, (int(m.group(2)), int(m.group(3))), kind, kitchen)
    # pot_1 / pot 1 / pot1 / pot #1 / onion crate 2
    m = re.fullmatch(r"([a-z][a-z_ \-]*?)[\s_\-#]*(\d+)", a)
    if m:
        kind = KIND_OF_ALIAS.get(_norm(m.group(1)))
        if kind is None:
            raise ParseError(f"unknown target {arg!r} in {raw!r}; {_ids_hint(kitchen)}")
        if kind == "counter":
            raise ParseError(f"name counters by position, e.g. counter_2_2 for the counter at (2,2)")
        oid = f"{kind}_{int(m.group(2))}"
        if oid not in kitchen.objects:
            have = ", ".join(o.id for o in kitchen.of_kind(kind)) or "none"
            raise ParseError(f"{oid} does not exist (this kitchen has: {have})")
        return Action(verb, raw, target=oid)
    kind = KIND_OF_ALIAS.get(_norm(a))
    if kind is None:
        raise ParseError(f"unknown target {arg!r} in {raw!r}; {_ids_hint(kitchen)}")
    if kind == "counter":
        raise ParseError("say which counter by position, e.g. counter_2_2 for the counter at (2,2)")
    if not kitchen.of_kind(kind):
        raise ParseError(f"there is no {kind} in this kitchen")
    return Action(verb, raw, kind_target=kind)


def _pos_action(verb: str, raw: str, pos: Pos, kind: str | None, kitchen: Kitchen) -> Action:
    x, y = pos
    if not (0 <= x < kitchen.width and 0 <= y < kitchen.height):
        raise ParseError(f"({x},{y}) is outside the kitchen (x 0..{kitchen.width - 1}, "
                         f"y 0..{kitchen.height - 1})")
    obj = kitchen.by_pos.get(pos)
    if obj is None:  # a floor tile
        if kind is not None:
            raise ParseError(f"there is no {kind} at ({x},{y}); it is floor")
        if verb == "interact":
            raise ParseError(f"({x},{y}) is floor; interact needs an object such as a pot, "
                             f"crate, rack, hatch or counter (use move_to to walk there)")
        return Action("move_to", raw, pos=pos)
    if kind is not None and obj.kind != kind:
        raise ParseError(f"({x},{y}) is {obj.id}, not a {kind}")
    return Action(verb, raw, target=obj.id)


def _norm(word: str) -> str:
    return re.sub(r"[\s\-]+", "_", word.strip()).strip("_")


def _ids_hint(kitchen: Kitchen) -> str:
    ids = [o.id for o in kitchen.objects.values() if o.kind != "counter"]
    return "targets are " + ", ".join(ids) + " or counter_X_Y"
