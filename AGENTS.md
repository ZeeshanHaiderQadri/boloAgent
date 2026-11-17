# Bolo - Project Overview

Bolo is a voice AI platform for building and deploying conversational AI agents with telephony and WebRTC support.

## Project Structure

```
bolo/
├── api/              # Backend - FastAPI application
├── ui/               # Frontend - Next.js application
├── scripts/          # Helper scripts for local development
├── docs/             # Mintlify documentation
├── pipecat/          # Pipecat framework (git submodule)
├── docker-compose.yaml       # Production/OSS deployment
├── docker-compose-local.yaml # Local development services
```

## Tech Stack

- **Backend**: Python with FastAPI
- **Frontend**: Next.js 15 with React 19, TypeScript, Tailwind CSS
- **Database**: PostgreSQL with SQLAlchemy (async)
- **Cache/Queue**: Redis with ARQ for background tasks
- **Storage**: MinIO (S3-compatible) for audio files

## Local Development

Contributor setup and service startup are documented in `docs/contribution/setup.mdx`.

## Environment Configuration

- `api/.env` - Backend environment variables. Source this when running repo-owned backend scripts against the dev DB (e.g. `python -m scripts.dump_docs_openapi`).
- `api/.env.test` - Test-only environment variables. Source this when running pytest so tests hit the test DB and never the dev/prod credentials in `api/.env`.
- `ui/.env` - Frontend environment variables

Typical invocation:

```bash
# Tests
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/...

# Backend scripts
source venv/bin/activate && set -a && source api/.env && set +a && python -m scripts.dump_docs_openapi
```

## Upstream & White-Label Conventions

Bolo is a white-label fork of **Dograh** (BSD 2-Clause, © 2025 Zansat Technologies
Private Limited). The upstream copyright notice is retained verbatim in `LICENSE`,
and attribution lives in `NOTICE`. Keep both intact when redistributing.

A handful of upstream identifiers are **deliberately preserved** because they are
contracts with code we do not own. Do not rename them:

| Identifier | Why it stays |
|---|---|
| `pipecat.services.dograh.*` and the classes it exports (`DograhLLMService`, `DograhTTSService`, `DograhSTTService`, `DograhSTTSettings`, `DograhTTSSettings`, `DograhFluxSTTService`) | Published by the `pipecat` submodule, a separate repository. Rename requires forking pipecat too. |
| `services.dograh.com` (default `MPS_API_URL`) | External Managed Platform Service host. Override the env var to self-host. |
| `dograhai/minio`, `github.com/dograh-hq/minio` | Community MinIO build; upstream MinIO stopped publishing images. |
| `DOGRAH_TTFS_P` | Environment variable read by the pipecat submodule. |

Everything else uses the **Bolo** name: `BOLO_*` env vars, `bolo-*` packages and
images, the `bolo` Helm chart, `@bolo/sdk`, `bolo-sdk`, and `boloagent.com` URLs.

`scripts/migrate/rename_to_bolo.py` documents the full rename mapping and the
protected set — read it before running bulk find-and-replace across the tree.

## Brand & Theme

The UI design system is **Teal Green** and lives in `ui/src/app/globals.css`
(CSS custom properties in `:root` / `.dark`). Brand artwork is generated, not
hand-edited:

```bash
python3 scripts/brand/generate_brand_assets.py   # logo, mark, inverse, imprint, favicon
```

It needs Pillow and a locally installed geometric sans (Avenir Next / Futura).
Change the palette constants at the top of that script, then re-run it and
update the `--cta` / `--primary` values in `globals.css` to match. The docs
palette lives in `docs/docs.json` (`colors.primary` / `light` / `dark`).
