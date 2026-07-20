"""
editor.py

Logica di montaggio video (ffmpeg), adattata dal prototipo monta_video.py
per essere richiamata dal backend invece che da riga di comando.

Riceve un "plan": una lista di blocchi narrativi, ognuno con una lista di
segmenti { source, start, duration, zoom, text }. All'interno di un blocco
i segmenti si susseguono con taglio secco; tra un blocco e il successivo
viene inserita una transizione forte (xfade zoomin).
"""

import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FONT_COVER = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_WORD = str(Path(__file__).parent / "fonts" / "Anton-Regular.ttf")

RESOLUTION = (1080, 1920)
FPS = 30
TRANSITION_DURATION = 0.4
TRANSITION_TYPE = "zoomin"

# Zone approssimative dove posizionare il banner di copertura, in base a
# dove l'analisi ha rilevato il testo originale della clip
COVER_ZONES = {
    "top": 0.10,
    "middle": 0.44,
    "bottom": 0.74,
    "none": 0.44,
}


def wrap_text(t: str, max_chars: int = 26) -> str:
    words = t.split()
    lines, cur = [], ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def esc_text(t: str) -> str:
    t = wrap_text(t)
    return (
        t.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )


def esc_word(t: str) -> str:
    return (
        t.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )


def build_segment(seg: dict, idx: int, workdir: Path) -> Path:
    w, h = RESOLUTION
    src = seg["source"]
    start = seg.get("start", 0)
    dur = seg["duration"]
    zoom = seg.get("zoom", False)
    text_mode = seg.get("text_mode", "cover")

    out = workdir / f"seg_{idx:03d}.mp4"

    filters = [f"fps={FPS}", f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]

    if zoom:
        filters.append(
            "zoompan=z='min(1+0.0008*on,1.06)':d=1:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={w}x{h}:fps={FPS}"
        )

    if text_mode == "cover" and seg.get("text"):
        # La clip ha gia' scritte proprie: copriamo con un banner bianco pieno
        # e mostriamo la nostra frase in nero sopra, nella stessa zona.
        zone = COVER_ZONES.get(seg.get("text_position", "middle"), 0.44)
        band_y = int(h * zone)
        band_h = 260
        filters.append(f"drawbox=x=0:y={band_y}:w={w}:h={band_h}:color=white@1.0:t=fill")
        filters.append(
            f"drawtext=text='{esc_text(seg['text'])}':fontfile={FONT_COVER}:"
            "fontsize=44:fontcolor=black:line_spacing=6:"
            f"x=(w-text_w)/2:y={band_y}+({band_h}-text_h)/2"
        )
    elif text_mode == "words" and seg.get("word_timings"):
        # La clip e' pulita: mostriamo una parola alla volta, veloce, in stile
        # bold con contorno, sincronizzata al tempo relativo dentro il segmento.
        for word, rel_start, rel_end in seg["word_timings"]:
            filters.append(
                f"drawtext=text='{esc_word(word.upper())}':fontfile={FONT_WORD}:"
                "fontsize=88:fontcolor=white:borderw=6:bordercolor=black:"
                "x=(w-text_w)/2:y=(h*0.42)-(text_h/2):"
                f"enable='between(t,{rel_start:.3f},{rel_end:.3f})'"
            )

    vf = ",".join(filters)

    cmd = [
        FFMPEG, "-y",
        "-ss", str(start), "-t", str(dur), "-i", src,
        "-vf", vf,
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def concat_hardcuts(paths: list[Path], out_path: Path):
    list_file = out_path.parent / f"{out_path.stem}_list.txt"
    with open(list_file, "w") as f:
        for p in paths:
            f.write(f"file '{p.resolve()}'\n")
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def get_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    r = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def hard_join(a: Path, b: Path, out_path: Path):
    """Unisce due file gia' incodificati con un taglio secco (nessuna
    transizione), usando il concat demuxer. Usato quando l'IA decide che
    tra un blocco e l'altro non c'e' una vera svolta narrativa."""
    list_file = out_path.parent / f"{out_path.stem}_hardjoin_list.txt"
    with open(list_file, "w") as f:
        f.write(f"file '{a.resolve()}'\n")
        f.write(f"file '{b.resolve()}'\n")
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def xfade_join(a: Path, b: Path, out_path: Path):
    dur_a = get_duration(a)
    offset = max(dur_a - TRANSITION_DURATION, 0)

    filter_complex = (
        f"[0:v][1:v]xfade=transition={TRANSITION_TYPE}:"
        f"duration={TRANSITION_DURATION}:offset={offset},format=yuv420p[v]"
    )
    cmd = [
        FFMPEG, "-y", "-i", str(a), "-i", str(b),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def mux_audio(video_path: Path, audio_path: Path, out_path: Path):
    """Aggiunge una traccia audio (voice-over) al video muto, tagliando
    l'audio o il video alla durata piu' corta tra i due."""
    cmd = [
        FFMPEG, "-y",
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def build_video(blocks: list[dict], workdir: Path, output_path: Path, audio_path: Path | None = None):
    """blocks: lista di { segments: [ {source, start, duration, zoom, text}, ... ] }"""
    workdir.mkdir(parents=True, exist_ok=True)

    block_outputs = []
    seg_idx = 0
    for b_i, block in enumerate(blocks):
        seg_paths = []
        for seg in block["segments"]:
            seg_paths.append(build_segment(seg, seg_idx, workdir))
            seg_idx += 1
        block_out = workdir / f"block_{b_i:02d}.mp4"
        concat_hardcuts(seg_paths, block_out)
        block_outputs.append(block_out)

    current = block_outputs[0]
    for i in range(1, len(block_outputs)):
        joined = workdir / f"joined_{i:02d}.mp4"
        transition_in = blocks[i].get("transition_in", "hard")
        if transition_in == "strong":
            xfade_join(current, block_outputs[i], joined)
        else:
            hard_join(current, block_outputs[i], joined)
        current = joined

    if audio_path is not None:
        muxed = workdir / "with_audio.mp4"
        mux_audio(current, audio_path, muxed)
        current = muxed

    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", str(current), str(output_path)], check=True)


def detect_scenes(video_path: Path, min_scene_duration: float = 1.2) -> list[tuple[float, float]]:
    """Rileva i cambi di scena dentro un file video (utile quando un file
    caricato e' in realta' una compilation con piu' momenti diversi).
    Ritorna una lista di (start, end) in secondi. Le scene troppo corte
    vengono unite a quella successiva per evitare micro-frammenti inutili."""
    duration = get_duration(video_path)

    cmd = [
        FFMPEG, "-i", str(video_path),
        "-filter:v", "select='gt(scene,0.3)',showinfo",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    cut_points = []
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].split()[0])
                cut_points.append(t)
            except (IndexError, ValueError):
                continue

    boundaries = [0.0] + sorted(cut_points) + [duration]

    scenes = []
    current_start = boundaries[0]
    for b in boundaries[1:]:
        if b - current_start >= min_scene_duration or b == boundaries[-1]:
            scenes.append((current_start, b))
            current_start = b
    if len(scenes) > 1 and (scenes[-1][1] - scenes[-1][0]) < min_scene_duration:
        prev_start, _ = scenes[-2]
        _, last_end = scenes[-1]
        scenes = scenes[:-2] + [(prev_start, last_end)]

    return scenes if scenes else [(0.0, duration)]


def extract_frames(
    video_path: Path,
    out_dir: Path,
    count: int = 3,
    start: float = 0.0,
    end: float | None = None,
) -> list[Path]:
    """Estrae 'count' fotogrammi equidistanti da un video (o da un intervallo
    start-end specifico, per una singola scena dentro un file piu' lungo),
    per farli analizzare a Claude (descrizione automatica in Galleria)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if end is None:
        end = get_duration(video_path)
    span = max(end - start, 0.1)
    paths = []
    for i in range(count):
        t = start + span * (i + 1) / (count + 1)
        out = out_dir / f"frame_{i}.jpg"
        cmd = [
            FFMPEG, "-y", "-ss", str(t), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3", str(out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        paths.append(out)
    return paths
