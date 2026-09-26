import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from call_it_what_you_want import ESPN, KALSHI, NCAAFB, Team, TeamName, Teams
from endgame.types import Game

from . import pull as pull_module
from .pull import FIELDS, pull
from .store import PULLS, LocalStore
from .summary import PullSummary, render
from .types import PricePoint, Side, VenueGame

TEAMS = Teams(
    [
        Team(
            "204",
            (
                TeamName("Oregon State Beavers", 2025, ESPN, NCAAFB),
                TeamName("ORST", 2026, KALSHI, NCAAFB),
            ),
        ),
        Team(
            "265",
            (
                TeamName("Washington State Cougars", 2025, ESPN, NCAAFB),
                TeamName("WSU", 2026, KALSHI, NCAAFB),
            ),
        ),
    ]
)
KICKOFF = datetime(2026, 9, 27, 2, 30, tzinfo=UTC)
GAMES = [
    Game(
        "Oregon State Beavers",
        0,
        "Washington State Cougars",
        0,
        False,
        True,
        KICKOFF,
        "401",
    )
]


def _venue_game(event: str, first: str, second: str) -> VenueGame:
    return VenueGame(
        KALSHI,
        "ncaafb",
        event,
        date(2026, 9, 26),
        None,
        (Side(first, (), f"{event}-{first}"), Side(second, (), f"{event}-{second}")),
        KICKOFF,
        KICKOFF,
        {},
    )


class FakeVenue:
    """A venue that lists fixed games and prices every contract the same."""

    name = KALSHI
    per_second = 1000.0
    listed = [
        _venue_game("KXNCAAFGAME-26SEP26WSUORST", "WSU", "ORST"),
        _venue_game("KXNCAAFGAME-26SEP26ZZZORST", "ZZZ", "ORST"),
    ]

    def __init__(self, http) -> None:
        pass

    async def games(self, league, start, end):
        return self.listed, ["KXNCAAFGAME-26SEP26ODD"]

    async def history(self, game, side):
        return [PricePoint(1789000000, 0.6, 0.59, 0.61, 10.0)]


@pytest.fixture
def fake_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(pull_module.VENUES, KALSHI, FakeVenue)


async def test_a_pull_writes_the_days_games_and_a_summary(
    tmp_path: Path, fake_venue: None
) -> None:
    store = LocalStore(tmp_path)

    summary = await pull(
        KALSHI,
        "ncaafb",
        date(2026, 9, 26),
        date(2026, 9, 26),
        store=store,
        games=GAMES,
        teams=TEAMS,
    )

    assert (summary.listed, summary.matched, summary.priced) == (2, 1, 1)
    assert summary.odd_events == ["KXNCAAFGAME-26SEP26ODD"]
    [miss] = summary.unmatched
    assert miss["event"] == "KXNCAAFGAME-26SEP26ZZZORST"
    assert miss["reason"].startswith("no team for ZZZ")
    assert summary.points == 2
    assert summary.written == ["markets/kalshi/ncaafb/2026-09-26.json"]

    day = json.loads((tmp_path / summary.written[0]).read_text())
    assert day["fields"] == list(FIELDS)
    [game] = day["games"]
    assert game["game_id"] == "401"
    # Oregon State is home by ESPN, though it's the venue's second side.
    assert game["home"]["code"] == "ORST"
    assert game["away"]["code"] == "WSU"
    assert game["home"]["prices"] == [[1789000000, 0.6, 0.59, 0.61, 10.0]]

    [key] = await store.keys(PULLS)
    assert PullSummary.from_json(await store.read(key)) == summary


async def test_the_summary_renders_its_misses(tmp_path: Path, fake_venue: None) -> None:
    summary = await pull(
        KALSHI,
        "ncaafb",
        date(2026, 9, 26),
        date(2026, 9, 26),
        store=LocalStore(tmp_path),
        games=GAMES,
        teams=TEAMS,
    )

    text = render(summary)

    assert "listed 2  matched 1  unmatched 1" in text
    assert "KXNCAAFGAME-26SEP26ZZZORST" in text
    assert "no team for ZZZ" in text


async def test_an_unknown_venue_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No venue 'fanduel'"):
        await pull(
            "fanduel",
            "nfl",
            date(2026, 9, 26),
            date(2026, 9, 26),
            store=LocalStore(tmp_path),
            games=[],
        )
