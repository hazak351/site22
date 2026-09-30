"""Análise do produto e da referência — heurística local (FFmpeg + Pillow).

Não usa IA de visão: reconhece cores, ritmo de cortes, brilho, paleta e intensidade de
movimento. Movimentos específicos (orbit, dolly, rack focus) vêm do texto do briefing;
um analisador de visão pode ser plugado depois (ver README, "Próximos passos").
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import media
from .config import Settings
from .models import Asset, ProductProfile, ReferenceAnalysis

CATEGORY_KEYWORDS = {  # ordem importa: a primeira categoria encontrada vence
    "audio": ["fone", "headphone", "earbud", "headset", "caixa de som", "speaker", "airpod", "tws"],
    "perfume": ["perfume", "fragrancia", "colonia", "eau de"],
    "cosmetico": ["cosmetico", "creme", "serum", "batom", "maquiagem", "skincare", "hidratante", "shampoo"],
    "comida": ["hamburguer", "burger", "pizza", "lanche", "sanduiche", "batata frita", "sobremesa", "bolo",
               "doce", "comida", "prato", "hot dog", "sorvete", "acai"],
    "bebida": ["bebida", "refrigerante", "cerveja", "suco", "drink", "cafe", "garrafa", "lata", "energetico", "vinho"],
    "eletronico": ["celular", "smartphone", "relogio", "smartwatch", "notebook", "carregador", "mouse", "teclado",
                   "camera fotografica", "console", "controle"],
    "moda": ["tenis", "sapato", "bolsa", "camiseta", "roupa", "oculos", "bone", "jaqueta"],
}

LOOKALIKES = {
    "audio": ["Apple AirPods", "generic white earbuds", "a different earbud or headphone design"],
    "perfume": ["a different bottle shape", "a famous perfume brand bottle"],
    "cosmetico": ["a different package design", "another brand's packaging"],
    "bebida": ["a different can or bottle design", "another brand's packaging"],
    "eletronico": ["an Apple or Samsung device", "a generic device"],
    "moda": ["a different model from a famous brand", "a generic product"],
    "comida": ["a different recipe", "a generic stock-photo version of the dish"],
    "geral": ["a generic product", "a different brand's product"],
}

FINISH_WORDS = {
    "fosco": "matte", "matte": "matte", "brilhante": "glossy", "glossy": "glossy", "metalico": "metallic",
    "metálico": "metallic", "acetinado": "satin", "cromado": "chrome", "transparente": "transparent",
    "vidro": "glass", "couro": "leather",
}

COLOR_WORDS = {
    "preto": "black", "preta": "black", "branco": "white", "branca": "white", "dourado": "gold",
    "dourada": "gold", "prata": "silver", "prateado": "silver", "cinza": "gray", "grafite": "graphite",
    "azul": "blue", "vermelho": "red", "vermelha": "red", "verde": "green", "rosa": "pink", "roxo": "purple",
    "amarelo": "yellow", "laranja": "orange", "marrom": "brown", "bege": "beige",
    "dourados": "gold", "douradas": "gold", "pretos": "black", "brancos": "white", "prateados": "silver",
}
ADJECTIVES = {"longa": "long", "longo": "long", "curta": "short", "curto": "short", "fina": "thin",
              "grossa": "thick", "reta": "straight", "curva": "curved", "redonda": "round", "quadrada": "square"}

ROLE_HINTS = [("frente", ["frente", "front", "frontal"]), ("lateral", ["lateral", "side", "lado", "perfil"]),
              ("3/4", ["34", "3-4", "3_4", "tres_quartos", "three"]), ("detalhe", ["detalhe", "detail", "close", "macro"])]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def detect_category(text: str) -> str:
    norm = normalize(text)
    for category, words in CATEGORY_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(w), norm) for w in words):
            return category
    return "geral"


def guess_role(filename: str, index: int) -> str:
    name = normalize(Path(filename).stem)
    for role, hints in ROLE_HINTS:
        if any(h in name for h in hints):
            return role
    return ["frente", "3/4", "lateral", "detalhe"][index] if index < 4 else "detalhe"


def analyze_product(settings: Settings, project_dir: Path, name: str, brief: str,
                    assets: list[Asset]) -> ProductProfile:
    text = f"{name} {brief}"
    category = detect_category(text)
    norm = normalize(text)
    images = [a for a in assets if a.kind == "image"]
    for i, asset in enumerate(images):
        if asset.role == "geral":
            asset.role = guess_role(asset.original_name or asset.path, i)

    # cores: somando as imagens do produto (fundo removido pela cor da borda)
    weights: dict[str, float] = {}
    for asset in images[:4]:
        for hex_color, share in media.dominant_colors(project_dir / asset.path, count=4):
            weights[hex_color] = weights.get(hex_color, 0) + share
    # ignora cores com pouca presença (bordas/antisserrilhado entre duas cores reais)
    minimum = 0.08 * max(1, len(images[:4]))
    colors = [c for c, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w >= minimum][:5]
    color_names: list[str] = []
    for c in colors:
        name_en = media.color_name(c)
        if name_en not in color_names:
            color_names.append(name_en)
    # cores citadas no texto têm prioridade (o usuário conhece o produto)
    stated = [en for pt, en in COLOR_WORDS.items() if re.search(r"\b" + normalize(pt) + r"\b", norm)]
    for en in reversed(stated):
        if en in color_names:
            color_names.remove(en)
        color_names.insert(0, en)

    finish = next((en for pt, en in FINISH_WORDS.items() if normalize(pt) in norm), "")
    details = _details(norm)

    profile = ProductProfile(
        name=name or "produto",
        description=brief.strip()[:500],
        category=category,
        colors=colors,
        color_names=color_names[:5],
        finish=finish,
        details=details[:6],
        forbidden_lookalikes=LOOKALIKES.get(category, LOOKALIKES["geral"]),
        images=assets,
    )
    profile.lock_text = product_lock_text(profile)
    return profile


def _details(norm: str) -> list[str]:
    """Detalhes citados no texto, traduzidos para o prompt (ex.: 'detalhes dourados' -> 'gold details')."""
    found = []
    for word in re.findall(r"detalhes? (?:em |de )?(\w+)", norm):
        if word in COLOR_WORDS:
            found.append(f"{COLOR_WORDS[word]} details/accents in the same places as the reference")
    for word in re.findall(r"haste (\w+)", norm):
        found.append(f"{ADJECTIVES.get(word, 'same')} stem, exact same stem length")
    for word in re.findall(r"acabamento (\w+)", norm):
        if word in FINISH_WORDS:
            found.append(f"{FINISH_WORDS[word]} finish")
    return list(dict.fromkeys(found))


def product_lock_text(p: ProductProfile) -> str:
    views = sorted({a.role for a in p.images if a.kind == "image"})
    colors = ", ".join(p.color_names) if p.color_names else "exactly as in the reference photos"
    lines = [
        f"PRODUCT LOCK (the hero product must match the reference images exactly): the product is the user's "
        f"'{p.name}'" + (f" ({p.category})" if p.category != "geral" else "") + ".",
        f"Keep identical shape, silhouette, proportions, length of every part and scale relative to the scene.",
        f"Colors: {colors}; same hue, saturation and color placement — no color shift.",
    ]
    if p.finish:
        lines.append(f"Finish/material: {p.finish}, unchanged.")
    if p.details:
        lines.append("Specific details to preserve: " + "; ".join(p.details) + ".")
    if views:
        lines.append("Reference views provided: " + ", ".join(views) + ".")
    lines += [
        "Logo and markings: keep exactly as in the reference — do not redraw, move, add or remove any logo or text.",
        "Do not add buttons, lights, cables, parts, textures or details that are not in the reference.",
        "Never turn it into " + ", ".join(p.forbidden_lookalikes) + ".",
    ]
    return " ".join(lines)


# ------------------------------------------------------------------ referência

CAMERA_KEYWORDS = {
    "macro": ["macro", "close extremo", "extreme close", "detalhe"],
    "orbit": ["orbit", "orbita", "girando ao redor", "360", "gira em volta"],
    "dolly in": ["dolly in", "dolly"],
    "push-in": ["push in", "push-in", "aproxima", "aproximacao"],
    "pull-out": ["pull out", "pull-out", "afasta", "revela"],
    "zoom": ["zoom"],
    "rack focus": ["rack focus", "troca de foco", "foco seletivo"],
    "top-down": ["top down", "vista de cima", "de cima", "overhead", "flat lay"],
    "slider": ["slider", "lateral suave", "travelling"],
    "slow motion": ["slow motion", "camera lenta", "slowmo"],
    "exploded view": ["exploded", "explodido", "componentes internos", "desmontado"],
}


def detect_camera_moves(text: str) -> list[str]:
    norm = normalize(text)
    return [move for move, words in CAMERA_KEYWORDS.items() if any(normalize(w) in norm for w in words)]


def analyze_reference(settings: Settings, project_dir: Path, asset: Asset | None, brief: str) -> ReferenceAnalysis:
    moves_from_text = detect_camera_moves(brief)
    if asset is None:
        return ReferenceAnalysis(present=False, camera_moves=moves_from_text,
                                 notes=["Sem referência: linguagem visual definida pelo briefing."])
    path = project_dir / asset.path
    work = project_dir / "analysis" / "reference"
    work.mkdir(parents=True, exist_ok=True)

    if asset.kind == "image":
        palette = [c for c, _ in media.dominant_colors(path, count=5, ignore_background=False)]
        light = media.brightness(path)
        return ReferenceAnalysis(
            present=True, kind="image", palette=palette, brightness=light, key=_key(light),
            camera_moves=moves_from_text,
            notes=["Referência em imagem: usada para paleta, luz e composição."],
        )

    info = media.video_info(settings, path)
    duration = info["duration"] or 0.1
    cuts = media.detect_cuts(settings, path)
    shots = len(cuts) + 1
    avg_shot = duration / shots
    times = [duration * (i + 0.5) / 8 for i in range(8)]
    frames = [media.extract_frame(settings, path, t, work / f"frame_{i:02d}.png") for i, t in enumerate(times)]
    light = sum(media.brightness(f) for f in frames) / len(frames)
    palette_weights: dict[str, float] = {}
    for f in frames[::2]:
        for c, share in media.dominant_colors(f, count=4, ignore_background=False):
            palette_weights[c] = palette_weights.get(c, 0) + share
    palette = [c for c, _ in sorted(palette_weights.items(), key=lambda kv: -kv[1])][:5]
    motion = media.motion_intensity(frames)
    pace = "rapido" if avg_shot < 1.5 else "lento" if avg_shot > 4 else "medio"
    moves = list(moves_from_text)
    if motion > 0.35 and "orbit" not in moves:
        moves.append("dynamic camera movement")
    if motion < 0.08 and not moves:
        moves.append("slow push-in")
    notes = [f"{shots} planos em {duration:.1f}s (média {avg_shot:.1f}s por plano) → ritmo {pace}.",
             f"Brilho médio {light:.2f} → iluminação {_key(light)}.",
             f"Intensidade de movimento {motion:.2f}."]
    return ReferenceAnalysis(
        present=True, kind="video", duration=round(duration, 3), fps=round(info["fps"], 3), cuts=cuts,
        avg_shot_length=round(avg_shot, 3), pace=pace, brightness=round(light, 3), key=_key(light),
        palette=palette, motion_intensity=motion, camera_moves=moves,
        transitions="cut" if pace == "rapido" else "fade", notes=notes,
    )


def _key(light: float) -> str:
    return "low-key" if light < 0.35 else "high-key" if light > 0.65 else "medio"
