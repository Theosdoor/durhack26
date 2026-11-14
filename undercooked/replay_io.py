"""Loading replays from disk: a Replay .json, or an Inspect .eval log (one Replay per sample and
epoch: in a multi-level run, one per level and epoch).

An .eval sample's replay is the InfoEvents with source "kitchen", in log order: the first is the
Header, the rest are records. `served_by` is filled by the season while it runs, from the raw
response of each model call (providers.track_served_provider / served_providers, collected in
season.Gen.add); the exporter takes it as logged.
"""

from __future__ import annotations

from pathlib import Path

from undercooked.replay import Header, Replay

KITCHEN = "kitchen"


def eval_status(path: str | Path) -> str:
    """The log's status ("success", "started", "error", "cancelled"), from the header only."""
    from inspect_ai.log import read_eval_log

    return read_eval_log(str(path), header_only=True).status


def eval_seasons(path: str | Path) -> list[tuple[Replay, dict[str, float] | None]]:
    """Every season in the log as (Replay, scorer values or None), ordered by level (the config's
    `levels` order, else by name), then epoch. The scorer values are the first dict-valued score
    in sample.scores (see task.season_values)."""
    from inspect_ai.event import InfoEvent
    from inspect_ai.log import read_eval_log

    log = read_eval_log(str(path), resolve_attachments=True)
    out: list[tuple[Replay, dict[str, float] | None]] = []
    for sample in log.samples or []:
        data = [ev.data for ev in sample.events
                if isinstance(ev, InfoEvent) and ev.source == KITCHEN]
        if not data:
            continue
        header = Header.model_validate(data[0])
        header.epoch = sample.epoch
        rep = Replay.model_validate({"header": header.model_dump(), "records": data[1:]})
        values = next((dict(sc.value) for sc in (sample.scores or {}).values()
                       if isinstance(sc.value, dict)), None)
        out.append((rep, values))
    if not out:
        raise ValueError(f"no kitchen records in {path}")
    order = ((log.eval.metadata or {}).get("config") or {}).get("levels") or []

    def key(item: tuple[Replay, dict | None]) -> tuple:
        r = item[0]
        lv = r.header.level
        return (order.index(lv) if lv in order else len(order), lv, r.header.epoch)

    return sorted(out, key=key)


def replays_from_eval(path: str | Path) -> list[Replay]:
    """Every season in the log, ordered by level (the config's `levels` order, else by name),
    then epoch."""
    return [rep for rep, _ in eval_seasons(path)]


def load_replay(path: str | Path) -> list[Replay]:
    """Return one Replay per season (sample and epoch) found in the file."""
    path = Path(path)
    if path.suffix == ".json":
        return [Replay.model_validate_json(path.read_text())]
    if path.suffix == ".eval":
        if not path.exists():
            raise FileNotFoundError(path)
        return replays_from_eval(path)
    raise ValueError(f"unsupported replay file: {path}")


def export(path: str | Path, out: str | Path) -> list[Path]:
    """Write the replays in `path` (.eval) as JSON. One season: `out`; several: out-e<epoch>.json,
    or out-<level>-e<epoch>.json when the log has several levels."""
    replays = load_replay(path)
    out = Path(out)
    levels = {r.header.level for r in replays}
    written = []
    for rep in replays:
        tag = f"{rep.header.level}-e{rep.header.epoch}" if len(levels) > 1 else f"e{rep.header.epoch}"
        target = out if len(replays) == 1 else out.with_name(
            f"{out.stem}-{tag}{out.suffix or '.json'}")
        target.write_text(rep.model_dump_json(indent=1))
        written.append(target)
    return written
