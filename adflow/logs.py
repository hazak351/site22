"""Logs por projeto em logs/<project_id>/ (texto + JSONL), sempre com redação de segredos."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(password|senha|passwd|pwd)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[\w\-.~+/=]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[\w\-.~+/=]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"ya29\.[\w\-.]+"), "[REDACTED_GOOGLE_TOKEN]"),
    (re.compile(r"(?i)((?:__Secure-|__Host-)?[\w-]*(?:SID|HSID|SSID|APISID|SAPISID|PSID|PSIDTS|PSIDCC)\s*=\s*)[^;\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(cookie\s*[:=]\s*).+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|access_token|refresh_token)\"?\s*[:=]\s*\"?)[^\"\s,&;]+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_obj(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if re.search(r"(?i)password|senha|cookie|token|secret", str(k)) else _redact_obj(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v) for v in obj]
    return obj


class ProjectLog:
    """Escreve logs/<id>/project.log (legível) e logs/<id>/events.jsonl (estruturado)."""

    def __init__(self, logs_dir: Path, project_id: str):
        self.dir = Path(logs_dir) / project_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.text_path = self.dir / "project.log"
        self.jsonl_path = self.dir / "events.jsonl"
        self._lock = threading.Lock()

    def _write(self, level: str, op: str, message: str, data: dict) -> None:
        stamp = datetime.now().isoformat(timespec="milliseconds")
        message = redact(message)
        data = _redact_obj(data)
        extra = ""
        if data:
            extra = " | " + " ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str)}" for k, v in data.items())
        line = f"{stamp} | {level:<5} | {op:<18} | {message}{extra}\n"
        record = {"time": stamp, "level": level, "op": op, "message": message, **data}
        with self._lock:
            with self.text_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def info(self, op: str, message: str, **data) -> None:
        self._write("INFO", op, message, data)

    def warn(self, op: str, message: str, **data) -> None:
        self._write("WARN", op, message, data)

    def error(self, op: str, message: str, **data) -> None:
        self._write("ERROR", op, message, data)

    def tail(self, lines: int = 200) -> list[str]:
        if not self.text_path.exists():
            return []
        content = self.text_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]
