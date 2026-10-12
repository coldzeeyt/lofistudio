# Railway deployment

- Project: [Lofi Studio](https://railway.com/project/6f582474-f153-4717-91db-6ba6d8b8681f)
- Environment: production
- App: `lofi-studio`, one replica, 2 vCPU / 2 GB ceiling, persistent `/data` volume (5000 MB)
- Redis: Railway Redis template, private networking, authentication, persistent volume, AOF every second, 256 MB Redis memory limit with `noeviction`
- Generated domain: https://lofi-studio-production.up.railway.app
- Source repository: https://github.com/coldzeeyt/lofistudio, branch `main`

The app media budget is 4 GiB to leave headroom in the plan's 5000 MB volume. Storage-aware bitrate ceilings preserve the chosen dimensions while allowing long videos to fit; long 1080p exports may have lower detail than those rendered with larger storage. Admissions reject jobs when even the minimum budget cannot fit. Media retention is 24 hours. Global submission limits: three active/queued jobs, one worker, six hours of requested video per UTC day.

The generated domain is configured in Railway, not hard-coded into the PWA. The API and UI use the same origin, so HTTPS uploads and downloads work without CORS or frontend environment changes.

See README.md for local use, configuration variables, redeploy instructions and public access limitations. See VALIDATION.md for tests and actual verification limits.

Verified live: 2026-10-12, deployment `7a2121e3-7227-4bde-a968-119182049976`, source commit `e97a5d5`. HTTPS health and a full 10-minute render/download/decode passed. App and Redis are online. See VALIDATION.md.

Automatic app redeploys watch `backend/`, `frontend/`, `library/` and the root Dockerfile. Documentation-only updates do not restart the renderer.
