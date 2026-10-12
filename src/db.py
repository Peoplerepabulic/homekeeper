"""DynamoDB single-table data layer (boto3).

The table name comes from HOMEKEEPER_TABLE. The AWS client is created lazily:
importing this module never touches the network.

Key layout (pk = "USER#<id>"):
    PROFILE                  household info, timezone            (hot path)
    APPL#<applianceId>       device record                       (both)
    TASK#<applianceId>#<tid> maintenance schedule                (both)
    ALERT#<alertId>          recall / anomaly alerts             (cold path)
    EVENT#<timestamp>        operation log (cross-session memory)(hot path)
    UPLOAD#<token>           one-time photo-upload token (TTL)   (hot path)
"""

import os
from datetime import date

TABLE_NAME = os.environ.get("HOMEKEEPER_TABLE", "homekeeper")
DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "demo-household-1")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_table = None


def _table_resource():
    """Lazily create the DynamoDB table resource (no AWS calls at import)."""
    global _table
    if _table is None:
        import boto3

        _table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
    return _table


def get_today() -> date:
    """Return "today", overridden by DEMO_CLOCK (YYYY-MM-DD) for demo reproducibility."""
    clock = os.environ.get("DEMO_CLOCK")
    if clock:
        return date.fromisoformat(clock)
    return date.today()


# --- key constructors -----------------------------------------------------


def pk(user_id: str = DEMO_USER_ID) -> str:
    return f"USER#{user_id}"


def sk_profile() -> str:
    return "PROFILE"


def sk_appliance(appliance_id: str) -> str:
    return f"APPL#{appliance_id}"


def sk_task(appliance_id: str, task_id: str) -> str:
    return f"TASK#{appliance_id}#{task_id}"


def sk_alert(alert_id: str) -> str:
    return f"ALERT#{alert_id}"


def sk_event(ts: str) -> str:
    return f"EVENT#{ts}"


def sk_upload(token: str) -> str:
    return f"UPLOAD#{token}"


# --- thin wrappers ---------------------------------------------------------


def put_item(sort_key: str, attrs: dict, user_id: str = DEMO_USER_ID) -> dict:
    """Put one item under pk=USER#<user_id>, sk=sort_key."""
    item = {"pk": pk(user_id), "sk": sort_key, **attrs}
    _table_resource().put_item(Item=item)
    return item


def get_item(sort_key: str, user_id: str = DEMO_USER_ID) -> dict | None:
    """Get one item by sort key; None if missing."""
    resp = _table_resource().get_item(Key={"pk": pk(user_id), "sk": sort_key})
    return resp.get("Item")


def query_prefix(sk_prefix: str, user_id: str = DEMO_USER_ID) -> list[dict]:
    """Query all items whose sort key starts with sk_prefix (paginated)."""
    from boto3.dynamodb.conditions import Key

    cond = Key("pk").eq(pk(user_id)) & Key("sk").begins_with(sk_prefix)
    return _paginate(cond)


def query_all(user_id: str = DEMO_USER_ID) -> list[dict]:
    """Every item in one household partition: one round trip for a small home."""
    from boto3.dynamodb.conditions import Key

    return _paginate(Key("pk").eq(pk(user_id)))


def _paginate(cond) -> list[dict]:
    items, kwargs = [], {"KeyConditionExpression": cond}
    while True:
        resp = _table_resource().query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def replace_partition(items: list[dict], user_id: str = DEMO_USER_ID) -> int:
    """Delete every item under USER#<user_id>, then write `items` (each has "sk")."""
    table = _table_resource()
    existing = query_all(user_id)
    with table.batch_writer() as batch:
        for it in existing:
            batch.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
    with table.batch_writer() as batch:
        for it in items:
            batch.put_item(Item={"pk": pk(user_id), **it})
    return len(items)
