"""Diretor criativo: transforma briefing + análises em direção, Environment Master e cenas.

A referência é tratada como LINGUAGEM VISUAL (ritmo, luz, paleta, movimento), nunca copiada.
"""

from __future__ import annotations

from .analysis import detect_camera_moves, normalize
from .models import CreativeDirection, EnvironmentMaster, ProductProfile, ReferenceAnalysis, Scene
from .planner import generation_duration_for

STYLE_KEYWORDS = {
    "premium": ["premium", "luxo", "luxuoso", "sofisticad", "elegante", "high end", "high-end", "cinematic",
                "cinematografic"],
    "energetico": ["energetic", "energetico", "dinamico", "rapido", "esportivo", "impactante", "explosivo"],
    "minimalista": ["minimalista", "minimal", "clean", "limpo", "simples"],
    "apetitoso": ["apetitoso", "suculento", "delicioso", "crocante", "derretendo", "saboroso"],
    "fresco": ["fresco", "refrescante", "gelado", "natural"],
}

CATEGORY_STYLE = {"comida": "apetitoso", "bebida": "fresco", "perfume": "premium", "cosmetico": "premium",
                  "audio": "premium", "eletronico": "premium", "moda": "energetico", "geral": "premium"}

STYLE_LOOK = {
    "premium": dict(grade="rich contrast, deep blacks, subtle warm highlights, clean specular reflections",
                    lens="100mm macro and 50mm cinema lens", lighting="low-key dramatic studio lighting",
                    music="cinematic minimal electronic with deep bass pulses"),
    "energetico": dict(grade="punchy contrast, saturated colors", lens="24mm to 50mm, dynamic",
                       lighting="bold high-contrast lighting with colored rim lights",
                       music="upbeat electronic, strong beat"),
    "minimalista": dict(grade="soft, clean, neutral tones", lens="85mm product lens",
                        lighting="soft diffused high-key lighting", music="light minimal piano/electronic"),
    "apetitoso": dict(grade="warm, rich, appetizing tones", lens="100mm macro",
                      lighting="warm side light that makes textures glisten", music="groovy warm beat"),
    "fresco": dict(grade="cool crisp tones with bright highlights", lens="100mm macro",
                   lighting="bright backlight with crisp rim light", music="fresh upbeat pop"),
}


def _match(norm: str, words: list[str]) -> bool:
    return any(w in norm for w in words)


def detect_style(brief: str, category: str) -> str:
    norm = normalize(brief)
    for style, words in STYLE_KEYWORDS.items():
        if _match(norm, words):
            return style
    return CATEGORY_STYLE.get(category, "premium")


def build_direction(brief: str, product: ProductProfile, reference: ReferenceAnalysis, cta: str = "") -> CreativeDirection:
    style = detect_style(brief, product.category)
    look = STYLE_LOOK[style]
    norm = normalize(brief)
    pace = reference.pace if reference.present else ("rapido" if style == "energetico" else "lento" if style in ("premium", "minimalista") else "medio")
    if "rapido" in norm or "dinamico" in norm:
        pace = "rapido"
    transition = "cut" if pace == "rapido" else "fade"
    if reference.present and reference.transitions == "cut":
        transition = "cut"
    camera = list(dict.fromkeys(detect_camera_moves(brief) + reference.camera_moves))
    notes = []
    if reference.present:
        notes.append("Referência usada como linguagem visual (ritmo, luz, paleta, movimento), sem copiar conteúdo.")
        notes += reference.notes
    return CreativeDirection(
        style=style, pace=pace, color_grade=look["grade"], lens=look["lens"], lighting=look["lighting"],
        music_mood=look["music"], transition=transition, camera_language=camera, cta=cta.strip(), notes=notes,
    )


# ------------------------------------------------------------ Environment Master

