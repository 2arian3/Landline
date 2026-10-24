import asyncio
import re
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from landline.api.demo import LAST_RESULTS, TORONTO, DemoSessions, window
from landline.api.judge import JudgeLines, StartLimit, client_ip
from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing
from landline.db.catalogue import Catalogue

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def _listing(i: int, rent: int, beds: float, sqft: int | None = 700) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=sqft)


def _client(cat: Catalogue | None = None) -> TestClient:
    if cat is None:
        cat = Catalogue([_listing(1, 2400, 1), _listing(2, 2700, 2), _listing(3, 3600, 2),
                         _listing(4, 1900, 0)], "test")
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    return TestClient(create_app(settings, cat))


def _one_bedrooms() -> Catalogue:
    """Five places that all fit the default $2,800 budget: four to call and one left pending."""
    return Catalogue([_listing(i, rent, 1) for i, rent in enumerate((2000, 2100, 2200, 2300, 2400), start=1)],
                     "test")


def test_health_and_catalogue():
    with _client() as c:
        assert c.get("/health").json()["listings"] == 4
        points = c.get("/api/catalogue").json()
        assert {p["listing_id"] for p in points} == {"t:1", "t:2", "t:3", "t:4"}


def test_health_counts_sessions_and_reports_higgs():
    with _client() as c:
        h = c.get("/health").json()
        assert (h["status"], h["sessions"], h["transport"], h["higgs"]) == ("ok", 0, "stub", "not configured")
        for sid in ("s_a", "s_b", "s_a"):
            c.get(f"/api/sessions/{sid}")
        assert c.get("/health").json()["sessions"] == 2
        c.app.state.higgs = "ok"   # what the voice layer sets once it has reached Higgs
        assert c.get("/health").json()["higgs"] == "ok"


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


def test_area_hook_sets_where_the_map_flies():
    with _client() as c:
        assert c.get("/api/sessions/s_area").json()["focus_area"] is None
        v = c.post("/api/sessions/s_area/area", json={"place": "  Christie   PITS "}).json()
        assert v["focus_area"] == {"label": "Christie Pits", "lat": 43.6646, "lng": -79.4206, "zoom": 15.0}
        assert v["version"] == 2
        # it stays through the next re-rank, and a place the demo doesn't know is a 404
        later = c.post("/api/sessions/s_area/preferences", json={"max_monthly": 3000}).json()
        assert later["focus_area"]["label"] == "Christie Pits"
        assert c.post("/api/sessions/s_area/area", json={"place": "Atlantis"}).status_code == 404
        king_and_bay = c.post("/api/sessions/s_area/area", json={"place": "King and Bay"}).json()
        assert king_and_bay["focus_area"]["zoom"] == 15.5


def test_area_hook_sends_a_runtime_session_area_focused():
    with _judge_client(2) as c:
        sid = c.post("/api/judge/start").json()["session_id"]
        v = c.post(f"/api/sessions/{sid}/area", json={"place": "Yorkville"}).json()
        assert v["focus_area"]["label"] == "Yorkville" and v["version"] == 2
        assert c.app.state.runtime.state(sid).focus_area.label == "Yorkville"
        assert sid not in c.app.state.sessions.views   # one writer: the demo store never saw it
        assert c.post(f"/api/sessions/{sid}/area", json={"place": "Atlantis"}).status_code == 404


def test_feedback_shows_on_the_card():
    with _client() as c:
        assert c.get("/api/sessions/s_fb").json()["board"][0]["reaction"] is None
        r = c.post("/api/sessions/s_fb/feedback",
                   json={"listing_id": "t:1", "reaction": "reject", "reason": "too dark"})
        assert r.status_code == 200
        card = next(b for b in r.json()["board"] if b["listing_id"] == "t:1")
        assert card["reaction"] == "too dark"
        assert r.json()["version"] == 2
        # the reaction survives the next re-rank
        later = c.post("/api/sessions/s_fb/preferences", json={"max_monthly": 3000, "min_beds": 1}).json()
        assert next(b for b in later["board"] if b["listing_id"] == "t:1")["reaction"] == "too dark"
        assert c.post("/api/sessions/s_fb/feedback",
                      json={"listing_id": "nope", "reaction": "like"}).status_code == 404
        assert c.post("/api/sessions/s_fb/feedback",
                      json={"listing_id": "t:1", "reaction": "love"}).status_code == 422


