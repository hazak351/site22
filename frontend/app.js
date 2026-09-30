"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const BEATS = {
  hook_macro: "abertura macro", food_hook: "abertura macro", orbit: "órbita", detail: "detalhes",
  exploded: "exploded view", top_down: "vista de cima", slow_motion: "câmera lenta",
  hero_float: "final flutuando", hero_static: "final hero",
};
const STATUS = {
  draft: "rascunho", running: "gerando", interrupted: "interrompido", done: "pronto", failed: "falhou", cancelled: "cancelado",
};

const state = {
  config: null,
  duration: 15,
  custom: false,
  aspect: "9:16",
  quality: "equilibrado",
  engine: "simulator",
  projectId: null,
  pollTimer: null,
  pollCount: 0,
};

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { "Content-Type": "application/json" };
    delete opts.json;
  }
  const res = await fetch(path, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail !== undefined ? data.detail : data;
    const message = typeof detail === "string" ? detail : (detail && detail.message) || JSON.stringify(detail);
    const error = new Error(message);
    error.status = res.status;
    error.detail = detail;
    throw error;
  }
  return data;
}

function formatTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m}min ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

/* ---------------------------------------------------------------- chips */

function renderChips(container, options, selected, onPick) {
  container.innerHTML = "";
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", String(option.value === selected));
    button.innerHTML = esc(option.label) + (option.hint ? `<small>${esc(option.hint)}</small>` : "");
    button.addEventListener("click", () => {
      container.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-checked", "false"));
      button.setAttribute("aria-checked", "true");
      onPick(option.value);
    });
    container.append(button);
  }
}

function setupChips() {
  const cfg = state.config;
  const durations = cfg.durations.map((d) => ({ value: d, label: `${d}s` }));
  durations.push({ value: "custom", label: "Personalizada" });
  renderChips($("#durations"), durations, state.duration, (value) => {
    state.custom = value === "custom";
    $("#custom-duration").hidden = !state.custom;
    if (state.custom) $("#custom-duration").focus();
    else state.duration = value;
    updateEstimate();
  });
  renderChips($("#aspects"), [
    { value: "9:16", label: "9:16", hint: "1080×1920" },
    { value: "16:9", label: "16:9", hint: "1920×1080" },
    { value: "1:1", label: "1:1", hint: "1080×1080" },
  ], state.aspect, (value) => { state.aspect = value; });
  renderChips($("#qualities"), Object.entries(cfg.modes).map(([key, mode]) => ({
    value: key, label: mode.label, hint: `${mode.credits_per_generation} créd./cena`,
  })), state.quality, (value) => { state.quality = value; updateEstimate(); });
  renderChips($("#engines"), [
    { value: "simulator", label: "Simulador", hint: "teste offline, sem créditos" },
    { value: "flow", label: "Google Flow (real)", hint: "usa créditos da sua conta" },
  ], state.engine, (value) => { state.engine = value; updateEstimate(); });
}

function currentDuration() {
  if (!state.custom) return Number(state.duration);
  const value = Number($("#custom-duration").value);
  return Number.isFinite(value) && value > 0 ? value : null;
}

let estimateTimer = null;
function updateEstimate() {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(async () => {
    const box = $("#estimate");
    const duration = currentDuration();
    if (!duration) {
      box.innerHTML = "<span>Informe a duração personalizada.</span>";
      return;
    }
    const maxCredits = Number($("#max_credits").value) || null;
    try {
      const plan = await api("/api/estimate", {
        method: "POST",
        json: { duration, quality: state.quality, engine: state.engine, max_credits: maxCredits },
      });
      const simulated = state.engine !== "flow";
      box.innerHTML = `
        <span>Vídeo: <strong>${duration}s</strong> · Plano: <strong>${plan.durations.map((d) => `${d}s`).join(" + ")}</strong>
        (${plan.scenes} ${plan.scenes === 1 ? "cena" : "cenas"})</span>
        <span>Estimativa: <strong>${plan.estimated} créditos</strong> · Máximo permitido: <strong>${plan.max_credits}</strong>
        · Tentativas por cena: <strong>${plan.max_attempts_per_scene}</strong></span>
        ${simulated ? '<span class="muted">Simulador: nenhum crédito real é usado.</span>' : ""}
        ${plan.within_budget ? "" : '<span class="warn">A estimativa passa do teto definido.</span>'}`;
    } catch (err) {
      box.textContent = err.message;
    }
  }, 200);
}

