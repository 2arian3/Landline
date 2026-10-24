"""A polite HTTP client for the pipeline.

- Identifies itself honestly (name, purpose, contact address) and never
  pretends to be a browser.
- Obeys robots.txt for every host.
- Waits at least `min_delay` seconds between requests to the same host.
- Backs off on 429 / 5xx (honouring Retry-After).
- Treats 401/403 as "the site doesn't want us here": it stops instead of
  retrying with a different identity.
- Caches responses on disk so reruns don't hit the site again.
"""
from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CACHE_DIR = Path(__file__).resolve().parent / "cache" / "http"


class Blocked(RuntimeError):
    """The site refused us (401/403). Do not work around it."""


class Disallowed(RuntimeError):
    """robots.txt disallows this URL for our user agent."""


@dataclass
class PoliteClient:
    contact: str
    min_delay: float = 2.0
    max_retries: int = 3
    cache_ttl_s: float = 6 * 3600
    cache_dir: Path = CACHE_DIR
    transport: httpx.BaseTransport | None = None   # injectable for tests
    _last: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    _client: httpx.Client | None = None

    @property
    def user_agent(self) -> str:
        return f"LandlineResearch/0.1 (hackathon prototype; +mailto:{self.contact})"

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": self.user_agent, "Accept-Language": "en-CA,en;q=0.8"},
                timeout=30,
                follow_redirects=True,
                transport=self.transport,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # ── politeness ──────────────────────────────────────────────────────────

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.min_delay:
                time.sleep(self.min_delay - gap)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            self._wait(parts.netloc)
            resp = self._http().get(f"{host}/robots.txt")
            if resp.status_code in (401, 403):
                raise Blocked(f"{host}/robots.txt answered {resp.status_code}")
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            self._robots[host] = rp
        return rp.can_fetch(self.user_agent, url)

    # ── fetching ────────────────────────────────────────────────────────────

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".html")

    def get(self, url: str, use_cache: bool = True) -> str:
        if not self.allowed(url):
            raise Disallowed(url)
        path = self._cache_path(url)
        if use_cache and path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl_s:
            return path.read_text(encoding="utf-8")
        resp = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text, encoding="utf-8")
        return resp.text

    def get_bytes(self, url: str) -> bytes:
        """`get` for an image: the same politeness, cached as bytes and kept (photos don't change)."""
        if not self.allowed(url):
            raise Disallowed(url)
        path = self._cache_path(url).with_suffix(".bin")
        if path.exists():
            return path.read_bytes()
        resp = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return resp.content

    def _fetch(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            self._wait(host)
            resp = self._http().get(url)
            if resp.status_code in (401, 403):
                raise Blocked(f"{url} answered {resp.status_code}")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 2))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")
