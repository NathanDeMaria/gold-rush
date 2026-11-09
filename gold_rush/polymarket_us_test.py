import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from call_it_what_you_want import ESPN, NCAAFB, POLYMARKET, Team, TeamName, Teams

from . import pull as pull_module
from .leagues import league
from .match import side_ids
from .polymarket_us import (
    POLYMARKET_US,
    _looks_like_a_game,
    book_levels,
    game_from_event,
    price_points,
)
from .pull import snapshot_books
from .store import LocalStore
from .types import PricePoint, Side, VenueGame

TESTDATA = Path(__file__).parent / "testdata"
MARKET = "aec-cfb-dart-prnce-2026-11-07"


def _load(name: str) -> dict:
    return json.loads((TESTDATA / name).read_text())


def test_a_closed_game_reads_its_moneyline_with_the_long_team_first() -> None:
    game = game_from_event(_load("polymarket_us_event.json"), "ncaafb")

    assert game is not None
    assert game.venue == POLYMARKET_US
    assert game.event == "cfb-dart-prnce-2026-11-07"
    assert game.day == date(2026, 11, 7)
    assert game.kickoff == datetime(2026, 11, 7, 17, tzinfo=UTC)
    assert [side.code for side in game.sides] == ["dart", "prnce"]
    assert game.sides[0].names == ("Dartmouth", "Big Green", "Dartmouth Big Green")
    assert [side.contract for side in game.sides] == [MARKET, MARKET]
    assert game.extra["market"] == MARKET
    assert game.opened == datetime(2026, 10, 24, 17, 30, 10, tzinfo=UTC)
    assert game.closed == datetime(2026, 11, 7, 20, 57, 26, tzinfo=UTC)


def test_an_older_untyped_moneyline_is_still_the_moneyline() -> None:
    event = _load("polymarket_us_event_2025.json")

    game = game_from_event(event, "mens")

    assert game is not None
    assert event["markets"][0]["sportsMarketType"] == "moneyline"
    assert game.extra["market"] == event["markets"][0]["slug"]


def test_an_open_market_has_no_close_yet() -> None:
    # An open market's end date is a placeholder weeks past the game.
    event = _load("polymarket_us_event.json")
    event["markets"][0]["closed"] = False

    game = game_from_event(event, "ncaafb")

    assert game is not None and game.closed is None


def test_an_event_without_a_moneyline_is_not_a_game() -> None:
    event = _load("polymarket_us_event.json")
    for market in event["markets"]:
        market["marketType"] = "props"
        market["sportsMarketType"] = "football_team_first_half_winner"

    assert game_from_event(event, "ncaafb") is None


def test_a_moneyline_needs_a_team_on_each_side() -> None:
    event = _load("polymarket_us_event.json")
    event["markets"][0]["marketSides"][1]["long"] = True

    assert game_from_event(event, "ncaafb") is None


def test_each_side_costs_its_own_ask_and_bids_one_minus_the_other() -> None:
    history = _load("polymarket_us_price_history.json")["history"]
    first = history[0]

    long_side = price_points(history, long=True)
    short_side = price_points(history, long=False)

    assert long_side[0] == PricePoint(
        first["timestamp"],
        None,
        round(1 - first["shortPrice"], 6),
        first["longPrice"],
        None,
    )
    assert short_side[0].ask == first["shortPrice"]
    assert short_side[0].bid == round(1 - first["longPrice"], 6)
    assert len(long_side) == len(short_side) == len(history)


def test_buying_the_short_side_sells_into_the_bids() -> None:
    book = _load("polymarket_us_book.json")
    data = book["marketData"]

    long_levels, short_levels = book_levels(book)

    assert long_levels[0] == [
        float(data["offers"][0]["px"]["value"]),
        float(data["offers"][0]["qty"]),
    ]
    assert short_levels[0] == [
        round(1 - float(data["bids"][0]["px"]["value"]), 6),
        float(data["bids"][0]["qty"]),
    ]
    assert [p for p, _ in long_levels] == sorted(p for p, _ in long_levels)
    assert [p for p, _ in short_levels] == sorted(p for p, _ in short_levels)


def test_a_book_keeps_only_the_levels_near_the_best() -> None:
    book = {
        "marketData": {
            "offers": [
                {"px": {"value": "0.40"}, "qty": "10"},
                {"px": {"value": "0.50"}, "qty": "20"},
                {"px": {"value": "0.51"}, "qty": "30"},
            ],
            "bids": [],
        }
    }

    long_levels, short_levels = book_levels(book)

    assert long_levels == [[0.40, 10.0], [0.50, 20.0]]
    assert short_levels == []


def test_its_codes_are_looked_up_as_the_international_venues() -> None:
    dartmouth = Team(
        "159",
        (
            TeamName("Dartmouth Big Green", 2025, ESPN, NCAAFB),
            TeamName("dart", 2026, POLYMARKET, NCAAFB),
        ),
    )
    side = Side("dart", ("Dartmouth",), MARKET)

    assert side_ids(side, POLYMARKET_US, league("ncaafb"), Teams([dartmouth])) == {
        "159"
    }


class FakeClient:
    per_second = 1000.0
    game = VenueGame(
        POLYMARKET_US,
        "ncaafb",
        "cfb-dart-prnce-2026-11-07",
        date(2026, 11, 7),
        datetime(2026, 11, 7, 17, tzinfo=UTC),
        (Side("dart", ("Dartmouth",), MARKET), Side("prnce", ("Princeton",), MARKET)),
        None,
        None,
        {"market": MARKET},
    )

    def __init__(self, http) -> None:
        pass

    async def open_games(self, spec):
        if spec.name == "womens":
            raise ConnectionError("listing is down")
        return [self.game]

    async def book(self, game):
        return _load("polymarket_us_book.json")


async def test_books_are_snapshot_per_league_and_a_failure_costs_only_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pull_module, "PolymarketUs", FakeClient)

    written, failures = await snapshot_books(
        ["ncaafb", "womens"], store=LocalStore(tmp_path)
    )

    assert failures == ["polymarket_us/womens: ConnectionError('listing is down')"]
    [key] = written
    assert key.startswith("markets/_books/polymarket_us/ncaafb/")
    snapshot = json.loads((tmp_path / key).read_text())
    [game] = snapshot["games"]
    assert game["event"] == "cfb-dart-prnce-2026-11-07"
    assert game["market"] == MARKET
    assert game["long"]["code"] == "dart"
    assert game["long"]["asks"][0] == [0.85, 13686.4]
    assert game["short"]["asks"][0] == [0.155, 5230.16]
    assert game["state"] == "MARKET_STATE_OPEN"


def test_only_a_game_shaped_slug_without_a_moneyline_is_odd() -> None:
    assert _looks_like_a_game({"slug": "cfb-dart-prnce-2026-11-07"})
    assert not _looks_like_a_game({"slug": "nfl-afcwest-2027-01-10-w"})


def test_a_womens_name_drops_its_suffix_and_offers_espns_shape() -> None:
    event = _load("polymarket_us_event.json")
    side = event["markets"][0]["marketSides"][0]
    side["team"] = {
        "name": "West Florida (W)",
        "safeName": "West Florida (W)",
        "alias": "Argonauts",
        "abbreviation": "wfla",
    }

    game = game_from_event(event, "womens")

    assert game is not None
    assert game.sides[0].names == (
        "West Florida",
        "Argonauts",
        "West Florida Argonauts",
    )
