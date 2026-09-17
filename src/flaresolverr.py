import asyncio
import os
import requests
from bs4 import BeautifulSoup
import datetime
import random
import time
from collections import defaultdict
from unidecode import unidecode
import re
import pytz
import threading
import aiohttp

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
FLARESOLVERR_MAX_TIMEOUT_MS = 60000

flaresolverr_semaphore = threading.Semaphore(3)
async_flaresolverr_semaphore = asyncio.Semaphore(3)


def flaresolverr_get(url: str) -> str | None:
    """Fetch a page's HTML through FlareSolverr (sync), bypassing Cloudflare challenges."""
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

    return solution["response"]


async def async_flaresolverr_get(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch a page's HTML through FlareSolverr (async), bypassing Cloudflare challenges."""
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
        print(f"Failed to fetch sales page {url} via FlareSolverr (status {solution.get('status')})")
        return None

    return solution["response"]