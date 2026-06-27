"""Structured JSON logging and event persistence for SOLVENT."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .paths import data_dir

LOG_PATH = data_dir() / "solvent.log"


def log_event(
    treasury,
    *,
    job_id: str,
    stage: str,
    stripe_ref: str | None = None,
    margin_est: float | None = None,
    margin_actual: float | None = None,
    duration_ms: float | None = None,
    simulated: bool | None = None,
    **extra,
) -> dict:
    """Emit a structured log line and persist to treasury events table."""
    record = {
        "ts": time.time(),
        "job_id": job_id,
        "stage": stage,
        "stripe_ref": stripe_ref,
        "margin_est": margin_est,
        "margin_actual": margin_actual,
        "duration_ms": duration_ms,
        "simulated": simulated,
        **extra,
    }
    record = {k: v for k, v in record.items() if v is not None}
    if treasury is not None:
        try:
            treasury.record_event(job_id, stage, record)
        except Exception:
            pass
    if os.environ.get("SOLVENT_LOG_JSON", "").strip() in ("1", "true", "yes"):
        line = json.dumps(record, default=str)
        print(line, file=sys.stderr)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass
    return record
