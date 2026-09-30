"""Quality Gate por cena.

Verificações técnicas e de consistência são locais (FFmpeg + histogramas de cor).
Verificações semânticas (produto deformado, logo trocado, texto errado, objetos extras,
aderência ao briefing, artefatos) exigem um analisador de visão; sem ele ficam como
"nao_avaliado" — nunca são marcadas como aprovadas sem terem sido avaliadas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from . import media
from .config import Settings
from .models import QualityCheck, QualityReport, Scene

SEMANTIC_CHECKS = [
    ("produto_correto", "Produto continua correto (forma, proporção, acabamento)"),
    ("logo", "Logo preservado"),
    ("texto", "Sem texto errado na imagem"),
    ("objetos_extras", "Sem objetos extras"),
    ("deformacoes", "Sem deformações"),
    ("briefing", "Combina com o briefing"),
    ("artefatos", "Sem artefatos visuais graves"),
]

FIXES = {
    "ambiente": "The previous take changed the set: keep EXACTLY the same surface, background, light direction and color temperature described in ENVIRONMENT LOCK.",
    "cor_produto": "The previous take drifted the product colors: match the reference product colors and finish exactly.",
    "tela_preta": "Keep the product visible and well lit during the entire shot, no fade to black.",
    "congelado": "Keep smooth continuous camera motion during the entire shot, no frozen frames.",
    "continuidade": "Start exactly where the previous shot ended: same framing, product scale and orientation.",
}


class VisionJudge(Protocol):
    """Interface para um avaliador de visão (ex.: modelo multimodal). Não incluso por padrão."""

    def evaluate(self, frames: list[Path], product_images: list[Path], brief: str, prompt: str) -> list[QualityCheck]:
        ...


def extract_scene_frames(settings: Settings, video: Path, scene: Scene, out_dir: Path) -> dict[str, Path]:
    info = media.video_info(settings, video)
    # clipes mais curtos que o pedido (o Flow às vezes devolve menos) não podem quebrar o Gate
    latest = max(0.0, info["duration"] - 0.12)
    start = min(scene.trim_start, latest)
    end = min(latest, scene.trim_start + scene.target_duration - 0.12)
    times = {
        "first": min(start + 0.05, latest),
        "mid": min(start + scene.target_duration / 2, latest),
        "last": max(start, end),
    }
    return {name: media.extract_frame(settings, video, t, out_dir / f"{name}.png") for name, t in times.items()}


def evaluate_scene(settings: Settings, video: Path, scene: Scene, frames: dict[str, Path],
                   product_images: list[Path], env_reference: Path | None, previous_last: Path | None,
                   brief: str = "", judge: VisionJudge | None = None) -> QualityReport:
    checks: list[QualityCheck] = []
    info = media.video_info(settings, video)

    # 1. duração
    needed = scene.trim_start + scene.target_duration
    if info["duration"] + 0.05 >= needed:
        checks.append(QualityCheck(name="duracao", status="ok", score=1.0, detail=f"{info['duration']:.2f}s disponíveis"))
    elif info["duration"] >= needed * 0.75:
        checks.append(QualityCheck(name="duracao", status="aviso", score=0.6,
                                   detail=f"{info['duration']:.2f}s < {needed:.2f}s (o editor completa congelando o final)"))
    else:
        checks.append(QualityCheck(name="duracao", status="falha", score=0.0,
                                   detail=f"{info['duration']:.2f}s, muito curto para {needed:.2f}s"))

    # 2. resolução
    short_side = min(info["width"], info["height"])
    status = "ok" if short_side >= 720 else "aviso" if short_side >= 360 else "falha"
    checks.append(QualityCheck(name="resolucao", status=status, score=min(1.0, short_side / 720),
                               detail=f"{info['width']}x{info['height']}"))

    # 3. tela preta / 4. congelado
    black = media.black_ratio(settings, video, info["duration"])
    checks.append(QualityCheck(name="tela_preta", status="falha" if black > settings.qg_max_black_ratio else "ok",
                               score=round(1 - black, 3), detail=f"{black:.0%} do tempo escuro"))
    frozen = media.freeze_ratio(settings, video, info["duration"])
    checks.append(QualityCheck(name="congelado", status="falha" if frozen > settings.qg_max_freeze_ratio else "ok",
                               score=round(1 - frozen, 3), detail=f"{frozen:.0%} do tempo parado"))

    # 5. cor do produto
    checks.append(_product_color_check(settings, frames["mid"], product_images))

    # 6. ambiente
    if env_reference and Path(env_reference).is_file():
        sim = media.image_similarity(frames["mid"], env_reference, which="border")
        checks.append(QualityCheck(name="ambiente", status="ok" if sim >= settings.qg_environment_min else "falha",
                                   score=sim, detail=f"similaridade do fundo com o Environment Master: {sim:.2f}"))
    else:
        checks.append(QualityCheck(name="ambiente", status="ok", score=None,
                                   detail="primeira cena: define a referência do ambiente"))

    # 7. continuidade
    if previous_last and Path(previous_last).is_file():
        sim = media.image_similarity(frames["first"], previous_last, which="full")
        checks.append(QualityCheck(name="continuidade", status="ok" if sim >= settings.qg_continuity_min else "aviso",
                                   score=sim, detail=f"último frame anterior × primeiro frame: {sim:.2f}"))

    # 8. semânticas
    if judge is not None:
        checks += judge.evaluate(list(frames.values()), product_images, brief, scene.prompt)
    else:
        for name, label in SEMANTIC_CHECKS:
            checks.append(QualityCheck(name=name, status="nao_avaliado", detail=f"{label}: requer analisador de visão"))

    scores = [c.score for c in checks if c.score is not None]
    failed = [c.name for c in checks if c.status == "falha"]
    return QualityReport(
        passed=not failed,
        score=round(sum(scores) / len(scores), 3) if scores else 0.0,
        checks=checks,
        fixes=[FIXES[name] for name in failed if name in FIXES],
    )


def _product_color_check(settings: Settings, frame: Path, product_images: list[Path]) -> QualityCheck:
    images = [p for p in product_images if Path(p).is_file()]
    if not images:
        return QualityCheck(name="cor_produto", status="nao_avaliado", detail="sem fotos do produto")
    merged = [0.0] * 18
    sat_total = 0.0
    for path in images[:4]:
        hist, sat = media.hue_profile(path, ignore_background=True)
        merged = [a + b * sat for a, b in zip(merged, hist)]
        sat_total += sat
    sat_avg = sat_total / len(images[:4])
    if sat_avg < 0.06:
        return QualityCheck(name="cor_produto", status="nao_avaliado",
                            detail="produto quase sem cor saturada (preto/branco/cinza): cor só é verificável com analisador de visão")
    norm = sum(merged) or 1.0
    product_hist = [x / norm for x in merged]
    frame_hist, frame_sat = media.hue_profile(frame, ignore_background=True)
    score = 0.0 if frame_sat < 0.01 else hue_presence(product_hist, frame_hist)
    status = "ok" if score >= settings.qg_product_color_min else "falha"
    return QualityCheck(name="cor_produto", status=status, score=score,
                        detail=f"matizes do produto × cena: {score:.2f}")


def hue_presence(product_hist: list[float], frame_hist: list[float], ratio: float = 0.25) -> float:
    """Quanto das matizes do produto aparece na cena (1 = todas presentes).

    Mede PRESENÇA, não igualdade de distribuição: cores extras do cenário não reprovam,
    mas um produto com a cor trocada (ex.: dourado → azul) reprova. Tolera ±1 faixa de matiz
    (mudança de luz).
    """
    bins = len(product_hist)
    main = [(b, w) for b, w in enumerate(product_hist) if w >= 0.05]
    total = sum(w for _, w in main) or 1.0
    score = 0.0
    for b, w in main:
        seen = frame_hist[b] + 0.5 * (frame_hist[(b - 1) % bins] + frame_hist[(b + 1) % bins])
        score += (w / total) * min(1.0, seen / (w * ratio))
    return round(score, 3)
