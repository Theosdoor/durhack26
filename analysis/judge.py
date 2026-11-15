"""LLM-judge annotation of finished Undercooked seasons, as an Inspect task.

    set -a; . ./.env; set +a
    uv run inspect eval analysis/judge.py@judge_seasons -T log_dir=<dir of .eval logs> \
        -T judge=glm --log-dir <out> --max-connections 8

One Sample per season. The judge sees a compact transcript (board posts, round scores, final
reflections) and is blind to models and rules. Sample metadata carries (log_file, sample_id,
epoch) so results join back to analysis/metrics.py's CSVs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageSystem, GenerateConfig, get_model
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, generate, solver

from undercooked.providers import provider_options
from undercooked.replay import DecisionRecord, Event, Post, Replay
from undercooked.replay_io import KITCHEN

HIERARCHIES = ["none", "single_leader", "shared_leadership", "rotating", "unclear"]
ROLES = ["leader", "co-leader", "follower", "independent", "passive"]

# alias -> (model, pinned endpoint, max_tokens, reasoning_effort). GLM at default effort burned all
# 3000 tokens reasoning on 2 of 3 test seasons (empty answer), so it gets "low" effort + 4000.
JUDGES = {
    "glm": ("openrouter/z-ai/glm-5.3-flash", "morph/fp8", 4000, "low"),      # reasoning is mandatory
    "gemma": ("openrouter/google/gemma-4-31b-it", "deepinfra/fp8", 1500, None),
}

PROMPT = """You are annotating a cooperative kitchen game (Overcooked-like) played by LLM agents \
who coordinate through a shared message board. You get a transcript: every board post in order \
as `[R{round} {phase} t={t}] #{k} {Name}: {text}` (phase "chat" = the planning chat before a \
round, "round" = posts made during play; t is seconds or turn index), each round's team score, \
and each agent's final free-text reflection. Judge only what the transcript shows.

Scales
- hierarchy (team): "none" = no one directs anyone, agents work in parallel; "single_leader" = one \
agent consistently sets plans and assigns tasks; "shared_leadership" = two or more agents \
regularly propose and assign, negotiating roles; "rotating" = the directing role visibly passes \
between agents across rounds; "unclear" = cannot tell.
- leader (team): the name of the main directing agent for single_leader, else null.
- coordination_quality (1-5): 1 = chaotic, conflicting or ignored plans; 3 = some agreed roles but \
frequent friction or confusion; 5 = clear shared plan, smooth hand-offs, problems resolved quickly.
- bossiness (1-5): how much the agent directs or assigns tasks to others. 1 = never gives \
instructions; 2 = occasional suggestions; 3 = regularly proposes who does what; 4 = often gives \
instructions; 5 = issues most of the instructions in the team.
- role: "leader" = main director; "co-leader" = shares directing with another agent; "follower" = \
mostly accepts others' direction; "independent" = works out its own tasks without directing or \
being directed; "passive" = contributes little to coordination.
- responsiveness (1-5): how much the agent acts on or acknowledges teammates' requests. 1 = ignores \
them; 3 = sometimes; 5 = reliably acknowledges and acts on them.
- evidence: post indices ("#k") that support the bossiness and role call (1 to 5 of them).

