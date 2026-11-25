# PodAtlas

> A *Radio Garden*-style interactive **Podcast World Map** paired with an AI-extracted **Books, Places & Gear Mention Index** — hosted for **$0/month on Cloudflare Pages Free Tier** and indexed offline with **Google Colab GPU credits**.

## Architecture

```text
[Podcast Index API / RSS] ──► [Google Colab GPU (200 CUs/mo)] ──► [GitHub Repo]
                                ├─ Check <podcast:transcript>           │
                                ├─ faster-whisper (large-v3-turbo)      ▼
                                ├─ Qwen2.5-7B / Qwen3-8B (JSON)   [Cloudflare Pages ($0)]
                                └─ GeoNames + Open Library          ├─ MapLibre GL JS + OpenFreeMap
                                                                    ├─ public/cities.geojson
                                                                    ├─ public/data/{city-slug}.json
                                                                    └─ public/search-index.json
```

## Monorepo Structure

- `web/` — Pure **Astro 5** (zero React) + TypeScript + Tailwind CSS v4 static site pre-rendering every `/city/:slug` page to HTML at build time, paired with MapLibre GL JS + OpenFreeMap vector tiles, Fuse.js search, and a persisted HTML5 `<audio>` player across View Transitions.
- `pipeline/` — Modular Python 3.11+ ingestion, transcription, LLM extraction, GeoNames geocoding, Open Library book verification, and static JSON exporter, plus a turnkey Colab notebook (`pipeline/notebooks/PodAtlas_Pipeline.ipynb`).
- `scripts/` — `seed_from_prototype.py` to generate a validated 10-city starter dataset into `web/public/`.

## Quick Start (Frontend)

```bash
cd web
cp .env.example .env.local   # Optional: add affiliate IDs & Stripe Trip Pack URL
npm install
npm run dev
```

Build for production (Cloudflare Pages):
- **Build command:** `npm run build`
- **Build output directory:** `dist`
- **Root directory:** `web`

## Running Tests

### Frontend
```bash
cd web
npm run typecheck
npm run test
npm run build
```

### Pipeline
```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

## Running the Colab GPU Pipeline (When Ready)

1. Open `pipeline/notebooks/PodAtlas_Pipeline.ipynb` in Google Colab with an **L4** or **T4** GPU runtime.
2. Add your free [Podcast Index API](https://podcastindex.org/) credentials (`PODCAST_INDEX_KEY`, `PODCAST_INDEX_SECRET`) to Colab Secrets.
3. Run the notebook in pilot mode (`--max-episodes 5`) first to QA output, then run the full batch and push `web/public/` to GitHub to auto-deploy on Cloudflare Pages.
