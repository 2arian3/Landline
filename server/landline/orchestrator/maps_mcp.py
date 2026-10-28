"""Google's Maps MCP server, Maps Grounding Lite (O-07a, #239), over httpx.

The server is https://mapstools.googleapis.com/mcp: Streamable HTTP and stateless (MCP 2026-07-28), so every
tools/call is one JSON-RPC POST with the API key in X-Goog-Api-Key, with no initialize and no session id. It
may answer with JSON or with an SSE stream for that one request; `_call` reads both. Any failure (no answer
within 3 s, an HTTP or JSON-RPC error, a tool's `isError`, such as the 403 a missing or wrong key gets) is one
log line and None. The key is never logged.

Three helpers return plain dataclasses for O-07b's `ask_maps`:
- `nearby(lat, lng, what)`: the closest of Google's top 3 matches around a point, and the walk to it;
- `route(origin, destination, mode)`: minutes and km by DRIVE, WALK, BICYCLE or TRANSIT;
- `weather(lat, lng, day)`: a one-line daily forecast, today to 9 days ahead.

Google's terms for Grounding Lite (Service Specific Terms §10) and Arian's call on them (option B, #239):
- Every answer carries Google's attribution and, for a place, its Google Maps link, to give with the answer
  along with `DISCLOSURE`. Walking minutes carry `WALK_WARNING`, since Google's walking routes are in beta.
- Google's place is not drawn on our own (MapLibre) map. Its coordinates are here for our own use, such as
  routing, not for a pin or a fly-to.
- Nothing here is cached. An answer is kept no longer than `keep_until` (30 days) and never used for training.

search_places has no name field: a place's name is only in Google's `summary`, which cites places as
"[0]", "[1]"... `place_name` takes the name from there when the text makes it clear, and gives None rather
than guess.
The model passes words only (hard rule 2): coordinates come from the listing, and minutes and km are computed
here from Google's numbers.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from landline.config import Settings

log = logging.getLogger(__name__)

URL = "https://mapstools.googleapis.com/mcp"
PROTOCOL = "2026-07-28"          # MCP's stateless revision
TIMEOUT_S = 3.0
NEARBY_RADIUS_M = 1500
NEARBY_RESULTS = 3               # search_places returns at most 5
REGION = "CA"
MODES = ("DRIVE", "WALK", "BICYCLE", "TRANSIT")
FORECAST_DAYS = 10               # daily forecasts cover today and the next 9 days
KEEP = timedelta(days=30)        # §10.2.2: Grounded Output may be cached for 30 days at most
ATTRIBUTION = "Google Maps"      # used only when an answer comes without its own attribution title
DISCLOSURE = "AI generated content may include information from Google Maps"
WALK_WARNING = "Walking directions are in beta and may be missing clear sidewalks or pedestrian paths."
TORONTO = ZoneInfo("America/Toronto")
_META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL,
    "io.modelcontextprotocol/clientInfo": {"name": "landline", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


@dataclass(frozen=True, kw_only=True)
class Answer:
    """What every answer carries for Google's terms: the attribution, shown exactly as given and linked to its
    URL, and when Google gave it."""
    attribution: str
    attribution_url: str | None
    fetched_at: datetime

    @property
    def keep_until(self) -> datetime:
        return self.fetched_at + KEEP


@dataclass(frozen=True, kw_only=True)
class Nearby(Answer):
    """A place near a point, and the walk to it. `name` is None when Google's summary doesn't make it
    clear; `minutes` and `distance_m` are None when Google found no walk. `lat`/`lng` are for our own use,
    not for our map (option B)."""
    name: str | None
    place_id: str
    lat: float | None
    lng: float | None
    maps_url: str | None          # the place on Google Maps: the source link to give with the answer
    summary: str                  # Google's own words about the search, citations and all
    minutes: int | None
    distance_m: int | None
    warning: str | None           # WALK_WARNING whenever there are walking minutes


@dataclass(frozen=True, kw_only=True)
class Route(Answer):
    mode: str
    minutes: int
    distance_m: int
    km: float
    warning: str | None           # WALK_WARNING for a walk


@dataclass(frozen=True, kw_only=True)
class Weather(Answer):
    day: date
    summary: str                  # e.g. "Light rain, high 12°C, low 6°C, 60% chance of rain."
    condition: str | None
    high_c: int | None
    low_c: int | None
    precip_pct: int | None


# A waypoint: (lat, lng), an address in words, or a place nearby() found.
Point = tuple[float, float] | str | Nearby


class MapsMCP:
    def __init__(self, api_key: str, url: str = URL, client: httpx.AsyncClient | None = None,
                 timeout_s: float = TIMEOUT_S, now: Callable[[], datetime] | None = None) -> None:
        self._key = api_key
        self.url = url
        self.timeout_s = timeout_s
        self.client = client or httpx.AsyncClient(timeout=timeout_s)
        self._now = now or (lambda: datetime.now(UTC))
        self._ids = itertools.count(1)

    async def close(self) -> None:
        await self.client.aclose()

    async def nearby(self, lat: float, lng: float, what: str) -> Nearby | None:
        """The closest of Google's top matches for `what` around (lat, lng), and the walk there from that
        point. None when Google can't be asked or finds nothing; a place with no walking minutes when only
        the walk fails."""
        what = " ".join((what or "").split())
        if not what:
            return None
        found = await self._call("search_places", {
            "textQuery": what,
            "locationBias": {"circle": {"center": _latlng(lat, lng), "radiusMeters": NEARBY_RADIUS_M}},
            "regionCode": REGION,
            "pageSize": NEARBY_RESULTS,
        })
        if found is None:
            return None
        places = found.get("places") if isinstance(found.get("places"), list) else []
        i = _closest(places, lat, lng)
        if i is None:
            log.info("maps mcp search_places: no match near %.4f,%.4f", lat, lng)
            return None
        place = places[i]
        pid = _place_id(place)
        loc = place.get("location") if isinstance(place.get("location"), dict) else {}
        links = place.get("googleMapsLinks") if isinstance(place.get("googleMapsLinks"), dict) else {}
        attribution, attribution_url = _attribution(place)
        summary = found.get("summary") if isinstance(found.get("summary"), str) else ""
        walk = await self._route({"latLng": _latlng(lat, lng)}, {"placeId": pid}, "WALK")
        return Nearby(
            name=place_name(summary, i, what), place_id=pid,
            lat=_number(loc.get("latitude")), lng=_number(loc.get("longitude")),
            maps_url=links.get("placeUrl") or attribution_url, summary=summary,
            minutes=to_minutes(walk[0]) if walk else None, distance_m=walk[1] if walk else None,
            warning=WALK_WARNING if walk else None,
            attribution=attribution, attribution_url=attribution_url, fetched_at=self._now())

    async def route(self, origin: Point, destination: Point, mode: str = "WALK") -> Route | None:
        """Minutes and km from origin to destination. An address is read near whichever end is coordinates.
        Raises ValueError for a mode Google doesn't have."""
        mode = mode.strip().upper()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        o, d = _waypoint(origin), _waypoint(destination)
        bias = None
        if "address" in o or "address" in d:   # "Bloor" means the Bloor near the listing
            bias = next((w["latLng"] for w in (o, d) if "latLng" in w), None)
        got = await self._route(o, d, mode, bias)
        if got is None:
            return None
        seconds, meters, route = got
        attribution, attribution_url = _attribution(route)
        return Route(mode=mode, minutes=to_minutes(seconds), distance_m=meters, km=round(meters / 1000, 1),
                     warning=WALK_WARNING if mode == "WALK" else None,
                     attribution=attribution, attribution_url=attribution_url, fetched_at=self._now())

    async def weather(self, lat: float, lng: float, day: date) -> Weather | None:
        """The day's forecast at (lat, lng) in one line. None for a day outside today to 9 days ahead (Toronto
        time), which Google doesn't forecast, without asking."""
        ahead = (day - self._now().astimezone(TORONTO).date()).days
        if not 0 <= ahead < FORECAST_DAYS:
            log.info("maps mcp lookup_weather: %s is outside the %d-day forecast", day, FORECAST_DAYS)
            return None
        out = await self._call("lookup_weather", {
            "location": {"latLng": _latlng(lat, lng)},
            "date": {"year": day.year, "month": day.month, "day": day.day},
            "unitsSystem": "METRIC",
        })
        if out is None:
            return None
        # The newer answer nests the day under "weather"; the documented one has it at the top.
        w = out["weather"] if isinstance(out.get("weather"), dict) else out
        cond = w.get("weatherCondition") if isinstance(w.get("weatherCondition"), dict) else {}
        desc = cond.get("description") if isinstance(cond.get("description"), dict) else {}
        condition = desc["text"].strip() or None if isinstance(desc.get("text"), str) else None
        high, low = _degrees(w.get("maxTemperature")), _degrees(w.get("minTemperature"))
        pct, kind = _precipitation(w.get("precipitation"))
        line = weather_line(condition, high, low, pct, kind)
        if line is None:
            log.warning("maps mcp lookup_weather: no forecast in the answer for %s", day)
            return None
        attribution, attribution_url = _attribution(w if isinstance(w.get("attribution"), dict) else out)
        return Weather(day=day, summary=line, condition=condition, high_c=high, low_c=low, precip_pct=pct,
                       attribution=attribution, attribution_url=attribution_url, fetched_at=self._now())

    async def _route(self, origin: dict, destination: dict, mode: str,
                     bias: dict | None = None) -> tuple[float, int, dict] | None:
        """Google's recommended route: (seconds, meters, the route), or None."""
        args: dict[str, Any] = {"origin": origin, "destination": destination, "travelMode": mode}
        if bias:
            args["locationBias"] = bias
        out = await self._call("compute_routes", args)
        if out is None:
            return None
        routes = out.get("routes") if isinstance(out.get("routes"), list) else []
        route = routes[0] if routes and isinstance(routes[0], dict) else {}
        seconds = duration_s(route.get("duration"))
        if seconds is None:
            log.info("maps mcp compute_routes: no %s route", mode)
            return None
        # Google's JSON leaves out zero values, so a route with no distanceMeters is 0 m long.
        meters = route.get("distanceMeters")
        return seconds, int(meters) if isinstance(meters, int | float) else 0, route

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """One tools/call: the tool's structured result, or None (and one log line) on any failure."""
        rid = next(self._ids)
        body = {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments, "_meta": _META}}
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": PROTOCOL, "Mcp-Method": "tools/call", "Mcp-Name": tool,
                   "X-Goog-Api-Key": self._key}
        try:
            async with asyncio.timeout(self.timeout_s):
                r = await self.client.post(self.url, json=body, headers=headers, timeout=self.timeout_s)
        except (TimeoutError, httpx.TimeoutException):
            log.warning("maps mcp %s: no answer within %gs", tool, self.timeout_s)
            return None
        except httpx.HTTPError as e:   # its message has the URL at most, never a header
            log.warning("maps mcp %s: request failed (%s: %s)", tool, type(e).__name__, str(e)[:200])
            return None
        msg = parse_message(r.text, r.headers.get("content-type", ""), rid)
        problem = _problem(r.status_code, msg)
        result = _structured(msg["result"]) if problem is None and msg else None
        if result is None:
            problem = problem or f"HTTP {r.status_code}, but no structured result"
            log.warning("maps mcp %s: %s", tool, problem.replace(self._key, "[key]"))
        return result


