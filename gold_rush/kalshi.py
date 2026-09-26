"""
Kalshi's game markets, and what they traded at.

A Kalshi game is an event -- `KXNFLGAME-26SEP24ATLGB` -- holding one yes/no
market per team: `...-ATL` pays if Atlanta wins, `...-GB` if Green Bay does.
Each has its own order book, so the two prices needn't sum to one; the gap
is the spread. The event ticker carries the date (US Eastern) and the two
codes run together, which is why the sides are read off the markets' own
tickers instead. Nothing in either says which team is home.

Markets move to a historical tier some weeks after they settle, and the two
tiers are separate endpoints: `/markets` with a close-time filter for the
live one, `/historical/markets` -- which ignores that filter -- for the
rest. The candlesticks differ between them too, down to the field names
(`close_dollars` live, `close` historical).

All of it is readable without an account.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from .http import Http
from .leagues import League
from .types import PricePoint, Side, VenueGame

KALSHI = "kalshi"
API = "https://api.elections.kalshi.com/trade-api/v2"

_PAGE = 1000
# Hourly candles. Kalshi also keeps minute and daily ones.
_PERIOD_MINUTES = 60
# Candles asked for per request. Kalshi doesn't document a cap; two weeks
# of hours is well inside anything it's been seen to return.
_HISTORY_WINDOW = timedelta(days=14)
_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS: dict[str, int] = {
    name: number
    for number, name in enumerate(
        "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1
    )
}


def event_day(event_ticker: str) -> date | None:
    """The date in an event ticker: `KXNFLGAME-26SEP24ATLGB` is 2026-09-24."""
    found = _EVENT_DATE.search(event_ticker)
    if found is None:
        return None
    year, month, day = found.groups()
    if month not in _MONTHS:
        return None
    return date(2000 + int(year), _MONTHS[month], int(day))


def games_from_markets(
    markets: Iterable[dict], league: str, series: str, historical: bool
) -> tuple[list[VenueGame], list[str]]:
    """
    Group a listing of markets into games, and say which events didn't make one.

    An event makes a game when it has exactly two markets on teams. A tie
    market, where a venue lists one, isn't a team and is left out; an event
    with any other count is reported by ticker rather than guessed at.
    """
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        by_event[market["event_ticker"]].append(market)
    games, odd = [], []
    for event, event_markets in sorted(by_event.items()):
        teams = [m for m in event_markets if _code(m) != "TIE"]
        day = event_day(event)
        if len(teams) != 2 or day is None:
            odd.append(event)
            continue
        teams.sort(key=lambda m: m["ticker"])
        first, second = (_side(m) for m in teams)
        games.append(
            VenueGame(
                venue=KALSHI,
                league=league,
                event=event,
                day=day,
                kickoff=None,
                sides=(first, second),
                opened=_earliest(m.get("open_time") for m in teams),
                closed=_latest(m.get("close_time") for m in teams),
                extra={"series": series, "historical": historical},
            )
        )
    return games, odd


def price_points(candles: Iterable[dict]) -> list[PricePoint]:
    """
    Kalshi's hourly candles as PricePoints, the close of each hour.

    Reads both tiers' spellings: `close_dollars`/`volume_fp` from the live
    endpoint and `close`/`volume` from the historical one. A candle for an
    hour nobody traded has an empty `price` and still has quotes.
    """
    points = []
    for candle in candles:
        points.append(
            PricePoint(
                at=int(candle["end_period_ts"]),
                price=_close(candle.get("price")),
                bid=_close(candle.get("yes_bid")),
                ask=_close(candle.get("yes_ask")),
                volume=_number(candle.get("volume_fp", candle.get("volume"))),
            )
        )
    return points


class Kalshi:
    """Kalshi's games and price histories, read through an `Http`."""

    name = KALSHI
    # Requests a second. Kalshi's basic tier allows about 20; candlesticks
    # have been seen to draw it down faster than that suggests.
    per_second = 8.0

    def __init__(self, http: Http) -> None:
        self._http = http

    async def games(
        self, league: League, start: date, end: date
    ) -> tuple[list[VenueGame], list[str]]:
        """
        Every game in `league` filed on a day from `start` to `end`, and the
        events that didn't parse as one.

        A market closes when its game settles, so the live tier is asked for
        markets closing from `start` to a few days past `end` -- a Monday
        night game settles on Tuesday, UTC -- and trimmed to the days asked
        for. The historical tier is read whole, since it ignores the filter,
        and only when the window reaches back past its cutoff.
        """
        cutoff = await self._historical_cutoff()
        games, odd = [], []
        for series in league.kalshi_series:
            live = await self._markets(
                "/markets",
                {
                    "series_ticker": series,
                    "min_close_ts": _epoch(start),
                    "max_close_ts": _epoch(end + timedelta(days=3)),
                },
            )
            found, bad = games_from_markets(live, league.name, series, False)
            games += found
            odd += bad
            if cutoff is None or start <= cutoff.date() + timedelta(days=3):
                old = await self._markets(
                    "/historical/markets", {"series_ticker": series}
                )
                found, bad = games_from_markets(old, league.name, series, True)
                seen = {game.event for game in games}
                games += [game for game in found if game.event not in seen]
                odd += bad
        return [g for g in games if start <= g.day <= end], sorted(set(odd))

    async def history(self, game: VenueGame, side: Side) -> list[PricePoint]:
        """Hourly candles for one side's market, from its open to its close."""
        if game.opened is None:
            return []
        until = game.closed or datetime.now(UTC)
        if game.extra.get("historical"):
            path = f"/historical/markets/{side.contract}/candlesticks"
        else:
            path = (
                f"/series/{game.extra['series']}/markets/{side.contract}/candlesticks"
            )
        points: dict[int, PricePoint] = {}
        start = game.opened
        while start < until:
            stop = min(start + _HISTORY_WINDOW, until)
            page = await self._http.get_json(
                API + path,
                {
                    "start_ts": int(start.timestamp()),
                    "end_ts": int(stop.timestamp()),
                    "period_interval": _PERIOD_MINUTES,
                },
            )
            for point in price_points(page.get("candlesticks") or []):
                points[point.at] = point
            start = stop
        return [points[at] for at in sorted(points)]

    async def _markets(self, path: str, params: dict[str, Any]) -> list[dict]:
        markets: list[dict] = []
        cursor = None
        while True:
            page = await self._http.get_json(
                API + path, {**params, "limit": _PAGE, "cursor": cursor}
            )
            markets += page.get("markets") or []
            cursor = page.get("cursor")
            if not cursor or not page.get("markets"):
                return markets

    async def _historical_cutoff(self) -> datetime | None:
        page = await self._http.get_json(API + "/historical/cutoff")
        return _timestamp(page.get("market_settled_ts"))


def _code(market: dict) -> str:
    return market["ticker"].rsplit("-", 1)[-1]


def _side(market: dict) -> Side:
    names = tuple(dict.fromkeys(n for n in (market.get("yes_sub_title"),) if n))
    return Side(code=_code(market), names=names, contract=market["ticker"])


def _close(ohlc: Any) -> float | None:
    if not isinstance(ohlc, dict):
        return None
    return _number(ohlc.get("close_dollars", ohlc.get("close")))


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _earliest(values: Iterable[Any]) -> datetime | None:
    stamps = [t for t in map(_timestamp, values) if t is not None]
    return min(stamps) if stamps else None


def _latest(values: Iterable[Any]) -> datetime | None:
    stamps = [t for t in map(_timestamp, values) if t is not None]
    return max(stamps) if stamps else None


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time(), tzinfo=UTC).timestamp())
