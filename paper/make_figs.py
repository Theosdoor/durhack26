"""Paper figures from analysis/metrics.py's season.csv.
Usage: uv run --with matplotlib python paper/make_figs.py <season.csv> <out dir>"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

d = pd.read_csv(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
MAPS = ["cramped_room", "asymmetric_advantages", "coordination_ring", "counter_circuit", "split_room"]
SHORT = {"cramped_room": "cramped", "asymmetric_advantages": "asymmetric", "coordination_ring": "ring",
         "counter_circuit": "circuit", "split_room": "split"}
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
FULL, BARE, OPEN = "#2f6aa8", "#d08a1e", "#3d8a3a"


def mse(x):
    x = np.asarray(x, float)
    return x.mean(), (x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else 0.0), len(x)


# Figure 2: 2-agent self-play, team total by model and rules, per map
self2 = d[(d.n_agents == 2) & (~d.mixed)]
teams = ["DeepSeek×2", "GLM×2", "Gemma×2", "Qwen3×2", "Muse×2"]
fig, axes = plt.subplots(1, 5, figsize=(7.0, 1.9), sharey=True)
for ax, lvl in zip(axes, MAPS):
    x = np.arange(len(teams))
    for k, (rules, col) in enumerate([("full", FULL), ("bare", BARE)]):
        vals = [mse(self2[(self2.team == t) & (self2.level == lvl) & (self2.rules == rules)].team_total)
                if len(self2[(self2.team == t) & (self2.level == lvl) & (self2.rules == rules)]) else (np.nan, 0, 0)
                for t in teams]
        ax.bar(x + (k - 0.5) * 0.38, [v[0] for v in vals], 0.38, yerr=[v[1] for v in vals],
               color=col, label=rules, error_kw={"lw": 0.6, "capsize": 1.5})
    ax.set_title(SHORT[lvl])
    ax.set_xticks(x, [t.split("×")[0] for t in teams], rotation=60)
axes[0].set_ylabel("team score (3 rounds)")
axes[0].legend(frameon=False, loc="upper left")
fig.tight_layout(pad=0.3)
fig.savefig(out / "fig2_models.pdf")

# Figure 3: DeepSeek self-play, team total by number of agents and rules, per map
ds = d[d.team.str.startswith("DeepSeek×")]
fig, axes = plt.subplots(1, 5, figsize=(7.0, 1.7), sharey=True)
for ax, lvl in zip(axes, MAPS):
    x = np.arange(3)
    for k, (rules, col) in enumerate([("full", FULL), ("bare", BARE)]):
        vals = []
        for n in (2, 3, 4):
            s = ds[(ds.level == lvl) & (ds.rules == rules) & (ds.n_agents == n)].team_total
            vals.append(mse(s) if len(s) else (np.nan, 0, 0))
        ax.bar(x + (k - 0.5) * 0.38, [v[0] for v in vals], 0.38, yerr=[v[1] for v in vals], color=col,
               label=rules, error_kw={"lw": 0.6, "capsize": 1.5})
    ax.set_title(SHORT[lvl])
    ax.set_xticks(x, ["2", "3", "4"])
    ax.set_xlabel("agents")
axes[0].set_ylabel("team score (3 rounds)")
axes[0].legend(frameon=False, loc="upper left")
fig.tight_layout(pad=0.3)
fig.savefig(out / "fig3_nagents.pdf")
print("wrote", out / "fig2_models.pdf", out / "fig3_nagents.pdf")