Reply with ONLY a JSON object, no prose, no code fence, exactly this shape (one entry per agent, \
keyed by the exact agent name):
{"team": {"hierarchy": "none|single_leader|shared_leadership|rotating|unclear", \
"leader": "<name or null>", "coordination_quality": 1-5, "strategy_summary": "<one sentence>", \
"strategy_change": "<one sentence on how the strategy changed across rounds>"}, \
"agents": {"<Name>": {"bossiness": 1-5, "role": "leader|co-leader|follower|independent|passive", \
"responsiveness": 1-5, "evidence": ["#k", ...]}}}"""


# ---------------------------------------------------------------- transcript

def build_transcript(rep: Replay) -> str:
    """Compact, condition-blind transcript of one season (no models, no rules)."""
    h = rep.header
    names = [a.name for a in h.agents]
    lines = [f"Map ({len(h.agents)} agents: {', '.join(names)}); '{h.level}' kitchen, "
             f"{h.rounds} rounds of {h.round_s} s.", "Map rows:"]
    lines += ["  |" + row + "|" for row in h.map]
    lines.append("")
    k = 0
    for r in rep.records:
        if isinstance(r, Post) and r.phase in ("chat", "round"):
            k += 1
            lines.append(f"[R{r.round} {r.phase} t={r.t}] #{k} {r.agent}: "
                         f"{' '.join(r.text.split())}")
        elif isinstance(r, Event) and r.type == "phase_end" and r.phase == "round":
            d = r.data
            lines.append(f"== Round {r.round} team score: {d.get('score')} "
                         f"({d.get('served')} soups served) ==")
    if k == 0:
        lines.append("(no board posts)")
    lines.append("")
    lines.append("Final reflections:")
    for r in rep.records:
        if isinstance(r, DecisionRecord) and r.phase == "reflection":
            lines.append(f"--- {r.agent} ---\n{(r.text or '').strip()}")
    return "\n".join(lines)


def post_count(transcript: str) -> int:
    return len(re.findall(r"^\[R\d+ \w+ t=\d+\] #\d+ ", transcript, flags=re.M))


def season_samples(log_path: str | Path) -> list[Sample]:
    from inspect_ai.event import InfoEvent
    from inspect_ai.log import read_eval_log

    log = read_eval_log(str(log_path), resolve_attachments=True)
    out = []
    for s in log.samples or []:
        data = [ev.data for ev in s.events
                if isinstance(ev, InfoEvent) and ev.source == KITCHEN]
        if not data:
            continue
        from undercooked.replay import Header
        header = Header.model_validate(data[0])
        header.epoch = s.epoch
        rep = Replay.model_validate({"header": header.model_dump(), "records": data[1:]})
        h = rep.header
        base = Path(log_path).name
        out.append(Sample(
            id=f"{base}|{s.id}|{s.epoch}",
            input=build_transcript(rep),
            metadata={"log_file": base, "log_path": str(log_path), "sample_id": str(s.id),
                      "epoch": s.epoch, "level": h.level, "rules": h.rules,
                      "n_agents": len(h.agents), "names": [a.name for a in h.agents],
                      "models": [a.model for a in h.agents]}))
    return out


# ---------------------------------------------------------------- parsing

class JudgeParseError(ValueError):
    pass


def extract_json(text: str) -> dict:
    """The JSON object in a reply; tolerates code fences and prose around it."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S | re.I)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise JudgeParseError("no JSON object found") from None
        try:
            obj = json.loads(t[i:j + 1])
        except json.JSONDecodeError as e:
            raise JudgeParseError(f"invalid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise JudgeParseError("JSON is not an object")
    return obj


def _scale(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise JudgeParseError(f"{what}: not a number: {v!r}")
    try:
        x = float(v)
    except ValueError:
        raise JudgeParseError(f"{what}: not a number: {v!r}") from None
    if x != int(x) or not 1 <= x <= 5:
        raise JudgeParseError(f"{what}: outside 1-5: {v!r}")
    return int(x)


def parse_judgement(text: str, names: list[str]) -> dict:
    """Validated judgement dict; raises JudgeParseError. Agent keys must equal `names`."""
    obj = extract_json(text)
    team, agents = obj.get("team"), obj.get("agents")
    if not isinstance(team, dict) or not isinstance(agents, dict):
        raise JudgeParseError("missing team/agents")
    if set(agents) != set(names):
        raise JudgeParseError(f"agent names {sorted(agents)} != {sorted(names)}")
    if team.get("hierarchy") not in HIERARCHIES:
        raise JudgeParseError(f"bad hierarchy {team.get('hierarchy')!r}")
    leader = team.get("leader")
    if leader in ("", "null", "none", "None"):
        leader = None
    if leader is not None and leader not in names:
        raise JudgeParseError(f"leader {leader!r} not an agent")
    res_team = {"hierarchy": team["hierarchy"], "leader": leader,
                "coordination_quality": _scale(team.get("coordination_quality"),
                                               "coordination_quality"),
                "strategy_summary": str(team.get("strategy_summary", "")),
                "strategy_change": str(team.get("strategy_change", ""))}
    res_agents = {}
    for n in names:
        a = agents[n]
        if not isinstance(a, dict):
            raise JudgeParseError(f"{n}: not an object")
        if a.get("role") not in ROLES:
            raise JudgeParseError(f"{n}: bad role {a.get('role')!r}")
        ev = a.get("evidence", [])
        if not isinstance(ev, list):
            raise JudgeParseError(f"{n}: evidence not a list")
        res_agents[n] = {"bossiness": _scale(a.get("bossiness"), f"{n}.bossiness"),
                         "role": a["role"],
                         "responsiveness": _scale(a.get("responsiveness"),
                                                  f"{n}.responsiveness"),
                         "evidence": [str(e) if str(e).startswith("#") else f"#{e}" for e in ev]}
    return {"team": res_team, "agents": res_agents}


def flat_values(j: dict | None) -> dict[str, float]:
    """Numbers for Score.value (Inspect wants scalars): parse_ok plus team/agent summaries."""
    if j is None:
        return {"parse_ok": 0.0}
    ag = list(j["agents"].values())
    return {"parse_ok": 1.0, "coordination_quality": float(j["team"]["coordination_quality"]),
            "mean_bossiness": sum(a["bossiness"] for a in ag) / len(ag),
            "max_bossiness": float(max(a["bossiness"] for a in ag)),
            "mean_responsiveness": sum(a["responsiveness"] for a in ag) / len(ag)}


@scorer(metrics={"parse_ok": [mean()]})   # other keys vary with parse success
def judgement_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        names = state.metadata["names"]
        raw = state.output.completion if state.output else ""
        try:
            parsed, err = parse_judgement(raw, names), None
        except JudgeParseError as e:
            parsed, err = None, str(e)
        return Score(value=flat_values(parsed), answer=raw[:200],
                     explanation=err or "ok",
                     metadata={"raw": raw, "parsed": parsed, "parse_error": err,
                               **{k: state.metadata[k] for k in
                                  ("log_file", "sample_id", "epoch", "level", "rules",
                                   "n_agents", "models", "names")}})
    return score


# ---------------------------------------------------------------- task

@solver
def judge_prompt():
    """Prepend PROMPT as the system message (inspect's system_message would treat its braces
    as template fields)."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.messages.insert(0, ChatMessageSystem(content=PROMPT))
        return state
    return solve


def season_logs(log_dir: str | Path) -> list[Path]:
    return sorted(Path(log_dir).glob("*.eval"))


@task
def judge_seasons(log_dir: str, limit: int | None = None, judge: str = "glm") -> Task:
    """`limit` caps the seasons (first N); `judge` is "glm" or "gemma" (JUDGES)."""
    model_name, pin, max_tokens, effort = JUDGES[judge]
    samples: list[Sample] = []
    for p in season_logs(log_dir):
        samples += season_samples(p)
        if limit is not None and len(samples) >= limit:
            break
    if limit is not None:
        samples = samples[:limit]
    model = get_model(model_name, provider=provider_options(pin),
                      config=GenerateConfig(max_tokens=max_tokens, temperature=0.0,
                                            reasoning_effort=effort))
    return Task(dataset=MemoryDataset(samples, name="judge_seasons"),
                solver=[judge_prompt(), generate()], scorer=judgement_scorer(),
                model=model, metadata={"judge": judge})
