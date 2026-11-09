# gold-rush

Kalshi, Polymarket and Polymarket US prices on the games cassandra predicts,
matched to the ESPN games endgame stores.

A pull lists one venue's games in one league over a range of days, matches
each to an ESPN game, fetches the hourly price history of both sides, and
writes it to the bucket next to endgame's seasons and odds. cassandra reads
it the way it reads the odds: as a benchmark first, the market a model's
win probability is scored against, and later as the price a bet would
actually get.

```shell
gold-rush games kalshi ncaafb 2026-09-19               # what a venue lists, parsed
gold-rush pull kalshi nfl                              # yesterday, to the bucket
gold-rush pull polymarket mens 2025-11-03 2026-04-07   # a season's backfill
gold-rush upcoming                                     # today and tomorrow, everything
gold-rush books                                        # Polymarket US's open order books
gold-rush report --last 3                              # what recent pulls did
```

`pull` and `report` write to and read from the bucket in endgame_aws's
config unless `--out` names a directory; `pull` matches against the seasons
there unless `--games-from` names another (a directory laid out like the
bucket works).

Every venue's market data is free to read without an account.

## What gets written

```
markets/{venue}/{league}/{YYYY-MM-DD}.json          one ESPN game day, US Eastern
markets/_pulls/{venue}/{league}/{started}.json      what one pull did
markets/_upcoming/{venue}/{league}/{started}.json   what one hourly pull did
markets/_books/polymarket_us/{league}/{read_at}.json  every open game's book
```

A day's file holds every game on that day the pull matched:

```json
{
  "venue": "kalshi", "league": "nfl", "day": "2026-09-24", "read_at": "...",
  "fields": ["at", "price", "bid", "ask", "volume"],
  "games": [{
    "game_id": "401...", "kickoff": "2026-09-25T00:15:00+00:00",
    "neutral_site": false, "event": "KXNFLGAME-26SEP24ATLGB",
    "home": {"code": "GB", "names": ["Green Bay"],
             "contract": "KXNFLGAME-26SEP24ATLGB-GB",
             "prices": [[1790290800, 0.65, 0.64, 0.65, 4024154.33], ...]},
    "away": {...}
  }]
}
```

Each price row is the end of an hour in epoch seconds, then what the side's
contract cost in dollars on a $1 payout -- a probability that side wins.
`price` is Kalshi's last trade in the hour or Polymarket's one quoted
number; `bid` and `ask` are the closing quotes, kept by Kalshi and
Polymarket US and always null on Polymarket. Polymarket US's rows are
quotes only: `price` and `volume` are null. Rows run from the market's open to
its close, so they include the game itself: cut them at `kickoff`.

Kalshi prices each team in its own order book, so its two sides needn't
sum to one. Polymarket's are one book quoted at one number, so they do.
Polymarket US's are one book too, but each side is what buying it costs --
the ask, and one minus the bid -- so they sum to a little over one, the
way Kalshi's asks do.

`home` and `away` are ESPN's, never the venue's, so a neutral-site game
is no different from any other.

A pull writes into a day's file, not over it: it replaces the games it
lists again and keeps the rest. So pulling a range twice is safe, and a
game a venue dates a day off from ESPN stays in its day -- Kalshi tickers a
7 pm tip in Honolulu with the next day's date, so the pull that lists it is
the next day's, writing into a file it didn't otherwise list.

## Matching

Neither venue has an id ESPN knows. A side is a code (`GB`, `sdak`) and a
few names, and call-it-what-you-want files every venue's codes and names
under the ESPN team they are -- see its `ciwyw venues`. A side resolves to
every team its code or names could be, and a venue game is the one ESPN
game, on its day or the day either side, whose two teams are its two sides.

Resolving to candidates rather than one team is deliberate: Kalshi writes
Washington State and Wayne State both as `WSU`, and it's the opponent and
the date that say which. A team the venue's roster leaves out but its games
don't -- Polymarket has UNC Wilmington games and no UNC Wilmington -- falls
back to ESPN's own name for it.

A game that doesn't match isn't written. It's in the pull's summary with
the reason, which is nearly always a code or name call-it-what-you-want
doesn't have; add the row there and pull the day again.

On four days of September 2026 football the two venues' matches agreed
with each other on every game both listed (133), home side included: the
home price just before kickoff differed by 0.002 on average in the NFL
and 0.007 in college football, and never by more than 0.03.

## The pull summary

The way a pull like this fails is quiet -- a renamed team, a new code, a
listing that comes back short -- and the pull still succeeds, just with
fewer games. So every pull writes what it did: games listed, matched and
not (each with its reason), endgame teams call-it-what-you-want doesn't
know, matched games with no prices, requests that failed. `gold-rush
report` prints them, newest first:

