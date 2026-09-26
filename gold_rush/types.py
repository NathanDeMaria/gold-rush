"""
What a venue's game looks like once it's been read, whichever venue it's from.

Kalshi and Polymarket disagree about nearly everything structural. Kalshi
lists a game as an event holding one yes/no market per team, priced in its
own order book each; Polymarket lists it as an event holding one market with
two outcome tokens that trade against each other. Kalshi keeps a bid and an
ask per hour in its history; Polymarket keeps one price. Kalshi's event says
the date and nothing about home; Polymarket's says the kickoff to the minute
and, lately, which team is home.

So the shape here is the one both can fill: a game is two `Side`s, each a
team as the venue wrote it plus the one contract that pays if that team
wins, and a price is a `PricePoint` whose bid and ask may be missing. Home
and away aren't a venue's to say -- `match` gets them from the ESPN game --
so a `VenueGame`'s sides are in the venue's order and nothing more.
"""

from datetime import date, datetime
from typing import NamedTuple


class Side(NamedTuple):
    """
    One team in a venue's game, and the contract on it winning.

    `code` is what the venue writes in its tickers or slugs ("GB", "sdak")
    and `names` every other spelling the game gives ("Green Bay", "South
    Dakota Coyotes"); both are looked up in call-it-what-you-want under the
    venue as a source. `contract` is what the price history is asked for
    by: a Kalshi market ticker or a Polymarket CLOB token id.
    """

    code: str
    names: tuple[str, ...]
    contract: str


class VenueGame(NamedTuple):
    """
    One game as a venue lists it.

    `event` is the venue's id for it -- a Kalshi event ticker, a Polymarket
    event slug -- and what a person searches for on the venue's site.
    `day` is the date the venue files it under, in the venue's own sense of
    a day (US Eastern for both, as far as has been seen), which is only
    good to a day either side; `kickoff` is exact when the venue says it.

    `opened` and `closed` bound the window a history is asked for. `extra`
    carries what one venue needs to fetch it and the other doesn't have:
    whether a Kalshi market has moved to its historical tier, and which
    series it's filed under.
    """

    venue: str
    league: str
    event: str
    day: date
    kickoff: datetime | None
    sides: tuple[Side, Side]
    opened: datetime | None
    closed: datetime | None
    extra: dict


class PricePoint(NamedTuple):
    """
    What a contract cost at one moment, in dollars on a $1 payout -- which
    makes each number a probability that side wins.

    `at` is the end of the period the point covers, in epoch seconds.
    `price` is a trade (Kalshi's last in the period) or the venue's one
    quoted number (Polymarket's); `bid` and `ask` are the best quotes at
    the end of the period where the venue keeps them. `volume` is contracts
    traded in the period, where it's known.
    """

    at: int
    price: float | None
    bid: float | None
    ask: float | None
    volume: float | None
