"""
main.py

Entry point dell'app. Espone:

- POST /api/clips               -> carica una nuova clip nella Galleria (viene descritta in automatico da Claude)
- GET  /api/clips                -> elenco clip in Galleria
- DELETE /api/clips/{clip_id}    -> rimuove una clip

- POST /api/generate             -> avvia la generazione di un nuovo video (testo + audio opzionale)
- GET  /api/jobs/{job_id}        -> stato di avanzamento della generazione
- GET  /api/videos                -> elenco video generati ("Video Creati")
- GET  /api/videos/{filename}     -> streaming/download del video

- GET  /api/login                 -> verifica la password (usata dal frontend al primo accesso)

Serve anche il frontend statico (cartella ../frontend) sulla root "/".
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import clips as clips_module
import generate as generate_module
from auth import require_password

app = FastAPI(title="Montaggio Video Automatico")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Login ----------

@app.get("/api/login")
def login(_: None = Depends(require_password)):
    return {"ok": True}


# ---------- Galleria ----------

@app.get("/api/clips")
def get_clips(_: None = Depends(require_password)):
    return clips_module.list_clips()


@app.post("/api/clips")
async def upload_clip(file: UploadFile = File(...), _: None = Depends(require_password)):
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        record = clips_module.add_clip(tmp_path, file.filename)
    finally:
        tmp_path.unlink(missing_ok=True)
    return record


@app.delete("/api/clips/{clip_id}")
def remove_clip(clip_id: str, _: None = Depends(require_password)):
    clips_module.delete_clip(clip_id)
    return {"ok": True}


# ---------- Generazione video ----------

@app.post("/api/generate")
async def generate_video(
    background_tasks: BackgroundTasks,
    script_text: str,
    audio: UploadFile | None = File(default=None),
    _: None = Depends(require_password),
):
    job_id = generate_module.create_job()

    audio_path = None
    if audio is not None:
        audio_dest = generate_module.STORAGE / "work" / job_id / f"voiceover{Path(audio.filename).suffix}"
        audio_dest.parent.mkdir(parents=True, exist_ok=True)
        with open(audio_dest, "wb") as f:
            shutil.copyfileobj(audio.file, f)
        audio_path = audio_dest

    background_tasks.add_task(generate_module.run_generation_job, job_id, script_text, audio_path)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(require_password)):
    return generate_module.get_job_status(job_id)


# ---------- Video creati ----------

@app.get("/api/videos")
def get_videos(_: None = Depends(require_password)):
    return generate_module.list_generated_videos()


@app.get("/api/videos/{filename}")
def get_video_file(filename: str, _: None = Depends(require_password)):
    path = generate_module.VIDEOS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video non trovato")
    return FileResponse(path, media_type="video/mp4")


# ---------- Frontend statico ----------

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
