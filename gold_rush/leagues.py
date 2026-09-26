"""
The leagues gold-rush pulls, in endgame's terms, and where each venue keeps them.

A league is named the way endgame names it -- `mens` rather than
call-it-what-you-want's `ncaambb` -- because the games a pull is matched to
are endgame's, stored under that name, and cassandra reads the result by it.

Kalshi files a league's games under one series ticker per kind of market;
`...GAME` is the moneyline. College football has two, since FCS-only games
live in their own series. Polymarket files them under a series per season
(`nfl-2025`, `nfl-2026`), plus an undated one from before it started doing
that, so a pull asks all of them and whatever /sports says is current.

NHL and WNBA aren't here yet. Both venues list them, but call-it-what-you-
want has no namespace for either league's ESPN ids, so there'd be nothing to
match a game to.
"""

from typing import NamedTuple

from call_it_what_you_want import ENDGAME, ESPN, NCAA, NCAAFB, NCAAMBB, NCAAWBB, NFL


class League(NamedTuple):
    name: str
    # call-it-what-you-want's name for it, and the ESPN id namespace its
    # teams are numbered in.
    names_league: str
    namespace: str
    # The source endgame's stored team names come from. College games are
    # stored under ESPN's names; the NFL's under endgame's own nicknames.
    stored_as: str
    kalshi_series: tuple[str, ...]
    # Polymarket's short code for the league, which /sports lists the
    # current series under, and every series id seen for it so far.
    polymarket_sport: str
    polymarket_series: tuple[int, ...]


LEAGUES = {
    league.name: league
    for league in (
        League("nfl", NFL, NFL, ENDGAME, ("KXNFLGAME",), "nfl", (1, 10187, 12185)),
        League(
            "ncaafb",
            NCAAFB,
            NCAA,
            ESPN,
            ("KXNCAAFGAME", "KXNCAAFCSGAME"),
            "cfb",
            (10002, 10210, 12756),
        ),
        League("mens", NCAAMBB, NCAA, ESPN, ("KXNCAAMBGAME",), "cbb", (10050, 10470)),
        League("womens", NCAAWBB, NCAA, ESPN, ("KXNCAAWBGAME",), "cwbb", (10471,)),
    )
}


def league(name: str) -> League:
    try:
        return LEAGUES[name]
    except KeyError:
        raise ValueError(
            f"gold-rush doesn't pull {name!r}. Available: {', '.join(LEAGUES)}."
        ) from None
