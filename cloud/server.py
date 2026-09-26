#!/usr/bin/env python3
"""Secure cloud gateway for the school scraping pipeline.

Runs the upstream google-maps-scraper locally inside the same container and exposes
an authenticated async API suitable for a Custom GPT / GPT Action.

No third-party Python packages are required.
"""

import csv
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/data/jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.getenv("PORT", "10000"))
API_KEY = os.getenv("SCHOOL_API_KEY", "").strip()
SCRAPER_BASE = os.getenv("SCRAPER_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
MAX_DEPTH = int(os.getenv("MAX_DEPTH", "10"))

_jobs = {}
_jobs_lock = threading.Lock()
_run_lock = threading.Lock()
_scraper_proc = None


def load_school_module():
    p = ROOT / "scripts" / "scrape_schools.py"
    spec = importlib.util.spec_from_file_location("scrape_schools", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCHOOL_MOD = load_school_module()


def start_scraper():
    global _scraper_proc
    cmd = ["google-maps-scraper", "-web", "-data-folder", "/gmapsdata"]
    _scraper_proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            req = Request(SCRAPER_BASE + "/api/v1/jobs", headers={"User-Agent": "testprep-school-api/1.0"})
            with urlopen(req, timeout=3) as r:
                if 200 <= r.status < 500:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("google-maps-scraper did not become ready")


def public_base(handler):
    proto = handler.headers.get("X-Forwarded-Proto", "https")
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host", "")
    return f"{proto}://{host}".rstrip("/")


def api_key_ok(handler):
    if not API_KEY:
        return False
    supplied = handler.headers.get("X-API-Key", "")
    if not supplied:
        auth = handler.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return secrets.compare_digest(supplied, API_KEY)


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def safe_job(job_id):
    if not job_id or any(ch not in "0123456789abcdef-" for ch in job_id.lower()):
        return None
    with _jobs_lock:
        return _jobs.get(job_id)


def run_job(job_id, query, city, depth, no_email):
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    csv_path = job_dir / "result.csv"
    xlsx_path = job_dir / "result.xlsx"
    log_path = job_dir / "run.log"

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()

    try:
        # One scraper job at a time is intentional: safer for Maps rate limiting and small cloud instances.
        with _run_lock:
            cmd = [
                sys.executable, str(ROOT / "scripts" / "scrape_schools.py"),
                query, "--city", city, "--depth", str(depth),
                "--out", str(csv_path),
            ]
            if no_email:
                cmd.append("--no-email")

            env = os.environ.copy()
            env["SCRAPER_BASE_URL"] = SCRAPER_BASE
            with open(log_path, "w", encoding="utf-8") as log:
                p = subprocess.run(
                    cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                    timeout=int(os.getenv("JOB_TIMEOUT_SECONDS", "1200")),
                )
            if p.returncode != 0:
                raise RuntimeError(f"pipeline exited with code {p.returncode}")

            SCHOOL_MOD.xlsx_from_csv(csv_path, xlsx_path)

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        with _jobs_lock:
            _jobs[job_id].update({
                "status": "completed",
                "completed_at": time.time(),
                "count": len(rows),
                "preview": rows[:20],
                "csv_path": str(csv_path),
                "xlsx_path": str(xlsx_path),
            })
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "failed",
                "completed_at": time.time(),
                "error": str(e),
            })


