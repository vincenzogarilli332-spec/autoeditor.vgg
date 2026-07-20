"""
generate.py

Sezione "Nuovo Video": l'utente incolla il testo/script del voice-over
(diviso in blocchi narrativi separati da una riga vuota o da '---'),
opzionalmente carica anche il file audio del voice-over gia' registrato.

Il flusso:
1. Divide il testo in blocchi (problema / smentita soluzioni / reveal / benefici...)
2. Chiede a Claude quale/i clip della Galleria usare per ogni blocco,
   rispettando le regole di montaggio (durate, zoom)
3. Costruisce il "plan" per editor.build_video (source, start, duration, zoom, text)
4. Genera il video finale con ffmpeg (+ audio se fornito)

NOTA su sincronizzazione audio-parola-per-parola (Whisper): questa prima
versione non fa sync automatico a livello di singola parola. Il testo di
ogni blocco appare per tutta la durata del blocco. E' un miglioramento
naturale da aggiungere in futuro (vedi README).
"""

import json
import time
import uuid
from pathlib import Path

from openai_client import choose_clips_for_blocks
from clips import CLIPS_DIR, list_clips
from editor import build_video

STORAGE = Path(__file__).parent / "storage"
VIDEOS_DIR = STORAGE / "videos"
JOBS_DIR = STORAGE / "jobs"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def split_into_blocks(script_text: str) -> list[str]:
    raw_blocks = [b.strip() for b in script_text.replace("---", "\n\n").split("\n\n")]
    return [b for b in raw_blocks if b]


def _job_file(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def create_job() -> str:
    job_id = uuid.uuid4().hex[:10]
    _set_job_status(job_id, "in_coda", "In coda...")
    return job_id


def _set_job_status(job_id: str, status: str, message: str, video_filename: str | None = None):
    data = {"status": status, "message": message, "video_filename": video_filename}
    _job_file(job_id).write_text(json.dumps(data))


def get_job_status(job_id: str) -> dict:
    f = _job_file(job_id)
    if not f.exists():
        return {"status": "non_trovato", "message": "Job non trovato", "video_filename": None}
    return json.loads(f.read_text())


def run_generation_job(job_id: str, script_text: str, audio_path: Path | None):
    """Funzione pensata per girare in background (BackgroundTasks di FastAPI)."""
    try:
        _set_job_status(job_id, "elaborazione", "Divido il testo in blocchi narrativi...")
        blocks_text = split_into_blocks(script_text)
        if not blocks_text:
            raise ValueError("Il testo e' vuoto: dividilo in blocchi separati da una riga vuota.")

        clip_library = list_clips()
        if not clip_library:
            raise ValueError(
                "La Galleria e' vuota: carica almeno qualche clip prima di generare un video."
            )

        _set_job_status(job_id, "elaborazione", "Claude sta scegliendo le clip migliori per ogni blocco...")
        chosen_blocks = choose_clips_for_blocks(blocks_text, clip_library)

        clip_by_id = {c["id"]: c for c in clip_library}

        plan_blocks = []
        for block_text, chosen in zip(blocks_text, chosen_blocks):
            segments = []
            for seg in chosen["segments"]:
                clip = clip_by_id.get(seg["clip_id"])
                if clip is None:
                    continue
                clip_path = CLIPS_DIR / clip["filename"]
                duration = min(seg.get("duration", 1.4), clip["duration"])
                segments.append(
                    {
                        "source": str(clip_path),
                        "start": clip.get("start", 0),
                        "duration": duration,
                        "zoom": seg.get("zoom", False),
                        "text": block_text,
                    }
                )
            if segments:
                plan_blocks.append({"segments": segments})

        if not plan_blocks:
            raise ValueError("Nessuna clip valida selezionata per il montaggio.")

        _set_job_status(job_id, "elaborazione", "Monto il video con ffmpeg...")
        workdir = STORAGE / "work" / job_id
        output_filename = f"{job_id}.mp4"
        output_path = VIDEOS_DIR / output_filename

        build_video(plan_blocks, workdir, output_path, audio_path=audio_path)

        _set_job_status(job_id, "completato", "Video pronto!", video_filename=output_filename)
    except Exception as e:
        _set_job_status(job_id, "errore", str(e))


def list_generated_videos() -> list[dict]:
    videos = []
    for f in sorted(VIDEOS_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        videos.append(
            {
                "filename": f.name,
                "created_at": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)
                ),
            }
        )
    return videos
