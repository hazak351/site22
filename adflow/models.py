"""Modelos de dados persistidos em data/projects/<id>/project.json."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class SceneStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    checking = "checking"
    approved = "approved"
    warning = "warning"  # aprovada com alertas (melhor tentativa disponível)
    failed = "failed"


class ProjectStatus(str, Enum):
    draft = "draft"
    running = "running"
    interrupted = "interrupted"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


STAGES: list[tuple[str, str]] = [
    ("product_analysis", "Produto analisado"),
    ("reference_analysis", "Referência analisada"),
    ("direction", "Direção criativa"),
    ("planning", "Planejamento das cenas"),
    ("prompts", "Prompts gerados"),
    ("flow_check", "Flow Doctor"),
    ("generation", "Geração das cenas"),
    ("editing", "Montagem"),
    ("export", "Exportação"),
]


class Asset(BaseModel):
    path: str  # relativo à pasta do projeto
    original_name: str = ""
    kind: str = "image"  # image | video
    role: str = "geral"  # frente | lateral | 3/4 | detalhe | geral


class ProductProfile(BaseModel):
    name: str = "produto"
    description: str = ""
    category: str = "geral"
    colors: list[str] = Field(default_factory=list)  # hex dominantes (sem fundo)
    color_names: list[str] = Field(default_factory=list)
    finish: str = ""
    details: list[str] = Field(default_factory=list)
    forbidden_lookalikes: list[str] = Field(default_factory=list)
    images: list[Asset] = Field(default_factory=list)
    lock_text: str = ""


class ReferenceAnalysis(BaseModel):
    present: bool = False
    kind: str = "none"  # video | image | none
    method: str = "heuristica-local"
    duration: float = 0.0
    fps: float = 0.0
    cuts: list[float] = Field(default_factory=list)
    avg_shot_length: float = 0.0
    pace: str = "medio"  # lento | medio | rapido
    brightness: float = 0.5
    key: str = "medio"  # low-key | medio | high-key
    palette: list[str] = Field(default_factory=list)
    motion_intensity: float = 0.0
    camera_moves: list[str] = Field(default_factory=list)
    transitions: str = "fade"
    notes: list[str] = Field(default_factory=list)


class EnvironmentMaster(BaseModel):
    setting: str = ""
    surface: str = ""
    background: str = ""
    light_direction: str = ""
    light_temperature: str = ""
    shadows: str = ""
    depth: str = ""
    palette: str = ""
    atmosphere: str = ""
    materials: str = ""
    reference_image: str | None = None  # frame da cena 1 usado como referência de ambiente

    def lock_text(self) -> str:
        return (
            "ENVIRONMENT LOCK (identical in every shot, never change): "
            f"setting: {self.setting}; surface: {self.surface}; background: {self.background}; "
            f"key light: {self.light_direction}, {self.light_temperature}; shadows: {self.shadows}; "
            f"depth: {self.depth}; palette: {self.palette}; atmosphere: {self.atmosphere}; "
            f"materials: {self.materials}. Do not introduce other tables, rooms, props, "
            "backgrounds or light sources."
        )


class CreativeDirection(BaseModel):
    style: str = "premium"
    pace: str = "medio"
    color_grade: str = ""
    lens: str = ""
    lighting: str = ""
    music_mood: str = ""
    transition: str = "fade"  # cut | fade | fadeblack
    camera_language: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    headline: str = ""
    cta: str = ""
    notes: list[str] = Field(default_factory=list)


class QualityCheck(BaseModel):
    name: str
    status: str  # ok | aviso | falha | nao_avaliado
    score: float | None = None
    detail: str = ""


class QualityReport(BaseModel):
    passed: bool = False
    score: float = 0.0
    checks: list[QualityCheck] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)  # reforços para o prompt da regeneração


class Attempt(BaseModel):
    number: int
    engine: str
    started_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
    status: str = "running"  # running | ok | falha | interrompida
    video: str | None = None
    credits: int = 0
    error: str | None = None
    prompt: str = ""
    quality: QualityReport | None = None


class Scene(BaseModel):
    index: int
    beat: str
    target_duration: float
    generation_duration: float
    trim_start: float = 0.0
    camera: str = ""
    shot: str = ""
    start_state: str = ""
    end_state: str = ""
    prompt: str = ""
    reference_images: list[str] = Field(default_factory=list)
    start_frame: str | None = None
    status: SceneStatus = SceneStatus.pending
    attempts: list[Attempt] = Field(default_factory=list)
    approved_attempt: int | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def approved_video(self) -> str | None:
        for attempt in self.attempts:
            if attempt.number == self.approved_attempt:
                return attempt.video
        return None


class CreditPlan(BaseModel):
    mode: str
    mode_label: str = ""
    model_hint: str = ""
    credits_per_generation: int
    outputs_per_prompt: int = 1
    scenes: int
    durations: list[float] = Field(default_factory=list)
    estimated: int
    worst_case: int
    max_credits: int
    max_attempts_per_scene: int
    used: int = 0
    simulated: bool = False


class Stage(BaseModel):
    key: str
    label: str
    status: StageStatus = StageStatus.pending
    started_at: str | None = None
    finished_at: str | None = None
    detail: str = ""


class Project(BaseModel):
    id: str
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    status: ProjectStatus = ProjectStatus.draft
    engine: str = "simulator"  # simulator | flow
    brief: str
    duration: float
    aspect: str  # 9:16 | 16:9 | 1:1
    quality: str  # economico | equilibrado | premium
    product_name: str = ""
    product_assets: list[Asset] = Field(default_factory=list)
    reference: Asset | None = None
    logo: Asset | None = None
    music: Asset | None = None
    cta: str = ""
    max_credits: int | None = None
    product_profile: ProductProfile | None = None
    reference_analysis: ReferenceAnalysis | None = None
    direction: CreativeDirection | None = None
    environment: EnvironmentMaster | None = None
    scenes: list[Scene] = Field(default_factory=list)
    credits: CreditPlan | None = None
    credits_confirmed: bool = False
    stages: list[Stage] = Field(default_factory=lambda: [Stage(key=k, label=l) for k, l in STAGES])
    current_stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_s: float = 0.0
    final_video: str | None = None
    final_report: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    def stage(self, key: str) -> Stage:
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(key)
