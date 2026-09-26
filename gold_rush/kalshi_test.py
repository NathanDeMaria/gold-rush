import json
from datetime import UTC, date, datetime
from pathlib import Path

from .kalshi import KALSHI, event_day, games_from_markets, price_points
from .types import PricePoint

TESTDATA = Path(__file__).parent / "testdata"


def _load(name: str) -> dict:
    return json.loads((TESTDATA / name).read_text())


def test_event_day() -> None:
    assert event_day("KXNFLGAME-26SEP24ATLGB") == date(2026, 9, 24)
    assert event_day("KXNCAAMBGAME-26MAR01FSUDUKE") == date(2026, 3, 1)
    assert event_day("KXNFLGAME") is None


def test_an_event_with_two_team_markets_is_a_game() -> None:
    markets = _load("kalshi_markets_live.json")["markets"]

    games, odd = games_from_markets(markets, "nfl", "KXNFLGAME", historical=False)

    assert odd == []
    [game] = games
    assert game.venue == KALSHI
    assert game.event == "KXNFLGAME-26SEP24ATLGB"
    assert game.day == date(2026, 9, 24)
    assert [side.code for side in game.sides] == ["ATL", "GB"]
    assert [side.names for side in game.sides] == [("Atlanta",), ("Green Bay",)]
    assert game.sides[1].contract == "KXNFLGAME-26SEP24ATLGB-GB"
    assert game.kickoff is None
    assert game.opened is not None and game.closed is not None
    assert game.opened < game.closed
    assert game.extra == {"series": "KXNFLGAME", "historical": False}


def test_a_historical_listing_is_marked_so_its_history_is_asked_there() -> None:
    markets = _load("kalshi_markets_historical.json")["markets"]

    [game], _ = games_from_markets(markets, "nfl", "KXNFLGAME", historical=True)

    assert game.event == "KXNFLGAME-26JAN10GBCHI"
    assert game.extra["historical"] is True


def test_an_event_without_two_team_markets_is_reported_not_guessed() -> None:
    [one, _] = _load("kalshi_markets_live.json")["markets"]
    tie = {**one, "ticker": "KXNFLGAME-26SEP24ATLGB-TIE"}

    games, odd = games_from_markets([one, tie], "nfl", "KXNFLGAME", historical=False)

    assert games == []
    assert odd == ["KXNFLGAME-26SEP24ATLGB"]


def test_a_tie_market_is_left_out_of_the_sides() -> None:
    markets = _load("kalshi_markets_live.json")["markets"]
    tie = {**markets[0], "ticker": "KXNFLGAME-26SEP24ATLGB-TIE"}

    [game], odd = games_from_markets(
        [*markets, tie], "nfl", "KXNFLGAME", historical=False
    )

    assert odd == []
    assert [side.code for side in game.sides] == ["ATL", "GB"]


def test_live_candles_read_the_dollar_fields() -> None:
    candles = _load("kalshi_candlesticks_live.json")["candlesticks"]

    points = price_points(candles)

    assert len(points) == len(candles)
    last = candles[-1]
    assert points[-1] == PricePoint(
        at=last["end_period_ts"],
        price=float(last["price"]["close_dollars"]) if last["price"] else None,
        bid=float(last["yes_bid"]["close_dollars"]),
        ask=float(last["yes_ask"]["close_dollars"]),
        volume=float(last["volume_fp"]),
    )


def test_historical_candles_read_the_plain_fields() -> None:
    candles = _load("kalshi_candlesticks_historical.json")["candlesticks"]

    points = price_points(candles)

    last = candles[-1]
    assert points[-1].price == float(last["price"]["close"])
    assert points[-1].bid == float(last["yes_bid"]["close"])
    assert points[-1].volume == float(last["volume"])


def test_an_hour_nobody_traded_still_has_its_quotes() -> None:
    [point] = price_points(
        [
            {
                "end_period_ts": 1789491600,
                "price": {},
                "volume_fp": "0.00",
                "yes_bid": {"close_dollars": "0.2400"},
                "yes_ask": {"close_dollars": "0.7200"},
            }
        ]
    )

    assert point == PricePoint(1789491600, None, 0.24, 0.72, 0.0)
    assert datetime.fromtimestamp(point.at, UTC).year == 2026
