# Pocket Worlds — automatische Instagram Reel-pipeline

Genereert dagelijks automatisch een nieuw diorama-concept (Claude), een still en
Reel-video (Leonardo.ai), en publiceert die op Instagram (pocket.worlds.ai).

## Wat je al hebt (uit eerdere stappen)

- Instagram app ID + app secret (Meta Developer Dashboard)
- Instagram access token (60 dagen geldig)

## Wat je nog nodig hebt

1. **Anthropic API key** — console.anthropic.com → API Keys → Create Key.
2. **Leonardo.ai API key** — leonardo.ai → Account settings → API Keys.
3. **Je numerieke Instagram User ID** (niet de App ID!) — zie hieronder.

## Stap 1 — Lokaal testen (aanbevolen vóór je dit automatiseert)

```bash
cd pocket-worlds-pipeline
python -m venv venv && source venv/bin/activate   # of: py -m venv venv && venv\Scripts\activate (Windows)
pip install -r requirements.txt

cp .env.example .env
# vul .env in met je echte sleutels
```

Haal je Instagram User ID op:

```bash
IG_ACCESS_TOKEN=jouw_token python get_ig_user_id.py
```

Zet de geprinte `IG_USER_ID` in je `.env`.

Test daarna alleen het genereren (concept + beeld + video), zonder te posten:

```bash
source .env  # of: gebruik python-dotenv, of exporteer de variabelen handmatig
python generate_post.py --generate
```

Check `media/` — daar moet een `.mp4` en een `.json` staan. Bekijk de video en de
caption.

De publish-stap (`--publish`) kun je pas écht end-to-end testen zodra het bestand
ook publiek bereikbaar is — dat gebeurt pas nadat je dit naar GitHub hebt gepusht
(zie hieronder). Instagram moet de video via een openbare URL kunnen ophalen.

## Stap 2 — Naar GitHub

1. Maak een **nieuwe GitHub-repository** aan (publiek, zodat `raw.githubusercontent.com`
   de video's kan serveren zonder authenticatie).
2. Push deze map naar die repository.
3. Ga naar **Settings → Secrets and variables → Actions** en voeg toe:
   - `ANTHROPIC_API_KEY`
   - `LEONARDO_API_KEY`
   - `IG_USER_ID`
   - `IG_ACCESS_TOKEN`
   - `MEDIA_PUBLIC_BASE_URL` → `https://raw.githubusercontent.com/<jouw-gebruikersnaam>/<repo-naam>/main/media`

## Stap 3 — Testen via GitHub Actions

Ga naar het tabblad **Actions** in je repository, kies de workflow "Daily Instagram
Reel", en klik **Run workflow** om 'm handmatig één keer te draaien (in plaats van
te wachten op de dagelijkse cron). Volg de logs live mee.

Als dat goed gaat, staat de workflow al ingesteld om dagelijks om 07:00
(Nederlandse tijd, bij benadering) automatisch te draaien — verder geen omkijken
naar nodig.

## Onderhoud

- **Access token ververst niet vanzelf** in deze versie: hij verloopt na 60 dagen.
  Zet een herinnering, of laten we dit later automatiseren met het
  `refresh_access_token`-endpoint.
- **Repo-grootte**: elke dag komt er een videobestand bij in `media/`. Voor een
  hobby-schaal project is dat geen probleem, maar wil je dit op termijn opschonen,
  dan kunnen we een stap toevoegen die oude bestanden verwijdert.
