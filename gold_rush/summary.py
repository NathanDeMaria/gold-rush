"""
What a pull did, written next to what it pulled.

The failure a pull like this is prone to is quiet. A venue renames a team, a
code nobody has filed shows up, a listing comes back short -- and the pull
succeeds, writing fewer games than it should, and a benchmark computed off
it is quietly about a different set of games. Nothing crashes, so a job's
exit status and its stdout say nothing useful.

So every pull writes this: how many games the venue listed, how many
matched an ESPN game and how many didn't, each miss with its reason, the
teams endgame stores that call-it-what-you-want doesn't know, which matched
games came back with no prices, and what failed outright. `gold-rush report`
reads them back; a miss is usually one row in call-it-what-you-want away
from fixed, and the report says which.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PullSummary:
    venue: str
    league: str
    start: str
    end: str
    started_at: str
    finished_at: str = ""
    listed: int = 0
    matched: int = 0
    # {"event", "day", "sides": [codes], "reason"}, one per venue game that
    # didn't match an ESPN game.
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    # Venue events that looked like games and didn't parse as one.
    odd_events: list[str] = field(default_factory=list)
    # endgame's names for teams call-it-what-you-want doesn't know, and how
    # many games each was in. Every one is a game nothing can match.
    unknown_teams: dict[str, int] = field(default_factory=dict)
    # Matched games with prices on both sides, and the ones without.
    priced: int = 0
    empty: list[str] = field(default_factory=list)
    points: int = 0
    # {"event", "error"}, one per game whose history couldn't be fetched.
    errors: list[dict[str, str]] = field(default_factory=list)
    written: list[str] = field(default_factory=list)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), indent=1).encode()

    @classmethod
    def from_json(cls, data: bytes) -> "PullSummary":
        return cls(**json.loads(data))


def render(summary: PullSummary, *, misses: int = 10) -> str:
    """A pull summary as a few lines a person reads."""
    lines = [
        f"{summary.venue} {summary.league} {summary.start}..{summary.end}"
        f"  pulled {summary.started_at}",
        f"  listed {summary.listed}  matched {summary.matched}"
        f"  unmatched {len(summary.unmatched)}  priced {summary.priced}"
        f"  no prices {len(summary.empty)}  errors {len(summary.errors)}"
        f"  points {summary.points:,}  files {len(summary.written)}",
    ]
    for miss in summary.unmatched[:misses]:
        lines.append(
            f"    {miss['event']}  {miss['day']}  {' vs '.join(miss['sides'])}"
            f"  -- {miss['reason']}"
        )
    if len(summary.unmatched) > misses:
        lines.append(f"    ... {len(summary.unmatched) - misses} more unmatched")
    if summary.unknown_teams:
        worst = sorted(summary.unknown_teams.items(), key=lambda kv: -kv[1])[:misses]
        named = ", ".join(f"{name} ({n})" for name, n in worst)
        lines.append(f"  endgame teams call-it-what-you-want doesn't know: {named}")
    for error in summary.errors[:misses]:
        lines.append(f"    error {error['event']}: {error['error']}")
    if summary.odd_events:
        lines.append(f"  events that didn't parse as a game: {len(summary.odd_events)}")
    return "\n".join(lines)
