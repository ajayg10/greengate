"""EcoLinks API.

POST /links {url}            -> 201 {code, short_url, url}
GET  /{code}                 -> 307 redirect (counts a hit)
GET  /api/links/{code}/stats -> {code, url, hits, created_at}
GET  /healthz, GET /version
"""

from __future__ import annotations

import os
import re
import secrets
import string

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

from app.health import version_info
from app.ratelimit import RateLimiter
from app.store import CodeTaken, get_store

ALPHABET = string.ascii_letters + string.digits
CODE_LEN = 7
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,16}$")

app = FastAPI(title="EcoLinks", version="1.0.0", docs_url="/docs", redoc_url=None)
create_limiter = RateLimiter(limit=int(os.environ.get("ECOLINKS_CREATE_PER_MIN", "60")), window_s=60)


class CreateLink(BaseModel):
    url: HttpUrl = Field(description="http(s) URL to shorten")


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def base_url(request: Request) -> str:
    return os.environ.get("ECOLINKS_PUBLIC_URL", str(request.base_url)).rstrip("/")


@app.get("/")
def index() -> dict[str, str]:
    return {"service": "EcoLinks", "docs": "/docs", "health": "/healthz", "version": "/version"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    return version_info()


@app.post("/links", status_code=201)
def create_link(body: CreateLink, request: Request) -> dict[str, str]:
    if not create_limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded, try again in a minute")
    url = str(body.url)
    if len(url) > 2048:
        raise HTTPException(status_code=422, detail="url too long (max 2048 characters)")
    store = get_store()
    for _ in range(5):
        code = new_code()
        try:
            store.create(code, url)
            break
        except CodeTaken:
            continue
    else:  # pragma: no cover - 62^7 codes make five collisions in a row practically impossible
        raise HTTPException(status_code=503, detail="could not allocate a code")
    return {"code": code, "short_url": f"{base_url(request)}/{code}", "url": url}


@app.get("/api/links/{code}/stats")
def link_stats(code: str) -> dict:
    if not CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="link not found")
    link = get_store().get(code)
    if not link:
        raise HTTPException(status_code=404, detail="link not found")
    return link.to_dict()


@app.get("/{code}")
def resolve(code: str) -> RedirectResponse:
    if not CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="link not found")
    link = get_store().hit(code)
    if not link:
        raise HTTPException(status_code=404, detail="link not found")
    return RedirectResponse(link.url, status_code=307)
