"""API FastAPI: criação com upload, estimativa, confirmação de créditos, execução, download e segurança."""

import time

import pytest
from fastapi.testclient import TestClient

from adflow.api import create_app
from adflow.models import Project, ProjectStatus
from adflow.storage import ProjectStore
from tests.conftest import needs_ffmpeg


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.app_state = app.state
        yield test_client


def _create(client, product_image, **fields):
    data = {"brief": "Anúncio premium do fone preto com detalhes dourados", "duration": "4",
            "aspect": "1:1", "quality": "equilibrado", "engine": "simulator", "cta": "Compre agora"}
    data.update(fields)
    files = [("product_files", (product_image.name, product_image.read_bytes(), "image/png"))]
    return client.post("/api/projects", data=data, files=files)


def test_health_config_and_frontend(client):
    health = client.get("/api/health").json()
    assert health["version"] and "ffmpeg" in health
    config = client.get("/api/config").json()
    assert config["aspects"] == ["9:16", "16:9", "1:1"]
    assert set(config["modes"]) == {"economico", "equilibrado", "premium"}
    page = client.get("/")
    assert page.status_code == 200 and "CRIAR VÍDEO AUTOMATICAMENTE".lower() in page.text.lower()


def test_estimate_endpoint(client):
    plan = client.post("/api/estimate", json={"duration": 10, "quality": "premium", "engine": "flow"}).json()
    assert plan["scenes"] == 3 and plan["estimated"] == 300 and plan["within_budget"]
    low = client.post("/api/estimate", json={"duration": 10, "quality": "premium", "max_credits": 50}).json()
    assert not low["within_budget"]
    assert client.post("/api/estimate", json={"duration": 0}).status_code == 400


def test_create_requires_product_and_valid_inputs(client, product_image):
    bad = client.post("/api/projects", data={"brief": "x", "duration": "4"},
                      files=[("product_files", ("notas.txt", b"oi", "text/plain"))])
    assert bad.status_code == 400
    assert _create(client, product_image, aspect="4:3").status_code == 400
    assert _create(client, product_image, brief="   ").status_code == 400


@needs_ffmpeg
def test_full_run_through_api(client, product_image, logo_image):
    files = [("product_files", (product_image.name, product_image.read_bytes(), "image/png")),
             ("logo", ("logo.png", logo_image.read_bytes(), "image/png"))]
    created = client.post("/api/projects", data={"brief": "fone premium fundo escuro", "duration": "4", "aspect": "1:1",
                                                 "quality": "equilibrado", "engine": "simulator"}, files=files)
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    assert client.post(f"/api/projects/{project_id}/start", json={}).json()["started"]
    client.app_state.runner.wait(120)
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["status"] == "done", project["error"]
    video = client.get(f"/api/projects/{project_id}/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4" and len(video.content) > 10000
    assert client.get(f"/api/projects/{project_id}/log").json()["lines"]
    assert client.post(f"/api/projects/{project_id}/resume").status_code == 409
    listed = client.get("/api/projects").json()
    assert listed[0]["id"] == project_id


def test_real_flow_start_requires_confirmation(client, product_image):
    project_id = _create(client, product_image, engine="flow").json()["id"]
    response = client.post(f"/api/projects/{project_id}/start", json={})
    assert response.status_code == 412
    assert response.json()["detail"]["credits"]["estimated"] > 0
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "draft"


def test_start_rejects_estimate_above_ceiling(client, product_image):
    project_id = _create(client, product_image, max_credits="5").json()["id"]
    response = client.post(f"/api/projects/{project_id}/start", json={})
    assert response.status_code == 400 and "teto" in response.json()["detail"]


def test_file_endpoint_blocks_path_traversal(client, product_image):
    project_id = _create(client, product_image).json()["id"]
    assert client.get(f"/api/projects/{project_id}/file/project.json").status_code == 200
    assert client.get(f"/api/projects/{project_id}/file/../../../etc/passwd").status_code == 404
    assert client.get("/api/projects/..%2F..%2Fetc").status_code in (400, 404)


def test_doctor_endpoint_simulator(client):
    report = client.post("/api/doctor", json={"engine": "simulator"}).json()
    assert report["ok"] and report["real"] is False
    assert "SIMULADOR" in report["message"]


def test_orphan_running_projects_marked_interrupted_on_start(settings):
    store = ProjectStore(settings)
    orphan = Project(id=store.new_id(), brief="x", duration=4, aspect="9:16", quality="equilibrado",
                     status=ProjectStatus.running)
    store.save(orphan)
    app = create_app(settings)
    assert app.state.interrupted_on_start == [orphan.id]
    assert store.load(orphan.id).status == ProjectStatus.interrupted
