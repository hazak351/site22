"""Motores de geração: simulador (offline) e Google Flow real via navegador (Playwright + CDP)."""

from __future__ import annotations

from ..config import Settings
from .base import DoctorReport, FlowEngine, FlowError, GenerationRequest, GenerationResult


def create_engine(name: str, settings: Settings, log=None, **kwargs) -> FlowEngine:
    if name == "simulator":
        from .simulator import SimulatorEngine
        return SimulatorEngine(settings, log=log, **kwargs)
    if name == "flow":
        from .driver import FlowBrowserEngine
        return FlowBrowserEngine(settings, log=log, **kwargs)
    raise ValueError(f"motor desconhecido: {name}")


__all__ = ["create_engine", "DoctorReport", "FlowEngine", "FlowError", "GenerationRequest", "GenerationResult"]
