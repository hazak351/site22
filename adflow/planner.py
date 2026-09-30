"""Divide a duração pedida em cenas compatíveis com as durações que o Flow gera."""

from __future__ import annotations

import math

PRESET_DURATIONS = [6, 10, 15, 20, 24, 30, 45, 60]


def plan_durations(total: float, generation_durations: list[float], min_scene: float = 2.0) -> list[float]:
    """Menor número de cenas possível, com durações equilibradas em segundos inteiros.

    Ex. (clipes de 8s): 20 -> [7, 7, 6]; 24 -> [8, 8, 8]; 30 -> [8, 8, 7, 7].
    Cada cena é gerada com a menor duração disponível >= alvo e o editor corta o excesso.
    """
    if total <= 0:
        raise ValueError("a duração precisa ser maior que zero")
    if total > 300:
        raise ValueError("duração máxima suportada: 300s")
    max_gen = max(generation_durations)
    count = max(1, math.ceil(total / max_gen - 1e-9))
    # não criar cenas menores que o mínimo (exceto quando o vídeo inteiro é curto)
    while count > 1 and total / count < min_scene:
        count -= 1
    whole = math.floor(total + 1e-9)
    fraction = round(total - whole, 3)
    base, remainder = divmod(whole, count)
    durations = [float(base + 1 if i < remainder else base) for i in range(count)]
    durations[-1] = round(durations[-1] + fraction, 3)
    if any(d > max_gen + 1e-9 for d in durations):  # só acontece com frações; redistribui
        durations = [round(total / count, 3)] * count
    return durations


def generation_duration_for(target: float, generation_durations: list[float]) -> float:
    options = sorted(generation_durations)
    for option in options:
        if option + 1e-9 >= target:
            return float(option)
    return float(options[-1])