async def test_demo_plan_is_the_top_four_places_with_a_pin_and_dials_nothing():
    flipped = _listing(9, 1900, 1).model_copy(update={"lng": 79.38})   # a flipped longitude: no pin
    s = DemoSessions(Catalogue([flipped, *_one_bedrooms().listings], "test"), tick_s=0)
    board = [c.listing_id for c in s.get("s1").board]
    planned = await s.plan_top("s1")
    assert planned.phase == "open" and planned.calls == [] and "s1" not in s.dialing
    assert planned.plan == [lid for lid in board if lid != "t:9"][:4]   # t:9 stays on the board, unplanned
    assert {c.status for c in planned.board} == {"pending"}


async def test_hanging_up_queues_the_plan_then_walks_it_to_results():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    seen = []
    publish = s.update

    async def spy(session_id, **kw):
        view = await publish(session_id, **kw)
        seen.append((view.phase, tuple(c.state for c in view.calls)))
        return view

    s.update = spy
    plan = (await s.plan_top("s1")).plan
    queued = await s.hang_up("s1")
    assert [c.listing_id for c in queued.calls] == plan
    assert all(re.fullmatch(r"c_[A-Za-z0-9_-]{10}", c.call_id) for c in queued.calls)
    await s.dialing["s1"]

    # Queued, then all dialing and ringing; the fourth never goes live; one hang-up at a time; then done.
    assert seen == [
        ("open", ()),
        ("calling", ("queued",) * 4),
        ("calling", ("dialing",) * 4),
        ("calling", ("ringing",) * 4),
        ("calling", ("live", "live", "live", "ringing")),
        ("calling", ("ended", "live", "live", "ringing")),
        ("calling", ("ended", "ended", "live", "ringing")),
        ("calling", ("ended", "ended", "ended", "ringing")),
        ("calling", ("ended",) * 4),
        ("done", ("ended",) * 4),
    ]
    done = s.get("s1")
    assert "s1" not in s.dialing and all(c.started_at for c in done.calls)
    cards = {c.listing_id: c for c in done.board}
    assert [cards[lid].status for lid in plan] == ["verified", "suspect", "booked", "no_answer"]
    assert (cards[plan[0]].rent, cards[plan[0]].monthly_cost) == (2000, 2180)   # parking through monthly_cost
    # The viewing falls inside the renter's Saturday afternoon, and the results lead with it.
    viewing = s.runs["s1"].outcomes[plan[2]].viewing
    start, end = window(viewing)
    assert start <= viewing < end and viewing.astimezone(TORONTO).strftime("%A %H:%M") == "Saturday 14:00"
    addr = {lid: c.address for lid, c in cards.items()}
    assert done.results == (
        f"I booked {addr[plan[2]]} for Saturday at 2 pm, inside your Saturday afternoon. "
        f"{addr[plan[0]]} is really $2,180 a month with parking. "
        f"The agent at {addr[plan[0]]} asked, \u201cWould they sign a 12-month lease?\u201d "
        "I said I'd check with you. "
        f"{addr[plan[1]]} wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. "
        f"Nobody picked up at {addr[plan[3]]}."
    )


async def test_hanging_up_without_a_plan_closes_the_brief():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    closed = await s.hang_up("s1")
    assert (closed.phase, closed.plan, closed.calls) == ("closed", [], []) and "s1" not in s.dialing
    assert (await s.plan_top("s1")).plan == []   # a closed brief takes no plan


async def test_reset_stops_the_calls_and_reopens_the_brief():
    s = DemoSessions(_one_bedrooms(), tick_s=60)
    await s.plan_top("s1")
    await s.hang_up("s1")
    walk = s.dialing["s1"]
    fresh = await s.reset("s1")
    await asyncio.sleep(0)
    assert walk.cancelled() and "s1" not in s.dialing
    assert (fresh.phase, fresh.plan, fresh.calls, fresh.results) == ("open", [], [], None)
    assert {c.status for c in fresh.board} == {"pending"}


