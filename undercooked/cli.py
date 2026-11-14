"""Command line: `undercooked run | view | export`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

VIEWER = Path(__file__).resolve().parent / "viewer.py"


@click.group()
def main() -> None:
    """Undercooked: LLM agents cooperating in a kitchen game."""


@main.command()
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--epochs", type=int, default=None, help="Seasons to run (default: config).")
@click.option("--rounds", type=int, default=None, help="Rounds per season (default: config).")
@click.option("--round-s", type=int, default=None, help="Seconds per round (default: config).")
@click.option("--no-practice", is_flag=True, help="Skip the practice phase.")
@click.option("--log-dir", default="logs", show_default=True)
def run(config: str, epochs: int | None, rounds: int | None, round_s: int | None,
        no_practice: bool, log_dir: str) -> None:
    """Run seasons of CONFIG (a YAML file in configs/) and print the log path."""
    from inspect_ai import eval as inspect_eval

    from undercooked.task import undercooked

    task = undercooked(config=config, rounds=rounds, round_s=round_s,
                       practice=False if no_practice else None, epochs=epochs)
    logs = inspect_eval(task, model="none/none", log_dir=log_dir)
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
