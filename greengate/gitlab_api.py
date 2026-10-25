"""Thin GitLab REST client with retries, backoff on 429/5xx and pagination."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class GitLabError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body: str) -> None:
        super().__init__(f"GitLab {method} {path} -> {status}: {body[:300]}")
        self.status = status


class GitLab:
    def __init__(
        self,
        token: str | None = None,
        api_url: str | None = None,
        project: str | int | None = None,
        *,
        job_token: bool = False,
        client: httpx.Client | None = None,
        max_retries: int = 5,
        sleep=time.sleep,
    ) -> None:
        self.api_url = (api_url or os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")).rstrip("/")
        project = project if project is not None else os.environ.get("CI_PROJECT_ID", "")
        self.project = quote(str(project), safe="")
        token = token if token is not None else os.environ.get("GITLAB_BOT_TOKEN", "")
        headers = {"JOB-TOKEN": token} if job_token else ({"PRIVATE-TOKEN": token} if token else {})
        self._client = client or httpx.Client(timeout=30.0)
        self._headers = headers
        self._max_retries = max_retries
        self._sleep = sleep

    # -- core -----------------------------------------------------------------
    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_url}/{path.lstrip('/')}"
        delay = 1.0
        for attempt in range(self._max_retries + 1):
            try:
                res = self._client.request(method, url, headers=self._headers, **kwargs)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
                self._sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if res.status_code in RETRY_STATUS and attempt < self._max_retries:
                retry_after = res.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay = min(delay * 2, 30)
                continue
            if res.status_code >= 400:
                raise GitLabError(res.status_code, method, path, res.text)
            return res
        raise AssertionError("unreachable")  # pragma: no cover

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None).json()

    def post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        res = self.request("POST", path, json=data or {})
        return res.json() if res.content else None

    def put(self, path: str, data: dict[str, Any] | None = None) -> Any:
        res = self.request("PUT", path, json=data or {})
        return res.json() if res.content else None

    def paginate(self, path: str, per_page: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            res = self.request("GET", path, params={**params, "per_page": per_page, "page": page})
            items = res.json()
            yield from items
            nxt = res.headers.get("x-next-page", "")
            if not nxt or not items:
                return
            page = int(nxt)

    # -- project helpers ------------------------------------------------------
    def p(self, suffix: str = "") -> str:
        return f"projects/{self.project}{suffix}"

    def pipeline_jobs(self, pipeline_id: int) -> list[dict[str, Any]]:
        return list(self.paginate(self.p(f"/pipelines/{pipeline_id}/jobs"), include_retried="true"))

    def pipeline(self, pipeline_id: int) -> dict[str, Any]:
        return self.get(self.p(f"/pipelines/{pipeline_id}"))

    def file_raw(self, path: str, ref: str) -> str:
        return self.request("GET", self.p(f"/repository/files/{quote(path, safe='')}/raw"), params={"ref": ref}).text