def test_demo_hooks_run_the_whole_hand_off():
    with _client(_one_bedrooms()) as c:
        c.app.state.sessions.tick_s = 0.01
        assert len(c.post("/api/sessions/s_flow/calls").json()["plan"]) == 4
        assert c.post("/api/sessions/s_flow/hang-up").json()["phase"] == "calling"
        deadline = time.monotonic() + 3
        view = c.get("/api/sessions/s_flow").json()
        while view["phase"] != "done" and time.monotonic() < deadline:
            time.sleep(0.02)
            view = c.get("/api/sessions/s_flow").json()
        assert view["phase"] == "done" and view["results"].startswith("I booked")
        statuses = [b["status"] for b in view["board"]]
        assert statuses == ["verified", "suspect", "booked", "no_answer", "pending"]
        assert c.post("/api/sessions/s_flow/reset").json()["phase"] == "open"


def _judge_client(cap: int, per_ip: int = 100, ready: bool = True) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        higgs_max_sessions=cap, judge_audio_ready=ready, judge_starts_per_ip=per_ip)
    return TestClient(create_app(settings, Catalogue([_listing(1, 2400, 1)], "test")))


def test_judge_mode_opens_soon_until_the_browser_audio_is_ready():
    with _judge_client(2, ready=False) as c:
        r = c.post("/api/judge/start")
        assert r.status_code == 503
        assert r.json() == {"detail": "Judge mode opens soon.", "reason": "closed"}
        assert c.app.state.judges.open == {} and c.get("/health").json()["sessions"] == 0


def test_judge_start_mints_a_session_and_its_audio_socket():
    with _judge_client(2) as c:
        r = c.post("/api/judge/start")
        assert r.status_code == 200
        sid = r.json()["session_id"]
        assert re.fullmatch(r"s_[A-Za-z0-9_-]{10}", sid)
        assert r.json()["ws_url"] == f"/api/judge/{sid}/audio"
        assert c.get("/health").json()["sessions"] == 1
        assert c.get(f"/api/sessions/{sid}").json()["version"] == 1


def test_judge_start_is_busy_when_every_line_is_taken():
    with _judge_client(2) as c:
        first = c.post("/api/judge/start").json()["session_id"]
        assert c.post("/api/judge/start").status_code == 200
        busy = c.post("/api/judge/start")
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "60"
        assert busy.json() == {"detail": "All lines are busy. Try again in a minute.", "reason": "busy",
                               "retry_after_s": 60}
        c.app.state.judges.hang_up(first)   # what the browser transport (A-03) does when a judge leaves
        assert c.post("/api/judge/start").status_code == 200


def test_judge_starts_are_limited_per_address():
    with _judge_client(10, per_ip=2) as c:
        judge = {"X-Real-IP": "203.0.113.7"}
        assert [c.post("/api/judge/start", headers=judge).status_code for _ in range(2)] == [200, 200]
        limited = c.post("/api/judge/start", headers=judge)
        assert limited.status_code == 429
        body = limited.json()
        assert body["reason"] == "rate_limited" and 0 < body["retry_after_s"] <= 600
        assert limited.headers["retry-after"] == str(body["retry_after_s"])
        assert body["detail"] == "Too many judge sessions from your network. Try again in 10 minutes."
        # Another address has its own count; without X-Real-IP the first X-Forwarded-For entry counts.
        other = {"X-Forwarded-For": "198.51.100.4, 10.0.0.9"}
        assert [c.post("/api/judge/start", headers=other).status_code for _ in range(3)] == [200, 200, 429]


def test_only_starts_that_open_a_line_count_against_an_address():
    with _judge_client(1, per_ip=1) as c:
        first = c.post("/api/judge/start", headers={"X-Real-IP": "203.0.113.7"}).json()["session_id"]
        other = {"X-Real-IP": "198.51.100.4"}
        assert c.post("/api/judge/start", headers=other).json()["reason"] == "busy"
        c.app.state.judges.hang_up(first)
        assert c.post("/api/judge/start", headers=other).status_code == 200


