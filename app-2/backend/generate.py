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
from editor import build_video, get_duration

STORAGE = Path(__file__).parent / "storage"
VIDEOS_DIR = STORAGE / "videos"
JOBS_DIR = STORAGE / "jobs"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

WORDS_PER_SECOND = 2.3  # ritmo di lettura tipico per un voice-over pubblicitario


def split_into_blocks(script_text: str) -> list[str]:
    raw_blocks = [b.strip() for b in script_text.replace("---", "\n\n").split("\n\n")]
    return [b for b in raw_blocks if b]


def estimate_block_targets(blocks_text: list[str], audio_path: Path | None) -> list[float]:
    """Calcola una durata-obiettivo (in secondi) per ogni blocco, in modo
    proporzionale alla sua lunghezza in parole. Se e' presente l'audio del
    voice-over, i target sommano alla sua durata reale (cosi' il video finale
    non risulta piu' corto dell'audio); altrimenti si stima dal ritmo di
    lettura tipico di un voice-over pubblicitario."""
    word_counts = [max(len(b.split()), 1) for b in blocks_text]
    total_words = sum(word_counts)

    if audio_path is not None:
        total_duration = get_duration(audio_path)
    else:
        total_duration = total_words / WORDS_PER_SECOND

    return [total_duration * (wc / total_words) for wc in word_counts]


def compute_word_timings(text: str, total_duration: float) -> list[tuple[str, float, float]]:
    """Divide un testo in parole e assegna a ciascuna una finestra di tempo
    (in secondi, relativa all'inizio del blocco), proporzionale alla
    lunghezza della parola, cosi' la lettura sembra naturale."""
    words = text.split()
    if not words:
        return []
    weights = [max(len(w), 2) for w in words]
    total_weight = sum(weights)
    timings = []
    t = 0.0
    for word, weight in zip(words, weights):
        dur = total_duration * (weight / total_weight)
        timings.append((word, t, t + dur))
        t += dur
    return timings


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

        _set_job_status(job_id, "elaborazione", "Calcolo il ritmo dai blocchi di testo...")
        block_targets = estimate_block_targets(blocks_text, audio_path)

        _set_job_status(job_id, "elaborazione", "L'IA sta scegliendo le clip e il ritmo del montaggio...")
        chosen_blocks = choose_clips_for_blocks(blocks_text, clip_library, block_targets)

        clip_by_id = {c["id"]: c for c in clip_library}

        plan_blocks = []
        for block_text, chosen in zip(blocks_text, chosen_blocks):
            raw_segments = []
            for seg in chosen["segments"]:
                clip = clip_by_id.get(seg["clip_id"])
                if clip is None:
                    continue
                clip_path = CLIPS_DIR / clip["filename"]
                duration = min(seg.get("duration", 1.4), clip["duration"])
                raw_segments.append(
                    {
                        "source": str(clip_path),
                        "start": clip.get("start", 0),
                        "duration": duration,
                        "zoom": seg.get("zoom", False),
                        "clip": clip,
                    }
                )
            if not raw_segments:
                continue

            # Se almeno una delle clip scelte per questo blocco ha gia' scritte
            # proprie, usiamo lo stile 'copertura' per l'intero blocco (piu'
            # semplice e coerente che mescolare due stili nello stesso blocco).
            overlay_clip = next(
                (s["clip"] for s in raw_segments if s["clip"].get("has_text_overlay")), None
            )

            segments = []
            if overlay_clip is not None:
                for s in raw_segments:
                    segments.append(
                        {
                            "source": s["source"],
                            "start": s["start"],
                            "duration": s["duration"],
                            "zoom": s["zoom"],
                            "text_mode": "cover",
                            "text": block_text,
                            "text_position": overlay_clip.get("text_position", "middle"),
                        }
                    )
            else:
                block_duration = sum(s["duration"] for s in raw_segments)
                word_timings = compute_word_timings(block_text, block_duration)
                offset = 0.0
                for s in raw_segments:
                    seg_start, seg_end = offset, offset + s["duration"]
                    local_words = []
                    for word, wstart, wend in word_timings:
                        rel_start = max(wstart, seg_start) - offset
                        rel_end = min(wend, seg_end) - offset
                        if rel_end - rel_start > 0.05:
                            local_words.append((word, round(rel_start, 3), round(rel_end, 3)))
                    segments.append(
                        {
                            "source": s["source"],
                            "start": s["start"],
                            "duration": s["duration"],
                            "zoom": s["zoom"],
                            "text_mode": "words",
                            "word_timings": local_words,
                        }
                    )
                    offset = seg_end

            plan_blocks.append(
                {
                    "segments": segments,
                    "transition_in": chosen.get("transition_in", "hard"),
                }
            )

        if not plan_blocks:
            raise ValueError("Nessuna clip valida selezionata per il montaggio.")

        # Se l'audio e' piu' lungo di quanto pianificato (per via degli arrotondamenti
        # o della liberta' creativa lasciata all'IA), allunghiamo l'ultimo segmento
        # per evitare che l'audio venga tagliato bruscamente alla fine.
        if audio_path is not None:
            audio_duration = get_duration(audio_path)
            num_strong = sum(
                1 for b in plan_blocks[1:] if b["transition_in"] == "strong"
            )
            planned_duration = (
                sum(seg["duration"] for b in plan_blocks for seg in b["segments"])
                - num_strong * 0.4
            )
            shortfall = audio_duration - planned_duration
            if shortfall > 0.15:
                plan_blocks[-1]["segments"][-1]["duration"] += min(shortfall, 3.0)

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
