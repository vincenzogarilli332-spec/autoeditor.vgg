"""
openai_client.py

Wrapper minimale per chiamare l'API di OpenAI (GPT-4.1 mini, con capacita'
di visione) via HTTP diretto — stessa struttura di claude_client.py, cosi'
il resto dell'app (clips.py, generate.py) non deve cambiare, cambia solo
quale "cervello" analizza le clip e sceglie il montaggio.

Usato per due cose:
1. Descrivere automaticamente una scena video guardando alcuni fotogrammi
   (sezione "Galleria").
2. Scegliere quali clip usare per ogni blocco narrativo del testo/voice-over
   (sezione "Nuovo Video").
"""

import base64
import json
import os

import httpx

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4.1-mini"


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "Manca la variabile d'ambiente OPENAI_API_KEY. "
            "Impostala nelle Environment Variables del servizio (Railway/Render)."
        )
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def describe_clip_from_frames(frame_paths: list[str]) -> dict:
    """Manda 2-3 fotogrammi di una scena a GPT-4.1 mini e chiede una
    descrizione breve, PIU' se sono presenti scritte/didascalie gia'
    'bruciate' nel video (es. sottotitoli di CapCut) e dove si trovano
    approssimativamente. Serve a decidere che stile di testo sovrimpresso
    usare quando quella clip verra' scelta per un video."""

    content = [
        {
            "type": "text",
            "text": (
                "Guarda questi fotogrammi presi da una clip video verticale "
                "usata per contenuti di marketing (stile social/Reels/TikTok). "
                "Rispondi SOLO con un JSON valido (nessun testo prima o dopo), "
                "con questa forma esatta:\n"
                '{"description": "descrizione breve (1-2 frasi, max 30 parole): '
                'soggetto, azione/gesto, ambientazione, prodotto visibile", '
                '"has_text_overlay": true/false (se nei fotogrammi sono gia\' '
                'visibili scritte/didascalie/sottotitoli sovrimpressi nel video, '
                'non conta il logo di un\'app), '
                '"text_position": "top"/"middle"/"bottom"/"none" (dove si trova '
                'approssimativamente il testo gia\' presente, "none" se has_text_overlay '
                'e\' false)}'
            ),
        }
    ]
    for fp in frame_paths:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    payload = {
        "model": MODEL,
        "max_tokens": 250,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }

    with httpx.Client(timeout=60) as client:
        r = client.post(OPENAI_API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data["choices"][0]["message"]["content"].strip()
    parsed = json.loads(raw)
    return {
        "description": parsed.get("description", "").strip(),
        "has_text_overlay": bool(parsed.get("has_text_overlay", False)),
        "text_position": parsed.get("text_position", "none"),
    }


def choose_clips_for_blocks(blocks: list[str], clip_library: list[dict], block_targets: list[float]) -> list[dict]:
    """Dato un elenco di blocchi di testo (ognuno una fase narrativa: problema,
    smentita soluzioni, reveal, benefici...), la libreria di clip disponibili
    (ognuna con id + descrizione) e una durata-obiettivo per ogni blocco
    (calcolata dalla durata reale dell'audio, proporzionalmente al testo),
    chiede al modello di scegliere, per ogni blocco, quali clip usare, con
    quale durata e quale tipo di transizione in entrata, seguendo le regole
    della tecnica di editing.

    Ritorna una lista allineata a 'blocks': per ogni blocco, un dizionario
    { transition_in: "hard"|"strong", segments: [ {clip_id, duration, zoom}, ... ] }.
    """

    rules = (
        "REGOLE DI MONTAGGIO DA SEGUIRE:\n"
        "- Ogni blocco ha una DURATA-OBIETTIVO indicata: la somma delle durate dei suoi "
        "segmenti deve avvicinarsi a quel numero (tolleranza +/- 15%), ma tu decidi come "
        "distribuirla: non tutte le clip devono durare uguale.\n"
        "- Varia le durate in modo intenzionale, seguendo il senso del testo in quel punto:\n"
        "  - Se la clip mostra un'azione lenta, un dettaglio, o accompagna un momento "
        "importante del testo, falla durare di piu' (fino a 2.5-3s).\n"
        "  - Se una scena e' lenta o ripetitiva (es. una persona che parla o si muove "
        "senza cambiare granche'), spezzala con un micro-taglio ritmico (0.7-1.0s) invece "
        "di lasciarla ferma a lungo: usa la stessa clip due volte con offset diversi se serve.\n"
        "  - Nei momenti di build-up o urgenza (es. prima di un reveal, una call to action) "
        "puoi accelerare il ritmo con piu' tagli brevi.\n"
        "  - Nei momenti calmi o esplicativi puoi rallentare con clip singole piu' lunghe.\n"
        "- Zoom leggero (Ken Burns) su circa 1 clip ogni 2-3, non di piu'.\n"
        "- Ogni blocco puo' contenere da 1 a 4 clip in sequenza (taglio secco tra loro dentro "
        "lo stesso blocco).\n"
        "- Scegli le clip la cui descrizione corrisponde meglio al contenuto del blocco.\n"
        "- Evita di ripetere sempre la stessa clip se ci sono alternative valide.\n\n"
        "REGOLE PER LA TRANSIZIONE TRA UN BLOCCO E IL PRECEDENTE (campo transition_in):\n"
        "- 'hard' (taglio secco, DEFAULT): usalo quando il blocco continua lo stesso filo "
        "narrativo del precedente, anche se cambia argomento in modo naturale.\n"
        "- 'strong' (transizione con avvicinamento): usalo SOLO in corrispondenza di una vera "
        "svolta narrativa importante (es. il momento del reveal del prodotto, un cambio netto "
        "di tono). Usalo con parsimonia: in un video di 5-7 blocchi, va bene al massimo 1-2 "
        "transizioni 'strong' in tutto, altrimenti il video risulta caotico invece che fluido.\n"
        "- Il primo blocco non ha transizione in entrata, ignora questo campo per il blocco 0.\n"
    )

    targets_text = "\n".join(
        f"BLOCCO {i}: durata-obiettivo {t:.1f}s" for i, t in enumerate(block_targets)
    )

    library_text = "\n".join(
        f"- id={c['id']}: {c['description']}" for c in clip_library
    )
    blocks_text = "\n".join(f"BLOCCO {i}: \"{b}\"" for i, b in enumerate(blocks))

    prompt = (
        f"{rules}\n\nDURATE OBIETTIVO PER BLOCCO:\n{targets_text}\n\n"
        f"LIBRERIA CLIP DISPONIBILI:\n{library_text}\n\n"
        f"TESTO DIVISO IN BLOCCHI NARRATIVI:\n{blocks_text}\n\n"
        "Per ogni blocco, scegli la sequenza di clip da usare e il tipo di transizione in "
        "entrata. Rispondi SOLO con un JSON valido (nessun testo prima o dopo, nessun blocco "
        "markdown), con questa forma esatta:\n"
        '{"blocks": [ {"transition_in": "hard", "segments": [ {"clip_id": "...", '
        '"duration": 1.8, "zoom": false}, ... ] }, ... ] }\n'
        "L'array 'blocks' deve avere esattamente la stessa lunghezza e lo stesso ordine dei "
        "BLOCCO elencati sopra."
    )

    payload = {
        "model": MODEL,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }

    with httpx.Client(timeout=60) as client:
        r = client.post(OPENAI_API_URL, headers=_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data["choices"][0]["message"]["content"].strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    parsed = json.loads(raw)
    return parsed["blocks"]