def from_settings(settings: Settings) -> MapsMCP | None:
    """The client, or None without GOOGLE_MAPS_API_KEY."""
    key = (settings.google_maps_api_key or "").strip()
    return MapsMCP(key) if key else None


# --- JSON-RPC over Streamable HTTP --------------------------------------------------------------------------

def parse_message(body: str, content_type: str, request_id: int | str | None = None) -> dict[str, Any] | None:
    """The JSON-RPC answer in a response body: the JSON object, or from an SSE stream the event whose id is
    `request_id` (any answer, when it's None)."""
    if "text/event-stream" not in content_type.lower():
        try:
            msg = json.loads(body)
        except ValueError:
            msg = None
        if isinstance(msg, dict) or "data:" not in body:
            return msg if isinstance(msg, dict) else None
    for data in _sse_data(body):
        try:
            msg = json.loads(data)
        except ValueError:
            continue
        if isinstance(msg, dict) and ("result" in msg or "error" in msg) \
                and (request_id is None or msg.get("id") == request_id):
            return msg
    return None


def _sse_data(body: str) -> list[str]:
    """Each SSE event's data, its `data:` lines joined by newlines."""
    events: list[str] = []
    lines: list[str] = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            if lines:
                events.append("\n".join(lines))
                lines = []
        elif line.startswith("data:"):
            value = line[5:]
            lines.append(value[1:] if value.startswith(" ") else value)
    if lines:
        events.append("\n".join(lines))
    return events