ENV_PRESETS = {
    "dark_tech": dict(setting="minimal dark product studio stage", surface="matte black acrylic surface with a soft reflection",
                      background="deep graphite seamless backdrop with a subtle center gradient",
                      light_direction="soft key light from the upper left at 45 degrees plus a thin rim light from behind right",
                      light_temperature="neutral 5600K key with a subtle warm rim",
                      shadows="soft controlled shadows falling to the right", depth="shallow depth of field, background falls off to black",
                      atmosphere="premium, sleek, very light haze", materials="black acrylic, graphite"),
    "light_studio": dict(setting="clean bright product studio", surface="seamless white matte surface",
                         background="soft white-to-light-gray seamless backdrop",
                         light_direction="large soft key light from the front left with soft fill from the right",
                         light_temperature="neutral 5600K daylight", shadows="very soft, short contact shadows",
                         depth="moderate depth of field", atmosphere="clean, airy, minimal", materials="white matte paper sweep"),
    "food_counter": dict(setting="dark restaurant counter set", surface="black slate countertop (no wood)",
                         background="out-of-focus dark graphite wall", light_direction="warm side light from the left",
                         light_temperature="warm 3200K", shadows="soft natural shadows falling to the right",
                         depth="shallow depth of field", atmosphere="appetizing, gentle steam rising", materials="black slate, graphite"),
    "drink_bar": dict(setting="dark studio bar set", surface="wet black glossy surface with small water droplets",
                      background="dark gradient with a soft backlight glow", light_direction="strong backlight with a crisp side key from the right",
                      light_temperature="cool 6000K", shadows="crisp shadows toward the camera",
                      depth="shallow depth of field", atmosphere="refreshing, condensation droplets", materials="glossy black stone, water droplets"),
    "luxury": dict(setting="luxury studio display", surface="polished black marble slab with subtle veins",
                   background="soft dark gradient in warm neutral tones",
                   light_direction="soft top key light with gentle rim light from behind left",
                   light_temperature="neutral-warm 4500K", shadows="soft elegant shadows",
                   depth="shallow depth of field with soft bokeh", atmosphere="elegant, soft haze", materials="black marble, brushed metal"),
}

CATEGORY_ENV = {"audio": "dark_tech", "eletronico": "dark_tech", "comida": "food_counter", "bebida": "drink_bar",
                "perfume": "luxury", "cosmetico": "luxury", "moda": "light_studio", "geral": "dark_tech"}


def build_environment(brief: str, product: ProductProfile, reference: ReferenceAnalysis,
                      direction: CreativeDirection) -> EnvironmentMaster:
    norm = normalize(brief)
    preset = CATEGORY_ENV.get(product.category, "dark_tech")
    wants_dark = _match(norm, ["fundo escuro", "fundo preto", "fundo grafite", "dark background", "escuro", "noturno"])
    wants_light = _match(norm, ["fundo branco", "fundo claro", "white background", "claro e limpo"])
    if wants_light:
        preset = "light_studio"
    elif wants_dark and preset == "light_studio":
        preset = "dark_tech"
    elif not wants_dark and reference.present and reference.key == "high-key":
        preset = "light_studio"
    env = EnvironmentMaster(**ENV_PRESETS[preset], palette="")
    if "bancada preta" in norm:
        env.surface = "black countertop"
    if "madeira" in norm and "sem madeira" not in norm:
        env.surface = "dark wooden surface"
        env.materials = "dark wood"
    product_colors = ", ".join(product.color_names[:3]) or "product colors"
    ref_palette = f"; reference tones {', '.join(reference.palette[:3])}" if reference.palette else ""
    base = "graphite and black" if preset != "light_studio" else "white and light gray"
    env.palette = f"{base} with accents matching the product ({product_colors}){ref_palette}"
    return env


# ------------------------------------------------------------ cenas / continuidade

