"""
Polymarket's game markets, and what they traded at.

A Polymarket game is an event -- slug `nfl-atl-gb-2026-09-25` -- holding a
moneyline market with two outcome tokens, one per team, that trade against
each other: one's price is one minus the other's. The same event holds the
game's spread and total ladders, and props hang off it as child events
(`parentEventId`), which are skipped.

Which market is the moneyline has been said two ways. Newer ones are typed,
`sportsMarketType: "moneyline"`; the older ones -- all of 2024-25 college
football, and March Madness 2025 -- are untyped, and are recognized by
shape instead: the market with the event's own slug and two outcomes that
aren't Yes and No.

Which team is which has also been said two ways. Newer events carry
`teams`, each with a code and whether it's home. Older ones carry only the
slug, `{league}-{code}-{code}-{date}`, whose codes run in the same order as
the outcomes, so a side is always a code and a name either way.

Price history is one number an hour (no bid or ask), asked for by token,
and at most about fifteen days of it per request.

All of it is readable without an account; Polymarket US, the regulated
venue a US account trades on, is a different exchange with its own API.
"""

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .http import Http
from .leagues import League
from .types import PricePoint, Side, VenueGame

POLYMARKET = "polymarket"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

_PAGE = 100
# Gamma refuses an offset past about 2,000, so a listing is asked for a
# week at a time: the busiest week of college basketball is ~500 games.
_LISTING_WINDOW = timedelta(days=7)
# Polymarket refuses a history window much past two weeks.
_HISTORY_WINDOW = timedelta(days=14)
_HISTORY_MINUTES = 60
_NOT_TEAMS = (["Yes", "No"], [])


def game_from_event(event: dict, league: str) -> VenueGame | None:
    """
    One Gamma event as a VenueGame, or None if it isn't a game with a
    moneyline -- a prop sub-event, a futures market, an event whose
    moneyline was never listed.
    """
    if event.get("parentEventId"):
        return None
    moneyline = _moneyline(event)
    if moneyline is None:
        return None
    outcomes = _json_list(moneyline.get("outcomes"))
    tokens = _json_list(moneyline.get("clobTokenIds"))
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    codes = _slug_codes(event.get("slug") or "")
    teams = event.get("teams") or []
    sides = []
    for index, (outcome, token) in enumerate(zip(outcomes, tokens)):
        team = _team_for(outcome, teams)
        code = (team or {}).get("abbreviation") or (codes[index] if codes else "")
        names = tuple(
            dict.fromkeys(
                n
                for n in (outcome, (team or {}).get("name"), (team or {}).get("alias"))
                if n
            )
        )
        sides.append(Side(code=code, names=names, contract=str(token)))
    kickoff = _timestamp(event.get("startTime") or moneyline.get("gameStartTime"))
    day = _day(event.get("eventDate")) or (kickoff.date() if kickoff else None)
    if day is None:
        return None
    return VenueGame(
        venue=POLYMARKET,
        league=league,
        event=event.get("slug") or str(event.get("id")),
        day=day,
        kickoff=kickoff,
        sides=(sides[0], sides[1]),
        opened=_timestamp(
            moneyline.get("acceptingOrdersTimestamp") or moneyline.get("startDate")
        ),
        closed=_timestamp(moneyline.get("closedTime")) or kickoff,
        extra={"event_id": str(event.get("id"))},
    )


def price_points(history: Iterable[dict]) -> list[PricePoint]:
    """`/prices-history` points as PricePoints: a price, and nothing else."""
    return [
        PricePoint(
            at=int(point["t"]), price=float(point["p"]), bid=None, ask=None, volume=None
        )
        for point in history
    ]


