"""
A pull: list a venue's games, match them to ESPN's, fetch their prices, write.

One pull is one venue, one league and a range of days. Games are matched
before any history is fetched, so a game that can't be matched costs one
listing and no history requests; histories are fetched a day at a time and
each day's file written as it finishes, so a long backfill that dies part
way keeps what it finished.

A day's file holds only matched games. What didn't match is in the pull's
summary with its reason, and pulling the same days again after the fix --
usually a row in call-it-what-you-want -- replaces those days' games.

A pull writes into a day's file rather than over it. The day a venue files
a game under and the day ESPN does can differ: Kalshi's ticker for a 7 pm
tip in Honolulu carries the next day's date, while ESPN's Eastern day is
the one it tipped on. So a pull of one day's listing writes a game or two
into the day before's file, and a file replaced whole would keep only
those -- every hourly pull from 2026-11-03 cut Kalshi's men's 2026-11-02
from 129 games to the one Seattle at Hawai'i. Each write keeps the file's
games except the ones this pull listed again, which it replaces: a re-pull
is still safe, and a game another pull matched to the day stays.
"""

import asyncio
import json
import pickle
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from call_it_what_you_want import Teams, default_teams
from endgame.types import Game, Season
from endgame_aws import list_all_keys, read_seasons

from .http import Http
from .kalshi import KALSHI, Kalshi
from .leagues import league as league_named
from .match import Matched, Schedule, match
from .polymarket import POLYMARKET, Polymarket
from .polymarket_us import POLYMARKET_US, PolymarketUs, book_levels
from .store import PULLS, UPCOMING, Store, book_key, day_key, pull_key
from .summary import PullSummary
from .types import PricePoint, VenueGame

VENUES = {KALSHI: Kalshi, POLYMARKET: Polymarket, POLYMARKET_US: PolymarketUs}
type Client = Kalshi | Polymarket | PolymarketUs
# The columns of each price row in a day's file, in order.
FIELDS = ("at", "price", "bid", "ask", "volume")


async def pull(
    venue: str,
    league: str,
    start: date,
    end: date,
    *,
    store: Store,
    games: Iterable[Game],
    teams: Teams | None = None,
    summaries: str = PULLS,
) -> PullSummary:
    """
    Pull `venue`'s games in `league` from `start` to `end`, into `store`.

    `games` is endgame's games for the league around those days -- see
    `read_games` -- and `teams` the registry to resolve both sides' names
    in, the bundled one by default. The summary goes under `summaries`.
    """
    if venue not in VENUES:
        raise ValueError(f"No venue {venue!r}. Available: {', '.join(VENUES)}.")
    spec = league_named(league)
    teams = teams or default_teams(spec.namespace)
    started = datetime.now(UTC)
    summary = PullSummary(
        venue=venue,
        league=league,
        start=start.isoformat(),
        end=end.isoformat(),
        started_at=started.strftime("%Y%m%dT%H%M%SZ"),
    )
    schedule = Schedule(games, spec, teams)
    summary.unknown_teams = dict(schedule.unknown_teams)
    async with Http(per_second=VENUES[venue].per_second) as http:
        client = VENUES[venue](http)
        listed, summary.odd_events = await client.games(spec, start, end)
        summary.listed = len(listed)
        relisted = {game.event for game in listed}
        by_day: dict[date, list[tuple[VenueGame, Matched]]] = defaultdict(list)
        for game in listed:
            result = match(game, schedule, spec, teams)
            if isinstance(result, Matched):
                by_day[result.game.day].append((game, result))
            else:
                summary.unmatched.append(
                    {
                        "event": game.event,
                        "day": game.day.isoformat(),
                        "sides": [side.code for side in game.sides],
                        "reason": result.reason,
                    }
                )
        summary.matched = sum(len(pairs) for pairs in by_day.values())
        for day in sorted(by_day):
            records = await _price_day(client, by_day[day], summary)
            if not records:
                continue
            key = day_key(venue, league, day.isoformat())
            kept = _kept(await store.read_if_exists(key), relisted, records)
            summary.kept += len(kept)
            await store.write(key, _day_file(venue, league, day, kept + records))
            summary.written.append(key)
    summary.finished_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    await store.write(
        pull_key(venue, league, summary.started_at, summaries), summary.to_json()
    )
    return summary


