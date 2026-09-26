"""
One HTTP client for both venues: a user agent, a pace, retries.

Both venues' reads are free and unauthenticated, and both are rate-limited
-- Kalshi's basic tier by a token bucket (about 20 requests a second),
Polymarket by Cloudflare per IP. A cap on requests in flight isn't enough to
stay under either: a fast endpoint turns eight in flight into forty a
second, and Kalshi answers the excess with 429s. So requests are also paced,
started no faster than the venue's `per_second`, and a 429 pushes the pace
back for every request waiting, not just the one refused -- otherwise they
all retry into the same wall. A 429 or a 5xx is retried; anything else is
the request being wrong, and raises.

Polymarket answers Python's default user agent with a 403, so every request
says what it is.
"""

import asyncio
from types import TracebackType
from typing import Any, Self

import aiohttp

USER_AGENT = "gold-rush (+https://github.com/NathanDeMaria/gold-rush)"

# Requests in flight at once, per client.
CONCURRENCY = 8
# Retries on a 429 or a 5xx, backing off 1, 2, 4, 8, 16, 32 seconds unless
# the venue says how long in Retry-After.
RETRIES = 6
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class Http:
    """
    A small async JSON client. Use as a context manager:

        async with Http(per_second=8) as http:
            page = await http.get_json(url, params)
    """

    def __init__(
        self, per_second: float = 10.0, concurrency: int = CONCURRENCY
    ) -> None:
        self._limit = asyncio.Semaphore(concurrency)
        self._interval = 1.0 / per_second
        self._next_start = 0.0
        self._pace = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Self:
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=60),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            await self._session.close()

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("Http is a context manager: `async with Http() as h`")
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}
        for attempt in range(RETRIES + 1):
            async with self._limit:
                await self._take_turn()
                async with self._session.get(url, params=query) as response:
                    if response.status not in _RETRY_STATUSES or attempt == RETRIES:
                        if response.status >= 400:
                            body = await response.text()
                            raise HttpError(response.status, str(response.url), body)
                        return await response.json(content_type=None)
                    wait = _retry_after(response) or 2.0**attempt
            await self._back_off(wait)
        raise AssertionError("unreachable")

    async def _take_turn(self) -> None:
        async with self._pace:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._next_start > now:
                await asyncio.sleep(self._next_start - now)
                now = loop.time()
            self._next_start = max(now, self._next_start) + self._interval

    async def _back_off(self, seconds: float) -> None:
        # Everyone waits, not only the request that was refused.
        async with self._pace:
            now = asyncio.get_running_loop().time()
            self._next_start = max(self._next_start, now + seconds)
        await asyncio.sleep(seconds)


def _retry_after(response: aiohttp.ClientResponse) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


class HttpError(Exception):
    """A request a venue refused, with what it said about why."""

    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"{status} from {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body