def openapi_schema(base):
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Testprep School Search API",
            "version": "1.0.0",
            "description": "Search Google Maps for schools, enrich public school leadership information, and prepare CSV/XLSX results."
        },
        "servers": [{"url": base}],
        "paths": {
            "/v1/school-search": {
                "post": {
                    "operationId": "startSchoolSearch",
                    "summary": "Start a school search and enrichment job",
                    "security": [{"ApiKeyAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["query", "city"],
                            "properties": {
                                "query": {"type": "string", "example": "CBSE schools in Lucknow"},
                                "city": {"type": "string", "example": "Lucknow, Uttar Pradesh"},
                                "depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH, "default": 5},
                                "no_email": {"type": "boolean", "default": False}
                            }
                        }}}
                    },
                    "responses": {"202": {"description": "Job accepted"}}
                }
            },
            "/v1/jobs/{job_id}": {
                "get": {
                    "operationId": "getSchoolSearchJob",
                    "summary": "Check job status and get result preview",
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Job status"}}
                }
            },
            "/v1/jobs/{job_id}/results": {
                "get": {
                    "operationId": "getSchoolSearchResults",
                    "summary": "Get completed school results as JSON",
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        {"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}
                    ],
                    "responses": {"200": {"description": "Result records"}}
                }
            }
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
            }
        }
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TestprepSchoolAPI/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, code, obj):
        data = json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def require_auth(self):
        if api_key_ok(self):
            return True
        self.send_json(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"

        if path == "/health":
            self.send_json(200, {
                "status": "ok",
                "scraper_process": bool(_scraper_proc and _scraper_proc.poll() is None),
                "api_key_configured": bool(API_KEY),
            })
            return

        if path in {"/openapi.json", "/openapi"}:
            self.send_json(200, openapi_schema(public_base(self)))
            return

        if not self.require_auth():
            return

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["v1", "jobs"]:
            job_id = parts[2]
            job = safe_job(job_id)
            if not job:
                self.send_json(404, {"error": "job_not_found"})
                return

            if len(parts) == 3:
                base = public_base(self)
                out = {k: v for k, v in job.items() if not k.endswith("_path")}
                if job.get("status") == "completed":
                    token = job["download_token"]
                    out["results_url"] = f"{base}/v1/jobs/{job_id}/results"
                    out["download_csv_url"] = f"{base}/v1/jobs/{job_id}/download.csv?token={token}"
                    out["download_xlsx_url"] = f"{base}/v1/jobs/{job_id}/download.xlsx?token={token}"
                self.send_json(200, out)
                return

            if len(parts) == 4 and parts[3] == "results":
                if job.get("status") != "completed":
                    self.send_json(409, {"error": "job_not_completed", "status": job.get("status")})
                    return
                try:
                    limit = max(1, min(500, int(parse_qs(u.query).get("limit", ["100"])[0])))
                except ValueError:
                    limit = 100
                with open(job["csv_path"], newline="", encoding="utf-8-sig") as f:
                    rows = list(csv.DictReader(f))
                self.send_json(200, {"job_id": job_id, "count": len(rows), "returned": min(limit, len(rows)), "results": rows[:limit]})
                return

            if len(parts) == 4 and parts[3] in {"download.csv", "download.xlsx"}:
                token = parse_qs(u.query).get("token", [""])[0]
                if not secrets.compare_digest(token, job.get("download_token", "")):
                    self.send_json(403, {"error": "invalid_download_token"})
                    return
                if job.get("status") != "completed":
                    self.send_json(409, {"error": "job_not_completed"})
                    return
                is_xlsx = parts[3].endswith(".xlsx")
                file_path = Path(job["xlsx_path"] if is_xlsx else job["csv_path"])
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if is_xlsx else "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="school-search-{job_id[:8]}.{"xlsx" if is_xlsx else "csv"}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path != "/v1/school-search":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.require_auth():
            return

        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 20_000:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            query = str(body.get("query", "")).strip()
            city = str(body.get("city", "")).strip()
            depth = int(body.get("depth", 5))
            no_email = bool(body.get("no_email", False))
        except Exception as e:
            self.send_json(400, {"error": "invalid_request", "detail": str(e)})
            return

        if not query or not city:
            self.send_json(400, {"error": "query_and_city_required"})
            return
        if len(query) > 200 or len(city) > 120:
            self.send_json(400, {"error": "input_too_long"})
            return
        if depth < 1 or depth > MAX_DEPTH:
            self.send_json(400, {"error": "depth_out_of_range", "max_depth": MAX_DEPTH})
            return

        # Protect a small cloud instance and avoid aggressive Maps usage.
        with _jobs_lock:
            active = sum(1 for j in _jobs.values() if j.get("status") in {"queued", "running"})
        if active >= 2:
            self.send_json(429, {"error": "too_many_active_jobs", "detail": "Wait for an existing job to finish."})
            return

        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "status": "queued",
            "query": query,
            "city": city,
            "depth": depth,
            "no_email": no_email,
            "created_at": time.time(),
            "download_token": secrets.token_urlsafe(24),
        }
        with _jobs_lock:
            _jobs[job_id] = job

        threading.Thread(target=run_job, args=(job_id, query, city, depth, no_email), daemon=True).start()
        base = public_base(self)
        self.send_json(202, {
            "job_id": job_id,
            "status": "queued",
            "status_url": f"{base}/v1/jobs/{job_id}",
            "message": "Job accepted. Poll status_url until status is completed or failed."
        })


def main():
    if not API_KEY:
        print("ERROR: SCHOOL_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(2)
    start_scraper()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Testprep School Search API listening on :{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
