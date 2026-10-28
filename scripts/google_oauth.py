"""Sign Landline's Google account in once and print its refresh token (O-08a): the calendar invites need it.

    uv run python scripts/google_oauth.py

Needs GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET, in the environment or .env: an OAuth client of
type Desktop app, in a Google Cloud project with the Calendar API enabled. While the consent screen is in
Testing, add the Landline account as a test user (its refresh tokens then last 7 days).

It opens the browser on Google's sign-in, waits for Google to send the browser back to a one-off server on
127.0.0.1, trades the code for tokens and prints only the refresh token, for .env as
GOOGLE_OAUTH_REFRESH_TOKEN. The access token and the client secret are never printed.
"""
# Path is inserted before the project imports. Ruff's import-order rule doesn't cover that.
# ruff: noqa: E402
from __future__ import annotations

import base64
import hashlib
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from landline.config import Settings  # reads .env the way the API does

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar.events"   # create events and invite guests, nothing more
WAIT_S = 300
TIMEOUT_S = 15


def main() -> int:
    settings = Settings.load()
    client_id, client_secret = settings.google_oauth_client_id, settings.google_oauth_client_secret
    if not client_id or not client_secret:
        print("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set, in .env or the "
              "environment: an OAuth client of type Desktop app", file=sys.stderr)
        return 1

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)   # PKCE: the code is useless to anyone without this
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    got: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "code" not in query and "error" not in query:
                self.send_error(404)
                return
            if query.get("state") != state:
                got["error"] = "the answer's state didn't match this sign-in"
            elif "error" in query:
                got["error"] = query["error"]
            else:
                got["code"] = query["code"]
            ok = "code" in got
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and go back to the terminal." if ok
                             else b"Sign-in failed. See the terminal.")

        def log_message(self, *args) -> None:   # the request line holds the code: keep it off the terminal
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Sign in with Landline's Google account. If no browser opens, visit:\n" + url, file=sys.stderr)
    webbrowser.open(url)

    deadline = time.monotonic() + WAIT_S
    try:
        while not got and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" not in got:
        print(f"no sign-in: {got.get('error', f'nothing came back within {WAIT_S} s')}", file=sys.stderr)
        return 1

    try:
        r = httpx.post(TOKEN_URL, timeout=TIMEOUT_S, data={
            "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect_uri,
            "client_id": client_id, "client_secret": client_secret, "code_verifier": verifier})
    except httpx.HTTPError as e:
        print(f"the token exchange failed ({e!r})", file=sys.stderr)
        return 1
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:   # an error body has no tokens: just Google's reason
        reason = f"{body.get('error')} {body.get('error_description', '')}".strip()
        print(f"Google refused the code ({r.status_code}): {reason}", file=sys.stderr)
        return 1
    refresh = body.get("refresh_token")
    if not refresh:
        print("Google sent no refresh token: remove Landline's access at myaccount.google.com/permissions "
              "and run this again", file=sys.stderr)
        return 1
    print("Put this line in .env (and the password manager), then push it with scripts/push-env.sh:\n",
          file=sys.stderr)
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={refresh}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
