"""
Which ESPN game a venue's game is.

A venue game is two sides, each a code and a few names, filed under a day.
The ESPN side is endgame's stored games: an id, a kickoff, and a home and an
away team under the name endgame stored them by. Neither side has an id the
other knows, so both go through call-it-what-you-want to ESPN team ids --
the venue's spellings scoped to that venue, endgame's to where endgame gets
its names -- and a venue game is the ESPN game whose two teams are its two
sides, on its day.

Every side resolves to a set of candidate teams rather than one, and that's
deliberate. Kalshi writes Washington State and Wayne State both as `WSU`;
the lookup can't say which, but only one of them is playing Oregon State on
a given Saturday, so requiring both sides to land on one game settles what
neither side can alone. A game that finds nothing, or more than one, isn't
matched, and says why.

Home and away come from the ESPN game, never the venue, so a neutral-site
game is the same case as any other.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from call_it_what_you_want import ESPN, Teams
from endgame.types import Game

from .leagues import League
from .types import Side, VenueGame

# Both venues file a game under its US Eastern date, and so does this.
EASTERN = ZoneInfo("America/New_York")


class ScheduledGame(NamedTuple):
    """An endgame game with its teams resolved to ESPN ids."""

    game_id: str
    kickoff: datetime
    day: date
    home: str
    away: str
    home_ids: frozenset[str]
    away_ids: frozenset[str]
    neutral_site: bool


class Matched(NamedTuple):
    """
    The ESPN game a venue game is, and which of its sides is home.

    `home` indexes `VenueGame.sides`.
    """

    game: ScheduledGame
    home: int


class Unmatched(NamedTuple):
    reason: str


class Schedule:
    """
    endgame's games for a league, indexed by Eastern date, with each team
    resolved to its ESPN ids.

    A team endgame stores under a name call-it-what-you-want has never seen
    resolves to nothing, and its games can't be matched; `unknown_teams`
    counts them, since that's a fix in call-it-what-you-want, not here.
    """

    def __init__(self, games: Iterable[Game], league: League, teams: Teams) -> None:
        self.unknown_teams: dict[str, int] = defaultdict(int)
        self._by_day: dict[date, list[ScheduledGame]] = defaultdict(list)
        for game in games:
            kickoff = _utc(game.date)
            home_ids = self._ids(game.home, league, teams)
            away_ids = self._ids(game.away, league, teams)
            day = kickoff.astimezone(EASTERN).date()
            self._by_day[day].append(
                ScheduledGame(
                    game_id=str(game.game_id),
                    kickoff=kickoff,
                    day=day,
                    home=game.home,
                    away=game.away,
                    home_ids=home_ids,
                    away_ids=away_ids,
                    neutral_site=bool(game.neutral_site),
                )
            )

    def on(self, day: date) -> list[ScheduledGame]:
        return self._by_day.get(day, [])

    def _ids(self, name: str, league: League, teams: Teams) -> frozenset[str]:
        found = teams.find(name, source=league.stored_as, league=league.names_league)
        if not found and league.stored_as == ESPN:
            # A school ESPN named differently in another sport is still the
            # same school, the way call-it-what-you-want's `venues` places it.
            found = teams.find(name, source=ESPN)
        if not found:
            self.unknown_teams[name] += 1
        return frozenset(t.espn_id for t in found)


def side_ids(side: Side, venue: str, league: League, teams: Teams) -> frozenset[str]:
    """
    Every ESPN team a venue's side could be.

    The venue's own spellings first: its code and names, scoped to it and
    to the league. Only when none of those is on file, ESPN's names -- for
    a team a venue's roster doesn't list but its games do, the way
    Polymarket's leaves out UNC Wilmington and still prices its games.
    """
    scope = {"league": league.names_league}
    found = {
        team.espn_id
        for spelling in (side.code, *side.names)
        if spelling
        for team in teams.find(spelling, source=venue, **scope)
    }
    if not found:
        found = {
            team.espn_id
            for name in side.names
            for team in teams.find(name, source=ESPN, **scope)
        }
    return frozenset(found)


def match(
    game: VenueGame, schedule: Schedule, league: League, teams: Teams
) -> Matched | Unmatched:
    """
    The ESPN game `game` is, or why there isn't exactly one.

    Looks on the venue's day first, then the day either side of it, and
    stops at the first day with any hit: a date the venue got wrong by one
    is the likely miss, not a rematch two days later.
    """
    first, second = (side_ids(side, game.venue, league, teams) for side in game.sides)
    for side, ids in zip(game.sides, (first, second)):
        if not ids:
            names = " / ".join(side.names) or "no name"
            return Unmatched(f"no team for {side.code or '?'} ({names})")
    for day in (game.day, game.day - timedelta(days=1), game.day + timedelta(days=1)):
        hits = []
        for scheduled in schedule.on(day):
            first_home = first & scheduled.home_ids and second & scheduled.away_ids
            second_home = second & scheduled.home_ids and first & scheduled.away_ids
            if first_home and second_home:
                return Unmatched(
                    f"both sides could be either team in {scheduled.game_id}"
                )
            if first_home or second_home:
                hits.append(Matched(scheduled, 0 if first_home else 1))
        if len(hits) == 1:
            return hits[0]
        if hits:
            ids = ", ".join(hit.game.game_id for hit in hits)
            return Unmatched(f"{len(hits)} games on {day} fit: {ids}")
    codes = " vs ".join(side.code for side in game.sides)
    return Unmatched(f"no ESPN game within a day of {game.day} for {codes}")


def _utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
