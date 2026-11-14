"""Loading replays from disk. Kept tiny so the .eval exporter can be dropped in later."""

from __future__ import annotations

from pathlib import Path

from undercooked.replay import Replay


def load_replay(path: str | Path) -> list[Replay]:
    """Return one Replay per season (epoch) found in the file."""
    path = Path(path)
    if path.suffix == ".json":
        return [Replay.model_validate_json(path.read_text())]
    if path.suffix == ".eval":
        raise NotImplementedError("export from .eval not built yet")
    raise ValueError(f"unsupported replay file: {path}")
