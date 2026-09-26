# Cloud deployment for ChatGPT

This repo can run as a single authenticated cloud service: the upstream Maps scraper runs privately inside the container and a small Python gateway is the only public port.

## What the cloud API provides

- `POST /v1/school-search` — starts an asynchronous school search.
- `GET /v1/jobs/{job_id}` — status, preview, and download links.
- `GET /v1/jobs/{job_id}/results` — JSON records for GPT.
- Short-lived-style per-job random download URLs for CSV/XLSX (valid while the instance is alive).
- `GET /openapi.json` — dynamic OpenAPI schema with the correct deployed hostname.
- `GET /health` — health check.
- `X-API-Key` authentication for all search/result endpoints.

The service deliberately runs one Maps scrape at a time. That is friendlier to a small free instance and reduces aggressive scraping/rate-limit risk.

## Deploy on Render

1. Merge the feature PR into `master`.
2. Sign in to Render and choose **New → Blueprint**.
3. Connect `testpreppublication/google-maps-scraper-kit`.
4. Render will read `render.yaml` and build `Dockerfile.cloud`.
5. The Blueprint generates `SCHOOL_API_KEY` automatically. After deploy, open the service's **Environment** page and copy the generated value. Keep it private.
6. Open `https://YOUR-SERVICE.onrender.com/health`. You should see `"status":"ok"`.
7. Open `https://YOUR-SERVICE.onrender.com/openapi.json` to verify the GPT schema.

Optional: add `BRAVE_SEARCH_API_KEY` in Render Environment if you later want automatic public-web person-profile candidate discovery. Do not commit this key.

## Connect to a Custom GPT / GPT Action

1. In the GPT editor, add an Action.
2. Import or paste the schema from `https://YOUR-SERVICE.onrender.com/openapi.json`.
3. Authentication: **API Key**.
4. Header name: `X-API-Key`.
5. Value: the generated `SCHOOL_API_KEY` from Render.
6. Save and test with:
   - “Find CBSE schools in Lucknow. Use depth 5.”
   - The GPT should call `startSchoolSearch`, poll `getSchoolSearchJob`, then call `getSchoolSearchResults`.

## Notes about free hosting

Free instances can sleep, restart, and use ephemeral storage. A restart can lose in-memory job status/results. Treat the free deployment as a trial/small-job setup. For reliable production use, move to persistent/paid compute.

The Maps scraper can be rate limited by Google. Keep depth conservative (default 5, cloud maximum 10), avoid many back-to-back jobs, and follow applicable terms/privacy/anti-spam requirements.