BEATS = {
    "hook_macro": dict(shot="Extreme macro reveal: light slowly sweeps across the product's signature detail emerging from darkness",
                       camera="slow macro push-in with a gentle rack focus", start="macro framing on a product detail, mostly in shadow",
                       end="product fully revealed, centered, three-quarter view facing right, filling about 40% of the frame"),
    "food_hook": dict(shot="Extreme macro of the food's textures: sauce, crunch and steam in slow motion",
                      camera="slow macro slider move with rack focus", start="macro on the most appetizing texture",
                      end="the whole dish centered, three-quarter view, filling about 45% of the frame"),
    "orbit": dict(shot="Smooth orbit around the product revealing its silhouette, materials and finish",
                  camera="slow 90-degree orbit from left to right at constant speed",
                  start="product centered, three-quarter view facing right", end="product centered, side profile facing right"),
    "detail": dict(shot="Close-up pass along the product's materials, edges and finish",
                   camera="slow lateral slider move left to right with shallow focus",
                   start="close-up on the product's side", end="product centered, three-quarter view facing right"),
    "exploded": dict(shot="Exploded view: the product's parts separate precisely in mid-air revealing internal components, then reassemble perfectly into the exact original product",
                     camera="locked-off camera with a very slow push-in", start="product centered, three-quarter view facing right",
                     end="product fully reassembled, identical to the reference, centered"),
    "top_down": dict(shot="Top-down overhead shot of the product on the surface", camera="slow overhead rotation",
                     start="overhead view, product centered", end="overhead view, product centered"),
    "slow_motion": dict(shot="Slow-motion hero moment with subtle particles in the light", camera="slow push-in",
                        start="product centered, three-quarter view facing right", end="product centered, three-quarter view facing right"),
    "hero_float": dict(shot="Hero finale: the product floats and rotates slowly at the center of frame with soft light sweeps, clean empty space above for the logo added in post",
                       camera="slow pull-out to the final hero framing, then hold steady for the last second",
                       start="product centered, three-quarter view facing right", end="product floating centered, front three-quarter view, stable hero framing"),
    "hero_static": dict(shot="Hero finale: the product rests perfectly lit at the center of frame, clean empty space above for the logo added in post",
                        camera="slow push-in that settles into a stable hero framing", start="product centered, three-quarter view facing right",
                        end="product centered, stable hero framing"),
}

BEAT_REQUESTS = [
    ("exploded", ["exploded", "explodido", "componentes internos", "desmontad"]),
    ("orbit", ["orbit", "orbita", "360", "girando", "gira em volta"]),
    ("top_down", ["top down", "vista de cima", "de cima", "overhead", "flat lay"]),
    ("slow_motion", ["slow motion", "camera lenta", "slowmo"]),
    ("detail", ["detalhe", "textura", "acabamento", "close"]),
]


def choose_beats(brief: str, category: str, count: int) -> list[str]:
    norm = normalize(brief)
    hook = "food_hook" if category in ("comida", "bebida") else "hook_macro"
    final = "hero_float" if _match(norm, ["flutuando", "flutua", "floating", "levitando", "levita"]) else "hero_static"
    if count == 1:
        return [final]
    requested = [beat for beat, words in BEAT_REQUESTS if _match(norm, words)]
    middle_slots = count - 2
    middle = requested[:middle_slots]
    fillers = ["orbit", "detail", "slow_motion", "top_down"]
    i = 0
    while len(middle) < middle_slots:
        candidate = fillers[i % len(fillers)]
        if candidate not in middle or i >= len(fillers):
            middle.append(candidate)
        i += 1
    return [hook, *middle, final]


def plan_scenes(durations: list[float], generation_durations: list[float], brief: str,
                product: ProductProfile) -> list[Scene]:
    beats = choose_beats(brief, product.category, len(durations))
    product_images = [a.path for a in product.images if a.kind == "image"]
    scenes: list[Scene] = []
    previous_end = ""
    for index, (duration, beat) in enumerate(zip(durations, beats), start=1):
        spec = BEATS[beat]
        start_state = previous_end or spec["start"]
        scenes.append(Scene(
            index=index, beat=beat, target_duration=duration,
            generation_duration=generation_duration_for(duration, generation_durations),
            camera=spec["camera"], shot=spec["shot"], start_state=start_state, end_state=spec["end"],
            reference_images=product_images[:4],
        ))
        previous_end = spec["end"]
    return scenes
