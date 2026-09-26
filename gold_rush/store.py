"""
Where a pull's output goes, and the one layout it's written in.

    markets/{venue}/{league}/{YYYY-MM-DD}.json
    markets/_pulls/{venue}/{league}/{YYYYMMDDTHHMMSSZ}.json

The first is the prices: one file per venue, league and ESPN game day
(US Eastern), holding every game on that day the pull matched. A re-pull of
a day replaces its file, so pulling a range twice is safe, and a day is
the unit cassandra reads.

The second is what each pull did -- see `summary`. It's the log that's
meant to be read: what was listed, what matched, what didn't and why,
without opening a container's stdout.

A store is a local directory or a bucket. Local is for trying things; the
scheduled pull writes to the bucket endgame's seasons and odds live in.
"""

import asyncio
from pathlib import Path
from typing import Protocol

from endgame_aws.io import list_all_keys, read_bytes, save_data_to_s3

PREFIX = "markets"
PULLS = f"{PREFIX}/_pulls"


def day_key(venue: str, league: str, day: str) -> str:
    return f"{PREFIX}/{venue}/{league}/{day}.json"


def pull_key(venue: str, league: str, started_at: str) -> str:
    return f"{PULLS}/{venue}/{league}/{started_at}.json"


class Store(Protocol):
    def describe(self) -> str: ...

    async def write(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def keys(self, prefix: str) -> list[str]: ...


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def describe(self) -> str:
        return str(self.root)

    async def write(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread((self.root / key).read_bytes)

    async def keys(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(
            str(path.relative_to(self.root))
            for path in base.rglob("*")
            if path.is_file()
        )


class S3Store:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def describe(self) -> str:
        return f"s3://{self.bucket}"

    async def write(self, key: str, data: bytes) -> None:
        await save_data_to_s3(self.bucket, key, data)

    async def read(self, key: str) -> bytes:
        return await read_bytes(self.bucket, key)

    async def keys(self, prefix: str) -> list[str]:
        return sorted([key async for key in list_all_keys(self.bucket, prefix)])


def open_store(where: str) -> Store:
    """`s3://bucket` for a bucket, anything else for a local directory."""
    if where.startswith("s3://"):
        bucket = where.removeprefix("s3://").strip("/")
        if not bucket or "/" in bucket:
            raise ValueError(f"Expected s3://<bucket>, got {where!r}")
        return S3Store(bucket)
    return LocalStore(Path(where))