```
polymarket ncaafb 2026-09-18..2026-09-21  pulled 20260926T211453Z
  listed 127  matched 125  unmatched 2  priced 125  no prices 0  errors 0  points 72,436  files 2
    cfb-gsc-mhud34ce7-2026-09-19  2026-09-19  gsc vs mhud34ce7  -- no team for gsc (Glenville State College / Pioneers)
    ... 1 more unmatched
kalshi ncaafb 2026-09-18..2026-09-21  pulled 20260926T211320Z
  listed 118  matched 118  unmatched 0  priced 118  no prices 0  errors 0  points 77,331  files 2
```

## The venues

What each has, as of September 2026:

| league | Kalshi (`KX…GAME`) | Polymarket | Polymarket US |
|---|---|---|---|
| nfl | 2025 preseason on | 2021 on; typed and with teams from 2025 | Nov 2025 on |
| ncaafb | 2025 on, FCS in its own series | late 2024 on; untyped until mid-2025 | Dec 2025 bowls, then 2026 on |
| mens | 2025-26 on | March Madness 2025, then 2025-26 on | Dec 2025 on |
| womens | 2025-26 on | 2025-26 on, barely traded | March Madness 2026, then 2026-27 on |

(Polymarket US's first months are from counting its listings in November
2026; a backfill's summaries are the real answer.)

NHL and WNBA are on both, and not pulled yet: call-it-what-you-want has no
namespace for their ESPN ids to match to.

**Kalshi.** A game is an event (`KXNFLGAME-26SEP24ATLGB`) holding one
yes/no market per team. The event ticker has the date; nothing says which
team is home. Settled markets move to a historical tier some weeks later,
with its own endpoints, which ignore the close-time filter and spell the
candlestick fields differently (`close` rather than `close_dollars`). The
basic tier allows about 20 requests a second; a pull paces itself at 8.

**Polymarket.** A game is an event (`nfl-atl-gb-2026-09-25`) holding a
moneyline market with two outcome tokens, plus spread and total ladders,
with props as child events. Newer moneylines are typed and newer events
list their teams, home or away; older ones are untyped and recognized by
shape, with their codes read off the slug. Each season is its own series.
History is one price an hour, at most about two weeks per request, and
Gamma's listings stop paging past an offset of about 2,000. Python's
default user agent gets a 403. This is Polymarket's international venue,
which a US account can't trade.

**Polymarket US** (`gateway.polymarket.us`) is the CFTC-regulated exchange
a US account trades on: separate books, separate prices. A game is an
event (`cfb-dart-prnce-2026-11-07`) holding dozens of markets, one of them
the moneyline (`marketType: "moneyline"`): a single yes/no instrument with
the `long` team on yes and the other on no, so buying the short team is
selling yes at the bid. Its price history is derived from the book, not
trades -- each hour's best ask and one minus the best bid, which are the
two sides' costs -- and one request returns a market's whole life, so a
finished game backfills at any time. Size isn't in the history and the
book is only ever now, so the hourly job snapshots every open game's book
into `markets/_books/` (`gold-rush books`): each side's buyable levels,
`[price, contracts]`, out to ten cents past the best. Those are keyed by
the venue's event and not matched; a day file's `event` says which ESPN
game it is. Events are filed under a series per league whose names don't
track seasons (`cbb-2025` holds November 2026's games), so a listing asks
every series seen plus the one the league endpoint calls active. Team
codes are the international venue's and are looked up under its name in
call-it-what-you-want. The public limit is 20 requests a second per IP; a
pull paces itself at 12. Taker fees are tiered by 30-day volume, from
0.095 × p × (1 − p) below $10,000 a month (see its fee schedule).

## On Batch

Pulls run on AWS Batch, on the queue cassandra and endgame share: one job
definition, and a daily pull of yesterday's games for each venue and league,
from 10:00 Central, after endgame's seasons have refreshed -- and hourly, one
`gold-rush upcoming` for today's and tomorrow's games, whose files the next
morning's daily pull replaces with the whole game. A backfill is the same job
with dates. `jobs/` is the terraform, and its README the setup and
the commands; the image is the `Dockerfile` here, pushed by CI on every merge
to main.

## Development

Open the folder in its devcontainer, or with uv installed:

```shell
make check   # ruff format --check, ruff check, ty -- what CI runs
make lint    # same, fixing what it can
make test    # pytest
```

Tooling comes from the [mirrorball](https://github.com/NathanDeMaria/mirrorball)
template; the image targets (`make build`, `make push`) are in `project.mk`.

Tests live next to the code they cover, named `*_test.py`, and run against
responses recorded from each venue in `gold_rush/testdata/`.
