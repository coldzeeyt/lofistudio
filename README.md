# Lofi Studio

React + TypeScript + Tailwind PWA, with a FastAPI/FFmpeg renderer and durable Redis queue. Upload nature footage, import music with permission, personalize the channel signature, and export a 10-minute, 30-minute, 1-hour or 3-hour H.264/AAC MP4 at 720p or 1080p.

## Run locally

With Docker Compose installed:

```sh
docker compose up --build
```

Open http://localhost:8000. Redis and media have persistent named volumes.

For development, install FFmpeg, DejaVu fonts, Redis 7, Python 3.12 and Node 22, then run from the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
redis-server --daemonize yes
python backend/prepare_library.py
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

The standard root Dockerfile is used by Railway. For a managed execution environment with a session proxy CA, `Dockerfile.cloud` accepts an optional BuildKit `proxy_ca` secret; mount the environment CA at build time rather than embedding it into the image.

The Vite proxy forwards `/api` to the backend. Production serves the compiled frontend from FastAPI. PWA installation and the service worker require HTTPS or localhost. Android/desktop show an install button when the browser permits; iPhone uses Safari → Share → Add to Home Screen. Offline access displays the cached UI; uploading, rendering status and downloads require connectivity.

## Rendering and music

1. Browser uploads use 1 MiB chunks with automatic connection retries, byte progress, and a saved upload position. Selecting the same file after an interruption resumes that session. The server accepts repeated chunks idempotently and validates the completed media before creating an asset. Uploaded files are streamed to UUID directories with per-file size limits, parsed by FFprobe, and restricted to approved media demuxers and local protocols. Assets and jobs require a private random bearer token kept in browser local storage; source paths are never returned.
2. FFmpeg first normalizes phone footage to the target dimensions and 24 fps using bounded decoder/encoder threads. It then seeks separate tail, head and middle inputs to avoid buffering the entire video. It builds one video cycle by dissolving its tail into its head and adjoining the middle. The cycle repeats with matching joins and gentle nature grading.
3. The 30 bundled songs are loudness-normalized into lossless FLAC during the Docker build, so jobs skip their normalization entirely. Imported music is normalized on its first use. Selected tracks are crossfaded into a playlist, then given a tail/head audio crossfade for repeated playback.
4. `showwaves` renders a scrolling waveform and `showfreqs` renders a frequency spectrum from the very same normalized music stream mapped into the output. Optional original nature audio is mixed separately; both visualizers react to music, not the ambient sound. Export metadata is stripped, including camera location tags. Channel text is read from a text file with expansion disabled; optional logos are overlaid.
5. FFmpeg's output timestamps drive stage progress. Redis stores the queue, cancellation and status independently of the browser. The single background worker terminates FFmpeg on cancellation. Interrupted active jobs are marked failed after a server restart, and queued jobs resume. Completed files download through short-lived private links.
6. Titles/descriptions are deterministic templates, with the actual selected track metadata included. No AI API calls.

Prepared nature loops and imported music are cached privately by content hash and preparation settings, so re-uploading identical footage can reuse its loop. Branding, playlist transitions and the music-synchronized waveform/spectrum are rendered for each export. The visualizers share one overlay to reduce full-frame processing. The cache uses least-recently-used eviction with a default 256 MiB budget between jobs; active inputs remain protected until rendering finishes. Cached files count toward the media storage budget and retention cleanup. The encoder retains its existing `veryfast` preset and CRF 20 quality setting.

The built-in library contains **30 lo-fi songs by HoliznaCC0**, published by the artist under CC0 1.0 on OpenGameArt. See [music provenance](library/LICENSES.md) and the per-track source URLs and hashes in `library/manifest.json`. Three tracks are selected automatically; users can shuffle, preview or choose up to ten tracks. The actual audio is bundled, so the renderer works without a music API or external download.

For additional imported music, users enter title, artist, license/permission and confirm they can use it on YouTube. Imported credits record the user's assertion and do not independently verify ownership.

To expand the operator-reviewed built-in library, place audio files under `library/` and add entries to `library/manifest.json`:

```json
[{"id":"unique-track-id","title":"Track title","artist":"Artist","license":"CC BY 4.0","source":"https://actual-license-and-source-page.example","file":"track.wav","sha256":"replace-with-the-exact-audio-file-sha256"}]
```

Replace that example with genuine, reviewed license information, the actual file and its 64-character SHA-256 checksum. The preparation build checks that checksum before processing. For commercial/monetized use, check that the license permits the intended use. The interface automatically selects a licensed playlist and allows changes; imported tracks form a repeating playlist. Music transitions use up to 2-second crossfades and shorter transitions for very short tracks.