async def upcoming(
    start: date,
    end: date,
    *,
    store: Store,
    games: Mapping[str, Iterable[Game]],
    venues: Iterable[str] | None = None,
) -> tuple[list[PullSummary], list[str]]:
    """
    Every venue's prices on every league in `games`, for `start` to `end`.

    The hourly job, for games that haven't been played: a day's file written
    during the day carries prices up to that hour, and the morning-after
    daily pull replaces it with the whole thing. `games` maps each league to
    endgame's games around those days (`read_games`).

    The venues run side by side -- different hosts -- and each one's leagues
    take turns, the way the daily schedules are spaced, so neither venue sees
    more than one pull at a time. A league that fails doesn't cost the others:
    it's returned as `"venue/league: error"` beside the summaries, for the
    caller to fail the job on once everything else is written. Summaries go
    under `UPCOMING`, not beside the daily ones.
    """
    failures: list[str] = []
    venues = tuple(VENUES) if venues is None else tuple(venues)

    async def one_venue(venue: str) -> list[PullSummary]:
        done = []
        for league, league_games in games.items():
            try:
                done.append(
                    await pull(
                        venue,
                        league,
                        start,
                        end,
                        store=store,
                        games=league_games,
                        summaries=UPCOMING,
                    )
                )
            except Exception as exc:  # reported, and fails the job at the end
                failures.append(f"{venue}/{league}: {exc!r}")
        return done

    per_venue = await asyncio.gather(*(one_venue(venue) for venue in venues))
    return [summary for done in per_venue for summary in done], failures


async def snapshot_books(
    leagues: Iterable[str], *, store: Store
) -> tuple[list[str], list[str]]:
    """
    Polymarket US's order book on every open game in each of `leagues`.

    Its price history has the best ask and bid but no size, and a book is
    only ever now, so this is the only record of how much could have been
    bought. One file per league per run, keyed by event and unmatched; each
    side is what buying it cost, `[price, contracts]` best first, out to
    ten cents past the best price. Returns the keys written and, as
    `"polymarket_us/league: error"`, the leagues that failed.
    """
    written: list[str] = []
    failures: list[str] = []
    async with Http(per_second=PolymarketUs.per_second) as http:
        client = PolymarketUs(http)
        for name in leagues:
            try:
                read_at = datetime.now(UTC)
                games = await client.open_games(league_named(name))
                books = await asyncio.gather(
                    *(client.book(game) for game in games), return_exceptions=True
                )
                records = []
                for game, book in zip(games, books):
                    if isinstance(book, BaseException):
                        failures.append(
                            f"{POLYMARKET_US}/{name} {game.event}: {book!r}"
                        )
                        continue
                    records.append(_book_record(game, book))
                key = book_key(POLYMARKET_US, name, read_at.strftime("%Y%m%dT%H%M%SZ"))
                await store.write(
                    key,
                    json.dumps(
                        {
                            "venue": POLYMARKET_US,
                            "league": name,
                            "read_at": read_at.isoformat(),
                            "fields": ["price", "contracts"],
                            "games": records,
                        }
                    ).encode(),
                )
                written.append(key)
            except Exception as exc:  # reported, and fails the job at the end
                failures.append(f"{POLYMARKET_US}/{name}: {exc!r}")
    return written, failures


def _book_record(game: VenueGame, book: dict) -> dict:
    long_levels, short_levels = book_levels(book)
    data = book.get("marketData") or {}
    stats = data.get("stats") or {}

    def side(index: int, levels: list[list[float]]) -> dict:
        venue_side = game.sides[index]
        return {
            "code": venue_side.code,
            "names": list(venue_side.names),
            "asks": levels,
        }

    return {
        "event": game.event,
        "market": game.extra["market"],
        "kickoff": game.kickoff.isoformat() if game.kickoff else None,
        "state": data.get("state"),
        "book_at": data.get("transactTime"),
        "contracts_traded": _number(stats.get("sharesTraded")),
        "long": side(0, long_levels),
        "short": side(1, short_levels),
    }


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(str(value))
    except ValueError:
        return None


