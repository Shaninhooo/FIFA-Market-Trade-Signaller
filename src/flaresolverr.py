import asyncio
import os
import threading
import time
import requests
import aiohttp

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
FLARESOLVERR_MAX_TIMEOUT_MS = 60000

flaresolverr_semaphore = threading.Semaphore(3)
async_flaresolverr_semaphore = asyncio.Semaphore(3)

# How long a solved Cloudflare cookie stays trusted before we proactively
# re-solve, even if the site hasn't rejected us yet. Tune this down if you
# start seeing "stale session" fallbacks a lot; up if refreshes feel wasteful.
CF_SESSION_TTL_SECONDS = 25 * 60

# Shared cookie/user-agent cache. Both the sync and async paths read/write
# this; each has its own lock, so in rare cases both could refresh at once —
# harmless, just means one extra FlareSolverr call, not a bug.
_cf_session = {"cookies": None, "user_agent": None, "expires_at": 0}
_cf_refresh_lock = threading.Lock()
_cf_async_refresh_lock = asyncio.Lock()


def _looks_blocked(status: int, text: str) -> bool:
    if status in (403, 503):
        return True
    return "just a moment" in text[:2000].lower()


# ========== Slow path: full FlareSolverr solve (unchanged behaviour) ==========

def flaresolverr_get(url: str) -> str | None:
    """Fetch a page's HTML through FlareSolverr (sync), bypassing Cloudflare challenges."""
    solved = _solve_via_flaresolverr(url)
    return solved["html"] if solved else None


def _solve_via_flaresolverr(url: str) -> dict | None:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": FLARESOLVERR_MAX_TIMEOUT_MS}

    with flaresolverr_semaphore:
        try:
            resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=FLARESOLVERR_MAX_TIMEOUT_MS / 1000 + 10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"FlareSolverr request failed for {url}: {e}")
            return None

    if data.get("status") != "ok":
        print(f"FlareSolverr failed for {url}: {data.get('message')}")
        return None

    solution = data["solution"]
    if solution.get("status") != 200:
        print(f"Failed to fetch {url} via FlareSolverr (status {solution.get('status')})")
        return None

    return {
        "html": solution["response"],
        "cookies": {c["name"]: c["value"] for c in solution.get("cookies", [])},
        "user_agent": solution.get("userAgent"),
    }


async def async_flaresolverr_get(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch a page's HTML through FlareSolverr (async), bypassing Cloudflare challenges."""
    solved = await _async_solve_via_flaresolverr(session, url)
    return solved["html"] if solved else None


async def _async_solve_via_flaresolverr(session: aiohttp.ClientSession, url: str) -> dict | None:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": FLARESOLVERR_MAX_TIMEOUT_MS}

    async with async_flaresolverr_semaphore:
        try:
            async with session.post(FLARESOLVERR_URL, json=payload) as resp:
                data = await resp.json()
        except aiohttp.ClientError as e:
            print(f"FlareSolverr request failed for {url}: {e}")
            return None

    if data.get("status") != "ok":
        print(f"FlareSolverr failed for {url}: {data.get('message')}")
        return None

    solution = data["solution"]
    if solution.get("status") != 200:
        print(f"Failed to fetch {url} via FlareSolverr (status {solution.get('status')})")
        return None

    return {
        "html": solution["response"],
        "cookies": {c["name"]: c["value"] for c in solution.get("cookies", [])},
        "user_agent": solution.get("userAgent"),
    }


# ========== Fast path: reuse cached Cloudflare cookies, no browser ==========

def fast_get(url: str) -> str | None:
    """Fetch a page using cached Cloudflare cookies via plain requests — no
    browser, no ~20s solve time. Falls back to a full FlareSolverr solve
    (which also refreshes the cache) if there's no valid session yet, or
    the site rejects the direct request."""

    def _try_fast():
        if time.time() >= _cf_session["expires_at"]:
            return False, None
        try:
            resp = requests.get(
                url,
                cookies=_cf_session["cookies"],
                headers={"User-Agent": _cf_session["user_agent"]},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"Fast request failed for {url}: {e}")
            return False, None
        if _looks_blocked(resp.status_code, resp.text):
            return False, None
        return True, resp.text

    ok, html = _try_fast()
    if ok:
        return html

    with _cf_refresh_lock:
        # Another thread may have refreshed it while we waited for the lock
        ok, html = _try_fast()
        if ok:
            return html

        solved = _solve_via_flaresolverr(url)
        if solved is None:
            return None
        _cf_session["cookies"] = solved["cookies"]
        _cf_session["user_agent"] = solved["user_agent"]
        _cf_session["expires_at"] = time.time() + CF_SESSION_TTL_SECONDS
        print("🍪 Refreshed Cloudflare session")
        return solved["html"]


async def async_fast_get(session: aiohttp.ClientSession, url: str) -> str | None:
    """Async version of fast_get."""

    async def _try_fast():
        if time.time() >= _cf_session["expires_at"]:
            return False, None
        try:
            async with session.get(
                url,
                cookies=_cf_session["cookies"],
                headers={"User-Agent": _cf_session["user_agent"]},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                text = await resp.text()
                blocked = _looks_blocked(resp.status, text)
        except aiohttp.ClientError as e:
            print(f"Fast request failed for {url}: {e}")
            return False, None
        if blocked:
            return False, None
        return True, text

    ok, html = await _try_fast()
    if ok:
        return html

    async with _cf_async_refresh_lock:
        ok, html = await _try_fast()
        if ok:
            return html

        solved = await _async_solve_via_flaresolverr(session, url)
        if solved is None:
            return None
        _cf_session["cookies"] = solved["cookies"]
        _cf_session["user_agent"] = solved["user_agent"]
        _cf_session["expires_at"] = time.time() + CF_SESSION_TTL_SECONDS
        print("🍪 Refreshed Cloudflare session (async)")
        return solved["html"]