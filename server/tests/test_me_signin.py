"""Clerk sign-in on /api/me (U-11a), with no database and no network: tokens are signed with a throwaway RSA
key whose public half the API gets as CLERK_JWT_KEY. A token that verifies reaches the history (here "history
is off", as there is no database); anything else is 401. The database cases (the renter's history, an unknown
number) are in test_admin.py."""
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings

PREVIEWS = r"https://landline-[a-z0-9-]+-aamel\.vercel\.app"


def _client(clerk=None) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        cors_origin_regex=PREVIEWS,
                        clerk_secret_key="sk_test_throwaway" if clerk else None,
                        clerk_jwt_key=clerk.pem if clerk else None)
    return TestClient(create_app(settings))


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_without_a_secret_key_sign_in_is_off_and_a_number_still_opens_history():
    with _client() as c:
        assert c.get("/api/me").json()["detail"] == "sign-in is off"
        assert c.post("/api/me", json={"phone": "(416) 555-0123"}).json()["detail"] == "history is off"


def test_with_sign_in_on_a_bare_phone_number_gets_401(clerk):
    with _client(clerk) as c:
        r = c.post("/api/me", json={"phone": "(416) 555-0123"})
        assert r.status_code == 401 and r.json()["detail"] == "sign in with your phone"


def test_a_valid_token_from_our_web_app_or_a_preview_reaches_the_history(clerk):
    with _client(clerk) as c:
        for azp in ("http://localhost:3000", "https://landline-git-u-11-aamel.vercel.app"):
            r = c.get("/api/me", headers=_bearer(clerk.token(azp=azp)))
            assert (r.status_code, r.json()["detail"]) == (404, "history is off"), azp


def test_a_native_apps_token_with_no_authorized_party_reaches_the_history(clerk):
    """The Expo app (U-13): Clerk sets azp from the Origin header, and a native request sends none."""
    with _client(clerk) as c:
        for azp in (None, ""):
            r = c.get("/api/me", headers=_bearer(clerk.token(azp=azp)))
            assert (r.status_code, r.json()["detail"]) == (404, "history is off"), azp


@pytest.mark.parametrize(("why", "token"), [
    ("expired", lambda k: k.token(expires_in=-3600)),
    ("another app", lambda k: k.token(azp="https://evil.example.com")),
    ("another app, close to ours", lambda k: k.token(azp="http://localhost:3000.evil.example.com")),
    ("an azp that isn't a string", lambda k: k.token(azp=["http://localhost:3000"])),
    ("no phone claim", lambda k: k.token(phone=None)),
    ("not a phone", lambda k: k.token(phone="call me maybe")),
    ("signed by another key", lambda k: k.token(key=rsa.generate_private_key(public_exponent=65537,
                                                                              key_size=2048))),
    ("not a token", lambda k: "not-a-jwt"),
])
def test_anything_but_a_valid_token_gets_401(clerk, why, token):
    with _client(clerk) as c:
        r = c.get("/api/me", headers=_bearer(token(clerk)))
        assert (r.status_code, r.json()["detail"]) == (401, "sign in with your phone"), why


def test_no_token_gets_401(clerk):
    with _client(clerk) as c:
        assert c.get("/api/me").status_code == 401


# PATCH /api/me/profile (U-12a): every field is checked in code before anything is saved. A bad field is a
# 422 that names it, whatever the token. The saved edits are in test_admin.py (Postgres).
@pytest.mark.parametrize(("body", "field"), [
    ({"email": "sara@example"}, "email"),                     # a bad email
    ({"email": "not an email"}, "email"),
    ({"max_monthly": "2600"}, "max_monthly"),                 # a budget sent as text
    ({"max_monthly": 2600.5}, "max_monthly"),                 # money in whole dollars
    ({"max_monthly": -1}, "max_monthly"),
    ({"max_monthly": 0}, "max_monthly"),                      # a $0 budget would empty every board
    ({"min_beds": 6}, "min_beds"),                            # beds from 0 to 5
    ({"min_beds": 1.25}, "min_beds"),
    ({"min_beds": "two"}, "min_beds"),
    ({"move_in": "2026-02-30"}, "move_in"),                   # a real date
    ({"move_in": "December 1"}, "move_in"),
    ({"move_in": 1764547200}, "move_in"),
    ({"shareable": ["phone"]}, "shareable"),                  # an unknown shareable field
    ({"shareable": ["pets", "pets"]}, "shareable"),
    ({"use_saved_search": "yes"}, "use_saved_search"),
    ({"areas": None}, "areas"),
    ({"areas": [""]}, "areas"),
    ({"areas": ["x" * 81]}, "areas"),
    ({"pets": [3]}, "pets"),
    ({"must_have": ["laundry"] * 21}, "must_have"),
    ({"budget": 2600}, "budget"),                             # an unknown field
])
def test_a_bad_profile_field_is_a_422_naming_it(clerk, body, field):
    with _client(clerk) as c:
        r = c.patch("/api/me/profile", headers=_bearer(clerk.token()), json=body)
        assert r.status_code == 422, body
        assert [e["loc"][1] for e in r.json()["detail"]] == [field], r.json()


def test_a_good_profile_edit_needs_a_token_and_a_database(clerk):
    edit = {"max_monthly": 2400, "min_beds": 1.5, "areas": [" Annex "], "pets": ["cat"], "must_have": [],
            "move_in": "2026-12-01", "email": " sara@example.com ", "shareable": ["name", "pets"],
            "use_saved_search": True}
    with _client(clerk) as c:
        assert c.patch("/api/me/profile", json=edit).status_code == 401
        r = c.patch("/api/me/profile", headers=_bearer(clerk.token()), json=edit)
        assert (r.status_code, r.json()["detail"]) == (404, "history is off")
        assert c.get("/api/me/profile", headers=_bearer(clerk.token())).json()["detail"] == "history is off"
        preflight = c.options("/api/me/profile", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type"})
        assert preflight.status_code == 200 and "PATCH" in preflight.headers["access-control-allow-methods"]
    with _client() as c:   # sign-in off
        assert c.patch("/api/me/profile", json=edit).json()["detail"] == "sign-in is off"