def _problem(status: int, msg: dict[str, Any] | None) -> str | None:
    """Why an answer can't be used, or None. A missing key is HTTP 403 with a result that has isError."""
    if msg is None:
        return f"HTTP {status}, no JSON-RPC answer"
    if "error" in msg:
        err = msg["error"] if isinstance(msg["error"], dict) else {}
        return f"HTTP {status}, JSON-RPC error {err.get('code')}: {str(err.get('message'))[:200]}"
    result = msg.get("result")
    if not isinstance(result, dict):
        return f"HTTP {status}, no result"
    if result.get("isError"):
        return f"HTTP {status}, tool error: {_text(result)[:200]}"
    if status >= 300:
        return f"HTTP {status}"
    return None


def _structured(result: dict[str, Any]) -> dict[str, Any] | None:
    """The tool's output: `structuredContent`, or else the first text content that is a JSON object."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            try:
                value = json.loads(c.get("text") or "")
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def _text(result: dict[str, Any]) -> str:
    return " ".join(c.get("text", "") for c in result.get("content") or []
                    if isinstance(c, dict) and isinstance(c.get("text"), str)).strip() or "(no message)"


# --- Reading Google's answers -------------------------------------------------------------------------------

def duration_s(duration: Any) -> float | None:
    """Seconds in a Google duration such as "812s" or "812.5s"."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", duration) if isinstance(duration, str) else None
    return float(m.group(1)) if m else None


