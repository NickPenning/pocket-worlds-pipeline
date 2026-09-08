#!/usr/bin/env python3
"""
Pocket Worlds — geautomatiseerde dagelijkse Instagram Reel-pipeline.

Stappen:
  1. Claude bedenkt een nieuw diorama-concept + image-prompt + caption + hashtags.
  2. Leonardo.ai genereert een still-afbeelding op basis van dat concept.
  3. ffmpeg maakt van die still een korte verticale pan/zoom-video (Ken Burns-effect,
     Reel-formaat) — deterministisch, geen AI-videogeneratie nodig.
  4. De video wordt lokaal opgeslagen in media/ (wordt door de GitHub Action gecommit
     zodat er een publieke URL ontstaat die Instagram kan ophalen).
  5. De video wordt als Reel gepubliceerd via de Instagram Graph API.

Benodigde environment variables (zie .env.example):
  ANTHROPIC_API_KEY
  LEONARDO_API_KEY
  IG_ACCESS_TOKEN
  IG_USER_ID
  MEDIA_PUBLIC_BASE_URL   bv. https://raw.githubusercontent.com/<jouw-gebruiker>/<repo>/main/media

Gebruik:
  python generate_post.py                # volledige pipeline: concept -> beeld -> video -> posten
  python generate_post.py --skip-publish  # alles t/m video genereren, NIET posten (handig om lokaal te testen)
"""

import os
import re
import sys
import time
import json
import tempfile
import argparse
import subprocess
from datetime import datetime, timezone

import requests
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = "claude-sonnet-5"
LEONARDO_BASE = "https://cloud.leonardo.ai/api/rest/v1"
IG_API_VERSION = os.environ.get("IG_API_VERSION", "v23.0")
IG_GRAPH_BASE = f"https://graph.instagram.com/{IG_API_VERSION}"

# Verticaal 9:16 Reel-formaat, binnen Leonardo's toegestane bereik (32-1536) voor de still,
# en meteen ook de output-resolutie van de ffmpeg pan/zoom-video.
VIDEO_WIDTH = 864
VIDEO_HEIGHT = 1536

# Pas dit gerust aan naar de exacte stijl die je voor pocket.worlds.ai wilt vasthouden.
BRAND_SYSTEM_PROMPT = """\
Je bedenkt dagelijkse content voor het Instagram-account "pocket.worlds.ai".
Het account toont surrealistische, hyperdetailleerde miniatuur-diorama-werelden:
kleine complete werelden in onverwachte objecten (een terrarium, een lamp, een \
theekopje, een horloge), fotorealistisch gerenderd, met warme, filmische belichting \
en een gevoel van verwondering en schaal-illusie.

Bedenk elke keer een NIEUW, onderscheidend concept — varieer settings, seizoenen, \
kleurenpaletten en het object waarin de wereld verstopt zit. Vermijd herhaling van \
eerdere clichés (geen generieke kristallen bollen, geen simpele terrariums zonder twist).

Antwoord ALLEEN met geldige JSON, geen uitleg, geen markdown-codeblok, in dit formaat:
{
  "concept_titel": "korte titel voor eigen administratie",
  "image_prompt": "gedetailleerde Engelstalige prompt voor een text-to-image model, \
geoptimaliseerd voor een verticale 9:16 Reel-still. Beschrijf de COMPOSITIE EXPLICIET, niet \
alleen sfeerwoorden: (1) benoem het alledaagse object (bv. matchbox, teacup, pocket watch) als \
duidelijk herkenbaar hoofdonderwerp, scherp in beeld; (2) beschrijf dat het object omgedraaid, \
geopend of gekanteld is zodat de HOLLE BINNENKANT naar de camera gericht is, en dat de \
miniatuurwereld daadwerkelijk IN die holte zit — met zichtbare wanden/rand van het object rondom \
de wereld die diepte en fysieke omsluiting tonen (bv. 'the thimble is tipped on its side, opening \
facing the camera, so the viewer looks directly into its hollow interior'). Vermijd expliciet een \
plat of geprojecteerd effect waarbij de wereld op het buitenoppervlak lijkt te zijn geplakt of \
geschilderd — het moet ondubbelzinnig een fysieke ruimte zijn waar de kijker in kijkt, geen losse \
elementen die los van het object in de lucht zweven; (3) benoem de camera-hoek (bv. 'eye-level macro shot', \
'top-down view into the open box') en waar het onderwerp in het frame staat (bv. 'object \
centered, filling the lower two-thirds of the vertical frame'); (4) beschrijf pas daarna stijl, \
belichting en materiaal-detail",
  "caption": "Engelstalige caption, kort en sfeervol, 1-3 zinnen",
  "hashtags": ["#..." , "#..."]
}
Gebruik 8 tot 15 relevante hashtags, mix van niche (miniatuurwerk, dioramakunst) en \
breder bereik (AI art, surreal art).
"""


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"Ontbrekende environment variable: {name}")
    return val


