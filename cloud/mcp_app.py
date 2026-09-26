#!/usr/bin/env python3
"""ChatGPT Plugin / MCP gateway for the Testprep school scraping pipeline.

This service exposes the school scraper as a Streamable HTTP MCP server at /mcp.
It reuses the existing background-job implementation from cloud/server.py.
"""

from __future__ import annotations

import csv
import os
import secrets
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from cloud import server as legacy

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://testprep-school-search-api.onrender.com",
).rstrip("/")
MAX_DEPTH = int(os.getenv("MAX_DEPTH", "10"))

mcp = MCPServer(
    "Testprep School Search",
    instructions=(
        "Use start_school_search for school discovery requests. "
        "Then poll get_school_search_job until status is completed or failed. "
        "After completion call get_school_search_results. "
        "Depth is a Google Maps scraper depth parameter; do not reinterpret it. "
        "Never invent missing leadership or social-profile data."
    ),
)


def _start_job(query: str, city: str, depth: int, no_email: bool) -> dict:
    query = (query or "").strip()
    city = (city or "").strip()
    if not query or not city:
        raise ValueError("query and city are required")
    if len(query) > 200 or len(city) > 120:
        raise ValueError("query or city is too long")
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")

    with legacy._jobs_lock:
        active = sum(
            1
            for j in legacy._jobs.values()
            if j.get("status") in {"queued", "running"}
        )
    if active >= 2:
        raise RuntimeError("Too many active jobs. Wait for an existing job to finish.")

    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "status": "queued",
        "query": query,
        "city": city,
        "depth": depth,
        "no_email": bool(no_email),
        "created_at": time.time(),
        "download_token": secrets.token_urlsafe(24),
    }
    with legacy._jobs_lock:
        legacy._jobs[job_id] = job

    threading.Thread(
        target=legacy.run_job,
        args=(job_id, query, city, depth, bool(no_email)),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Job accepted. Poll get_school_search_job using this job_id.",
    }


@mcp.tool(
    title="Start school search",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        open_world_hint=True,
    ),
)
def start_school_search(
    query: str,
    city: str,
    depth: int = 5,
    no_email: bool = False,
) -> dict:
    """Start a Google Maps school search and leadership enrichment job.

    Use for requests such as CBSE schools in Lucknow or ICSE schools in Kanpur.
    Depth is the Maps scraper depth. Use 5 by default unless the user specifies another value.
    """
    return _start_job(query, city, depth, no_email)


@mcp.tool(
    title="Check school search job",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    ),
)
def get_school_search_job(job_id: str) -> dict:
    """Check a school-search job. Poll until status is completed or failed."""
    job = legacy.safe_job(job_id)
    if not job:
        raise ValueError("job not found")

    out = {k: v for k, v in job.items() if not k.endswith("_path") and k != "download_token"}
    if job.get("status") == "completed":
        token = job["download_token"]
        out["download_csv_url"] = (
            f"{PUBLIC_BASE_URL}/download/{job_id}.csv?token={token}"
        )
        out["download_xlsx_url"] = (
            f"{PUBLIC_BASE_URL}/download/{job_id}.xlsx?token={token}"
        )
    return out


@mcp.tool(
    title="Get school search results",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    ),
)
def get_school_search_results(job_id: str, limit: int = 100) -> dict:
    """Return completed school results as structured JSON and include Excel/CSV download URLs."""
    job = legacy.safe_job(job_id)
    if not job:
        raise ValueError("job not found")
    if job.get("status") != "completed":
        raise RuntimeError(f"job is not completed; current status: {job.get('status')}")

    limit = max(1, min(500, int(limit)))
    with open(job["csv_path"], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    token = job["download_token"]
    return {
        "job_id": job_id,
        "count": len(rows),
        "returned": min(limit, len(rows)),
        "results": rows[:limit],
        "download_csv_url": f"{PUBLIC_BASE_URL}/download/{job_id}.csv?token={token}",
        "download_xlsx_url": f"{PUBLIC_BASE_URL}/download/{job_id}.xlsx?token={token}",
    }


async def health(_: Request):
    return JSONResponse(
        {
            "status": "ok",
            "mode": "mcp",
            "mcp_endpoint": "/mcp",
            "scraper_process": bool(
                legacy._scraper_proc and legacy._scraper_proc.poll() is None
            ),
        }
    )


def _download_path(job_id: str, suffix: str, token: str) -> Path | None:
    job = legacy.safe_job(job_id)
    if not job or job.get("status") != "completed":
        return None
    if not secrets.compare_digest(token, job.get("download_token", "")):
        return None
    return Path(job["xlsx_path"] if suffix == "xlsx" else job["csv_path"])


async def download_xlsx(request: Request):
    path = _download_path(
        request.path_params["job_id"],
        "xlsx",
        request.query_params.get("token", ""),
    )
    if not path or not path.exists():
        return PlainTextResponse("Not found or invalid token", status_code=404)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"school-search-{request.path_params['job_id'][:8]}.xlsx",
    )


async def download_csv(request: Request):
    path = _download_path(
        request.path_params["job_id"],
        "csv",
        request.query_params.get("token", ""),
    )
    if not path or not path.exists():
        return PlainTextResponse("Not found or invalid token", status_code=404)
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"school-search-{request.path_params['job_id'][:8]}.csv",
    )


# Render terminates TLS in front of the container. Allow the public service host.
security = TransportSecuritySettings(
    allowed_hosts=[
        "testprep-school-search-api.onrender.com",
        "testprep-school-search-api.onrender.com:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
    ],
    allowed_origins=[
        "https://chatgpt.com",
        "https://chat.openai.com",
        "https://testprep-school-search-api.onrender.com",
    ],
)

mcp_http_app = mcp.streamable_http_app(
    transport_security=security,
    json_response=True,
    stateless_http=True,
)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    legacy.start_scraper()
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            if legacy._scraper_proc and legacy._scraper_proc.poll() is None:
                legacy._scraper_proc.terminate()


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/download/{job_id}.xlsx", download_xlsx, methods=["GET"]),
        Route("/download/{job_id}.csv", download_csv, methods=["GET"]),
        Mount("/", app=mcp_http_app),
    ],
    lifespan=lifespan,
)
