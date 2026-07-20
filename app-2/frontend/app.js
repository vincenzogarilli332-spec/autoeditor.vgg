const API = ""; // stesso dominio del frontend

let APP_PASSWORD = localStorage.getItem("app_password") || "";

function authHeaders() {
  return APP_PASSWORD ? { "X-App-Password": APP_PASSWORD } : {};
}

async function apiFetch(path, options = {}) {
  const res = await fetch(API + path, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  if (res.status === 401) {
    localStorage.removeItem("app_password");
    location.reload();
    throw new Error("Password non valida");
  }
  return res;
}

// ---------------- Login ----------------

const loginOverlay = document.getElementById("login-overlay");
const appEl = document.getElementById("app");

async function tryLogin(password) {
  const res = await fetch(API + "/api/login", { headers: { "X-App-Password": password } });
  return res.ok;
}

async function boot() {
  if (APP_PASSWORD) {
    const ok = await tryLogin(APP_PASSWORD);
    if (ok) {
      showApp();
      return;
    }
  }
  loginOverlay.classList.remove("hidden");
}

document.getElementById("login-submit").addEventListener("click", async () => {
  const pw = document.getElementById("login-password").value;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  const ok = await tryLogin(pw);
  if (ok) {
    APP_PASSWORD = pw;
    localStorage.setItem("app_password", pw);
    showApp();
  } else {
    errorEl.textContent = "Password errata, riprova.";
  }
});

function showApp() {
  loginOverlay.classList.add("hidden");
  appEl.classList.remove("hidden");
  startClock();
  loadClips();
  loadVideos();
}

// ---------------- Timecode nell'header (decorativo) ----------------

function startClock() {
  const el = document.getElementById("timecode");
  setInterval(() => {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    el.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}:00`;
  }, 1000);
}

// ---------------- Tab navigation ----------------

const tabs = document.querySelectorAll(".tab");
const views = document.querySelectorAll(".view");
const sectionTitle = document.getElementById("section-title");
const titles = { galleria: "GALLERIA", nuovo: "NUOVO VIDEO", "video-creati": "VIDEO CREATI" };

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.view;
    tabs.forEach((t) => t.classList.toggle("active", t === tab));
    views.forEach((v) => v.classList.toggle("active", v.id === `view-${target}`));
    sectionTitle.textContent = titles[target];
    if (target === "video-creati") loadVideos();
    if (target === "galleria") loadClips();
  });
});

// ---------------- Galleria ----------------

const clipsGrid = document.getElementById("clips-grid");
const uploadStatus = document.getElementById("upload-status");
const fileInput = document.getElementById("file-input");

async function loadClips() {
  const res = await apiFetch("/api/clips");
  const clips = await res.json();
  if (clips.length === 0) {
    clipsGrid.innerHTML = `<div class="empty-state">Nessuna clip ancora. Caricane qualcuna per iniziare a costruire la libreria.</div>`;
    return;
  }
  clipsGrid.innerHTML = clips
    .map(
      (c) => `
    <div class="clip-card" data-id="${c.id}">
      <div class="clip-thumb"></div>
      <div class="clip-info">
        <p class="clip-desc">${escapeHtml(c.description || "Descrizione non disponibile")}</p>
        <div class="clip-meta">${fmtTime(c.start)}–${fmtTime(c.start + c.duration)} (${c.duration}s) · ${escapeHtml(c.original_filename)}</div>
      </div>
      <button class="clip-delete" title="Rimuovi" data-id="${c.id}">×</button>
    </div>`
    )
    .join("");

  clipsGrid.querySelectorAll(".clip-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await apiFetch(`/api/clips/${btn.dataset.id}`, { method: "DELETE" });
      loadClips();
    });
  });
}

document.getElementById("backup-btn").addEventListener("click", async () => {
  const res = await apiFetch("/api/clips/backup");
  const data = await res.json();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `backup-clip-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

fileInput.addEventListener("change", async () => {
  const files = Array.from(fileInput.files);
  for (const file of files) {
    uploadStatus.textContent = `Analizzo "${file.name}" (rilevo le scene e le descrivo)...`;
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await apiFetch("/api/clips", { method: "POST", body: form });
      const scenes = await res.json();
      const n = Array.isArray(scenes) ? scenes.length : 1;
      uploadStatus.textContent = `"${file.name}": trovate e descritte ${n} scen${n === 1 ? "a" : "e"}.`;
      loadClips();
    } catch (e) {
      uploadStatus.textContent = `Errore su "${file.name}": ${e.message}`;
    }
  }
  setTimeout(() => (uploadStatus.textContent = ""), 4000);
  fileInput.value = "";
});

// ---------------- Nuovo Video ----------------

const scriptText = document.getElementById("script-text");
const audioInput = document.getElementById("audio-input");
const audioLabel = document.getElementById("audio-label");
const generateBtn = document.getElementById("generate-btn");
const generateStatus = document.getElementById("generate-status");
const generateMessage = document.getElementById("generate-message");
const generateResult = document.getElementById("generate-result");

audioInput.addEventListener("change", () => {
  if (audioInput.files[0]) {
    audioLabel.textContent = `🎙 ${audioInput.files[0].name}`;
  }
});

generateBtn.addEventListener("click", async () => {
  const text = scriptText.value.trim();
  if (!text) {
    alert("Scrivi prima il testo del video, diviso in blocchi.");
    return;
  }

  generateResult.classList.add("hidden");
  generateResult.innerHTML = "";
  generateStatus.classList.remove("hidden");
  generateMessage.textContent = "Invio la richiesta...";
  generateBtn.disabled = true;

  const params = new URLSearchParams({ script_text: text });
  const form = new FormData();
  if (audioInput.files[0]) form.append("audio", audioInput.files[0]);

  try {
    const res = await apiFetch(`/api/generate?${params.toString()}`, {
      method: "POST",
      body: form,
    });
    const { job_id } = await res.json();
    pollJob(job_id);
  } catch (e) {
    generateStatus.classList.add("hidden");
    showGenerateError(e.message);
    generateBtn.disabled = false;
  }
});

function pollJob(jobId) {
  const interval = setInterval(async () => {
    const res = await apiFetch(`/api/jobs/${jobId}`);
    const data = await res.json();
    generateMessage.textContent = data.message;

    if (data.status === "completato") {
      clearInterval(interval);
      generateStatus.classList.add("hidden");
      generateBtn.disabled = false;
      generateResult.classList.remove("hidden");
      generateResult.innerHTML = `<video controls src="${videoUrl(data.video_filename)}"></video>`;
      loadVideos();
    } else if (data.status === "errore") {
      clearInterval(interval);
      generateStatus.classList.add("hidden");
      generateBtn.disabled = false;
      showGenerateError(data.message);
    }
  }, 2500);
}

function showGenerateError(message) {
  generateResult.classList.remove("hidden");
  generateResult.innerHTML = `<div class="error-box">Non sono riuscito a generare il video: ${escapeHtml(message)}</div>`;
}

// ---------------- Video Creati ----------------

const videosList = document.getElementById("videos-list");

async function loadVideos() {
  const res = await apiFetch("/api/videos");
  const videos = await res.json();
  if (videos.length === 0) {
    videosList.innerHTML = `<div class="empty-state">Ancora nessun video generato. Vai su "Nuovo Video" per crearne uno.</div>`;
    return;
  }
  videosList.innerHTML = videos
    .map(
      (v) => `
    <div class="video-card">
      <video controls preload="none" src="${videoUrl(v.filename)}"></video>
      <div class="video-meta">
        <span>${v.created_at}</span>
        <a href="${videoUrl(v.filename)}" download>Scarica</a>
      </div>
    </div>`
    )
    .join("");
}

// ---------------- Utils ----------------

function videoUrl(filename) {
  return `/api/videos/${filename}?pw=${encodeURIComponent(APP_PASSWORD)}`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

boot();