async def read_games(league: str, start: date, end: date, source: str) -> list[Game]:
    """
    endgame's games for `league` within two days of `start`..`end`.

    `source` is `s3://bucket` for the stored seasons, or a directory laid
    out like the bucket (`seasons/{year}/{league}.pkl`) for trying a pull
    against a copy.

    Only the seasons that can hold those days are read. endgame names a
    season for the year it starts in, so a January basketball game is in
    the previous year's season: a day's games are in its year's season or
    the one before, and a daily pull reads two files rather than every
    season since 2001.
    """
    years = set(range(start.year - 1, end.year + 1))
    if source.startswith("s3://"):
        bucket = source.removeprefix("s3://").strip("/")
        seasons = [
            season
            async for key in list_all_keys(bucket, "seasons/")
            if _season_year(key, league) in years
            for season in await read_seasons(bucket, key)
        ]
    else:
        seasons = [
            season
            for path in sorted(Path(source).glob(f"seasons/*/{league}.pkl"))
            if _season_year(str(path.relative_to(source)), league) in years
            for season in _unpickle(path)
        ]
    low, high = start - timedelta(days=2), end + timedelta(days=2)
    return [
        game
        for season in seasons
        for week in season.weeks
        for game in week.games
        if low <= game.date.date() <= high
    ]


async def _price_day(
    client: Client,
    pairs: list[tuple[VenueGame, Matched]],
    summary: PullSummary,
) -> list[dict]:
    fetched = await asyncio.gather(
        *(_both_sides(client, game) for game, _ in pairs), return_exceptions=True
    )
    records = []
    for (game, matched), histories in zip(pairs, fetched):
        if isinstance(histories, BaseException):
            summary.errors.append({"event": game.event, "error": repr(histories)})
            continue
        if not all(histories):
            summary.empty.append(game.event)
        else:
            summary.priced += 1
        summary.points += sum(len(h) for h in histories)
        records.append(_record(game, matched, histories))
    return records


async def _both_sides(
    client: Client, game: VenueGame
) -> tuple[list[PricePoint], list[PricePoint]]:
    first, second = await asyncio.gather(
        client.history(game, game.sides[0]), client.history(game, game.sides[1])
    )
    return first, second


def _record(
    game: VenueGame,
    matched: Matched,
    histories: tuple[list[PricePoint], list[PricePoint]],
) -> dict:
    home = matched.home
    away = 1 - home

    def side(index: int) -> dict:
        venue_side = game.sides[index]
        return {
            "code": venue_side.code,
            "names": list(venue_side.names),
            "contract": venue_side.contract,
            "prices": [list(point) for point in histories[index]],
        }

    return {
        "game_id": matched.game.game_id,
        "kickoff": matched.game.kickoff.isoformat(),
        "neutral_site": matched.game.neutral_site,
        "event": game.event,
        "home": side(home),
        "away": side(away),
    }


def _kept(
    existing: bytes | None, relisted: set[str], records: list[dict]
) -> list[dict]:
    """The games in a day's file that a write of `records` leaves in place.

    Everything but what this pull has a newer answer for: any game whose
    venue event it listed -- matched again, or now unmatched, as a full
    re-pull would drop it -- or whose ESPN game it matched. A file written
    with other columns is from a layout these rows can't sit beside, and is
    replaced.
    """
    if existing is None:
        return []
    day = json.loads(existing)
    if day.get("fields") != list(FIELDS):
        return []
    replaced = {record["game_id"] for record in records}
    return [
        game
        for game in day["games"]
        if game["event"] not in relisted and game["game_id"] not in replaced
    ]


def _day_file(venue: str, league: str, day: date, records: list[dict]) -> bytes:
    return json.dumps(
        {
            "venue": venue,
            "league": league,
            "day": day.isoformat(),
            "read_at": datetime.now(UTC).isoformat(),
            "fields": list(FIELDS),
            "games": records,
        }
    ).encode()


# How endgame keys a stored season, the pattern cassandra reads them by.
_SEASON_KEY = re.compile(r"^seasons/(\d+)/([^/]+)\.pkl$")


def _season_year(key: str, league: str) -> int | None:
    """The season year a stored key is for, if it's one of `league`'s."""
    found = _SEASON_KEY.match(key)
    if found is None or found.group(2) != league:
        return None
    return int(found.group(1))


def _unpickle(path: Path) -> list[Season]:
    with path.open("rb") as file:
        loaded = pickle.load(file)
    return loaded if isinstance(loaded, list) else [loaded]