class Polymarket:
    """Polymarket's games and price histories, read through an `Http`."""

    name = POLYMARKET
    # Requests a second. Cloudflare's published limits are far above this;
    # it slows a client down before refusing it, so there's little to gain.
    per_second = 15.0

    def __init__(self, http: Http) -> None:
        self._http = http

    async def games(
        self, league: League, start: date, end: date
    ) -> tuple[list[VenueGame], list[str]]:
        """
        Every game in `league` filed on a day from `start` to `end`, and the
        events that didn't parse as one.

        Asked of every series the league has been filed under plus whatever
        /sports says is current, a week of event end dates at a time, padded
        a day either side since an event's end date is its kickoff in UTC.
        """
        games: dict[str, VenueGame] = {}
        odd: set[str] = set()
        for series in await self._series(league):
            window = start - timedelta(days=1)
            while window <= end + timedelta(days=1):
                stop = window + _LISTING_WINDOW
                for event in await self._events(series, window, stop):
                    game = game_from_event(event, league.name)
                    if game is not None:
                        games.setdefault(game.event, game)
                    elif not event.get("parentEventId") and _looks_like_a_game(event):
                        odd.add(event.get("slug") or str(event.get("id")))
                window = stop
        found = [g for g in games.values() if start <= g.day <= end]
        return sorted(found, key=lambda g: (g.day, g.event)), sorted(odd)

    async def history(self, game: VenueGame, side: Side) -> list[PricePoint]:
        """Hourly prices for one side's token, from its open to its close."""
        if game.opened is None:
            return []
        until = game.closed or datetime.now(UTC)
        points: dict[int, PricePoint] = {}
        start = game.opened
        while start < until:
            stop = min(start + _HISTORY_WINDOW, until)
            page = await self._http.get_json(
                CLOB + "/prices-history",
                {
                    "market": side.contract,
                    "startTs": int(start.timestamp()),
                    "endTs": int(stop.timestamp()),
                    "fidelity": _HISTORY_MINUTES,
                },
            )
            for point in price_points(page.get("history") or []):
                points[point.at] = point
            start = stop
        return [points[at] for at in sorted(points)]

    async def _series(self, league: League) -> list[int]:
        sports = await self._http.get_json(GAMMA + "/sports")
        current = [
            int(s["series"])
            for s in sports
            if s.get("sport") == league.polymarket_sport
            and str(s.get("series", "")).isdigit()
        ]
        return list(dict.fromkeys((*league.polymarket_series, *current)))

    async def _events(self, series: int, start: date, end: date) -> list[dict]:
        events: list[dict] = []
        while True:
            page = await self._http.get_json(
                GAMMA + "/events",
                {
                    "series_id": series,
                    "end_date_min": start.isoformat(),
                    "end_date_max": end.isoformat(),
                    "limit": _PAGE,
                    "offset": len(events),
                },
            )
            if not page:
                return events
            events += page


def _moneyline(event: dict) -> dict | None:
    markets = event.get("markets") or []
    typed = [m for m in markets if m.get("sportsMarketType") == "moneyline"]
    if typed:
        return typed[0]
    for market in markets:
        if (
            market.get("sportsMarketType") is None
            and market.get("slug") == event.get("slug")
            and _json_list(market.get("outcomes")) not in _NOT_TEAMS
        ):
            return market
    return None


def _looks_like_a_game(event: dict) -> bool:
    # A slug shaped like a game's, so a game Polymarket listed without a
    # moneyline is counted rather than silently dropped.
    return _slug_codes(event.get("slug") or "") is not None


def _slug_codes(slug: str) -> tuple[str, str] | None:
    # `nfl-atl-gb-2026-09-25`: league, two codes, a date. Anything else --
    # a futures market, a prop -- has no codes to read.
    parts = slug.split("-")
    if len(parts) != 6 or not all(p.isdigit() for p in parts[3:]):
        return None
    return parts[1], parts[2]


def _team_for(outcome: str, teams: list[dict]) -> dict | None:
    wanted = outcome.casefold()
    for team in teams:
        spellings = (team.get("name"), team.get("alias"), team.get("abbreviation"))
        if any(s and s.casefold() == wanted for s in spellings):
            return team
    return None


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _timestamp(value: Any) -> datetime | None:
    # Gamma writes three formats: "2026-09-25T00:15:00Z",
    # "2026-09-25 00:15:00+00" and "2025-08-24 03:43:49+00".
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if text.endswith("+00"):
        text += ":00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
