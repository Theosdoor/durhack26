"""Command line: `undercooked run | view | export`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

VIEWER = Path(__file__).resolve().parent / "viewer.py"


def default_max_samples(task) -> int:
    """Every season of the run at once: samples (levels) x epochs."""
    return max(1, len(task.dataset) * (task.epochs or 1))


@click.group()
def main() -> None:
    """Undercooked: LLM agents cooperating in a kitchen game."""


@main.command()
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--epochs", type=int, default=None, help="Seasons to run (default: config).")
@click.option("--rounds", type=int, default=None, help="Rounds per season (default: config).")
@click.option("--round-s", type=int, default=None, help="Seconds per round (default: config).")
@click.option("--no-practice", is_flag=True, help="Skip the practice phase.")
@click.option("--max-samples", type=int, default=None,
              help="Seasons run at once (default: all epochs).")
@click.option("--no-pipeline", is_flag=True,
              help="Wait for every model call before the clock moves on (same results, slower).")
@click.option("--reasoning", type=click.Choice(["on", "off"]), default=None,
              help="Model reasoning (default: config decision.reasoning).")
@click.option("--comms", type=click.Choice(["full", "planning_only", "none"]), default=None,
              help="Communication condition (default: config comms).")
@click.option("--debrief", type=click.Choice(["private", "full", "score_only"]), default=None,
              help="Private turn after each round (full: its older name), or the score only "
                   "(default: config debrief).")
@click.option("--rules", type=click.Choice(["full", "bare", "open", "recipe", "minimal",
                                            "minimal_hint"]),
              default=None, help="How much the system prompt explains (default: config rules).")
@click.option("--scoring", type=click.Choice(["soups", "orders"]), default=None,
              help="Every soup scores, or soups fill orders (default: config scoring).")
@click.option("--reflection/--no-reflection", default=None,
              help="Final reflection questions (default: config reflection).")
@click.option("--model", "model", default=None,
              help="Self-play: every agent uses this model (full Inspect name, e.g. "
                   "openrouter/deepseek/deepseek-v4.1-flash; add !reasoning if its endpoint "
                   "cannot turn reasoning off).")
@click.option("--provider", default=None, help="Endpoint tag for --model, e.g. baidu/fp8.")
@click.option("--agents", default=None,
              help='Mixed team: "model1@provider1,model2@provider2" (each may end !reasoning); '
                   "replaces the config's agents.")
@click.option("--levels", default=None,
              help="Levels to play, comma-separated: one season per level and epoch "
                   "(default: config level / levels).")
@click.option("--log-dir", default="logs", show_default=True)
def run(config: str, epochs: int | None, rounds: int | None, round_s: int | None,
        no_practice: bool, max_samples: int | None, no_pipeline: bool, reasoning: str | None,
        comms: str | None, debrief: str | None, rules: str | None, scoring: str | None,
        reflection: bool | None, model: str | None, provider: str | None, agents: str | None,
        levels: str | None, log_dir: str) -> None:
    """Run seasons of CONFIG (a YAML file in configs/) and print the log path."""
    from inspect_ai import eval as inspect_eval

    from undercooked.task import undercooked

    task = undercooked(config=config, rounds=rounds, round_s=round_s,
                       practice=False if no_practice else None, epochs=epochs,
                       pipeline=False if no_pipeline else None, reasoning=reasoning,
                       comms=comms, debrief=debrief, levels=levels, rules=rules,
                       scoring=scoring, reflection=reflection, agents=agents,
                       agent_model=model, agent_provider=provider)
    # one sample per level, `epochs` seasons each: by default run them all at once (Inspect
    # would otherwise cap it at its own default, which follows the task model, none/none). The
    # agents' model calls are still limited per model by max_connections.
    logs = inspect_eval(task, model="none/none", log_dir=log_dir,
                        max_samples=max_samples or default_max_samples(task))
    for log in logs:
        click.echo(f"status: {log.status}")
        if log.results is not None:
            for s in log.results.scores:
                vals = ", ".join(f"{k}={m.value:g}" for k, m in s.metrics.items())
                click.echo(f"{s.name}: {vals}")
        click.echo(log.location)
    if any(log.status != "success" for log in logs):
        sys.exit(1)


@main.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def view(path: str) -> None:
    """Open the Streamlit replay viewer on PATH (.eval or replay .json)."""
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(VIEWER), "--", path]))


@main.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.argument("out", type=click.Path(dir_okay=False))
def export(path: str, out: str) -> None:
    """Export the replays in PATH (.eval) to OUT (.json; one file per season)."""
    from undercooked.replay_io import export as do_export

    for p in do_export(path, out):
        click.echo(p)


if __name__ == "__main__":
    main()