# ---------------------------------------------------------------------------
# Stap 1 — Concept + caption via Claude
# ---------------------------------------------------------------------------

def generate_concept(anthropic_api_key: str, max_attempts: int = 3) -> dict:
    log("Vraag Claude om een nieuw diorama-concept...")
    client = Anthropic(api_key=anthropic_api_key)
    required_keys = {"image_prompt", "caption", "hashtags"}

    for attempt in range(1, max_attempts + 1):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=BRAND_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Genereer het concept voor vandaag, {datetime.now().strftime('%d %B %Y')}.",
                }
            ],
        )
        if response.stop_reason == "max_tokens":
            log(f"  ...poging {attempt}/{max_attempts}: antwoord afgekapt door max_tokens, probeer opnieuw.")
            continue

        raw_text = "".join(block.text for block in response.content if block.type == "text").strip()
        # Claude wikkelt het antwoord soms in een ```json-codeblok, ondanks de instructie dat niet te doen.
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        try:
            concept = json.loads(raw_text)
        except json.JSONDecodeError as e:
            log(f"  ...poging {attempt}/{max_attempts}: ongeldige JSON ({e}), probeer opnieuw.")
            continue

        missing = required_keys - concept.keys()
        if missing:
            log(f"  ...poging {attempt}/{max_attempts}: velden ontbreken ({missing}), probeer opnieuw.")
            continue

        log(f"Concept: {concept.get('concept_titel', '(geen titel)')}")
        return concept

    sys.exit(f"Claude gaf {max_attempts} keer achter elkaar geen bruikbare JSON terug.")


# ---------------------------------------------------------------------------
# Stap 2 — Still-afbeelding via Leonardo.ai
# ---------------------------------------------------------------------------

def leonardo_headers(api_key: str) -> dict:
    return {
        "accept": "application/json",
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }


# Naam van het model dat we via platformModels opzoeken voor de still-generatie. Leonardo's eigen
# foundationmodel Phoenix 1.0 is uitgekozen om zijn sterke prompt-adherence — belangrijk om het
# object en de compositie herkenbaar te houden i.p.v. een vage sfeervolle textuur.
LEONARDO_PHOTOREAL_MODEL_NAME = "Phoenix 1.0"


def _find_leonardo_model_id(leonardo_api_key: str, model_name: str) -> str:
    """Zoekt via Leonardo's platformModels-endpoint het model-ID op dat bij model_name hoort."""
    resp = requests.get(
        f"{LEONARDO_BASE}/platformModels",
        headers=leonardo_headers(leonardo_api_key),
        timeout=30,
    )
    resp.raise_for_status()
    for model in resp.json().get("custom_models", []):
        if model.get("name") == model_name:
            return model["id"]
    sys.exit(f"Kon Leonardo-model '{model_name}' niet vinden via platformModels.")


def generate_image(leonardo_api_key: str, image_prompt: str) -> str:
    log("Genereer still-afbeelding via Leonardo.ai...")
    model_id = _find_leonardo_model_id(leonardo_api_key, LEONARDO_PHOTOREAL_MODEL_NAME)
    resp = requests.post(
        f"{LEONARDO_BASE}/generations",
        headers=leonardo_headers(leonardo_api_key),
        json={
            "prompt": image_prompt,
            "num_images": 1,
            "width": VIDEO_WIDTH,
            "height": VIDEO_HEIGHT,
            "modelId": model_id,
            "presetStyle": "CINEMATIC",
            "alchemy": True,
        },
        timeout=30,
    )
    resp.raise_for_status()
    generation_id = resp.json()["sdGenerationJob"]["generationId"]

    _, image_url = _poll_leonardo_generation(leonardo_api_key, generation_id)
    log(f"Still klaar: {image_url}")
    return image_url