/* ---------------------------------------------------------------- arquivos */

function previewFiles(input, target) {
  input.addEventListener("change", () => {
    target.innerHTML = "";
    for (const file of input.files) {
      if (file.type.startsWith("image/")) {
        const img = document.createElement("img");
        img.alt = file.name;
        img.src = URL.createObjectURL(file);
        target.append(img);
      } else {
        const box = document.createElement("div");
        box.className = "file";
        box.textContent = file.name.slice(0, 18);
        target.append(box);
      }
    }
  });
}

/* ---------------------------------------------------------------- criar */

async function onSubmit(event) {
  event.preventDefault();
  const errorBox = $("#form-error");
  errorBox.textContent = "";
  const duration = currentDuration();
  if (!duration) {
    errorBox.textContent = "Informe a duração.";
    return;
  }
  const form = new FormData();
  form.append("brief", $("#brief").value);
  form.append("duration", String(duration));
  form.append("aspect", state.aspect);
  form.append("quality", state.quality);
  form.append("engine", state.engine);
  form.append("product_name", $("#product_name").value);
  form.append("cta", $("#cta").value);
  form.append("max_credits", $("#max_credits").value);
  for (const file of $("#product_files").files) form.append("product_files", file);
  for (const id of ["reference", "logo", "music"]) {
    const file = $("#" + id).files[0];
    if (file) form.append(id, file);
  }
  const button = $("#btn-create");
  button.disabled = true;
  try {
    const project = await api("/api/projects", { method: "POST", body: form });
    if (project.engine === "flow") {
      const confirmed = await confirmCredits(project);
      if (!confirmed) {
        errorBox.textContent = "Geração cancelada: nenhum crédito foi usado. O projeto ficou salvo como rascunho.";
        loadProjects();
        return;
      }
      await api(`/api/projects/${project.id}/start`, { method: "POST", json: { confirm_credits: true } });
    } else {
      await api(`/api/projects/${project.id}/start`, { method: "POST", json: {} });
    }
    openProject(project.id);
  } catch (err) {
    errorBox.textContent = err.message;
  } finally {
    button.disabled = false;
  }
}

function confirmCredits(project) {
  const plan = project.credits;
  $("#confirm-body").innerHTML = `
    <p>O AdFlow vai abrir o Google Flow no navegador de automação e gerar <strong>${plan.scenes} cenas</strong>
    (${plan.durations.map((d) => d + "s").join(" + ")}) no modo <strong>${esc(plan.mode_label)}</strong>
    (${esc(plan.model_hint)}).</p>
    <ul>
      <li>Estimativa: <strong>${plan.estimated} créditos</strong> (${plan.credits_per_generation} por geração)</li>
      <li>Tentativas por cena: até ${plan.max_attempts_per_scene} (regenera só a cena reprovada)</li>
      <li>Pior caso: ${plan.worst_case} créditos — o teto abaixo nunca é ultrapassado</li>
    </ul>
    <p class="muted">Antes de gastar qualquer crédito o Flow Doctor verifica a interface. Se algo falhar, nada é gerado.
    Valores de crédito vêm de config/credits.json e podem diferir do Flow.</p>`;
  $("#confirm-max").textContent = plan.max_credits;
  const check = $("#confirm-check");
  const ok = $("#confirm-ok");
  check.checked = false;
  ok.disabled = true;
  check.onchange = () => { ok.disabled = !check.checked; };
  const dialog = $("#confirm-dialog");
  return new Promise((resolve) => {
    dialog.onclose = () => resolve(dialog.returnValue === "ok" && check.checked);
    dialog.showModal();
  });
}

