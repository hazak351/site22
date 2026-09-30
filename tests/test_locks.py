"""Product Lock, Environment Lock, Continuity Lock, direção criativa e prompts."""

from adflow import analysis, director, prompts
from adflow.models import Asset, ReferenceAnalysis

BRIEF = ("Crie um anúncio de 24 segundos desse fone preto com detalhes dourados. Quero estilo premium, fundo escuro, "
         "câmera cinematográfica, exploded view mostrando componentes internos e final com produto flutuando e logo.")


def _profile(settings, product_image, brief=BRIEF, name="Fone X"):
    asset = Asset(path=product_image.name, original_name=product_image.name)
    return analysis.analyze_product(settings, product_image.parent, name, brief, [asset])


def test_product_lock_preserves_identity(settings, product_image):
    profile = _profile(settings, product_image)
    assert profile.category == "audio"
    assert profile.color_names[:2] == ["black", "gold"]
    assert profile.images[0].role == "frente"
    lock = profile.lock_text
    for phrase in ("PRODUCT LOCK", "shape", "proportions", "black", "gold", "Logo", "Do not add",
                   "Apple AirPods", "Fone X", "gold details"):
        assert phrase in lock, phrase


def test_detected_colors_come_from_the_image(settings, product_image):
    profile = _profile(settings, product_image, brief="Anúncio do meu produto", name="Produto")
    assert "gold" in profile.color_names
    assert "black" in profile.color_names or "dark gray" in profile.color_names


def test_image_roles_from_filenames():
    assert analysis.guess_role("fone_lateral.png", 0) == "lateral"
    assert analysis.guess_role("detalhe-haste.jpg", 0) == "detalhe"
    assert analysis.guess_role("IMG_001.jpg", 2) == "lateral"


def test_categories():
    assert analysis.detect_category("hambúrguer de bacon suculento") == "comida"
    assert analysis.detect_category("perfume Y em frasco de vidro") == "perfume"
    assert analysis.detect_category("latinha de refrigerante gelada") == "bebida"
    assert analysis.detect_category("Fone X") == "audio"


def test_environment_master_dark_premium(settings, product_image):
    profile = _profile(settings, product_image)
    ref = ReferenceAnalysis()
    direction = director.build_direction(BRIEF, profile, ref)
    env = director.build_environment(BRIEF, profile, ref, direction)
    assert direction.style == "premium"
    assert "graphite" in env.background
    lock = env.lock_text()
    for field in ("setting", "surface", "background", "key light", "shadows", "depth", "palette", "atmosphere", "materials"):
        assert field in lock
    assert "never change" in lock


def test_environment_master_food_never_wood(settings, product_image):
    profile = _profile(settings, product_image, brief="hambúrguer bacon sobre bancada preta, luz quente lateral",
                       name="Hambúrguer Bacon")
    assert profile.category == "comida"
    direction = director.build_direction("hambúrguer", profile, ReferenceAnalysis())
    env = director.build_environment("hambúrguer bacon sobre bancada preta", profile, ReferenceAnalysis(), direction)
    assert env.surface == "black countertop"
    assert "warm" in env.light_temperature
    assert "Do not introduce other tables" in env.lock_text()


def test_environment_light_background_request(settings, product_image):
    profile = _profile(settings, product_image, brief="fundo branco, clean")
    env = director.build_environment("fundo branco, clean", profile, ReferenceAnalysis(),
                                     director.build_direction("fundo branco, clean", profile, ReferenceAnalysis()))
    assert "white" in env.background


def test_scene_plan_follows_brief_and_continuity(settings, product_image):
    profile = _profile(settings, product_image)
    scenes = director.plan_scenes([8.0, 8.0, 8.0], [8.0], BRIEF, profile)
    assert [s.beat for s in scenes] == ["hook_macro", "exploded", "hero_float"]
    for previous, current in zip(scenes, scenes[1:]):
        assert current.start_state == previous.end_state  # Continuity Lock
    assert all(s.reference_images for s in scenes)


def test_every_prompt_carries_all_locks(settings, product_image):
    profile = _profile(settings, product_image)
    ref = ReferenceAnalysis()
    direction = director.build_direction(BRIEF, profile, ref)
    env = director.build_environment(BRIEF, profile, ref, direction)
    scenes = director.plan_scenes([8.0, 8.0, 8.0], [8.0], BRIEF, profile)
    texts = [prompts.build_prompt(s, 3, "9:16", direction, profile, env) for s in scenes]
    env_lock = env.lock_text()
    for text in texts:
        assert profile.lock_text in text
        assert env_lock in text  # ambiente idêntico em todas as cenas
        assert "CONTINUITY LOCK" in text
        assert "vertical 9:16" in text
        assert "on-screen text" in text and "generated logos" in text  # texto/logo só na pós
    fixed = prompts.build_prompt(scenes[1], 3, "9:16", direction, profile, env, fixes=["keep the same set"])
    assert fixed.endswith("CORRECTIONS FOR THIS TAKE (highest priority): keep the same set")


def test_square_is_generated_wide_and_cropped():
    assert prompts.generation_aspect("1:1") == "16:9"
    assert prompts.generation_aspect("9:16") == "9:16"
    assert "1:1" in prompts.ASPECT_TEXT["1:1"]


def test_reference_video_analysis(settings, clip_factory, tmp_path):
    clip = clip_factory(4.0)
    asset = Asset(path=clip.name, original_name=clip.name, kind="video")
    ref = analysis.analyze_reference(settings, clip.parent, asset, "com orbit e rack focus")
    assert ref.present and ref.kind == "video"
    assert abs(ref.duration - 4.0) < 0.2
    assert ref.key == "low-key"
    assert "orbit" in ref.camera_moves and "rack focus" in ref.camera_moves
    assert ref.palette
    assert ref.notes