def test_start_limit_frees_an_address_as_its_window_passes():
    now = [0.0]
    limit = StartLimit(per_ip=2, window_s=600, clock=lambda: now[0])
    limit.record("a")
    now[0] = 100.0
    limit.record("a")
    now[0] = 200.0
    assert limit.wait_s("a") == 400 and limit.wait_s("b") == 0
    now[0] = 600.5
    assert limit.wait_s("a") == 0


def test_client_ip_trusts_the_gateway_header_first():
    forwarded = {"x-forwarded-for": "198.51.100.4, 10.0.0.9"}
    assert client_ip({"x-real-ip": "203.0.113.7", **forwarded}, "10.0.0.2") == "203.0.113.7"
    assert client_ip(forwarded, "10.0.0.2") == "198.51.100.4"
    assert client_ip({}, "10.0.0.2") == "10.0.0.2"
    assert client_ip({}, None) == "unknown"


def test_judge_lines_free_at_their_deadline():
    now = [0.0]
    lines = JudgeLines(cap=1, deadline_s=600, clock=lambda: now[0])
    first = lines.take()
    assert first is not None and lines.take() is None
    now[0] = 600.5
    second = lines.take()
    assert second is not None and second != first


def test_settings_read_the_higgs_cap(monkeypatch):
    monkeypatch.setenv("HIGGS_MAX_SESSIONS", "3")
    assert Settings.load().higgs_max_sessions == 3
    monkeypatch.setenv("HIGGS_MAX_SESSIONS", "")
    assert Settings.load().higgs_max_sessions == 5


def test_settings_read_judge_mode(monkeypatch):
    for value, ready in (("1", True), ("true", True), ("", False), ("0", False), ("no", False)):
        monkeypatch.setenv("JUDGE_AUDIO_READY", value)
        assert Settings.load().judge_audio_ready is ready
    monkeypatch.setenv("JUDGE_STARTS_PER_IP", "8")
    monkeypatch.setenv("JUDGE_START_WINDOW_S", "")
    s = Settings.load()
    assert (s.judge_starts_per_ip, s.judge_start_window_s) == (8, 600)


def test_returning_renter_is_welcomed_by_name():
    with _client() as c:
        assert c.get("/api/sessions/s_back").json()["returning"] is False
        v = c.post("/api/sessions/s_back/returning", json={"name": "  Sara "}).json()
        assert (v["returning"], v["user_name"], v["version"]) == (True, "Sara", 2)
        # it survives the next re-rank
        later = c.post("/api/sessions/s_back/preferences", json={"max_monthly": 3000}).json()
        assert (later["returning"], later["user_name"]) == (True, "Sara")
        off = c.post("/api/sessions/s_back/returning", json={"returning": False}).json()
        assert (off["returning"], off["user_name"]) == (False, None)
        assert c.post("/api/sessions/s_back/returning", json={"name": " "}).json()["user_name"] is None
        assert c.post("/api/sessions/s_back/returning", json={"name": "x" * 41}).status_code == 422


async def test_a_returning_call_opens_with_the_last_results():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    first = await s.welcome_back("s1", True, "Sara")
    assert first.agent_says == f"Welcome back, Sara. {LAST_RESULTS}"   # nothing run yet: the fixture
    await s.plan_top("s1")
    await s.hang_up("s1")
    await s.dialing["s1"]
    results = s.get("s1").results
    assert results and not s.get("s1").agent_says.startswith("Welcome back")   # calls done: Landline moved on
    again = await s.reset("s1")   # the renter calls again
    assert again.agent_says == f"Welcome back, Sara. {results}"
    assert (await s.plan_top("s1")).agent_says.endswith("is on top.")   # once there's a plan, it moves on
    await s.reset("s1")
    assert (await s.welcome_back("s1", False, None)).agent_says.endswith("is on top.")