/* ---------------------------------------------------------------- progresso */

function openProject(id) {
  state.projectId = id;
  history.replaceState(null, "", `#project=${id}`);
  $("#create-card").hidden = true;
  $("#progress-card").hidden = false;
  $("#log").textContent = "";
  poll();
}

async function poll() {
  clearTimeout(state.pollTimer);
  if (!state.projectId) return;
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    renderProject(project);
    state.pollCount += 1;
    if (state.pollCount % 3 === 1 || !project.running) refreshLog();
    const active = project.running || project.status === "running" || project.status === "draft";
    state.pollTimer = setTimeout(poll, active ? 1000 : 5000);
    if (!active) loadProjects();
  } catch (err) {
    $("#alerts").innerHTML = `<div class="alert alert--err">${esc(err.message)}</div>`;
    state.pollTimer = setTimeout(poll, 3000);
  }
}

async function refreshLog() {
  if (!state.projectId) return;
  try {
    const data = await api(`/api/projects/${state.projectId}/log?lines=300`);
    const box = $("#log");
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    box.textContent = data.lines.join("\n");
    if (atBottom) box.scrollTop = box.scrollHeight;
  } catch (_) { /* ignora */ }
}

function sceneLine(scene) {
  const last = scene.attempts[scene.attempts.length - 1];
  const beat = BEATS[scene.beat] || scene.beat;
  const base = `Cena ${scene.index} · ${scene.target_duration}s · ${beat}`;
  switch (scene.status) {
    case "approved": return { cls: "approved", icon: "✓", text: base };
    case "warning": return { cls: "warning", icon: "!", text: `${base} — aprovada com alertas` };
    case "generating": return { cls: "running", icon: '<span class="spinner"></span>', text: `${base} — gerando… (tentativa ${last ? last.number : 1})` };
    case "checking": return { cls: "running", icon: '<span class="spinner"></span>', text: `${base} — Quality Gate…` };
    case "failed": return { cls: "failed", icon: "✕", text: `${base} — falhou` };
    default:
      if (last && last.status === "falha") return { cls: "pending", icon: "↻", text: `${base} — regenerando` };
      return { cls: "pending", icon: "•", text: `${base} — aguardando` };
  }
}

