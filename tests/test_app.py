import pytest
from fastapi.testclient import TestClient

from app import main
from app.ratelimit import RateLimiter
from app.store import MemoryStore, set_store


@pytest.fixture()
def client(monkeypatch):
    set_store(MemoryStore())
    monkeypatch.setattr(main, "create_limiter", RateLimiter(limit=1000, window_s=60))
    yield TestClient(main.app, follow_redirects=False)
    set_store(None)


def test_index_and_health(client):
    assert client.get("/").json()["service"] == "EcoLinks"
    assert client.get("/healthz").json() == {"status": "ok"}


def test_version_reports_sha_and_revision(client, monkeypatch):
    monkeypatch.setenv("GG_GIT_SHA", "abc1234")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "7")
    body = client.get("/version").json()
    assert body["sha"] == "abc1234"
    assert body["revision"] == "7"
    assert body["service"] == "ecolinks"


def test_create_resolve_and_stats(client):
    res = client.post("/links", json={"url": "https://example.org/a?b=1"})
    assert res.status_code == 201
    body = res.json()
    code = body["code"]
    assert len(code) == 7
    assert body["short_url"].endswith("/" + code)

    redirect = client.get(f"/{code}")
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://example.org/a?b=1"
    client.get(f"/{code}")

    stats = client.get(f"/api/links/{code}/stats").json()
    assert stats["hits"] == 2
    assert stats["url"] == "https://example.org/a?b=1"


def test_codes_with_uppercase_letters_resolve(client, monkeypatch):
    monkeypatch.setattr(main, "new_code", lambda: "AbCdEf9")
    code = client.post("/links", json={"url": "https://example.org/upper"}).json()["code"]
    assert code == "AbCdEf9"
    assert client.get("/AbCdEf9").status_code == 307


@pytest.mark.parametrize("url", ["not a url", "javascript:alert(1)", "ftp://example.org/file", ""])
def test_rejects_invalid_urls(client, url):
    assert client.post("/links", json={"url": url}).status_code == 422


def test_rejects_too_long_url(client):
    long_url = "https://example.org/" + "a" * 2100
    assert client.post("/links", json={"url": long_url}).status_code == 422


def test_unknown_and_malformed_codes_are_404(client):
    assert client.get("/zzzzzzz").status_code == 404
    assert client.get("/bad-code!").status_code == 404
    assert client.get("/api/links/zzzzzzz/stats").status_code == 404
    assert client.get("/api/links/bad-code!/stats").status_code == 404


def test_collision_retries_with_a_new_code(client, monkeypatch):
    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr(main, "new_code", lambda: next(codes))
    assert client.post("/links", json={"url": "https://example.org/1"}).json()["code"] == "AAAAAAA"
    assert client.post("/links", json={"url": "https://example.org/2"}).json()["code"] == "BBBBBBB"


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "create_limiter", RateLimiter(limit=2, window_s=60))
    for _ in range(2):
        assert client.post("/links", json={"url": "https://example.org"}).status_code == 201
    res = client.post("/links", json={"url": "https://example.org"}, headers={"x-forwarded-for": "1.2.3.4"})
    assert res.status_code == 201  # a different client has its own budget
    assert client.post("/links", json={"url": "https://example.org"}).status_code == 429


def test_public_url_override(client, monkeypatch):
    monkeypatch.setenv("ECOLINKS_PUBLIC_URL", "https://short.example/")
    body = client.post("/links", json={"url": "https://example.org"}).json()
    assert body["short_url"] == f"https://short.example/{body['code']}"
