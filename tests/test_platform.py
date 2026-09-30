"""Logs sem segredos, arquivos Windows (CRLF), sintaxe do frontend e comando de verificação."""

import json
import shutil
import subprocess
import sys

import pytest

from adflow.logs import ProjectLog, redact
from tests.conftest import ROOT


def test_redaction_of_secrets():
    samples = {
        "senha=MinhaSenha123": "MinhaSenha123",
        "password: hunter2": "hunter2",
        "Cookie: SID=abc123def; HSID=xyz789": "abc123def",
        "__Secure-3PSID=g.a000verysecret": "g.a000verysecret",
        "Authorization: Bearer ya29.a0AfH6SMBsecret": "ya29.a0AfH6SMBsecret",
        "token=eyJhbGciOiJIUzI1NiJ9.payload": "eyJhbGciOiJIUzI1NiJ9",
        '{"api_key": "sk-ant-123456"}': "sk-ant-123456",
    }
    for text, secret in samples.items():
        assert secret not in redact(text), text


def test_project_log_redacts_and_writes_both_formats(tmp_path):
    log = ProjectLog(tmp_path, "p1")
    log.info("flow", "login com senha=abc12345", cookie="SID=zzz", scene=2, prompt="PRODUCT LOCK")
    text = (tmp_path / "p1" / "project.log").read_text(encoding="utf-8")
    event = json.loads((tmp_path / "p1" / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "abc12345" not in text and "zzz" not in text
    assert event["cookie"] == "[REDACTED]" and event["scene"] == 2 and event["op"] == "flow"
    assert "PRODUCT LOCK" in text


@pytest.mark.parametrize("name", [p.name for p in ROOT.glob("*.bat")])
def test_bat_files_use_crlf(name):
    data = (ROOT / name).read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n"), f"{name} tem quebras de linha LF sem CR"
    assert b"powershell" not in data.lower()


def test_bat_files_exist_and_gitattributes_keeps_crlf():
    assert (ROOT / "start.bat").is_file()
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat -text" in attributes or "*.bat text eol=crlf" in attributes


def test_gitignore_protects_session_and_outputs():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("data/", "logs/", ".venv/", ".env"):
        assert entry in ignored


def test_requirements_have_no_compiled_only_extras():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "uvicorn[standard]" not in reqs  # evitaria uvloop/httptools (compilação)
    assert "numpy" not in reqs and "opencv" not in reqs


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js não instalado")
def test_frontend_js_syntax():
    subprocess.run(["node", "--check", str(ROOT / "frontend" / "app.js")], check=True)


def test_check_command_runs():
    proc = subprocess.run([sys.executable, "-m", "adflow", "check"], cwd=ROOT, capture_output=True, text=True)
    assert "Python" in proc.stdout and "fastapi" in proc.stdout
