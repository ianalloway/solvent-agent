"""Exercise service CLI startup in fresh processes with persisted financial state."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "ledger",
    "spend_reservations",
    "jobs",
    "job_stages",
    "job_metrics",
    "stripe_checkout",
    "nano_invoices",
    "events",
    "chat_messages",
    "chat_sessions",
    "telegram_pairing",
)

# Stub only the server event loop, not argument parsing or app construction.
# This exercises `python -m solvent serve` startup without binding a test port.
SERVER_BOOTSTRAP = """
import runpy
import sys
from types import SimpleNamespace

def run(app, **kwargs):
    assert app.state.agent.t is not None
    print('server app initialized')

sys.modules['uvicorn'] = SimpleNamespace(run=run)
sys.argv = ['solvent', 'serve', *sys.argv[1:]]
runpy.run_module('solvent', run_name='__main__')
"""

SEED_HISTORY = """
from solvent.treasury import Treasury

t = Treasury()
t.seed(4200)
t.upsert_job('existing-job', 'completed')
t.earn(5000, 'Paid', job_id='existing-job', stripe_ref='pi_prior')
t.reserve_spend(200, 'research', 'Unresolved provider charge', 'existing-job')
t.complete_stage('existing-job', 'fulfill', 'fulfill:existing-job', {'text': 'Delivered'})
t.upsert_metrics('existing-job', actual_cost_cents=100)
t.upsert_checkout('existing-job', 'cs_prior', 'https://example.test/prior', 'paid')
t.record_event('existing-job', 'completed', {'proof': 'pi_prior'})
session = t.get_or_create_chat_session('telegram', '123')
t.add_chat_message(session['id'], 'user', 'Historical message')
t.create_pairing_code('123')
# Preserve the historical invoice schema even after the active rail is removed.
with t._conn() as conn, conn:
    conn.execute("INSERT INTO nano_invoices (job_id, invoice_id, status, ts) "
                 "VALUES ('old-nano', 'historical-invoice', 'paid', 1)")
"""


@pytest.fixture(params=("worker", "serve"))
def command(request):
    if request.param == "serve" and importlib.util.find_spec("fastapi") is None:
        pytest.skip("FastAPI is not installed")
    return request.param


@pytest.fixture
def service_env(tmp_path):
    # Never inherit provider credentials or live integration settings.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SOLVENT_", "STRIPE_", "NVIDIA_", "SMTP_", "TELEGRAM_"))
    }
    env.update(SOLVENT_HOME=str(tmp_path), PYTHONPATH=str(ROOT))
    return env


def _run(env, *args):
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _start(command, env, *args):
    if command == "serve":
        return _run(env, "-c", SERVER_BOOTSTRAP, *args)
    return _run(env, "-m", "solvent", "worker", "--once", *args)


def _history(env):
    result = _run(env, "-c", SEED_HISTORY)
    assert result.returncode == 0, result.stderr
    return _snapshot(env)


def _snapshot(env):
    db = Path(env["SOLVENT_HOME"]) / "data" / "solvent.db"
    with sqlite3.connect(db) as conn:
        return {
            table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in TABLES
        }


@pytest.mark.parametrize("args", ((), ("--keep-balance",), ("--seed", "200")))
def test_repeated_startup_preserves_all_history(command, service_env, args):
    before = _history(service_env)
    assert all(before.values()), "Every protected table must contain historical data"
    for _ in range(2):
        result = _start(command, service_env, *args)
        assert result.returncode == 0, result.stderr
        assert _snapshot(service_env) == before


@pytest.mark.parametrize(
    ("args", "seed"),
    (
        ((), 10_000),
        (("--seed", "200"), 20_000),
        (("--seed", "19.99"), 1999),
        (("--seed", "0"), 0),
        (("--seed", "92233720368547758.07"), 2**63 - 1),
    ),
)
def test_only_explicit_reset_clears_history(command, service_env, args, seed):
    _history(service_env)
    result = _start(command, service_env, "--reset-state", *args)
    assert result.returncode == 0, result.stderr
    after = _snapshot(service_env)
    assert all(not rows for table, rows in after.items() if table != "ledger")
    ledger = after["ledger"]
    assert len(ledger) == (1 if seed else 0)
    if seed:
        assert ledger[0][1:3] == ("capital", seed)
    # A subsequent ordinary start must not reset or seed again.
    result = _start(command, service_env)
    assert result.returncode == 0, result.stderr
    assert _snapshot(service_env) == after


@pytest.mark.parametrize(
    "args",
    (
        ("--reset-state", "--keep-balance"),
        ("--reset-state", "--seed", "-1"),
        ("--reset-state", "--seed", "nan"),
        ("--reset-state", "--seed", "inf"),
        ("--reset-state", "--seed", "100000000000000000"),
        ("--reset-state", "--seed", "1e1000000"),
        ("--reset",),  # Destructive flags must be spelled out, not abbreviated.
    ),
)
def test_invalid_startup_options_leave_history_untouched(command, service_env, args):
    before = _history(service_env)
    result = _start(command, service_env, *args)
    assert result.returncode == 2, result.stderr
    assert _snapshot(service_env) == before


def test_new_home_starts_empty_without_implicit_capital(command, service_env):
    result = _start(command, service_env)
    assert result.returncode == 0, result.stderr
    assert all(not rows for rows in _snapshot(service_env).values())


def test_help_explains_default_and_destructive_option(command, service_env):
    result = _start(command, service_env, "--help")
    assert result.returncode == 0, result.stderr
    assert "--reset-state" in result.stdout
    assert "DESTRUCTIVE" in result.stdout
    assert "preserve all existing treasury state (the default" in result.stdout