function renderProject(p) {
  const running = p.running || p.status === "running";
  $("#progress-title").textContent = p.status === "done" ? "Anúncio pronto" : running ? "Criando seu anúncio" : `Projeto ${STATUS[p.status] || p.status}`;
  const credits = p.credits || {};
  const stage = p.stages.find((s) => s.key === p.current_stage);
  let elapsed = p.elapsed_s;
  if (running && p.updated_at) elapsed += Math.max(0, (Date.now() - Date.parse(p.updated_at)) / 1000);
  const creditLabel = credits.simulated ? "créditos (simulados)" : "créditos";
  $("#metrics").innerHTML = `
    <div class="metric"><span>Etapa atual</span><b>${esc(stage ? stage.label : (STATUS[p.status] || p.status))}</b></div>
    <div class="metric"><span>Estimados</span><b>${credits.estimated ?? "—"}</b></div>
    <div class="metric"><span>Usados · ${creditLabel}</span><b>${credits.used ?? 0} / ${credits.max_credits ?? "—"}</b></div>
    <div class="metric"><span>Tempo decorrido</span><b>${formatTime(elapsed)}</b></div>
    <div class="metric"><span>Motor</span><b>${p.engine === "flow" ? "Flow real" : "Simulador"}</b></div>`;

  const list = $("#stages");
  list.innerHTML = "";
  for (const s of p.stages) {
    const li = document.createElement("li");
    li.className = `stage stage--${s.status}`;
    const icon = { done: "✓", failed: "✕", running: '<span class="spinner"></span>', skipped: "–" }[s.status] || "•";
    const label = s.status === "running" ? `${s.label}…` : s.label;
    li.innerHTML = `<span class="stage__icon">${icon}</span><span>${esc(label)}${s.detail ? `<span class="stage__detail">${esc(s.detail)}</span>` : ""}</span>`;
    if (s.key === "generation" && p.scenes.length) {
      const ul = document.createElement("ul");
      ul.className = "scenes";
      for (const scene of p.scenes) {
        const line = sceneLine(scene);
        const item = document.createElement("li");
        item.className = `scene--${line.cls}`;
        item.innerHTML = `${line.icon} ${esc(line.text)}`;
        ul.append(item);
      }
      li.lastElementChild.append(ul);
    }
    list.append(li);
  }

  const alerts = [];
  if (p.error) {
    const flowError = p.final_report && p.final_report.last_flow_error;
    let extra = "";
    if (flowError && flowError.screenshot) {
      const name = flowError.screenshot.split(/[\\/]/).pop();
      extra = ` <a href="/api/diagnostics/${encodeURIComponent(name)}" target="_blank" rel="noopener">ver screenshot</a>`;
    }
    alerts.push(`<div class="alert alert--err"><strong>Erro:</strong> ${esc(p.error)}${extra}</div>`);
  }
  for (const w of p.warnings || []) alerts.push(`<div class="alert alert--warn">${esc(w)}</div>`);
  if (p.engine !== "flow") alerts.push('<div class="alert alert--info">SIMULADOR: as cenas são sintéticas (FFmpeg). Nada foi enviado ao Google Flow.</div>');
  $("#alerts").innerHTML = alerts.join("");

  const actions = $("#actions");
  actions.innerHTML = "";
  const addButton = (label, cls, handler) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `btn ${cls}`;
    b.textContent = label;
    b.addEventListener("click", handler);
    actions.append(b);
  };
  if (running) {
    addButton("Cancelar", "btn--ghost", async () => {
      await api(`/api/projects/${p.id}/cancel`, { method: "POST" });
      poll();
    });
  } else if (["interrupted", "failed", "cancelled"].includes(p.status)) {
    addButton("Retomar de onde parou", "btn--primary", async () => {
      try {
        await api(`/api/projects/${p.id}/resume`, { method: "POST" });
        poll();
      } catch (err) {
        $("#alerts").insertAdjacentHTML("afterbegin", `<div class="alert alert--err">${esc(err.message)}</div>`);
      }
    });
  } else if (p.status === "draft") {
    addButton("Iniciar geração", "btn--primary", async () => {
      try {
        if (p.engine === "flow") {
          if (!(await confirmCredits(p))) return;
          await api(`/api/projects/${p.id}/start`, { method: "POST", json: { confirm_credits: true } });
        } else {
          await api(`/api/projects/${p.id}/start`, { method: "POST", json: {} });
        }
        poll();
      } catch (err) {
        $("#alerts").insertAdjacentHTML("afterbegin", `<div class="alert alert--err">${esc(err.message)}</div>`);
      }
    });
  }

  const result = $("#result");
  if (p.status === "done" && p.final_video) {
    const src = `/api/projects/${p.id}/video`;
    if (!result.querySelector("video")) {
      result.innerHTML = `<video controls playsinline src="${src}"></video>
        <div class="actions"><a class="btn btn--primary" href="${src}" download>Baixar MP4</a></div>`;
    }
  } else {
    result.innerHTML = "";
  }
}

/* ---------------------------------------------------------------- listas / saúde */

