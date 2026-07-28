"""Tag EventSourcing meeting records (c# + cls#) with their ProjectId.

Runs off the EventSourcing table's DynamoDB stream. For a newly-created call
record we look up the meeting's project in MongoDB (active_meetings_board, keyed
by lma_call_id == CallId) and write `ProjectId` onto BOTH the call record and its
list row in ONE transaction — so project members can see the meeting (Fact A,
docs/BACKEND_INTEGRATION_SPEC.md §4).

Event-type awareness (this tagging write is itself a stream event, so we must
not re-trigger ourselves forever):

  * Only the CALL record drives tagging — PK == SK == "c#" + CallId. List-row
    (cls#) stream events are ignored; we tag the list row FROM the call record.
  * REMOVE events are ignored (nothing to tag).
  * If the record ALREADY carries ProjectId we skip. This is the guard that
    stops our own write (a MODIFY on this same table/stream) from looping —
    it holds even if the event-source mapping ever delivers MODIFY events.

The list-row key is derived from the call record's own CreatedAt (present on the
stream NewImage), exactly as the createCall resolver built it — never a scan.

Required environment variables:
    TABLE_NAME                        : the EventSourcing table name
    ACTIVE_MEETINGS_BOARD_COLLECTION  : Mongo collection (default active_meetings_board)
    AWS_REGION                        : provided by the Lambda runtime
"""

import os

import boto3

from mongo_client import get_collection

_TABLE_NAME       = os.environ["TABLE_NAME"]
_BOARD_COLLECTION = os.environ.get("ACTIVE_MEETINGS_BOARD_COLLECTION", "active_meetings_board")

_ddb = boto3.client("dynamodb")


def _project_id_for_call(call_id):
    """Look up the meeting's project in the board by CallId. Returns a ULID or None."""
    doc = get_collection(_BOARD_COLLECTION).find_one(
        {"lma_call_id": call_id},
        {"project_ids": 1, "_id": 0},
    )
    if not doc:
        return None
    project_ids = doc.get("project_ids") or []
    return project_ids[0] if project_ids else None


def _list_keys(created_at, call_id):
    """
    Derive the list-row key from the call's CreatedAt + CallId.
    6 shards/day, 4 hours each (24 / 6 = 4).
        list_pk = "cls#{date}#s#{hour//4:02d}"
        list_sk = "ts#{CreatedAt}#id#{CallId}"
    """
    date  = created_at[0:10]
    hour  = int(created_at[11:13])
    shard = f"{hour // 4:02d}"
    return f"cls#{date}#s#{shard}", f"ts#{created_at}#id#{call_id}"


def tag_from_record(record):
    """
    Process one stream record. Returns one of: "tagged", "skip", "no_project".
    Never raises for expected conditions — callers still wrap it defensively.
    """
    event_name = record.get("eventName")
    if event_name not in ("INSERT", "MODIFY"):
        return "skip"   # REMOVE, etc.

    image = record.get("dynamodb", {}).get("NewImage")
    if not image:
        return "skip"

    pk = image.get("PK", {}).get("S", "")
    sk = image.get("SK", {}).get("S", "")

    # Only the call record drives tagging (PK == SK == "c#" + CallId).
    if not pk.startswith("c#") or pk != sk:
        return "skip"

    # Loop guard / idempotency: already tagged → do nothing. This is what stops
    # our own tag write from re-triggering endlessly.
    if image.get("ProjectId", {}).get("S"):
        return "skip"

    created_at = (image.get("CreatedAt") or image.get("createdAt") or {}).get("S")
    if not created_at:
        print(f"project-tag: no CreatedAt on {pk} — cannot derive list row; skip")
        return "skip"

    call_id    = pk[2:]                     # strip the "c#" prefix
    project_id = _project_id_for_call(call_id)
    if not project_id:
        # Board may not have the meeting/project yet (created before it went
        # ACTIVE), or the meeting simply isn't linked to a project.
        print(f"project-tag: no project for call_id={call_id} — nothing to tag")
        return "no_project"

    list_pk, list_sk = _list_keys(created_at, call_id)

    # Tag BOTH rows in ONE transaction — attribute_exists guards against
    # creating phantom rows; if either is missing the whole write is cancelled.
    _ddb.transact_write_items(TransactItems=[
        {"Update": {
            "TableName":           _TABLE_NAME,
            "Key":                 {"PK": {"S": pk}, "SK": {"S": sk}},
            "UpdateExpression":    "SET ProjectId = :p",
            "ExpressionAttributeValues": {":p": {"S": project_id}},
            "ConditionExpression": "attribute_exists(PK)",
        }},
        {"Update": {
            "TableName":           _TABLE_NAME,
            "Key":                 {"PK": {"S": list_pk}, "SK": {"S": list_sk}},
            "UpdateExpression":    "SET ProjectId = :p",
            "ExpressionAttributeValues": {":p": {"S": project_id}},
            "ConditionExpression": "attribute_exists(PK)",
        }},
    ])
    print(f"project-tag: c#{call_id} + {list_pk} / {list_sk} → ProjectId={project_id}")
    return "tagged"