def to_minutes(seconds: float) -> int:
    """Whole minutes, rounded half up, and at least 1: 812 s is 14 minutes."""
    return max(1, math.floor(seconds / 60 + 0.5))


def weather_line(condition: str | None, high: int | None, low: int | None, pct: int | None,
                 kind: str = "precipitation") -> str | None:
    parts = [p for p in (
        condition,
        f"high {high}°C" if high is not None else None,
        f"low {low}°C" if low is not None else None,
        f"{pct}% chance of {kind}" if pct is not None else None,
    ) if p]
    if not parts:
        return None
    line = ", ".join(parts)
    return line[0].upper() + line[1:] + "."


def _degrees(t: Any) -> int | None:
    d = t.get("degrees") if isinstance(t, dict) else None
    return math.floor(d + 0.5) if isinstance(d, int | float) else None


def _precipitation(p: Any) -> tuple[int | None, str]:
    """(chance in %, its kind). A probability with no percent is 0%: Google's JSON leaves out zero values."""
    prob = p.get("probability") if isinstance(p, dict) else None
    if not isinstance(prob, dict):
        return None, "precipitation"
    kind = prob.get("type") if isinstance(prob.get("type"), str) else ""
    if not kind or kind.endswith("UNSPECIFIED"):
        kind = "precipitation"
    kind = kind.lower().replace("_", " ")
    pct = prob.get("percent", 0)
    return (int(pct) if isinstance(pct, int | float) else None), kind


def _attribution(thing: dict[str, Any]) -> tuple[str, str | None]:
    a = thing.get("attribution") if isinstance(thing.get("attribution"), dict) else {}
    title = a.get("title").strip() if isinstance(a.get("title"), str) and a["title"].strip() else ATTRIBUTION
    url = a.get("url") if isinstance(a.get("url"), str) and a["url"] else None
    return title, url


def _place_id(place: Any) -> str | None:
    if not isinstance(place, dict):
        return None
    pid = place.get("id") or (place.get("place") or "").removeprefix("places/")
    return pid if isinstance(pid, str) and pid else None


def _closest(places: list[Any], lat: float, lng: float) -> int | None:
    """The index of the place closest to (lat, lng) in a straight line, among those with an id; the first
    of them when none has a location."""
    ids = [i for i, p in enumerate(places) if _place_id(p)]
    located = []
    for i in ids:
        loc = places[i].get("location")
        if isinstance(loc, dict) and _number(loc.get("latitude")) is not None \
                and _number(loc.get("longitude")) is not None:
            located.append((_metres(lat, lng, loc["latitude"], loc["longitude"]), i))
    if located:
        return min(located)[1]
    return ids[0] if ids else None