async function loadProjects() {
  try {
    const projects = await api("/api/projects");
    const list = $("#projects");
    list.innerHTML = projects.length ? "" : '<li class="muted">Nenhum projeto ainda.</li>';
    for (const p of projects.slice(0, 15)) {
      const li = document.createElement("li");
      li.className = "project";
      li.innerHTML = `<div class="project__text">${esc(p.brief)}<small>${esc(p.id)} · ${p.duration}s · ${esc(p.aspect)} ·
        ${p.engine === "flow" ? "Flow real" : "simulador"} · <strong>${esc(STATUS[p.status] || p.status)}</strong></small></div>`;
      const open = document.createElement("button");
      open.type = "button";
      open.className = "btn btn--ghost";
      open.textContent = "Abrir";
      open.addEventListener("click", () => openProject(p.id));
      li.append(open);
      list.append(li);
    }
  } catch (_) { /* ignora */ }
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const pill = (cls, label) => `<span class="pill pill--${cls}">${esc(label)}</span>`;
    $("#health").innerHTML = [
      pill(h.ffmpeg && h.ffprobe ? "ok" : "err", h.ffmpeg ? "FFmpeg ok" : "FFmpeg ausente"),
      pill(h.browser ? "ok" : "err", h.browser ? "navegador ok" : "sem Edge/Chrome"),
      pill(h.cdp_alive ? "ok" : "idle", h.cdp_alive ? "navegador do Flow aberto" : "navegador do Flow fechado"),
    ].join("");
    if (h.interrupted_on_start && h.interrupted_on_start.length) {
      $("#form-error").textContent = `Projetos interrompidos encontrados (${h.interrupted_on_start.join(", ")}). Abra e clique em "Retomar".`;
    }
  } catch (_) { /* ignora */ }
}

/* ---------------------------------------------------------------- Flow Doctor */

async function runDoctor(engine) {
  const body = $("#doctor-body");
  body.innerHTML = `<p><span class="spinner"></span> Rodando Flow Doctor (${engine === "flow" ? "Flow REAL" : "simulador"})…</p>`;
  $("#doctor-run").disabled = true;
  $("#doctor-sim").disabled = true;
  try {
    const r = await api("/api/doctor", { method: "POST", json: { engine } });
    const items = r.checks.map((c) => `<li>${c.ok ? "✅" : c.critical ? "❌" : "⚠️"} ${esc(c.label)}
      ${c.detail ? `<small>${esc(c.detail)}</small>` : ""}${!c.ok && c.selector ? `<small>procurado: ${esc(c.selector)}</small>` : ""}</li>`).join("");
    body.innerHTML = `
      <p><strong>${r.ok ? "Pronto" : "Falhou"}</strong> — ${esc(r.message)}</p>
      <ul class="doctor-list">${items}</ul>
      ${r.url ? `<p class="muted">URL: ${esc(r.url)}</p>` : ""}
      ${r.screenshot_url ? `<img class="doctor-shot" alt="Screenshot de diagnóstico" src="${r.screenshot_url}">` : ""}
      ${!r.real ? '<p class="muted">Resultado do SIMULADOR — não diz nada sobre o Flow real.</p>' : ""}`;
    loadHealth();
  } catch (err) {
    body.innerHTML = `<div class="alert alert--err">${esc(err.message)}</div>`;
  } finally {
    $("#doctor-run").disabled = false;
    $("#doctor-sim").disabled = false;
  }
}

/* ---------------------------------------------------------------- init */

async function init() {
  state.config = await api("/api/config");
  setupChips();
  previewFiles($("#product_files"), $("#product-thumbs"));
  previewFiles($("#reference"), $("#reference-thumbs"));
  previewFiles($("#logo"), $("#logo-thumbs"));
  $("#custom-duration").addEventListener("input", updateEstimate);
  $("#max_credits").addEventListener("input", updateEstimate);
  $("#create-form").addEventListener("submit", onSubmit);
  $("#btn-new").addEventListener("click", () => {
    clearTimeout(state.pollTimer);
    state.projectId = null;
    history.replaceState(null, "", location.pathname);
    $("#progress-card").hidden = true;
    $("#create-card").hidden = false;
  });
  $("#btn-doctor").addEventListener("click", () => {
    $("#doctor-body").innerHTML = "";
    $("#doctor-dialog").showModal();
  });
  $("#doctor-run").addEventListener("click", () => runDoctor("flow"));
  $("#doctor-sim").addEventListener("click", () => runDoctor("simulator"));
  updateEstimate();
  loadHealth();
  loadProjects();
  const match = location.hash.match(/project=([\w-]+)/);
  if (match) openProject(match[1]);
}

init().catch((err) => { $("#form-error").textContent = `Falha ao carregar: ${err.message}`; });