def _poll_leonardo_generation(api_key: str, generation_id: str, url_field: str = "url", max_wait_s: int = 300):
    """Poll GET /generations/{id} totdat status COMPLETE is EN het gevraagde url-veld gevuld is
    (bij image-to-video blijft 'generated_images[].motionMP4URL' nog even null nadat de status al
    COMPLETE is). Geeft (id, url) van het eerste item in 'generated_images' terug."""
    start = time.time()
    while time.time() - start < max_wait_s:
        resp = requests.get(
            f"{LEONARDO_BASE}/generations/{generation_id}",
            headers=leonardo_headers(api_key),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("generations_by_pk", {})
        status = data.get("status")
        if status == "FAILED":
            sys.exit(f"Leonardo-generatie mislukt: {data}")
        if status == "COMPLETE":
            items = data.get("generated_images") or []
            if not items:
                sys.exit(f"Leonardo meldt COMPLETE maar 'generated_images' is leeg: {data}")
            first = items[0]
            url = first.get(url_field)
            if url:
                return first["id"], url
        log(f"  ...status: {status}, nog even wachten")
        time.sleep(5)
    sys.exit("Timeout bij wachten op Leonardo-generatie.")


# ---------------------------------------------------------------------------
# Stap 3 — Pan/zoom-video via ffmpeg (Ken Burns-effect)
# ---------------------------------------------------------------------------

def download_file(url: str, dest_path: str) -> None:
    log(f"Download {url} naar {dest_path}...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)


def create_pan_zoom_video(image_path: str, output_path: str, duration: int = 5) -> None:
    """Maakt van een still-afbeelding een verticale video met een langzame zoom-in (Ken Burns-effect),
    puur lokaal met ffmpeg — geen AI-videogeneratie nodig. Een stille audiotrack wordt toegevoegd
    omdat Instagram's Reels-validatie soms strenger is voor video's zonder audiospoor."""
    log(f"Maak pan/zoom-video van de still ({duration}s)...")
    fps = 25
    frames = duration * fps
    # zoompan berekent het zoom-venster op de resolutie die het als input krijgt. Op de uiteindelijke
    # 864x1536 zijn de stapjes per frame zo klein dat ze soms op dezelfde pixel afronden, wat als
    # trillende beweging oogt. Fix: eerst flink opschalen (subpixel-precisie), zoompan schaalt via
    # zijn eigen 's=' vervolgens terug naar de output-resolutie.
    upscale = 4
    upscaled_w, upscaled_h = VIDEO_WIDTH * upscale, VIDEO_HEIGHT * upscale
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-vf",
        # x/y sturen het zoom-doel: horizontaal gecentreerd, verticaal richting het onderste deel
        # van het frame — daar staat het object/de miniatuurwereld volgens onze compositie-instructie
        # aan Claude ("filling the lower two-thirds of the vertical frame"). Zonder expliciete x/y
        # zoomt zoompan standaard naar de linkerbovenhoek, wat niet is wat we willen.
        f"scale={upscaled_w}:{upscaled_h}:force_original_aspect_ratio=increase,"
        f"crop={upscaled_w}:{upscaled_h},"
        f"zoompan=z='min(zoom+0.002,1.28)'"
        f":x='iw/2-(iw/zoom/2)'"
        f":y='max(0,min(ih*0.65-(ih/zoom/2),ih-ih/zoom))'"
        f":d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={fps}",
        "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("ffmpeg niet gevonden — installeer het (bv. 'brew install ffmpeg' op macOS).")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ffmpeg-fout bij het maken van de pan/zoom-video:\n{e.stderr}")
    log(f"Pan/zoom-video klaar: {output_path}")


# ---------------------------------------------------------------------------
# Stap 4 — Publiceren via Instagram Graph API
# ---------------------------------------------------------------------------

def create_media_container(
    ig_user_id: str, access_token: str, video_public_url: str, media_type: str, caption: str = None
) -> str:
    log(f"Maak {media_type}-media-container aan bij Instagram...")
    data = {
        "media_type": media_type,
        "video_url": video_public_url,
        "access_token": access_token,
    }
    if caption:  # Stories ondersteunen geen caption-veld
        data["caption"] = caption
    resp = requests.post(
        f"{IG_GRAPH_BASE}/{ig_user_id}/media",
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    creation_id = resp.json()["id"]
    log(f"Container aangemaakt: {creation_id}")
    return creation_id


def wait_until_ready(creation_id: str, access_token: str, max_wait_s: int = 600) -> None:
    log("Wacht tot Instagram de video heeft verwerkt...")
    start = time.time()
    while time.time() - start < max_wait_s:
        resp = requests.get(
            f"{IG_GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            log("Video is verwerkt, klaar om te publiceren.")
            return
        if status == "ERROR":
            sys.exit("Instagram meldt een fout bij het verwerken van de video.")
        log(f"  ...status: {status}, nog even wachten")
        time.sleep(10)
    sys.exit("Timeout bij wachten tot Instagram de video heeft verwerkt.")


def publish_media(ig_user_id: str, access_token: str, creation_id: str) -> None:
    log("Publiceer...")
    resp = requests.post(
        f"{IG_GRAPH_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    log(f"Gepubliceerd! Media-id: {resp.json().get('id')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_generate() -> None:
    """Stap A: concept -> beeld -> pan/zoom-video -> lokaal opslaan (video + caption.json). Publiceert NIET."""
    anthropic_key = require_env("ANTHROPIC_API_KEY")
    leonardo_key = require_env("LEONARDO_API_KEY")

    concept = generate_concept(anthropic_key)
    image_url = generate_image(leonardo_key, concept["image_prompt"])

    today = datetime.now().strftime("%Y-%m-%d")
    video_filename = f"{today}.mp4"
    meta_filename = f"{today}.json"
    os.makedirs("media", exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        still_path = tmp.name
    try:
        download_file(image_url, still_path)
        create_pan_zoom_video(still_path, os.path.join("media", video_filename))
    finally:
        os.remove(still_path)

    caption = concept["caption"] + "\n\n" + " ".join(concept["hashtags"])
    with open(os.path.join("media", meta_filename), "w") as f:
        json.dump({"video_filename": video_filename, "caption": caption}, f, indent=2)

    log(f"Klaar. Video: media/{video_filename}  |  Metadata: media/{meta_filename}")
    log("Volgende stap: commit + push deze twee bestanden, en draai dan `--publish` voor dezelfde datum.")


def cmd_publish(date: str) -> None:
    """Stap B: leest de eerder opgeslagen video + caption en publiceert 'm op Instagram.
    Ga ervan uit dat media/<date>.mp4 en media/<date>.json al gepusht zijn naar GitHub."""
    ig_user_id = require_env("IG_USER_ID")
    ig_access_token = require_env("IG_ACCESS_TOKEN")
    media_public_base_url = require_env("MEDIA_PUBLIC_BASE_URL")

    meta_path = os.path.join("media", f"{date}.json")
    if not os.path.exists(meta_path):
        sys.exit(f"Bestand niet gevonden: {meta_path} (heb je eerst `--generate` gedraaid?)")
    with open(meta_path) as f:
        meta = json.load(f)

    video_public_url = f"{media_public_base_url.rstrip('/')}/{meta['video_filename']}"
    log(f"Publieke video-URL die Instagram gaat ophalen: {video_public_url}")

    creation_id = create_media_container(ig_user_id, ig_access_token, video_public_url, "REELS", meta["caption"])
    wait_until_ready(creation_id, ig_access_token)
    publish_media(ig_user_id, ig_access_token, creation_id)

    # Ook delen als Story, zodat 'ie meteen in het verhaal van het account verschijnt.
    story_creation_id = create_media_container(ig_user_id, ig_access_token, video_public_url, "STORIES")
    wait_until_ready(story_creation_id, ig_access_token)
    publish_media(ig_user_id, ig_access_token, story_creation_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--generate",
        action="store_true",
        help="Genereer concept/beeld/video en sla lokaal op in media/. Publiceert niet.",
    )
    mode.add_argument(
        "--publish",
        metavar="YYYY-MM-DD",
        help="Publiceer een eerder gegenereerde en gepushte video voor deze datum.",
    )
    args = parser.parse_args()

    if args.generate:
        cmd_generate()
    else:
        cmd_publish(args.publish)


if __name__ == "__main__":
    main()
