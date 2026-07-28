"""Enrich new meeting records with human-readable owner details.

Triggered by the EventSourcing table's DynamoDB stream. When a meeting is created,
`Owner` / `SharedWith` hold Cognito usernames (``microsoftentraid_<oid>``) because that
is what the AppSync resolvers authorize against. Those strings are unreadable in the UI,
and a VTL resolver cannot call Cognito to translate them.

This function resolves them once, at write time, and stores display-only attributes
alongside:

    OwnerName / OwnerEmail / SharedWithNames / SharedWithEmails

The authorization fields are never modified.

Only INSERT events for call records (``c#``) and list rows (``cls#``) reach this
function - the event source mapping filters out transcript segments (~98% of table
writes) and all MODIFY events. The MODIFY exclusion also prevents this function's own
writes from re-triggering it.

This function ALSO tags meetings with their ProjectId (see ``project_tagger``): it
looks the meeting's project up in MongoDB (active_meetings_board, by CallId) and
writes ``ProjectId`` onto the call + list records so project members can see it.
That tagging is event-type aware and idempotent (it skips already-tagged rows), so
it stays safe even if MODIFY events are ever delivered.
"""

import os
import time

import boto3

import project_tagger

TABLE_NAME = os.environ["TABLE_NAME"]
USER_POOL_ID = os.environ["USER_POOL_ID"]
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "900"))

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
table = dynamodb.Table(TABLE_NAME)

# Directory cached across invocations - the pool changes far more slowly than meetings
# are created, so this usually costs one Cognito call per container lifetime.
_cache = {"expires": 0.0, "by_username": {}, "by_email": {}, "by_firstname": {}}


def _load_directory():
    now = time.time()
    if now < _cache["expires"] and _cache["by_username"]:
        return _cache

    by_username, by_email, by_firstname = {}, {}, {}
    paginator = cognito.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=USER_POOL_ID):
        for user in page["Users"]:
            attrs = {a["Name"]: a["Value"] for a in user["Attributes"]}
            email = attrs.get("email", "")
            name = attrs.get("name") or " ".join(
                x for x in (attrs.get("given_name"), attrs.get("family_name")) if x
            )
            entry = {"name": name or email or user["Username"], "email": email}

            by_username[user["Username"]] = entry
            if email:
                key = email.lower()
                # Prefer the federated account when an email has duplicate rows: it is
                # the one carrying the name attribute.
                if key not in by_email or user["Username"].startswith("microsoftentraid_"):
                    by_email[key] = entry
                by_firstname.setdefault(key.split("@")[0].split(".")[0], entry)

    _cache.update(
        expires=now + CACHE_TTL_SECONDS,
        by_username=by_username,
        by_email=by_email,
        by_firstname=by_firstname,
    )
    return _cache


def _resolve(value, directory):
    """Map a stored Owner/SharedWith value to {name, email}, or None."""
    if not value:
        return None
    v = str(value).strip()
    if v in directory["by_username"]:
        return directory["by_username"][v]
    if "@" in v:
        return directory["by_email"].get(v.lower())
    # Legacy bare names such as 'yogi' / 'tarun'.
    return directory["by_firstname"].get(v.lower())


def _parse_shared_with(image):
    """SharedWith is stored inconsistently - a DynamoDB list, or a bracketed string."""
    raw = image.get("SharedWith")
    if not raw:
        return []
    if "L" in raw:
        return [x.get("S", "").strip() for x in raw["L"] if x.get("S", "").strip()]
    text = str(raw.get("S", "")).strip().strip("[]")
    return [p.strip().strip("'\"") for p in text.split(",") if p.strip()]


def handler(event, context):
    directory = _load_directory()
    updated = skipped = failed = 0
    tagged = 0

    for record in event.get("Records", []):
        # ── Project tagging (independent of owner enrichment) ──────────
        # Look up the meeting's project in MongoDB and tag its c#/cls# records.
        # Event-type aware + idempotent (skips already-tagged rows) so our own
        # write never re-triggers this. Never fails the batch.
        try:
            if project_tagger.tag_from_record(record) == "tagged":
                tagged += 1
        except Exception as exc:  # noqa: BLE001
            print(f"project-tag failed: {exc}")

        try:
            image = record["dynamodb"]["NewImage"]
            pk = image["PK"]["S"]
            sk = image["SK"]["S"]

            updates = {}

            owner = image.get("Owner", {}).get("S")
            who = _resolve(owner, directory)
            if who:
                updates["OwnerName"] = who["name"]
                if who["email"]:
                    updates["OwnerEmail"] = who["email"]
            elif owner:
                print(f"unresolved owner {owner!r} on {pk}")

            shared = _parse_shared_with(image)
            if shared:
                names, emails = [], []
                for entry in shared:
                    r = _resolve(entry, directory)
                    names.append(r["name"] if r else entry)
                    if r and r["email"]:
                        emails.append(r["email"])
                updates["SharedWithNames"] = names
                if emails:
                    updates["SharedWithEmails"] = emails

            if not updates:
                skipped += 1
                continue

            names_map = {f"#{i}": k for i, k in enumerate(updates)}
            values_map = {f":{i}": v for i, (_, v) in enumerate(updates.items())}
            expression = "SET " + ", ".join(f"#{i} = :{i}" for i in range(len(updates)))

            table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression=expression,
                ExpressionAttributeNames=names_map,
                ExpressionAttributeValues=values_map,
                # The record must still exist; never resurrect a deleted row.
                ConditionExpression="attribute_exists(PK)",
            )
            updated += 1

        except Exception as exc:  # noqa: BLE001
            # Never fail the batch: a display name is not worth blocking the stream or
            # forcing retries that would stall later records.
            failed += 1
            print(f"enrichment failed: {exc}")

    print(f"updated={updated} skipped={skipped} failed={failed} tagged={tagged}")
    return {"updated": updated, "skipped": skipped, "failed": failed, "tagged": tagged}
