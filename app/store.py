"""Link storage behind a small interface: in-memory for tests and local runs, DynamoDB on AWS."""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass
class Link:
    code: str
    url: str
    hits: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodeTaken(Exception):
    """The generated code already exists."""


class LinkStore(Protocol):
    def create(self, code: str, url: str) -> Link: ...
    def get(self, code: str) -> Link | None: ...
    def hit(self, code: str) -> Link | None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self) -> None:
        self._links: dict[str, Link] = {}
        self._lock = threading.Lock()

    def create(self, code: str, url: str) -> Link:
        with self._lock:
            if code in self._links:
                raise CodeTaken(code)
            link = Link(code=code, url=url, hits=0, created_at=_now())
            self._links[code] = link
            return link

    def get(self, code: str) -> Link | None:
        return self._links.get(code)

    def hit(self, code: str) -> Link | None:
        with self._lock:
            link = self._links.get(code)
            if link:
                link.hits += 1
            return link


class DynamoStore:
    """Table with partition key `code` (string). Pay-per-request, so idle cost is zero."""

    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def from_env(cls, table_name: str) -> DynamoStore:
        import boto3  # imported lazily: tests and local runs never need it

        return cls(boto3.resource("dynamodb").Table(table_name))

    @staticmethod
    def _link(item: dict[str, Any]) -> Link:
        return Link(code=item["code"], url=item["url"], hits=int(item.get("hits", 0)), created_at=item["created_at"])

    def create(self, code: str, url: str) -> Link:
        from botocore.exceptions import ClientError

        link = Link(code=code, url=url, hits=0, created_at=_now())
        try:
            self._table.put_item(Item=link.to_dict(), ConditionExpression="attribute_not_exists(code)")
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise CodeTaken(code) from err
            raise
        return link

    def get(self, code: str) -> Link | None:
        item = self._table.get_item(Key={"code": code}).get("Item")
        return self._link(item) if item else None

    def hit(self, code: str) -> Link | None:
        from botocore.exceptions import ClientError

        try:
            res = self._table.update_item(
                Key={"code": code},
                UpdateExpression="ADD hits :one",
                ConditionExpression="attribute_exists(code)",
                ExpressionAttributeValues={":one": 1},
                ReturnValues="ALL_NEW",
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return self._link(res["Attributes"])


_store: LinkStore | None = None


def get_store() -> LinkStore:
    global _store
    if _store is None:
        table = os.environ.get("ECOLINKS_TABLE")
        _store = DynamoStore.from_env(table) if table else MemoryStore()
    return _store


def set_store(store: LinkStore | None) -> None:
    """Test hook."""
    global _store
    _store = store
