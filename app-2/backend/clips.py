"""
clips.py

Gestisce la "Galleria": le clip video grezze caricate dall'utente.
Per ogni clip caricata:
1. La salviamo su disco (storage/clips/<id>.mp4)
2. Estraiamo alcuni fotogrammi
3. Mandiamo i fotogrammi a Claude per farceli descrivere
4. Salviamo la descrizione in un piccolo "database" JSON (storage/clips_meta.json)

Questo elenco di descrizioni e' poi quello che il modello usa per scegliere
quale clip abbinare a ogni blocco di testo, nella sezione "Nuovo Video".
"""

import json
import shutil
import uuid
from pathlib import Path

from openai_client import describe_clip_from_frames
from editor import detect_scenes, extract_frames, get_duration

STORAGE = Path(__file__).parent / "storage"
CLIPS_DIR = STORAGE / "clips"
META_FILE = STORAGE / "clips_meta.json"

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
if not META_FILE.exists():
    META_FILE.write_text("[]")


def _load_meta() -> list[dict]:
    return json.loads(META_FILE.read_text())


def _save_meta(items: list[dict]):
    META_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False))


def list_clips() -> list[dict]:
    return _load_meta()


def delete_clip(clip_id: str):
    items = _load_meta()
    target = next((c for c in items if c["id"] == clip_id), None)
    items = [c for c in items if c["id"] != clip_id]
    _save_meta(items)

    if target is None:
        return
    # Rimuove il file fisico solo se nessun'altra scena lo referenzia ancora
    still_used = any(c["filename"] == target["filename"] for c in items)
    if not still_used:
        (CLIPS_DIR / target["filename"]).unlink(missing_ok=True)


def add_clip(tmp_path: Path, original_filename: str) -> list[dict]:
    """Salva un nuovo file video caricato, rileva le scene al suo interno
    (un file puo' essere una compilation con piu' momenti diversi) e
    descrive ogni scena separatamente con Claude. Ritorna la lista dei
    record delle scene create (una libreria puo' avere piu' 'clip'
    provenienti dallo stesso file fisico)."""
    file_id = uuid.uuid4().hex[:12]
    ext = Path(original_filename).suffix or ".mp4"
    dest = CLIPS_DIR / f"{file_id}{ext}"
    shutil.copy(tmp_path, dest)

    scenes = detect_scenes(dest)

    records = []
    for scene_start, scene_end in scenes:
        scene_id = uuid.uuid4().hex[:12]
        frames_dir = STORAGE / "tmp_frames" / scene_id
        frame_paths = extract_frames(
            dest, frames_dir, count=2, start=scene_start, end=scene_end
        )
        description = describe_clip_from_frames([str(p) for p in frame_paths])
        shutil.rmtree(frames_dir, ignore_errors=True)

        records.append(
            {
                "id": scene_id,
                "filename": dest.name,
                "original_filename": original_filename,
                "start": round(scene_start, 2),
                "duration": round(scene_end - scene_start, 2),
                "description": description,
            }
        )

    items = _load_meta()
    items.extend(records)
    _save_meta(items)
    return records
