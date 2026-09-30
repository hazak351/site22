"""Contrato comum entre o simulador e o Flow real."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from ..models import now_iso


class FlowError(RuntimeError):
    """Falha de automação com dados de diagnóstico (nunca contém cookies ou senhas)."""

    def __init__(self, message: str, step: str = "", selector: str = "", url: str = "",
                 screenshot: str | None = None, html: str | None = None, retryable: bool = False,
                 credits_spent: bool = False):
        super().__init__(message)
        self.step = step
        self.selector = selector
        self.url = url
        self.screenshot = screenshot
        self.html = html
        self.retryable = retryable
        self.credits_spent = credits_spent

    def to_dict(self) -> dict:
        return {"message": str(self), "step": self.step, "selector": self.selector, "url": self.url,
                "screenshot": self.screenshot, "html": self.html, "retryable": self.retryable}


class DoctorCheck(BaseModel):
    key: str
    label: str
    ok: bool
    critical: bool = True
    detail: str = ""
    selector: str = ""


class DoctorReport(BaseModel):
    engine: str
    real: bool
    ok: bool = False
    time: str = Field(default_factory=now_iso)
    checks: list[DoctorCheck] = Field(default_factory=list)
    failed_step: str | None = None
    message: str = ""
    url: str = ""
    screenshot: str | None = None
    html_snippet: str | None = None


@dataclass
class GenerationRequest:
    project_id: str
    scene_index: int
    attempt: int
    prompt: str
    aspect: str  # aspecto de geração (16:9 ou 9:16)
    duration: float
    output_path: Path
    reference_images: list[Path] = field(default_factory=list)
    start_frame: Path | None = None
    model_match: str = ""
    environment_hint: dict = field(default_factory=dict)  # usado só pelo simulador


@dataclass
class GenerationResult:
    video_path: Path
    meta: dict = field(default_factory=dict)


class FlowEngine(Protocol):
    name: str
    real: bool

    def doctor(self) -> DoctorReport: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def close(self) -> None: ...
