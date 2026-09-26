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
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
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



DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Testprep School Search</title>
<style>
:root{font-family:Inter,system-ui,Arial,sans-serif;color:#14213d;background:#f6f8fb}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:1180px;margin:0 auto;padding:28px}
.hero{background:#fff;border:1px solid #e7ebf0;border-radius:18px;padding:24px;box-shadow:0 8px 30px rgba(20,33,61,.08)}
h1{margin:0 0 6px;font-size:28px}.sub{color:#667085;margin-bottom:22px}
.grid{display:grid;grid-template-columns:2fr 2fr .8fr 1.2fr;gap:12px}
label{font-size:12px;font-weight:700;color:#475467;display:block;margin-bottom:6px}
input,select,button{width:100%;padding:12px;border:1px solid #d0d5dd;border-radius:10px;font-size:14px}
button{background:#14213d;color:#fff;border:0;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}.status{margin-top:16px;padding:12px;border-radius:10px;background:#f2f4f7;color:#344054}
.actions{display:flex;gap:10px;margin:16px 0}.actions a{display:none;padding:10px 14px;border-radius:9px;background:#eef2ff;text-decoration:none;color:#273a8a;font-weight:700}
table{width:100%;border-collapse:collapse;background:#fff;font-size:12px}th,td{border-bottom:1px solid #eaecf0;padding:9px;text-align:left;vertical-align:top;max-width:260px;word-break:break-word}
th{position:sticky;top:0;background:#f9fafb}.tablewrap{overflow:auto;max-height:62vh;border:1px solid #e7ebf0;border-radius:12px}
.note{font-size:12px;color:#667085;margin-top:10px}.keyrow{margin-top:14px;display:grid;grid-template-columns:1fr auto;gap:10px}.keyrow button{width:auto}
@media(max-width:800px){.grid{grid-template-columns:1fr}.wrap{padding:14px}}
</style>
</head>
<body><div class="wrap">
<div class="hero">
<h1>Testprep School Search</h1>
<div class="sub">Google Maps school discovery + phone/email/website + leadership + social links + Excel.</div>
<div class="grid">
<div><label>Search query</label><input id="query" value="CBSE schools in Lucknow"></div>
<div><label>City</label><input id="city" value="Lucknow, Uttar Pradesh"></div>
<div><label>Depth</label><select id="depth"><option>3</option><option selected>5</option><option>8</option><option>10</option></select></div>
<div><label>Email extraction</label><select id="email"><option value="false" selected>Include emails</option><option value="true">Skip emails (faster)</option></select></div>
</div>
<div class="keyrow"><input id="key" type="password" placeholder="Paste SCHOOL_API_KEY once (stored only in this browser)"><button id="saveKey">Save key</button></div>
<div style="margin-top:14px"><button id="run">Search Schools</button></div>
<div id="status" class="status">Ready.</div>
<div class="actions"><a id="xlsx" target="_blank">Download Excel</a><a id="csv" target="_blank">Download CSV</a></div>
<div class="note">Keep depth conservative. Only public school/leadership information should be used, and unverified personal social profiles should be reviewed before outreach.</div>
</div>
<div style="height:18px"></div>
<div id="table" class="tablewrap" style="display:none"></div>
</div>
<script>
const $=id=>document.getElementById(id);
$("key").value=localStorage.getItem("testprep_school_key")||"";
$("saveKey").onclick=()=>{localStorage.setItem("testprep_school_key",$("key").value.trim());$("status").textContent="API key saved in this browser.";};
function headers(){return {"Content-Type":"application/json","X-API-Key":$("key").value.trim()};}
function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]));}
function render(rows){
 if(!rows||!rows.length){$("table").style.display="none";return}
 const preferred=["title","address","phone","emails","website","leadership_name","leadership_role","leadership_source","linkedin_person","facebook_person","instagram_person","instagram","facebook","linkedin","review_rating","review_count","profile_confidence","verification_status"];
 const keys=[...preferred.filter(k=>rows.some(r=>r[k])),...Object.keys(rows[0]).filter(k=>!preferred.includes(k))];
 let html="<table><thead><tr>"+keys.map(k=>"<th>"+esc(k)+"</th>").join("")+"</tr></thead><tbody>";
 html+=rows.map(r=>"<tr>"+keys.map(k=>"<td>"+(String(r[k]||"").startsWith("http")?'<a target="_blank" href="'+esc(r[k])+'">open</a>':esc(r[k]))+"</td>").join("")+"</tr>").join("");
 html+="</tbody></table>";$("table").innerHTML=html;$("table").style.display="block";
}
async function poll(id){
 for(let i=0;i<160;i++){
   await new Promise(r=>setTimeout(r,5000));
   const res=await fetch("/dashboard/job/"+id,{headers:headers()}); const j=await res.json();
   $("status").textContent="Job "+id.slice(0,8)+" — "+(j.status||"unknown")+(j.count!=null?" — "+j.count+" schools":"");
   if(j.status==="failed") throw new Error(j.error||"Job failed");
   if(j.status==="completed"){
      if(j.download_xlsx_url){$("xlsx").href=j.download_xlsx_url;$("xlsx").style.display="inline-block"}
      if(j.download_csv_url){$("csv").href=j.download_csv_url;$("csv").style.display="inline-block"}
      const rr=await fetch("/dashboard/results/"+id+"?limit=500",{headers:headers()}); const data=await rr.json();
      render(data.results||[]); return;
   }
 }
 throw new Error("Timed out waiting for job.");
}
$("run").onclick=async()=>{
 const btn=$("run"); btn.disabled=true;$("xlsx").style.display="none";$("csv").style.display="none";$("table").style.display="none";
 try{
   if(!$("key").value.trim()) throw new Error("Please enter SCHOOL_API_KEY.");
   localStorage.setItem("testprep_school_key",$("key").value.trim());
   $("status").textContent="Starting search…";
   const res=await fetch("/dashboard/start",{method:"POST",headers:headers(),body:JSON.stringify({query:$("query").value,city:$("city").value,depth:Number($("depth").value),no_email:$("email").value==="true"})});
   const j=await res.json(); if(!res.ok) throw new Error(j.error||j.detail||"Could not start job");
   $("status").textContent="Job accepted: "+j.job_id+" — waiting for results…";
   await poll(j.job_id);
 }catch(e){$("status").textContent="Error: "+e.message}finally{btn.disabled=false}
};
</script></body></html>"""


def _dashboard_auth(request: Request) -> bool:
    configured = os.getenv("SCHOOL_API_KEY", "").strip()
    supplied = request.headers.get("X-API-Key", "").strip()
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


async def dashboard(_: Request):
    return HTMLResponse(DASHBOARD_HTML)


async def dashboard_start(request: Request):
    if not _dashboard_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        result = _start_job(
            str(body.get("query", "")),
            str(body.get("city", "")),
            int(body.get("depth", 5)),
            bool(body.get("no_email", False)),
        )
        return JSONResponse(result, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=429)


async def dashboard_job(request: Request):
    if not _dashboard_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        return JSONResponse(get_school_search_job(request.path_params["job_id"]))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


async def dashboard_results(request: Request):
    if not _dashboard_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        limit = int(request.query_params.get("limit", "100"))
        return JSONResponse(get_school_search_results(request.path_params["job_id"], limit))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


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
        Route("/", dashboard, methods=["GET"]),
        Route("/dashboard", dashboard, methods=["GET"]),
        Route("/dashboard/start", dashboard_start, methods=["POST"]),
        Route("/dashboard/job/{job_id}", dashboard_job, methods=["GET"]),
        Route("/dashboard/results/{job_id}", dashboard_results, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/download/{job_id}.xlsx", download_xlsx, methods=["GET"]),
        Route("/download/{job_id}.csv", download_csv, methods=["GET"]),
        Mount("/", app=mcp_http_app),
    ],
    lifespan=lifespan,
)
