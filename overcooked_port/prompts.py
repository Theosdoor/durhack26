"""Load agent-facing text from the Markdown files in prompts/ (repo root).

Each file is a list of `## name` sections; a section's text is everything up to the next
heading (`## ` or `# `), without leading/trailing blank lines. `# ` headings and the text under
them are notes for editors and are ignored. `{placeholders}` are filled with str.format, so literal braces
must be doubled. Files are re-read when they change on disk, so edits apply to the next prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_cache: dict[str, tuple[float, dict[str, str]]] = {}


class PromptError(KeyError):
    pass


def sections(file: str) -> dict[str, str]:
    path = PROMPTS_DIR / f"{file}.md"
    mtime = path.stat().st_mtime
    hit = _cache.get(file)
    if hit and hit[0] == mtime:
        return hit[1]
    out: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").split("\n") + ["## "]:
        if line.startswith("## ") or line.startswith("# "):
            if name is not None:
                out[name] = "\n".join(lines).strip("\n")
            name = line[3:].strip() if line.startswith("## ") else None
            lines = []
        elif name is not None:
            lines.append(line)
    _cache[file] = (mtime, out)
    return out


def render(file: str, section: str, **values: Any) -> str:
    """The section's text with its {placeholders} filled from `values`."""
    try:
        template = sections(file)[section]
    except KeyError:
        raise PromptError(f"prompts/{file}.md has no section '## {section}'") from None
    try:
        return template.format(**values)
    except KeyError as e:
        raise PromptError(f"prompts/{file}.md ## {section}: no value for placeholder "
                          f"{{{e.args[0]}}} (given: {', '.join(sorted(values)) or 'none'})") \
            from None
    except (IndexError, ValueError) as e:
        raise PromptError(f"prompts/{file}.md ## {section}: bad placeholder ({e}); "
                          f"double literal braces as {{{{ }}}}") from None
