"""Geração dos prompts de cena com Product Lock, Environment Lock e Continuity Lock."""

from __future__ import annotations

from .models import CreativeDirection, EnvironmentMaster, ProductProfile, Scene

ASPECT_TEXT = {
    "9:16": "vertical 9:16 frame",
    "16:9": "horizontal 16:9 frame",
    "1:1": "frame composed for a centered 1:1 square crop: keep all action in the center with safe margins",
}

AVOID = ("on-screen text, captions, subtitles, watermarks, generated logos or brand names, extra products, "
         "hands or people, other objects on the surface, morphing, deformation, melting, flicker, "
         "cuts or scene changes within the shot, change of background or lighting")


def generation_aspect(aspect: str, square_source: str = "16:9") -> str:
    """O Flow gera 16:9 ou 9:16; 1:1 é recortado na pós-produção."""
    return square_source if aspect == "1:1" else aspect


def build_prompt(scene: Scene, total_scenes: int, aspect: str, direction: CreativeDirection,
                 product: ProductProfile, environment: EnvironmentMaster, fixes: list[str] | None = None) -> str:
    parts = [
        f"Premium product commercial, shot {scene.index} of {total_scenes}, one continuous {scene.generation_duration:g}-second "
        f"shot, {ASPECT_TEXT.get(aspect, aspect)}.",
        f"SHOT: {scene.shot}.",
        f"CAMERA: {scene.camera}; lens: {direction.lens}; pace: {_pace(direction.pace)}.",
        f"LOOK: {direction.style} commercial, {direction.lighting}, color grade: {direction.color_grade}.",
        product.lock_text,
        environment.lock_text(),
        continuity_text(scene),
        "AUDIO: subtle cinematic sound design matching the motion (soft whooshes, ambience); no voice, no music.",
        f"AVOID: {AVOID}.",
    ]
    if fixes:
        parts.append("CORRECTIONS FOR THIS TAKE (highest priority): " + " ".join(fixes))
    return "\n".join(p for p in parts if p)


def continuity_text(scene: Scene) -> str:
    text = (f"CONTINUITY LOCK: this shot is part of one continuous commercial. It starts with: {scene.start_state}. "
            f"It ends with: {scene.end_state}. Keep the same product scale, orientation, screen direction "
            "(camera stays in front of the product, movement left to right), same lighting and same set as the other shots.")
    if scene.start_frame:
        text += " The first frame must match the provided start frame exactly."
    return text


def _pace(pace: str) -> str:
    return {"lento": "slow and elegant", "medio": "smooth, medium", "rapido": "fast and energetic"}.get(pace, pace)
