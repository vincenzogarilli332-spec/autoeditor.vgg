"""
claude_client.py

Wrapper minimale per chiamare l'API di Anthropic (Claude) via HTTP diretto,
senza dipendere dall'SDK ufficiale (cosi' basta 'httpx' come dipendenza).

Serve per due cose nel progetto:
1. Descrivere automaticamente una clip video guardando alcuni fotogrammi
   (usato dalla sezione "Galleria" quando si carica un nuovo video).
2. Scegliere quale clip usare per ogni blocco narrativo del testo/voice-over
   (usato dalla sezione "Nuovo Video").
"""

import base64
import json
import os

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "Manca la variabile d'ambiente ANTHROPIC_API_KEY. "
            "Impostala nelle Environment Variables del servizio (Railway/Render)."
        )
    return key


def _headers() -> dict:
    return {
        "x-api-key": _api_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def describe_clip_from_frames(frame_paths: list[str]) -> str:
    """Manda 2-3 fotogrammi di una clip a Claude e chiede una descrizione breve,
    pensata per essere poi usata come 'tag' di ricerca quando si sceglie quale
    clip usare per un blocco di testo del video."""

    content = [
        {
            "type": "text",
            "text": (
                "Guarda questi fotogrammi presi da una clip video verticale "
                "usata per contenuti di marketing (stile social/Reels/TikTok). "
                "Scrivi una descrizione breve (1-2 frasi, max 30 parole) che "
                "catturi: soggetto, azione/gesto, ambientazione, ed eventuale "
                "prodotto visibile. Rispondi SOLO con la descrizione, senza "
                "preamboli."
            ),
        }
    ]
    for fp in frame_paths:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            }
        )

    payload = {
        "model": MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": content}],
    }

    with httpx.Client(timeout=60) as client:
        r = client.post(ANTHROPIC_API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    text_blocks = [b["text"] for b in data["content"] if b.get("type") == "text"]
    return " ".join(text_blocks).strip()


def choose_clips_for_blocks(blocks: list[str], clip_library: list[dict]) -> list[dict]:
    """Dato un elenco di blocchi di testo (ognuno una fase narrativa: problema,
    smentita soluzioni, reveal, benefici...) e la libreria di clip disponibili
    (ognuna con id + descrizione), chiede a Claude di scegliere, per ogni
    blocco, quali clip usare e con quali parametri di montaggio (durata,
    zoom), seguendo le regole della tecnica di editing.

    Ritorna una lista allineata a 'blocks': per ogni blocco, una lista di
    segmenti { clip_id, duration, zoom }.
    """

    rules = (
        "REGOLE DI MONTAGGIO DA SEGUIRE:\n"
        "- Durata clip per taglio-ritmico (stessa scena/energia): 0.7-1.0s\n"
        "- Durata clip per taglio-scena (cambio di contesto): 1.5-2.5s (default 2s)\n"
        "- Media generale desiderata: circa 1.3-1.4s per clip\n"
        "- Zoom leggero (Ken Burns) su circa 1 clip ogni 2-3\n"
        "- Ogni blocco narrativo puo' contenere 2-4 clip in sequenza (taglio secco tra loro)\n"
        "- Scegli le clip la cui descrizione corrisponde meglio al contenuto del blocco "
        "(es. blocco 'problema' -> clip che mostrano il problema/situazione, non il prodotto in azione)\n"
        "- Evita di ripetere sempre la stessa clip se ci sono alternative valide\n"
    )

    library_text = "\n".join(
        f"- id={c['id']}: {c['description']}" for c in clip_library
    )
    blocks_text = "\n".join(f"BLOCCO {i}: \"{b}\"" for i, b in enumerate(blocks))

    prompt = (
        f"{rules}\n\nLIBRERIA CLIP DISPONIBILI:\n{library_text}\n\n"
        f"TESTO DIVISO IN BLOCCHI NARRATIVI:\n{blocks_text}\n\n"
        "Per ogni blocco, scegli la sequenza di clip da usare. Rispondi SOLO con "
        "un JSON valido (nessun testo prima o dopo, nessun blocco markdown), con "
        "questa forma esatta:\n"
        '{"blocks": [ {"segments": [ {"clip_id": "...", "duration": 1.8, '
        '"zoom": false}, ... ] }, ... ] }\n'
        "L'array 'blocks' deve avere esattamente la stessa lunghezza e lo stesso "
        "ordine dei BLOCCO elencati sopra."
    )

    payload = {
        "model": MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }

    with httpx.Client(timeout=60) as client:
        r = client.post(ANTHROPIC_API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    text_blocks = [b["text"] for b in data["content"] if b.get("type") == "text"]
    raw = " ".join(text_blocks).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    parsed = json.loads(raw)
    return parsed["blocks"]
