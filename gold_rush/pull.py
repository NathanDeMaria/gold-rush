"""
A pull: list a venue's games, match them to ESPN's, fetch their prices, write.

One pull is one venue, one league and a range of days. Games are matched
before any history is fetched, so a game that can't be matched costs one
listing and no history requests; histories are fetched a day at a time and
each day's file written as it finishes, so a long backfill that dies part
way keeps what it finished.

A day's file holds only matched games. What didn't match is in the pull's
summary with its reason, and pulling the same days again after the fix --
usually a row in call-it-what-you-want -- replaces the day's file whole.
"""

import asyncio
import json
import pickle
import re
from collections import defaultdict
from collections.abc import Iterable
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
from .store import Store, day_key, pull_key
from .summary import PullSummary
from .types import PricePoint, VenueGame

VENUES = {KALSHI: Kalshi, POLYMARKET: Polymarket}
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
) -> PullSummary:
    """
    Pull `venue`'s games in `league` from `start` to `end`, into `store`.

    `games` is endgame's games for the league around those days -- see
    `read_games` -- and `teams` the registry to resolve both sides' names
    in, the bundled one by default.
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
            await store.write(key, _day_file(venue, league, day, records))
            summary.written.append(key)
    summary.finished_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    await store.write(pull_key(venue, league, summary.started_at), summary.to_json())
    return summary


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
    client: Kalshi | Polymarket,
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
    client: Kalshi | Polymarket, game: VenueGame
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
