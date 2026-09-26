"""
gold-rush's command line.

    gold-rush games kalshi ncaafb 2026-09-19             # what a venue lists
    gold-rush pull kalshi nfl                            # yesterday, to the bucket
    gold-rush pull polymarket mens 2025-11-03 2026-04-07 # a season's backfill
    gold-rush report                                     # what recent pulls did

`pull` and `report` default to the bucket endgame's seasons live in, read
from endgame_aws's config the way every other job reads it; `--out` points
either at a directory instead. `pull` also reads the seasons it matches
against from there unless `--games-from` says otherwise.
"""

import asyncio
import sys
from datetime import date, datetime, timedelta

from .http import Http
from .leagues import league as league_named
from .match import EASTERN
from .pull import VENUES, pull, read_games
from .store import PULLS, open_store
from .summary import PullSummary, render


class Cli:
    def games(
        self, venue: str, league: str, start: str, end: str | None = None
    ) -> None:
        """
        List a venue's games in a league, as parsed, without matching them.

            gold-rush games polymarket cfb 2026-09-19
        """
        first, last = _days(start, end)
        listed, odd = asyncio.run(_list(venue, league, first, last))
        for game in listed:
            sides = "  vs  ".join(
                f"{side.code} ({' / '.join(side.names)})" for side in game.sides
            )
            kickoff = game.kickoff.isoformat() if game.kickoff else "-"
            print(f"{game.day}  {kickoff:25s}  {game.event}  {sides}")
        print(
            f"{len(listed)} games, {len(odd)} events that didn't parse", file=sys.stderr
        )

    def pull(
        self,
        venue: str,
        league: str,
        start: str | None = None,
        end: str | None = None,
        out: str | None = None,
        games_from: str | None = None,
    ) -> None:
        """
        Pull a venue's prices for a league's games from `start` to `end`.

        Both default to yesterday, US Eastern -- the games that have
        settled since the last daily run. Writes each day's games and a
        summary of the pull to `--out`, and prints the summary.
        """
        first, last = _days(start, end)
        where = out or _bucket()
        store = open_store(where)
        summary = asyncio.run(
            _pull(venue, league, first, last, store, games_from or _bucket())
        )
        print(render(summary))
        print(f"wrote to {store.describe()}", file=sys.stderr)

    def report(
        self,
        out: str | None = None,
        venue: str | None = None,
        league: str | None = None,
        last: int = 5,
        misses: int = 10,
    ) -> None:
        """
        Print what the most recent pulls did, newest first.

            gold-rush report --venue kalshi --last 3
        """
        store = open_store(out or _bucket())
        prefix = "/".join(p for p in (PULLS, venue, league) if p)
        summaries = asyncio.run(_summaries(store, prefix, last))
        if not summaries:
            print(f"No pulls under {store.describe()}/{prefix}.")
        for summary in summaries:
            print(render(summary, misses=misses))


async def _list(venue: str, league: str, start: date, end: date):
    if venue not in VENUES:
        raise SystemExit(f"No venue {venue!r}. Available: {', '.join(VENUES)}.")
    client = VENUES[venue]
    async with Http(per_second=client.per_second) as http:
        return await client(http).games(league_named(league), start, end)


async def _pull(venue, league, start, end, store, games_from):
    games = await read_games(league, start, end, games_from)
    return await pull(venue, league, start, end, store=store, games=games)


async def _summaries(store, prefix: str, last: int) -> list[PullSummary]:
    keys = await store.keys(prefix)
    # Keys end in the pull's start time, so newest last whatever the venue.
    newest = sorted(keys, key=lambda k: k.rsplit("/", 1)[-1])[-last:][::-1]
    return [PullSummary.from_json(await store.read(key)) for key in newest]


def _days(start: str | None, end: str | None) -> tuple[date, date]:
    if start is None:
        yesterday = datetime.now(EASTERN).date() - timedelta(days=1)
        return yesterday, yesterday
    first = date.fromisoformat(str(start))
    last = date.fromisoformat(str(end)) if end is not None else first
    if last < first:
        raise SystemExit(f"end {last} is before start {first}")
    return first, last


def _bucket() -> str:
    from endgame_aws import Config

    return f"s3://{Config.init_from_file().bucket}"


def main() -> None:
    import fire

    fire.Fire(Cli)
