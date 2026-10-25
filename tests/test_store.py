import pytest
from botocore.exceptions import ClientError

from app import store as store_mod
from app.ratelimit import RateLimiter
from app.store import CodeTaken, DynamoStore, MemoryStore


def _conditional_failure(op):
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, op)


class FakeTable:
    """Just enough of a boto3 DynamoDB Table for the store."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression):
        if Item["code"] in self.items:
            raise _conditional_failure("PutItem")
        self.items[Item["code"]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key["code"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, **kwargs):
        item = self.items.get(Key["code"])
        if not item:
            raise _conditional_failure("UpdateItem")
        item["hits"] = int(item.get("hits", 0)) + kwargs["ExpressionAttributeValues"][":one"]
        return {"Attributes": dict(item)}


@pytest.mark.parametrize("make", [MemoryStore, lambda: DynamoStore(FakeTable())])
def test_store_contract(make):
    s = make()
    link = s.create("abc1234", "https://example.org")
    assert link.hits == 0
    with pytest.raises(CodeTaken):
        s.create("abc1234", "https://example.org/other")
    assert s.get("abc1234").url == "https://example.org"
    assert s.hit("abc1234").hits == 1
    assert s.hit("abc1234").hits == 2
    assert s.get("missing") is None
    assert s.hit("missing") is None


def test_dynamo_store_reraises_other_errors():
    class Broken(FakeTable):
        def put_item(self, Item, ConditionExpression):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

        def update_item(self, Key, **kwargs):
            raise ClientError({"Error": {"Code": "InternalServerError"}}, "UpdateItem")

    s = DynamoStore(Broken())
    with pytest.raises(ClientError):
        s.create("abc1234", "https://example.org")
    with pytest.raises(ClientError):
        s.hit("abc1234")


def test_get_store_picks_backend(monkeypatch):
    store_mod.set_store(None)
    monkeypatch.delenv("ECOLINKS_TABLE", raising=False)
    assert isinstance(store_mod.get_store(), MemoryStore)
    store_mod.set_store(None)
    monkeypatch.setenv("ECOLINKS_TABLE", "ecolinks-links")
    monkeypatch.setattr(DynamoStore, "from_env", classmethod(lambda cls, name: cls(FakeTable())))
    assert isinstance(store_mod.get_store(), DynamoStore)
    store_mod.set_store(None)


def test_rate_limiter_window_slides():
    now = [0.0]
    rl = RateLimiter(limit=2, window_s=10, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a")
    assert not rl.allow("a")
    now[0] = 10.0
    assert rl.allow("a")
