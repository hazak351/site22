"""Localização resiliente: vários candidatos por elemento (papel/rótulo/texto/CSS), tentados em ordem.

Prefere seletores semânticos (role, label, placeholder) a CSS frágil. Registra qual candidato
funcionou, para o Flow Doctor mostrar exatamente o que foi encontrado ou procurado.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


def rx(pattern: str) -> re.Pattern:
    """Regex para o Playwright. O Playwright converte para regex JavaScript, que NÃO aceita '(?i)':
    o prefixo vira a flag IGNORECASE (que o Playwright traduz para /i)."""
    flags = 0
    while pattern.startswith("(?i)"):
        pattern = pattern[4:]
        flags |= re.IGNORECASE
    return re.compile(pattern, flags)


def load_selectors(config_dir: Path) -> dict:
    return json.loads((Path(config_dir) / "flow_selectors.json").read_text(encoding="utf-8"))


def describe(candidate: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in candidate.items() if k != "state")


def build(scope, candidate: dict):
    if "role" in candidate:
        if "name" in candidate:
            return scope.get_by_role(candidate["role"], name=rx(candidate["name"]))
        return scope.get_by_role(candidate["role"])
    if "placeholder" in candidate:
        return scope.get_by_placeholder(rx(candidate["placeholder"]))
    if "label" in candidate:
        return scope.get_by_label(rx(candidate["label"]))
    if "text" in candidate:
        return scope.get_by_text(rx(candidate["text"]))
    if "css" in candidate:
        return scope.locator(candidate["css"])
    raise ValueError(f"candidato inválido: {candidate}")


class SelectorError(ValueError):
    pass


def _ready(locator, state: str) -> bool:
    try:
        if locator.count() == 0:
            return False
        if state == "attached":
            return True
        return locator.first.is_visible()
    except Exception as exc:
        if "InvalidSelector" in str(exc) or "Invalid regular expression" in str(exc):
            raise SelectorError(str(exc).splitlines()[0]) from exc
        return False


def find(scope, candidates: list[dict], timeout_ms: int = 5000, poll_ms: int = 250):
    """Devolve (locator, descrição) do primeiro candidato disponível, ou (None, candidatos tentados)."""
    deadline = time.monotonic() + timeout_ms / 1000
    tried = " | ".join(describe(c) for c in candidates)
    invalid: dict[int, str] = {}
    while True:
        for index, candidate in enumerate(candidates):
            if index in invalid:
                continue
            try:
                locator = build(scope, candidate)
                ready = _ready(locator, candidate.get("state", "visible"))
            except (SelectorError, ValueError, re.error) as exc:  # seletor mal configurado: mostrar no diagnóstico
                invalid[index] = f"SELETOR INVÁLIDO ({describe(candidate)}): {exc}"
                continue
            state = candidate.get("state", "visible")
            if ready:
                target = locator.first
                if state != "attached":
                    for i in range(min(locator.count(), 10)):  # primeiro VISÍVEL
                        if locator.nth(i).is_visible():
                            target = locator.nth(i)
                            break
                return target, describe(candidate)
        if time.monotonic() >= deadline:
            return None, " | ".join([tried, *invalid.values()]) if invalid else tried
        time.sleep(poll_ms / 1000)


def any_text(page, patterns: list[str]) -> str | None:
    """Procura textos visíveis (ex.: mensagens de erro); devolve o primeiro encontrado."""
    for pattern in patterns:
        try:
            locator = page.get_by_text(rx(pattern))
            count = locator.count()
            for i in range(min(count, 5)):
                if locator.nth(i).is_visible():
                    return (locator.nth(i).inner_text(timeout=1000) or pattern).strip()[:300]
        except Exception:
            continue
    return None
