from datetime import UTC, date, datetime

import pytest
from call_it_what_you_want import (
    ENDGAME,
    ESPN,
    KALSHI,
    NCAAFB,
    NFL,
    Team,
    TeamName,
    Teams,
)
from endgame.types import Game

from .leagues import league
from .match import Matched, Schedule, Unmatched, match, side_ids
from .types import Side, VenueGame

NCAAFB_LEAGUE = league("ncaafb")
NFL_LEAGUE = league("nfl")


def _team(espn_id: str, espn_name: str, *venue_names: str) -> Team:
    names = [TeamName(espn_name, 2025, ESPN, NCAAFB)]
    names += [TeamName(n, 2026, KALSHI, NCAAFB) for n in venue_names]
    return Team(espn_id, tuple(names))


# Kalshi writes Washington State and Wayne State both as WSU.
WASHINGTON_STATE = _team("265", "Washington State Cougars", "WSU", "Washington St.")
WAYNE_STATE = _team("131", "Wayne State Warriors", "WSU", "Wayne St.")
OREGON_STATE = _team("204", "Oregon State Beavers", "ORST", "Oregon St.")
FERRIS_STATE = _team("2222", "Ferris State Bulldogs", "FSU", "Ferris St.")
# On ESPN's books but not on the venue's roster.
UNCW = _team("350", "UNC Wilmington Seahawks")

COLLEGE = Teams([WASHINGTON_STATE, WAYNE_STATE, OREGON_STATE, FERRIS_STATE, UNCW])


def _game(home: str, away: str, when: datetime, game_id: str) -> Game:
    return Game(home, 0, away, 0, False, True, when, game_id)


def _venue_game(day: date, *sides: tuple[str, str]) -> VenueGame:
    first, second = (Side(code, (name,), f"{code}-contract") for code, name in sides)
    return VenueGame(
        KALSHI, "ncaafb", "EVENT", day, None, (first, second), None, None, {}
    )


# Saturday night in Corvallis is Sunday morning in UTC.
SATURDAY_NIGHT = datetime(2026, 9, 27, 2, 30, tzinfo=UTC)


@pytest.fixture
def schedule() -> Schedule:
    return Schedule(
        [
            _game(
                "Oregon State Beavers", "Washington State Cougars", SATURDAY_NIGHT, "1"
            ),
            _game(
                "Ferris State Bulldogs",
                "Wayne State Warriors",
                datetime(2026, 9, 26, 17, tzinfo=UTC),
                "2",
            ),
        ],
        NCAAFB_LEAGUE,
        COLLEGE,
    )


def test_a_code_two_schools_share_is_settled_by_the_opponent(
    schedule: Schedule,
) -> None:
    game = _venue_game(
        date(2026, 9, 26), ("WSU", "Washington St."), ("ORST", "Oregon St.")
    )

    result = match(game, schedule, NCAAFB_LEAGUE, COLLEGE)

    assert isinstance(result, Matched)
    assert result.game.game_id == "1"
    # Home is ESPN's to say: Oregon State, the venue's second side.
    assert result.home == 1


def test_the_same_code_finds_the_other_school_against_its_opponent(
    schedule: Schedule,
) -> None:
    game = _venue_game(date(2026, 9, 26), ("FSU", "Ferris St."), ("WSU", "Wayne St."))

    result = match(game, schedule, NCAAFB_LEAGUE, COLLEGE)

    assert isinstance(result, Matched)
    assert result.game.game_id == "2"
    assert result.home == 0


def test_a_game_is_filed_under_its_eastern_day(schedule: Schedule) -> None:
    [game] = schedule.on(date(2026, 9, 26))[:1]

    assert game.day == date(2026, 9, 26)
    assert game.kickoff == SATURDAY_NIGHT


def test_a_day_off_by_one_still_matches(schedule: Schedule) -> None:
    game = _venue_game(
        date(2026, 9, 27), ("WSU", "Washington St."), ("ORST", "Oregon St.")
    )

    result = match(game, schedule, NCAAFB_LEAGUE, COLLEGE)

    assert isinstance(result, Matched)
    assert result.game.game_id == "1"


def test_no_game_that_day_says_so(schedule: Schedule) -> None:
    game = _venue_game(
        date(2026, 10, 3), ("WSU", "Washington St."), ("ORST", "Oregon St.")
    )

    result = match(game, schedule, NCAAFB_LEAGUE, COLLEGE)

    assert result == Unmatched(
        "no ESPN game within a day of 2026-10-03 for WSU vs ORST"
    )


def test_a_code_nobody_filed_names_the_side(schedule: Schedule) -> None:
    game = _venue_game(
        date(2026, 9, 26), ("ZZZ", "Nowhere St."), ("ORST", "Oregon St.")
    )

    result = match(game, schedule, NCAAFB_LEAGUE, COLLEGE)

    assert result == Unmatched("no team for ZZZ (Nowhere St.)")


def test_a_team_off_the_venues_roster_falls_back_to_espns_name() -> None:
    side = Side("uncw", ("UNC Wilmington Seahawks",), "token")

    assert side_ids(side, KALSHI, NCAAFB_LEAGUE, COLLEGE) == {"350"}


def test_a_shared_code_is_every_team_it_could_be() -> None:
    side = Side("WSU", (), "c")

    assert side_ids(side, KALSHI, NCAAFB_LEAGUE, COLLEGE) == {"265", "131"}


def test_an_nfl_game_resolves_endgames_nicknames() -> None:
    teams = Teams(
        [
            Team(
                "9",
                (
                    TeamName("Green Bay Packers", 2025, ESPN, NFL),
                    TeamName("packers", 2026, ENDGAME, NFL),
                    TeamName("GB", 2026, KALSHI, NFL),
                ),
            ),
            Team(
                "1",
                (
                    TeamName("Atlanta Falcons", 2025, ESPN, NFL),
                    TeamName("falcons", 2026, ENDGAME, NFL),
                    TeamName("ATL", 2026, KALSHI, NFL),
                ),
            ),
        ]
    )
    schedule = Schedule(
        [_game("packers", "falcons", datetime(2026, 9, 25, 0, 15, tzinfo=UTC), "401")],
        NFL_LEAGUE,
        teams,
    )
    game = VenueGame(
        KALSHI,
        "nfl",
        "KXNFLGAME-26SEP24ATLGB",
        date(2026, 9, 24),
        None,
        (Side("ATL", ("Atlanta",), "a"), Side("GB", ("Green Bay",), "g")),
        None,
        None,
        {},
    )

    result = match(game, schedule, NFL_LEAGUE, teams)

    assert isinstance(result, Matched)
    assert (result.game.game_id, result.home) == ("401", 1)


def test_endgame_teams_nobody_knows_are_counted() -> None:
    schedule = Schedule(
        [_game("Mystery Tech Owls", "Oregon State Beavers", SATURDAY_NIGHT, "9")],
        NCAAFB_LEAGUE,
        COLLEGE,
    )

    assert dict(schedule.unknown_teams) == {"Mystery Tech Owls": 1}