def _metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _number(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _latlng(lat: float, lng: float) -> dict[str, float]:
    return {"latitude": lat, "longitude": lng}


def _waypoint(p: Point) -> dict[str, Any]:
    if isinstance(p, Nearby):
        return {"placeId": p.place_id}
    if isinstance(p, str):
        return {"address": p}
    lat, lng = p
    return {"latLng": _latlng(lat, lng)}


# --- A place's name, from Google's summary ------------------------------------------------------------------

_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_BOLD = r"(?:\*\*(.+?)\*\*|__(.+?)__)"
_BOLD_LAST = re.compile(_BOLD + r"[\s:,.\-–—]*$")
_BOLD_LEAD = re.compile(r"\s*(?:[*\-•]|\d+[.)])?\s*" + _BOLD)
_ABBREV = re.compile(r"[A-Z][a-z]{0,2}\.")          # St. Mt. Dr. Co. Ave.
_CLAUSE_END = re.compile(r"[,;:.!?)\]]$")
# What comes between a place's name and where it is: ", ", " at ", " is located on ", " - ".
_BEFORE_PLACE = re.compile(
    r"(?:,|(?:\b(?:is|are)\s+)?(?:\blocated\s+)?\b(at|on|along|near|by)|\s([-–—]))\s*$")
# Lower-case words that can sit inside a name: "Bank of Montreal", "Fresh & Wild".
_CONNECTORS = {"of", "the", "and", "&", "de", "du", "des", "la", "le"}
# Words that start a sentence before a name ("Try Loblaws [0]"), and never are one alone.
_LEADING = {"a", "an", "at", "in", "on", "by", "try", "visit", "check", "consider", "there", "here", "nearby",
            "also", "another", "near", "you", "your", "it", "its", "this", "that", "these", "those", "both",
            "one", "for", "from", "with", "head", "go", "yes", "closest", "nearest", "then", "and", "or",
            "but", "if", "while", "however", "alternatively", "additionally", "options", "option"}
_GENERIC = {"google", "google maps", "toronto", "ontario", "canada", "downtown", "midtown", "the", "a", "an"}
_STREET = {"st", "street", "ave", "av", "avenue", "rd", "road", "blvd", "boulevard", "dr", "drive", "cres",
           "crescent", "ct", "crt", "court", "ln", "lane", "pkwy", "parkway", "hwy", "highway", "terr",
           "terrace", "way", "w", "e", "n", "s", "west", "east", "north", "south"}
# Query words too general to pick out a name: "grocery store" names no store.
_GENERAL = {"store", "stores", "shop", "shops", "grocery", "groceries", "pharmacy", "drugstore",
            "supermarket", "market", "gym", "park", "cafe", "coffee", "restaurant", "near", "nearby",
            "closest", "nearest", "the", "and", "station", "school", "bank", "food", "place", "places", "one",
            "any"}


def place_name(summary: str, index: int = 0, query: str = "") -> str | None:
    """The name of place `index` in a search_places summary, from the text that cites it as "[index]"; None
    when that isn't clear. In order: bold text cited for that place alone, the name-like words just before
    its citation (past an address, if one is in between), then a name in a sentence that cites it and
    shares a word with `query`."""
    if not summary:
        return None
    groups = _citation_groups(summary)
    mine = [(k, g) for k, g in enumerate(groups) if index in g[2]]
    segments = []   # the text between a citation of this place alone and the citation or line before it
    for k, (start, _, indices) in mine:
        if indices == {index}:
            prev_end = groups[k - 1][1] if k else 0
            segments.append(summary[max(prev_end, summary.rfind("\n", 0, start) + 1):start])
    for seg in segments:
        if name := _bold_name(seg):
            return name
    for seg in segments:
        if name := _name_before(seg):
            return name
    words = {w for w in re.findall(r"[\w'’-]+", query.lower()) if len(w) >= 3 and w not in _GENERAL}
    if words:
        for _, (start, end, _) in mine:
            for run in _runs(_sentence(summary, start, end)):
                if {w.lower().strip(".,:;!?'’") for w in run.split()} & words and (name := _clean(run)):
                    return name
    return None


def _citation_groups(summary: str) -> list[tuple[int, int, set[int]]]:
    """(start, end, places cited) for each citation; "[0][1]" and "[0, 1]" are one citation of two places."""
    groups: list[tuple[int, int, set[int]]] = []
    for m in _CITE.finditer(summary):
        cited = {int(x) for x in m.group(1).split(",")}
        if groups and not summary[groups[-1][1]:m.start()].strip():
            start, _, before = groups[-1]
            groups[-1] = (start, m.end(), before | cited)
        else:
            groups.append((m.start(), m.end(), cited))
    return groups


def _bold_name(seg: str) -> str | None:
    """Bold text right before the citation, or leading the line (a list item's name)."""
    for m in (_BOLD_LAST.search(seg), _BOLD_LEAD.match(seg)):
        if m and (name := _clean(m.group(1) or m.group(2))):
            return name
    return None


def _name_before(seg: str) -> str | None:
    """The name-like words just before a citation. When they're an address, or follow "at", "on", "near" or a
    dash, they say where the place is, so the name is the run before them: "Shoppers Drug Mart at 360 Bloor
    St W [0]", "the Shoppers Drug Mart at Bloor and Christie [0]"."""
    text = _drop_parenthetical(seg.replace("**", "").replace("__", "").rstrip(" \t,:;.-–—"))
    run, rest = _tail_run(text)
    if not run:
        return None
    sep = _BEFORE_PLACE.search(rest)
    address = _addressy(run.split())
    if not address and not (sep and (sep.group(1) or sep.group(2))):
        return _clean(run)
    if sep:
        earlier, _ = _tail_run(_drop_parenthetical(rest[:sep.start()].rstrip(" \t,")))
        if name := _clean(earlier):
            return name
    if address:
        return None
    # Nothing name-like before it. "shop at Loblaws [0]" names the place; "a pharmacy at Bloor and Christie
    # [0]" and "a pharmacy near Christie Pits [0]" don't.
    if sep and (sep.group(2) or (sep.group(1) == "at" and not {"and", "&"} & set(run.split()))):
        return _clean(run)
    return None


def _drop_parenthetical(text: str) -> str:
    """Text without a trailing "(...)", such as an address or a rating."""
    text = text.rstrip()
    if text.endswith(")") and (i := text.rfind("(")) >= 0:
        return text[:i].rstrip(" \t,")
    return text


def _tail_run(text: str) -> tuple[str, str]:
    """The name-like words that end `text`, and the text before them."""
    words = list(re.finditer(r"\S+", text))
    start = len(words)
    while start > 0:
        w = words[start - 1].group()
        if start < len(words) and _CLAUSE_END.search(w) and not _ABBREV.fullmatch(w):
            break   # the word ends a clause before the run
        if not (_name_word(w) or w in _CONNECTORS):
            break
        start -= 1
    if start == len(words):
        return "", text
    return " ".join(w.group() for w in words[start:]), text[:words[start].start()]


def _runs(text: str) -> list[str]:
    """Each run of name-like words in `text`, in order, citations and bold marks left out."""
    runs: list[str] = []
    current: list[str] = []
    for w in _CITE.sub(" ", text).replace("**", " ").replace("__", " ").split():
        if _name_word(w) or (current and w in _CONNECTORS):
            current.append(w)
            if _CLAUSE_END.search(w) and not _ABBREV.fullmatch(w):
                runs.append(" ".join(current))
                current = []
        elif current:
            runs.append(" ".join(current))
            current = []
    if current:
        runs.append(" ".join(current))
    return runs


def _sentence(summary: str, start: int, end: int) -> str:
    """The sentence a citation sits in (or ends, when it comes right after the full stop)."""
    line_start = summary.rfind("\n", 0, start) + 1
    line_end = summary.find("\n", end)
    line = summary[line_start:len(summary) if line_end < 0 else line_end]
    at = start - line_start
    cuts = [0] + [m.end() for m in re.finditer(r"[.!?]\s+", line)
                  if not _ABBREV.fullmatch(line[:m.start() + 1].split()[-1])] + [len(line)]
    k = max(i for i, c in enumerate(cuts) if c <= at)
    if k > 0 and not line[cuts[k]:at].strip():
        k -= 1
    return line[cuts[k]:cuts[k + 1]] if k + 1 < len(cuts) else line[cuts[k]:]


def _name_word(w: str) -> bool:
    return w == "&" or w[:1].isupper() or w[:1].isdigit()


def _addressy(words: list[str]) -> bool:
    """A street number, a postal code, or a last word like "St" or "West"."""
    if any(re.fullmatch(r"\d+[A-Za-z]?", w.rstrip(".,")) for w in words):
        return True
    if re.search(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", " ".join(words)):
        return True
    return words[-1].rstrip(".,").lower() in _STREET


def _clean(raw: str | None) -> str | None:
    """`raw` as a place name, or None if it doesn't look like one: a few capitalised words, not an address."""
    words = " ".join((raw or "").split()).strip(" *_\"“”‘’()[]").rstrip(".,:;!?").split()
    while len(words) > 1 and words[0].lower() in _LEADING:
        words.pop(0)
    while words and words[0] in _CONNECTORS:
        words.pop(0)
    while words and words[-1] in _CONNECTORS:
        words.pop()
    if not words or len(words) > 8 or not all(_name_word(w) or w in _CONNECTORS for w in words):
        return None
    name = " ".join(words)
    if len(name) > 60 or not re.search(r"[^\W\d_]", name) or name.lower() in _GENERIC \
            or (len(words) == 1 and words[0].lower() in _LEADING) or _addressy(words):
        return None
    return name
