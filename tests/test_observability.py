"""Unit tests for solvent.observability.log_event."""

import json
from unittest.mock import MagicMock

from solvent.observability import log_event


def test_log_event_returns_dict_without_none_values(tmp_path, monkeypatch):
    """log_event returns a dict with all keys populated and Nones stripped."""
    log_file = tmp_path / "solvent.log"
    monkeypatch.setattr("solvent.observability.LOG_PATH", log_file)

    treasury_mock = MagicMock()
    result = log_event(
        treasury_mock,
        job_id="J1",
        stage="quote",
        stripe_ref="cs_123",
        margin_est=10.5,
        margin_actual=None,
        duration_ms=250.0,
        simulated=False,
        accept=True,
    )

    assert isinstance(result, dict)
    assert result["job_id"] == "J1"
    assert result["stage"] == "quote"
    assert result["stripe_ref"] == "cs_123"
    assert result["margin_est"] == 10.5
    assert result["duration_ms"] == 250.0
    assert result["simulated"] is False
    assert result["accept"] is True
    # None-valued fields must be absent from the returned dict
    assert "margin_actual" not in result


def test_log_event_writes_json_line_to_log(tmp_path, monkeypatch):
    """log_event appends a JSON line to LOG_PATH."""
    log_file = tmp_path / "solvent.log"
    monkeypatch.setattr("solvent.observability.LOG_PATH", log_file)

    log_event(MagicMock(), job_id="J1", stage="invoice", simulated=True)

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["job_id"] == "J1"
    assert parsed["stage"] == "invoice"
    assert parsed["simulated"] is True


def test_log_event_calls_treasury_record_event():
    """log_event forwards the event dict to treasury.record_event."""
    treasury_mock = MagicMock()
    log_event(
        treasury_mock,
        job_id="J1",
        stage="paid",
        amount=5000,
    )
    treasury_mock.record_event.assert_called_once()
    args, kwargs = treasury_mock.record_event.call_args
    # record_event(job_id, stage, record)
    assert args[0] == "J1"
    assert args[1] == "paid"
    passed_record = args[2]
    assert passed_record["job_id"] == "J1"
    assert passed_record["stage"] == "paid"
    assert passed_record["amount"] == 5000


def test_log_event_handles_missing_treasury_gracefully(tmp_path, monkeypatch):
    """log_event must not raise when treasury is None."""
    log_file = tmp_path / "solvent.log"
    monkeypatch.setattr("solvent.observability.LOG_PATH", log_file)

    result = log_event(None, job_id="J1", stage="quote")
    assert result["job_id"] == "J1"
    assert log_file.exists()


def test_log_event_handles_oserror_gracefully(monkeypatch):
    """log_event must not raise when the log file is unwritable."""
    treasury_mock = MagicMock()

    def bad_open(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.open", bad_open)
    result = log_event(treasury_mock, job_id="J1", stage="quote")
    assert result["job_id"] == "J1"
    treasury_mock.record_event.assert_called_once()


def test_log_event_json_env_var_streams_to_stderr(monkeypatch, capsys):
    """When SOLVENT_LOG_JSON is set, also emit to stderr."""
    monkeypatch.setenv("SOLVENT_LOG_JSON", "1")
    treasury_mock = MagicMock()

    log_event(treasury_mock, job_id="J1", stage="fulfilled", vendor="nemotron")

    captured = capsys.readouterr()
    assert captured.err.strip() != ""
    line = captured.err.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["job_id"] == "J1"
    assert parsed["stage"] == "fulfilled"
    assert parsed["vendor"] == "nemotron"
