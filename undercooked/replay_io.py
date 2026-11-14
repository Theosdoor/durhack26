"""Loading replays from disk: a Replay .json, or an Inspect .eval log (one Replay per sample/epoch).

An .eval sample's replay is the InfoEvents with source "kitchen", in log order: the first is the
Header, the rest are records. `served_by` is filled by the season while it runs, from the raw
response of each model call (providers.track_served_provider / served_providers, collected in
season.Gen.add); the exporter takes it as logged.
"""

from __future__ import annotations

from pathlib import Path

from undercooked.replay import Header, Replay

KITCHEN = "kitchen"


def replays_from_eval(path: str | Path) -> list[Replay]:
    from inspect_ai.event import InfoEvent
    from inspect_ai.log import read_eval_log

    log = read_eval_log(str(path), resolve_attachments=True)
    out: list[Replay] = []
    for sample in log.samples or []:
        data = [ev.data for ev in sample.events
                if isinstance(ev, InfoEvent) and ev.source == KITCHEN]
        if not data:
            continue
        header = Header.model_validate(data[0])
        header.epoch = sample.epoch
        out.append(Replay.model_validate({"header": header.model_dump(), "records": data[1:]}))
    if not out:
        raise ValueError(f"no kitchen records in {path}")
    return sorted(out, key=lambda r: r.header.epoch)


def load_replay(path: str | Path) -> list[Replay]:
    """Return one Replay per season (epoch) found in the file."""
    path = Path(path)
    if path.suffix == ".json":
        return [Replay.model_validate_json(path.read_text())]
    if path.suffix == ".eval":
        if not path.exists():
            raise FileNotFoundError(path)
        return replays_from_eval(path)
    raise ValueError(f"unsupported replay file: {path}")


def export(path: str | Path, out: str | Path) -> list[Path]:
    """Write the replays in `path` (.eval) as JSON. One season: `out`; several: out-e<epoch>.json."""
    replays = load_replay(path)
    out = Path(out)
    written = []
    for rep in replays:
        target = out if len(replays) == 1 else out.with_name(
            f"{out.stem}-e{rep.header.epoch}{out.suffix or '.json'}")
        target.write_text(rep.model_dump_json(indent=1))
        written.append(target)
    return written
