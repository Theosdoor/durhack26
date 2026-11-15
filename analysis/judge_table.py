"""Judge .eval logs -> judges.csv (one row per agent-season per judge) and an agreement summary.

    uv run python analysis/judge_table.py <judge log dir> [-o judges.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from inspect_ai.log import read_eval_log

COLS = ["judge", "log_file", "sample_id", "epoch", "level", "rules", "n_agents", "agent", "model",
        "parse_ok", "parse_error", "hierarchy", "leader", "coordination_quality", "bossiness",
        "role", "responsiveness", "evidence", "strategy_summary", "strategy_change"]


def rows_from_logs(log_dir: Path) -> list[dict]:
    rows = []
    for p in sorted(log_dir.glob("*.eval")):
        log = read_eval_log(str(p))
        judge = (log.eval.metadata or {}).get("judge") or log.eval.model
        for s in log.samples or []:
            sc = next(iter((s.scores or {}).values()), None)
            if sc is None:
                continue
            m = sc.metadata or {}
            parsed = m.get("parsed")
            base = {k: m.get(k) for k in ("log_file", "sample_id", "epoch", "level", "rules",
                                          "n_agents")}
            base.update(judge=judge, parse_ok=int(parsed is not None),
                        parse_error=m.get("parse_error") or "")
            for i, name in enumerate(m.get("names") or []):
                r = dict(base, agent=name, model=(m.get("models") or [""] * 9)[i])
                if parsed:
                    a, t = parsed["agents"][name], parsed["team"]
                    r.update(hierarchy=t["hierarchy"], leader=t["leader"] or "",
                             coordination_quality=t["coordination_quality"],
                             bossiness=a["bossiness"], role=a["role"],
                             responsiveness=a["responsiveness"], evidence=" ".join(a["evidence"]),
                             strategy_summary=t["strategy_summary"],
                             strategy_change=t["strategy_change"])
                rows.append(r)
    return rows


def _ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float:
    """Pearson correlation of average ranks (NaN if either side is constant)."""
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else float("nan")


def kappa(x: list, y: list) -> float:
    """Unweighted Cohen's kappa (NaN if chance agreement is 1)."""
    n = len(x)
    po = sum(a == b for a, b in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in set(x) | set(y))
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def summary(rows: list[dict]) -> str:
    out = []
    by_judge = defaultdict(list)
    for r in rows:
        by_judge[r["judge"]].append(r)
    for j, rs in sorted(by_judge.items()):
        seasons = {(r["log_file"], r["sample_id"], r["epoch"]): r["parse_ok"] for r in rs}
        bad = sum(1 for v in seasons.values() if not v)
        out.append(f"{j}: {len(seasons)} seasons, parse failures {bad} "
                   f"({100 * bad / max(len(seasons), 1):.1f}%)")
    judges = sorted(by_judge)
    for a in range(len(judges)):
        for b in range(a + 1, len(judges)):
            A = {(r["log_file"], r["sample_id"], r["epoch"], r["agent"]): r
                 for r in by_judge[judges[a]] if r["parse_ok"]}
            B = {(r["log_file"], r["sample_id"], r["epoch"], r["agent"]): r
                 for r in by_judge[judges[b]] if r["parse_ok"]}
            keys = sorted(A.keys() & B.keys())
            out.append(f"\n{judges[a]} vs {judges[b]}: {len(keys)} agent-seasons both parsed")
            if len(keys) < 3:
                continue
            out.append(f"  bossiness Spearman {spearman([A[k]['bossiness'] for k in keys], [B[k]['bossiness'] for k in keys]):.3f}")
            out.append(f"  responsiveness Spearman {spearman([A[k]['responsiveness'] for k in keys], [B[k]['responsiveness'] for k in keys]):.3f}")
            out.append(f"  role Cohen's kappa {kappa([A[k]['role'] for k in keys], [B[k]['role'] for k in keys]):.3f}")
            sk = sorted({k[:3] for k in keys})
            ha = [next(A[k]["hierarchy"] for k in keys if k[:3] == s) for s in sk]
            hb = [next(B[k]["hierarchy"] for k in keys if k[:3] == s) for s in sk]
            out.append(f"  hierarchy Cohen's kappa {kappa(ha, hb):.3f} ({len(sk)} seasons)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    a = ap.parse_args(argv)
    rows = rows_from_logs(a.log_dir)
    out = a.out or a.log_dir / "judges.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
