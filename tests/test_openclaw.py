"""SOLVENT as an OpenClaw skill: install, report, the skill's API contract, doctor checks."""

import json
import re

import pytest

from solvent import openclaw, ops
from solvent.access import is_loopback_url, public_exposure
from solvent.config import SolventConfig, effective_base_url, save_config

TOKEN = "d" * 32
HARDENED = {"SOLVENT_LOCAL_ACCESS": "never", "SOLVENT_DASHBOARD_TOKEN": TOKEN}
DEPLOYMENT_ENV = (
    "SOLVENT_BASE_URL",
    "SOLVENT_DASHBOARD_TOKEN",
    "SOLVENT_LOCAL_ACCESS",
    "OPENCLAW_WORKSPACE_DIR",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated SOLVENT_HOME and $HOME with no saved config or deployment env."""
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path / "solvent"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in DEPLOYMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _frontmatter(text: str) -> dict:
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


# --- exposure ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8787", "http://localhost:8787", "http://[::1]:8787", "127.0.0.1"]
)
def test_loopback_urls_have_no_exposure(url):
    assert is_loopback_url(url)
    assert public_exposure(url, env={}) == []


def test_plain_http_on_a_public_host_is_flagged():
    gaps = public_exposure("http://203.0.113.5:8787", env=HARDENED)
    assert len(gaps) == 1 and "plain http" in gaps[0]


def test_https_without_local_access_never_is_flagged():
    assert public_exposure("https://agent.example.com", env=HARDENED) == []
    gaps = public_exposure("https://agent.example.com", env={"SOLVENT_DASHBOARD_TOKEN": TOKEN})
    assert gaps == ["https base URL means a reverse proxy; set SOLVENT_LOCAL_ACCESS=never"]


def test_short_dashboard_token_is_flagged():
    env = {"SOLVENT_LOCAL_ACCESS": "never", "SOLVENT_DASHBOARD_TOKEN": "changeme"}
    gaps = public_exposure("https://agent.example.com", env=env)
    assert len(gaps) == 1 and "under 32 characters" in gaps[0]


@pytest.mark.parametrize("url", ["", "http://[::1", "https://"])
def test_malformed_base_url_is_flagged(url):
    assert not is_loopback_url(url)
    assert "not a valid URL" in public_exposure(url, env=HARDENED)[0]


# --- base URL resolution ---------------------------------------------------------


def test_base_url_comes_from_env_then_default(home, monkeypatch):
    assert effective_base_url() == ("http://127.0.0.1:8787", "default")
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    assert effective_base_url() == ("https://agent.example.com", "env")


def test_saved_config_wins_over_env_like_serve(home, monkeypatch):
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    save_config(SolventConfig(base_url="http://127.0.0.1:8787"))
    assert effective_base_url() == ("http://127.0.0.1:8787", "config")


# --- installing the skill --------------------------------------------------------


def test_default_skills_dir(home, monkeypatch):
    assert openclaw.default_skills_dir() == home / "home" / ".openclaw" / "workspace" / "skills"
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(home / "ws"))
    assert openclaw.default_skills_dir() == home / "ws" / "skills"


def test_install_is_idempotent_and_keeps_local_edits(home):
    skills = home / "skills"
    path = openclaw.skill_path(skills)
    assert openclaw.skill_status(skills) == "missing"
    assert openclaw.install_skill(skills) == "installed"
    assert path.read_text(encoding="utf-8") == openclaw.skill_text()
    assert openclaw.install_skill(skills) == "unchanged"

    path.write_text("my edits", encoding="utf-8")
    assert openclaw.skill_status(skills) == "different"
    assert openclaw.install_skill(skills) == "kept"
    assert path.read_text(encoding="utf-8") == "my edits"
    assert openclaw.install_skill(skills, force=True) == "updated"
    assert openclaw.skill_status(skills) == "current"


def test_report_never_contains_the_dashboard_token(home, monkeypatch):
    secret = "s" * 40
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", secret)
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    data = openclaw.report(home / "skills")
    assert secret not in json.dumps(data)
    assert data["gateway_env"]["SOLVENT_URL"] == "https://agent.example.com"
    assert data["notes"] == []


def test_report_explains_loopback_and_missing_token(home):
    notes = " ".join(openclaw.report(home / "skills")["notes"])
    assert "inside a container 127.0.0.1 is the container" in notes
    assert "SOLVENT_DASHBOARD_TOKEN is not set" in notes


def test_connect_cli_installs_and_reports(home, capsys):
    skills = home / "skills"
    openclaw.main(["--install-skill", "--skills-dir", str(skills), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["install"] == "installed" and out["skill"]["status"] == "current"

    openclaw.main(["--skills-dir", str(skills)])
    text = capsys.readouterr().out
    assert "pairs with your OpenClaw Gateway" in text
    assert "SOLVENT_URL=http://127.0.0.1:8787" in text


def test_connect_cli_exits_nonzero_when_it_keeps_an_edited_skill(home, capsys):
    skills = home / "skills"
    openclaw.skill_path(skills).parent.mkdir(parents=True)
    openclaw.skill_path(skills).write_text("mine", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        openclaw.main(["--install-skill", "--skills-dir", str(skills)])
    assert exc.value.code == 1
    assert "Use --force" in capsys.readouterr().out


def test_connect_rejects_force_without_install(home):
    with pytest.raises(SystemExit) as exc:
        openclaw.main(["--force"])
    assert exc.value.code == 2


def test_connect_is_dispatched_by_the_cli(home, monkeypatch, capsys):
    from solvent.__main__ import main

    monkeypatch.setattr("sys.argv", ["solvent", "connect", "--json"])
    main()
    assert json.loads(capsys.readouterr().out)["install"] is None


# --- the skill's contract ----------------------------------------------------------


def test_skill_frontmatter_follows_the_openclaw_format():
    meta = _frontmatter(openclaw.skill_text())
    assert meta["name"] == openclaw.SKILL_NAME == openclaw.SKILL_TEMPLATE.parent.name
    assert re.fullmatch(r"[a-z0-9-]+", meta["name"])
    assert len(meta["description"]) < 160
    gating = json.loads(meta["metadata"])["openclaw"]
    assert gating["requires"] == {"bins": ["curl"], "env": ["SOLVENT_URL"]}
    assert gating["primaryEnv"] == "SOLVENT_DASHBOARD_TOKEN"


def test_skill_only_names_commands_solvent_understands():
    named = set(re.findall(r"`(/[a-z]+)", openclaw.skill_text()))
    known = set(ops.COMMANDS) | {"/help", "/jobs", "/quote", "/status"}
    assert named and named <= known


def test_skill_routes_and_response_shapes_match_the_server(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from solvent import server, treasury
    from solvent.webhook_log import WebhookLog

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    client = TestClient(server.create_app(fresh=True), base_url="http://127.0.0.1")

    text = openclaw.skill_text()
    named = {
        re.sub(r"^/jobs/[^/]+$", "/jobs/{job_id}", p)
        for p in re.findall(r"\$SOLVENT_URL(/[^\"\s]*)", text)
    }
    routes = {route.path for route in client.app.routes}
    assert named == {"/health", "/api/chat", "/jobs", "/jobs/{job_id}"} and named <= routes

    assert client.get("/health").json()["status"] == "ok"
    bodies = [json.loads(b) for b in re.findall(r"<<'JSON'\n(.*?)\nJSON", text, re.S)]
    chat, order = bodies
    auth = {"X-Solvent-Dashboard-Token": TOKEN}
    reply = client.post("/api/chat", json={**chat, "message": "/status"}, headers=auth).json()
    assert set(reply) == {"reply", "session_id"} and reply["session_id"] == "openclaw"
    assert client.post("/api/chat", json=chat).status_code == 403

    placed = client.post("/jobs", json=order).json()
    assert placed["stage"] == "invoice" and placed["url"] and placed["job_id"]
    public = client.get(f"/jobs/{placed['job_id']}").json()
    assert set(public["job"]) == {"id", "status"} and public["job"]["status"] == "awaiting_payment"
    assert "metrics" in client.get(f"/jobs/{placed['job_id']}", headers=auth).json()


# --- doctor ------------------------------------------------------------------------


@pytest.fixture
def doctor(home, monkeypatch):
    from solvent import treasury

    monkeypatch.setenv("SOLVENT_WORKSPACE", str(home / "workspace"))
    monkeypatch.setattr(treasury, "DB_PATH", home / "solvent.db")
    from solvent.doctor import run_checks

    return lambda: {c["name"]: c for c in run_checks()}


def test_doctor_flags_a_saved_base_url_overriding_the_env(doctor, monkeypatch):
    save_config(SolventConfig(base_url="http://127.0.0.1:8787"))
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    check = doctor()["base_url"]
    assert check["ok"] is False
    assert "solvent config set base_url https://agent.example.com" in check["detail"]


def test_doctor_base_url_and_exposure_pass_when_consistent(doctor, monkeypatch):
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    for name, value in HARDENED.items():
        monkeypatch.setenv(name, value)
    checks = doctor()
    assert checks["base_url"]["ok"] and "(from env)" in checks["base_url"]["detail"]
    assert checks["public_exposure"] == {"name": "public_exposure", "ok": True, "detail": "ok"}


def test_doctor_reports_public_exposure(doctor, monkeypatch):
    monkeypatch.setenv("SOLVENT_BASE_URL", "http://203.0.113.5:8787")
    check = doctor()["public_exposure"]
    assert check["ok"] is False and "plain http" in check["detail"]


def test_doctor_reports_the_openclaw_skill(doctor, home):
    assert "no OpenClaw workspace" in doctor()["openclaw_skill"]["detail"]
    skills = openclaw.default_skills_dir()
    skills.parent.mkdir(parents=True)
    assert "not installed" in doctor()["openclaw_skill"]["detail"]
    openclaw.install_skill(skills)
    check = doctor()["openclaw_skill"]
    assert check["ok"] and check["detail"].startswith("installed")
