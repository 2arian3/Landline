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


@pytest.mark.parametrize(("why", "token"), [
    ("expired", lambda k: k.token(expires_in=-3600)),
    ("another app", lambda k: k.token(azp="https://evil.example.com")),
    ("no authorized party", lambda k: k.token(azp=None)),
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