## Railway deployment

Create a Railway project with **two services**:

1. **Redis**: add Railway's Redis template. Keep its persistent volume and enable AOF persistence. Use a bounded memory limit with `noeviction` so metadata is not silently evicted.
2. **Lofi Studio**: connect this GitHub repository. Railway builds the root `Dockerfile`. `.railway/railway.ts` defines the project resources using current Railway Infrastructure as Code. Config as Code (`railway.toml`) is deprecated and is not used. Attach a persistent volume mounted at **`/data`**. Generate a public HTTPS domain for this service.

Set service variables:

| Variable | Value / purpose |
| --- | --- |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` (replace `Redis` with your Redis service name) |
| `DATA_DIR` | `/data` |
| `MAX_UPLOAD_MB` | `250` maximum bytes per upload |
| `MAX_STORAGE_GB` | `4` for a 5000 MB Railway volume; use `40` with larger local/production storage |
| `MAX_ACTIVE_JOBS` | `3`, global running + queued limit |
| `MAX_RENDER_SECONDS` | `10800`; use `600` initially to reduce costs |
| `RETENTION_HOURS` | `24` |
| `PREPARATION_CACHE_MB` | `256`, reusable loop/imported-music cache budget |
| `MAX_DAILY_RENDER_SECONDS` | `21600`, global six hours of requested video per UTC day |

Railway injects `PORT`. The container runs exactly **one Uvicorn process and one service replica**. Do not enable horizontal scaling: this design keeps the queue consumer alongside the API on the same persistent volume, since Railway volumes cannot be shared between separate services. Redis is a separate service. A future multi-worker version should move media to object storage and use distributed leases.

Provision 2 vCPU and at least 2 GB RAM to start, and benchmark your own footage before opening public access. Rendering 3-hour 1080p videos is substantial CPU, disk and bandwidth work. FFmpeg limits encoder threads, processes one render at a time, and enforces a wall-time limit. Configure Railway usage alerts and spending limits yourself in the workspace billing settings. A spending limit may stop services rather than preserve uninterrupted rendering.

**Before a public launch:** add account authentication, account quotas, edge rate limiting, and monitor actual usage. The current anonymous session model provides private jobs and global queue/storage limits, but is not paid-account access control: a person can create multiple sessions. Keep an initial deployment private or behind access control. Apply an HTTP request-size limit at your ingress and use Railway metrics for CPU, memory, disk and egress. Uploads are streamed and disk-limited in-app; admission reserves estimated scratch/output space for active jobs. The encoder caps its peak bitrate to fit the admitted output budget, with a hard file-size ceiling. On the 5 GB Railway volume, 3-hour exports use a lower bitrate; resolution stays 720p/1080p, but detailed footage may look softer. Increasing storage allows a higher bitrate. A global six-hour requested-video budget per UTC day bounds submissions even across anonymous sessions; cancellation consumes that budget conservatively. A truncated file is marked failed rather than offered as a complete export.

Cleanup runs on the worker loop. Expired job/media folders are removed after the retention window; source folders referenced by active jobs are protected, intermediate files are removed when a render ends. Download availability is up to 24 hours from job creation (Redis TTL renews during progress). Clearing browser storage loses access to old renders. Saving the browser token is not an account recovery system.

Cloning/building the repo creates no live resources. To manage a Railway project from the provided IaC file, run `npm ci --prefix .railway`, link the intended project with the Railway CLI, then review `railway config plan` before `railway config apply`. Generated `*.up.railway.app` domains are created in the dashboard/CLI and omitted from the IaC file. The live deployment created for this project is recorded in `DEPLOYMENT.md`.

## Validation

With a local Redis running, execute:

```sh
python backend/prepare_library.py
python -m pytest backend/tests -q
npm ci --prefix frontend
npm run build --prefix frontend
```

Tests use Redis **database 15**, flush that database, and generate synthetic media in `/tmp/lofi-integration`. Use a disposable local Redis, never production. They exercise actual uploads and validation, ownership, queue admission, cancellation, FFmpeg rendering at 720p/1080p, two-track crossfades, original audio mixing, safe channel text, logo overlay, export dimensions/duration/codecs, and complete MP4 decoding. Short test durations are renderer-only; the public API restricts durations to the four supported long-form choices.

See `VALIDATION.md` for the checks actually run in the build environment and their limits.

If a render fails during initial preparation, use **Retry render** to reuse its uploaded source. Two retries are allowed for the same prepaid job; they do not charge the global requested-video budget again. Expired source files must be uploaded again.
