"""Loads the agent-facing prompt texts from the top-level `prompts/` folder (Markdown files).

Two kinds of file (see prompts/README.md):
  * a plain file (`load("rules", ...)`): the whole file is one text;
  * a sections file (`load("tools", "act.ignored", ...)`): `## key` headings split it into small
    texts. Text before the first heading is ignored.
Leading and trailing newlines of a text are dropped (so an editor's final newline is harmless);
spaces are kept. `{placeholders}` are filled with `str.format`; literal braces must be doubled.
A placeholder the caller does not provide raises `PromptError` naming the file.
"""

from __future__ import annotations

import string
from functools import lru_cache
from pathlib import Path

from undercooked.config import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"

# (file stem, section or None) -> the keyword sets it was formatted with (read by the tests)
CALLS: dict[tuple[str, str | None], list[frozenset[str]]] = {}


class PromptError(ValueError):
    """A prompt file is missing, malformed or lacks a value for one of its placeholders."""


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(f"prompt file not found: {path}") from None


@lru_cache(maxsize=None)
def sections(name: str) -> dict[str, str]:
    """The `## key` sections of prompts/<name>.md."""
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def close() -> None:
        if key is not None:
            out[key] = "\n".join(buf).strip("\n")

    for line in _read(name).split("\n"):
        if line.startswith("## "):
            close()
            key, buf = line[3:].strip(), []
            if key in out:
                raise PromptError(f"prompts/{name}.md: duplicate section {key!r}")
        else:
            buf.append(line)
    close()
    return out


def raw(name: str, section: str | None = None) -> str:
    """The unformatted text of a plain file, or of one section of a sections file."""
    if section is None:
        return _read(name).strip("\n")
    try:
        return sections(name)[section]
    except KeyError:
        raise PromptError(f"prompts/{name}.md: no section {section!r} "
                          f"(has {sorted(sections(name))})") from None


def placeholders(text: str) -> set[str]:
    return {field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(text)
            if field}


def load(name: str, section: str | None = None, /, **values: object) -> str:
    """The text of prompts/<name>.md (or one of its sections), formatted with `values`."""
    text = raw(name, section)
    where = f"prompts/{name}.md" + (f" [{section}]" if section else "")
    CALLS.setdefault((name, section), []).append(frozenset(values))
    try:
        return text.format(**values)
    except KeyError as e:
        raise PromptError(f"{where}: placeholder {{{e.args[0]}}} has no value (the code provides "
                          f"{sorted(values) or 'none'})") from None
    except (ValueError, IndexError) as e:
        raise PromptError(f"{where}: bad placeholder or unescaped brace ({e}); "
                          f"literal braces must be doubled") from None
