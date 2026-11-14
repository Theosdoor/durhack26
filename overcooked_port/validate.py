"""Validate an .eval log against the copied replay schema and print a season summary.

    uv run python -m overcooked_port.validate logs/<file>.eval
"""

from __future__ import annotations

import sys
from collections import Counter

from inspect_ai.event import InfoEvent
from inspect_ai.log import read_eval_log

from overcooked_port.replay import DecisionRecord, Event, Header, Post, Replay


def replays(path: str) -> list[Replay]:
    log = read_eval_log(path, resolve_attachments=True)
    out = []
    for s in log.samples or []:
        data = [e.data for e in s.events if isinstance(e, InfoEvent) and e.source == "kitchen"]
        header = Header.model_validate(data[0])
        header.epoch = s.epoch
        out.append(Replay.model_validate({"header": header.model_dump(), "records": data[1:]}))
    return out


def main(path: str) -> None:
    for rep in replays(path):
        kinds = Counter(type(r).__name__ for r in rep.records)
        print(f"epoch {rep.header.epoch}: valid Replay, {len(rep.records)} records {dict(kinds)}")
        print("agents:", [(a.name, a.model, a.provider) for a in rep.header.agents])
        served = Counter(d.served_by for d in rep.select(DecisionRecord))
        print("served_by:", dict(served))
        ends = [e for e in rep.select(Event) if e.type == "phase_end" and e.phase == "round"]
        print("round scores:", [e.data.get("score") for e in ends])
        print("cutoffs:", sum(1 for e in rep.select(Event) if e.type == "cutoff"),
              "fails:", sum(1 for e in rep.select(Event) if e.type == "fail"))
        for p in rep.select(Post):
            print(f"  [{p.phase} r{p.round} t{p.t}] {p.agent}: {p.text}")


if __name__ == "__main__":
    main(sys.argv[1])
