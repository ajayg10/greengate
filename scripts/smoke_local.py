"""Integration smoke test against a running EcoLinks server: create, resolve, stats, health.

    python scripts/smoke_local.py http://127.0.0.1:8080
"""

from __future__ import annotations

import sys
import time

import httpx


def main(base: str) -> int:
    base = base.rstrip("/")
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        for _ in range(30):  # wait for the server
            try:
                if c.get(f"{base}/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            print("server did not come up")
            return 1
        created = c.post(f"{base}/links", json={"url": "https://example.org/smoke"})
        assert created.status_code == 201, created.text
        code = created.json()["code"]
        resolved = c.get(f"{base}/{code}")
        assert resolved.status_code == 307 and resolved.headers["location"] == "https://example.org/smoke"
        stats = c.get(f"{base}/api/links/{code}/stats").json()
        assert stats["hits"] == 1, stats
        assert c.get(f"{base}/does-not-exist").status_code == 404
        assert c.get(f"{base}/version").json()["service"] == "ecolinks"
    print(f"smoke OK against {base} (code {code})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"))
