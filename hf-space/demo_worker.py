"""Run the Gradio sample jobs inside a throwaway interpreter and data home."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from solvent.agent import Solvent
from solvent.jobs import SAMPLE_JOBS
from solvent.money import dollars_to_cents


def main() -> None:
    events: list[dict] = []
    agent = Solvent(seed_cents=dollars_to_cents(sys.argv[1]), fresh=True, on_event=events.append)
    for job in SAMPLE_JOBS:
        agent.handle_job(dict(job))
    Path("result.json").write_text(
        json.dumps({"snapshot": agent.t.snapshot(), "events": events}), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
