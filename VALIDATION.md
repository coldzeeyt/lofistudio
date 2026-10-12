# Validation performed

Build environment: Python 3.12, Node 22-compatible tooling, FFmpeg, a real Redis 7 server, Chromium via Playwright, and Docker. No mocked renderer or simulated progress.

- `npm run build --prefix frontend`: TypeScript and production Vite build passed.
- `python -m pytest backend/tests -q`: **9 tests passed** in the final run. Covers actual upload/probe validation, owner isolation, admission limits, queued and running cancellation, active-source retention, rejection of external playlists and invalid files, 720p/1080p renders with two-track crossfades, original sound mixing, channel text and logo, full decode of short MP4 exports, all 30 library file hashes, range-based music preview, and a real three-song library export with waveform and spectrum.
- Production multi-stage Docker image built successfully, with the optional environment CA mounted only at build time. Redis/app containers started and `/api/health` returned OK.
- Playwright completed the upload → music import → branding → Generate workflow; progress survived reload. A full 600-second 720p MP4 downloaded successfully through the actual download-ticket endpoint and decoded completely without FFmpeg errors (150,743,022 bytes, H.264 + AAC).
- Playwright checked the production container's **30-song library**, automatic selection of three tracks, actual audio preview playback, branding and generation, and persistence of job status after reload.
- Desktop and 390px mobile browser checks: no JavaScript errors or horizontal overflow. Manifest and PWA PNG icon returned HTTP 200. Screenshots are in `docs/screenshots/`.

Two 600-second exports were tested: the imported-music workflow and a production-container built-in playlist with waveform+spectrum (74,960,090 bytes, 1280×720, H.264/AAC). Both downloaded and fully decoded. The completed-video browser test verified title/description/credits, clipboard copying, and the actual MP4 download button. Pixel comparison confirmed the waveform changes with the music.

A subsequent repeat-boundary check exposed an FFmpeg scheduling issue when the same audio input fed both sides of a tail/head crossfade. The fix uses independent playlist inputs and sample-count trims. The final regression verifies the exact retained seam length and the first/last-sample continuity (zero sample jump for the real-library fixture). The video cycle boundary changed by 5.472 mean pixel levels, within the normal adjacent-frame range (95th percentile 7.111). Final regression coverage also verifies a 3-hour 1080p job fits a 4 GiB media budget and the daily global quota rejects excess submissions.

Limitations: 30-minute, 1-hour and 3-hour exports use the same bounded-loop pipeline but were not rendered end-to-end in this environment. Real iPhone/Android installation was not tested on hardware. Live Railway deployment status is recorded in DEPLOYMENT.md; workspace billing alerts/spending settings were not changed. The current single-replica session-token model needs accounts/edge quotas before unrestricted public launch; see README.
