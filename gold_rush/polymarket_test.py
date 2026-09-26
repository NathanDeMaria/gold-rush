import json
from datetime import UTC, date, datetime
from pathlib import Path

from .polymarket import POLYMARKET, game_from_event, price_points
from .types import PricePoint

TESTDATA = Path(__file__).parent / "testdata"


def _load(name: str) -> dict:
    return json.loads((TESTDATA / name).read_text())


def test_a_typed_moneyline_with_teams() -> None:
    event = _load("polymarket_event_typed.json")

    game = game_from_event(event, "nfl")

    assert game is not None
    assert game.venue == POLYMARKET
    assert game.event == "nfl-atl-gb-2026-09-25"
    # Filed under the Eastern date; the slug's date is kickoff in UTC.
    assert game.day == date(2026, 9, 24)
    assert game.kickoff == datetime(2026, 9, 25, 0, 15, tzinfo=UTC)
    assert [side.code for side in game.sides] == ["atl", "gb"]
    assert game.sides[0].names == ("Falcons", "Atlanta Falcons")
    assert game.sides[1].names == ("Packers", "Green Bay Packers")
    tokens = json.loads(event["markets"][0]["clobTokenIds"])
    assert [side.contract for side in game.sides] == tokens


def test_an_untyped_moneyline_takes_its_codes_from_the_slug() -> None:
    event = _load("polymarket_event_untyped.json")

    game = game_from_event(event, "ncaafb")

    assert game is not None
    assert game.event == "cfb-frsno-ku-2025-08-23"
    assert [side.code for side in game.sides] == ["frsno", "ku"]
    assert [side.names for side in game.sides] == [("Fresno State",), ("Kansas",)]
    assert game.opened is not None and game.closed is not None


def test_a_prop_sub_event_is_not_a_game() -> None:
    event = {**_load("polymarket_event_typed.json"), "parentEventId": 848221}

    assert game_from_event(event, "nfl") is None


def test_an_event_without_a_moneyline_is_not_a_game() -> None:
    event = _load("polymarket_event_typed.json")
    event["markets"] = [
        m for m in event["markets"] if m.get("sportsMarketType") != "moneyline"
    ]

    assert game_from_event(event, "nfl") is None


def test_a_yes_no_market_on_the_events_slug_is_not_a_moneyline() -> None:
    event = _load("polymarket_event_untyped.json")
    event["markets"][0]["outcomes"] = '["Yes", "No"]'

    assert game_from_event(event, "ncaafb") is None


def test_prices_history_is_a_price_and_nothing_else() -> None:
    history = _load("polymarket_prices_history.json")["history"]

    points = price_points(history)

    assert points[0] == PricePoint(history[0]["t"], history[0]["p"], None, None, None)
