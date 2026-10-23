from datetime import UTC, datetime

from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing
from landline.db.catalogue import Catalogue

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def _listing(i: int, rent: int, beds: float, sqft: int | None = 700) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=sqft)


def _client() -> TestClient:
    cat = Catalogue([_listing(1, 2400, 1), _listing(2, 2700, 2), _listing(3, 3600, 2), _listing(4, 1900, 0)],
                    "test")
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    return TestClient(create_app(settings, cat))


def test_health_and_catalogue():
    with _client() as c:
        assert c.get("/health").json()["listings"] == 4
        points = c.get("/api/catalogue").json()
        assert {p["listing_id"] for p in points} == {"t:1", "t:2", "t:3", "t:4"}


def test_session_board_follows_preferences_and_bumps_version():
    with _client() as c:
        first = c.get("/api/sessions/s1").json()
        assert first["version"] == 1
        assert [b["listing_id"] for b in first["board"]] == ["t:1", "t:2"]   # beds >= 1, within 2800 * 1.1

        updated = c.post("/api/sessions/s1/preferences", json={"max_monthly": 4000, "min_beds": 2}).json()
        assert updated["version"] == 2
        assert {b["listing_id"] for b in updated["board"]} == {"t:2", "t:3"}
        assert updated["board"][0]["rank"] == 1


def test_cors_allows_listed_origins_only():
    cat = Catalogue([_listing(1, 2400, 1)], "test")
    settings = Settings(database_url=None, web_url="https://landline.vercel.app", transport="stub",
                        cors_origins=("https://api.example.org/",),
                        cors_origin_regex=r"https://landline-[a-z0-9-]+\.vercel\.app")
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    with TestClient(create_app(settings, cat)) as c:
        for origin in ("https://landline.vercel.app", "https://api.example.org", "http://localhost:3000",
                       "https://landline-git-ui-team.vercel.app"):
            res = c.options("/api/sessions/s1/preferences", headers={"Origin": origin, **preflight})
            assert res.status_code == 200
            assert res.headers["access-control-allow-origin"] == origin
        assert c.get("/api/catalogue", headers={"Origin": "https://landline.vercel.app"}).headers[
            "access-control-allow-origin"] == "https://landline.vercel.app"

        assert c.options("/api/sessions/s1/preferences",
                         headers={"Origin": "https://evil.example", **preflight}).status_code == 400
        assert "access-control-allow-origin" not in c.get(
            "/api/catalogue", headers={"Origin": "https://evil.example"}).headers


def test_settings_read_cors_from_env(monkeypatch):
    monkeypatch.setenv("WEB_URL", "")
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example , https://b.example/ ,")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", "")
    s = Settings.load()
    assert s.web_url == "http://localhost:3000"
    assert s.allowed_origins() == ["http://localhost:3000", "https://a.example", "https://b.example"]
    assert s.cors_origin_regex is None


def test_focus_rejects_unknown_listing():
    with _client() as c:
        assert c.post("/api/sessions/s1/focus", json={"listing_id": "nope"}).status_code == 404
        focused = c.post("/api/sessions/s1/focus", json={"listing_id": "t:2"}).json()
        assert focused["focus_listing_id"] == "t:2"


def test_feedback_shows_on_the_card():
    with _client() as c:
        assert c.get("/api/sessions/s_fb").json()["board"][0]["reaction"] is None
        r = c.post("/api/sessions/s_fb/feedback", json={"listing_id": "t:1", "reaction": "reject", "reason": "too dark"})
        assert r.status_code == 200
        card = next(b for b in r.json()["board"] if b["listing_id"] == "t:1")
        assert card["reaction"] == "too dark"
        assert r.json()["version"] == 2
        # the reaction survives the next re-rank
        later = c.post("/api/sessions/s_fb/preferences", json={"max_monthly": 3000, "min_beds": 1}).json()
        assert next(b for b in later["board"] if b["listing_id"] == "t:1")["reaction"] == "too dark"
        assert c.post("/api/sessions/s_fb/feedback", json={"listing_id": "nope", "reaction": "like"}).status_code == 404
        assert c.post("/api/sessions/s_fb/feedback", json={"listing_id": "t:1", "reaction": "love"}).status_code == 422
