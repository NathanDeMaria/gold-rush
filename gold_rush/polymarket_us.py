"""
Polymarket US's game markets: what they cost to buy, hour by hour, and
how much was on offer.

Polymarket US is the CFTC-regulated exchange a US account trades on. It
is not the international Polymarket in `polymarket`: its own order books,
its own API (`gateway.polymarket.us`), and its own prices, which are the
ones a bet from here would actually get. Its public market data needs no
account.

A game is an event -- slug `cfb-dart-prnce-2026-11-07` -- holding dozens
of markets: halves, quarters, spreads, totals and props, and one
moneyline, `marketType: "moneyline"` (its `sportsMarketType` is
`moneyline` on older games and `football_team_full_game_winner` or
`basketball_team_full_game_winner` on newer ones). The moneyline is one
yes/no instrument with a team on each side: buying yes backs the `long`
team, and buying no -- which is selling yes at the bid -- backs the
other. So both sides trade in one book, and buying the short team costs
one minus the best bid.

**Price history** (`/v1/price-history`) is derived from that book, not
from trades: `longPrice` is the best ask and `shortPrice` is one minus
the best bid, i.e. what buying each side cost. Asked for hourly it
returns a snapshot on every hour, and one request covers a market's
whole life, so a finished game can be backfilled at any time. Each side's
row is that side's ask, and its bid is one minus the other side's ask.
There's no trade price or volume in it.

**Size** isn't in the history. The book (`/v1/markets/{slug}/book`) is
only ever now, so `book_snapshot` reads it on every open game each hour,
and those snapshots are the only record of how much could be bought.

Events are filed under a series per league, but a series' name doesn't
say its season -- `cbb-2025` holds November 2026's games -- so a listing
asks every series seen for the league plus the one the league endpoint
calls active.

Team codes are the international venue's (`dart`, `prnce`), so they're
looked up in call-it-what-you-want under `polymarket` (`SPELLED_AS`).
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from .http import Http
from .leagues import League
from .polymarket import POLYMARKET
from .types import EASTERN, PricePoint, Side, VenueGame

POLYMARKET_US = "polymarket_us"
# The venue whose spellings this one's codes and names are filed under.
SPELLED_AS = POLYMARKET
GATEWAY = "https://gateway.polymarket.us"

_PAGE = 100
_LISTING_WINDOW = timedelta(days=7)
# How far ahead `open_games` looks. Football markets open two to three
# weeks before kickoff.
_OPEN_AHEAD = timedelta(days=30)
# One request has returned 60 days of hourly history; a market is open for
# two or three weeks. Asked in 30-day pieces in case one runs long.
_HISTORY_WINDOW = timedelta(days=30)
_HISTORY_MINUTES = 60
# Book levels kept per side in a snapshot: everything within this much of
# the best price, up to `_BOOK_LEVELS` of them.
_BOOK_REACH = 0.10
_BOOK_LEVELS = 20
_WOMENS = " (W)"


def game_from_event(event: dict, league: str) -> VenueGame | None:
    """
    One event as a VenueGame, or None if it has no two-team moneyline.

    The long team is the first side. `extra["market"]` is the moneyline's
    slug, what its history and book are asked for by; both sides carry it
    as their contract, since they're the two sides of one instrument.
    """
    moneyline = moneyline_market(event)
    if moneyline is None:
        return None
    sides = moneyline_sides(moneyline)
    if sides is None:
        return None
    kickoff = _timestamp(
        event.get("startTime") or moneyline.get("gameStartTime")
    ) or _timestamp(event.get("startDate"))
    if kickoff is None:
        return None
    slug = moneyline["slug"]
    return VenueGame(
        venue=POLYMARKET_US,
        league=league,
        event=event.get("slug") or str(event.get("id")),
        day=kickoff.astimezone(EASTERN).date(),
        kickoff=kickoff,
        sides=(
            _side(sides[0], slug),
            _side(sides[1], slug),
        ),
        opened=_timestamp(moneyline.get("startDate")),
        # An open market's end date is a placeholder weeks out.
        closed=_timestamp(moneyline.get("endDate"))
        if moneyline.get("closed")
        else None,
        extra={"market": slug, "event_id": str(event.get("id"))},
    )


def moneyline_market(event: dict) -> dict | None:
    for market in event.get("markets") or []:
        if (
            market.get("marketType") == "moneyline"
            or market.get("sportsMarketType") == "moneyline"
        ):
            return market
    return None


def moneyline_sides(market: dict) -> tuple[dict, dict] | None:
    """The market's long side and its short side, or None if they aren't
    one team each."""
    sides = market.get("marketSides") or []
    longs = [s for s in sides if s.get("long") is True and s.get("team")]
    shorts = [s for s in sides if s.get("long") is False and s.get("team")]
    if len(sides) != 2 or len(longs) != 1 or len(shorts) != 1:
        return None
    return longs[0], shorts[0]


def price_points(history: Iterable[dict], long: bool) -> list[PricePoint]:
    """
    `/v1/price-history` points as one side's PricePoints.

    A side's ask is its own display price, its bid one minus the other's.
    A point missing either price is skipped rather than half-filled.
    """
    points = []
    for point in history:
        long_ask, short_ask = point.get("longPrice"), point.get("shortPrice")
        if long_ask is None or short_ask is None:
            continue
        ask, other = (long_ask, short_ask) if long else (short_ask, long_ask)
        points.append(
            PricePoint(
                at=int(point["timestamp"]),
                price=None,
                bid=round(1 - float(other), 6),
                ask=float(ask),
                volume=None,
            )
        )
    return points


def book_levels(book: dict) -> tuple[list[list[float]], list[list[float]]]:
    """
    What buying each side of a moneyline costs now, best first: the long
    side's `[price, contracts]` levels and the short side's.

    The long side buys from the offers. The short side buys by selling
    into the bids, so its price at each level is one minus the bid.
    """
    data = book.get("marketData") or {}
    long_levels = sorted(
        (_money(level["px"]), float(level["qty"])) for level in data.get("offers") or []
    )
    short_levels = sorted(
        (round(1 - _money(level["px"]), 6), float(level["qty"]))
        for level in data.get("bids") or []
    )
    return _near_best(long_levels), _near_best(short_levels)


class PolymarketUs:
    """Polymarket US's games, price histories and books, through an `Http`."""

    name = POLYMARKET_US
    # Requests a second. The public limit is 20 per second per IP.
    per_second = 12.0

    def __init__(self, http: Http) -> None:
        self._http = http
        # A game's two sides share one history; fetch it once.
        self._histories: dict[str, list[dict]] = {}

    async def games(
        self, league: League, start: date, end: date
    ) -> tuple[list[VenueGame], list[str]]:
        """
        Every game in `league` filed on a day from `start` to `end`, and the
        events slugged like a game that had no moneyline to read.

        Listed by kickoff a week at a time, padded a day either side, since
        an Eastern day runs into the next UTC one.
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
                    elif _looks_like_a_game(event):
                        odd.add(event.get("slug") or str(event.get("id")))
                window = stop
        found = [g for g in games.values() if start <= g.day <= end]
        return sorted(found, key=lambda g: (g.day, g.event)), sorted(odd)

    async def open_games(self, league: League) -> list[VenueGame]:
        """Every game in `league` whose moneyline is open now."""
        now = datetime.now(UTC)
        games: dict[str, VenueGame] = {}
        for series in await self._series(league):
            listed = await self._paged(
                {
                    "seriesId": series,
                    "startTimeMin": _iso(now - timedelta(hours=12)),
                    "startTimeMax": _iso(now + _OPEN_AHEAD),
                }
            )
            for event in listed:
                game = game_from_event(event, league.name)
                if game is not None and game.closed is None:
                    games.setdefault(game.event, game)
        return sorted(games.values(), key=lambda g: (g.day, g.event))

    async def history(self, game: VenueGame, side: Side) -> list[PricePoint]:
        """Hourly asks and bids for one side, from its open to its close."""
        if game.opened is None:
            return []
        market = game.extra["market"]
        if market not in self._histories:
            self._histories[market] = await self._market_history(market, game)
        return price_points(self._histories[market], long=side == game.sides[0])

    async def book(self, game: VenueGame) -> dict:
        return await self._http.get_json(
            f"{GATEWAY}/v1/markets/{game.extra['market']}/book"
        )

    async def _market_history(self, market: str, game: VenueGame) -> list[dict]:
        assert game.opened is not None
        until = game.closed or datetime.now(UTC)
        points: dict[int, dict] = {}
        start = game.opened - timedelta(hours=1)
        while start < until:
            stop = min(start + _HISTORY_WINDOW, until + timedelta(hours=1))
            page = await self._http.get_json(
                GATEWAY + "/v1/price-history",
                {
                    "symbol": market,
                    "timestamp.startTimestamp": int(start.timestamp()),
                    "timestamp.endTimestamp": int(stop.timestamp()),
                    "fidelity": _HISTORY_MINUTES,
                },
            )
            for point in page.get("history") or []:
                points[int(point["timestamp"])] = point
            start = stop
        return [points[at] for at in sorted(points)]

    async def _series(self, league: League) -> list[int]:
        found = await self._http.get_json(
            f"{GATEWAY}/v2/leagues/{league.polymarket_us_league}"
        )
        active = (found.get("league") or found).get("activeSeriesId")
        current = [int(active)] if str(active or "").isdigit() else []
        return list(dict.fromkeys((*league.polymarket_us_series, *current)))

    async def _events(self, series: int, start: date, end: date) -> list[dict]:
        return await self._paged(
            {
                "seriesId": series,
                "startTimeMin": _iso(datetime.combine(start, time(), tzinfo=UTC)),
                "startTimeMax": _iso(datetime.combine(end, time(), tzinfo=UTC)),
            }
        )

    async def _paged(self, params: dict[str, Any]) -> list[dict]:
        events: list[dict] = []
        while True:
            page = await self._http.get_json(
                GATEWAY + "/v1/events",
                {**params, "limit": _PAGE, "offset": len(events)},
            )
            found = page.get("events") or []
            events += found
            if len(found) < _PAGE:
                return events


def _looks_like_a_game(event: dict) -> bool:
    # `cfb-dart-prnce-2026-11-07`: league, two codes, a date. A series also
    # holds futures (`nfl-afcwest-2027-01-10-w`), which aren't games.
    parts = (event.get("slug") or "").split("-")
    return len(parts) == 6 and all(p.isdigit() for p in parts[3:])


def _side(side: dict, market: str) -> Side:
    team = side["team"]
    # Women's teams off the international venue's roster are named
    # "West Florida (W)". ESPN's name, the fallback for a team nobody filed,
    # is school and mascot with no suffix: "West Florida Argonauts".
    name = (team.get("name") or "").removesuffix(_WOMENS)
    alias = team.get("alias") or ""
    school_and_mascot = f"{name} {alias}" if name and alias else ""
    names = tuple(
        dict.fromkeys(
            n
            for n in (
                name,
                (team.get("safeName") or "").removesuffix(_WOMENS),
                alias,
                school_and_mascot,
            )
            if n
        )
    )
    return Side(code=team.get("abbreviation") or "", names=names, contract=market)


def _near_best(levels: list[tuple[float, float]]) -> list[list[float]]:
    if not levels:
        return []
    best = levels[0][0]
    return [
        [price, qty] for price, qty in levels if price <= best + _BOOK_REACH + 1e-9
    ][:_BOOK_LEVELS]


def _money(value: Any) -> float:
    return float(value["value"] if isinstance(value, dict) else value)


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
