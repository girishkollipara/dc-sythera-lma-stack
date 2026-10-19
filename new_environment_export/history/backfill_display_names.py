#!/usr/bin/env python3
"""Backfill display-name attributes onto LMA meeting records.

WHY
---
`Owner` and `SharedWith` store Cognito usernames (``microsoftentraid_<oid>``) because
that is what the AppSync resolvers compare against for authorization. Those strings
are unreadable in the UI.

This script adds *display-only* attributes alongside them:

    OwnerName        / OwnerEmail
    SharedWithNames  / SharedWithEmails

The authorization fields (`Owner`, `SharedWith`) are NEVER modified.

Both records of a meeting are updated - the call record (``c#``) which backs the
detail view, and the list row (``cls#``) which backs the meetings list - so names
show in both places.

Historic `Owner` values come in three shapes (created by different code paths):
    microsoftentraid_<oid>   scheduled path      -> resolved by username
    an email address         join-now path       -> resolved by email
    a bare name ('yogi')     legacy, pre-16 June -> best-effort, else left alone

USAGE
-----
    python backfill_display_names.py            # preview only, writes nothing
    python backfill_display_names.py --apply    # perform the writes
"""

import argparse
import sys
from collections import defaultdict

import boto3

REGION = "us-east-1"
POOL_ID = "us-east-1_pOCRCo1Wx"
TABLE = "LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
cognito = boto3.client("cognito-idp", region_name=REGION)
table = dynamodb.Table(TABLE)


def build_directory():
    """One pass over the user pool -> lookup maps for every identifier shape."""
    by_username, by_email, by_firstname = {}, {}, {}

    for page in cognito.get_paginator("list_users").paginate(UserPoolId=POOL_ID):
        for user in page["Users"]:
            attrs = {a["Name"]: a["Value"] for a in user["Attributes"]}
            email = attrs.get("email", "")
            name = attrs.get("name") or " ".join(
                x for x in (attrs.get("given_name"), attrs.get("family_name")) if x
            )
            # Never leave a blank display value - fall back down the chain.
            display = name or email or user["Username"]
            entry = {"name": display, "email": email}

            by_username[user["Username"]] = entry
            if email:
                # Prefer the federated account when an email has duplicates: it is the
                # one that carries the name attribute.
                if email.lower() not in by_email or user["Username"].startswith("microsoftentraid_"):
                    by_email[email.lower()] = entry
                local = email.split("@")[0].split(".")[0].lower()
                by_firstname.setdefault(local, entry)

    return by_username, by_email, by_firstname


def resolve(value, by_username, by_email, by_firstname):
    """Map a stored Owner/SharedWith value to {name, email}, or None if unresolvable."""
    if not value:
        return None
    v = str(value).strip()
    if v in by_username:
        return by_username[v]
    if "@" in v and v.lower() in by_email:
        return by_email[v.lower()]
    # Legacy bare names such as 'yogi' / 'tarun'.
    return by_firstname.get(v.lower())


def parse_shared_with(value):
    """SharedWith is stored inconsistently - a list, or a comma/bracket string."""
    if not value:
        return []
    if isinstance(value, (list, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip().strip("[]")
    return [p.strip().strip("'\"") for p in text.split(",") if p.strip()]


def scan_meeting_rows():
    """Every call record and list row in the table."""
    rows, kwargs = [], {}
    while True:
        resp = table.scan(
            ProjectionExpression="PK,SK,#o,SharedWith,OwnerName,OwnerEmail",
            ExpressionAttributeNames={"#o": "Owner"},
            **kwargs,
        )
        for item in resp["Items"]:
            pk = str(item.get("PK", ""))
            if pk.startswith("c#") or pk.startswith("cls#"):
                rows.append(item)
        if "LastEvaluatedKey" not in resp:
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform writes (default: preview only)")
    args = ap.parse_args()

    print("Building user directory from Cognito ...")
    by_username, by_email, by_firstname = build_directory()
    print(f"  {len(by_username)} users, {len(by_email)} distinct emails\n")

    print("Scanning meeting records ...")
    rows = scan_meeting_rows()
    print(f"  {len(rows)} rows (call records + list rows)\n")

    planned, unresolved = [], defaultdict(int)

    for item in rows:
        owner = item.get("Owner")
        who = resolve(owner, by_username, by_email, by_firstname)

        updates = {}
        if who:
            if item.get("OwnerName") != who["name"]:
                updates["OwnerName"] = who["name"]
            if who["email"] and item.get("OwnerEmail") != who["email"]:
                updates["OwnerEmail"] = who["email"]
        elif owner:
            unresolved[str(owner)] += 1

        shared = parse_shared_with(item.get("SharedWith"))
        if shared:
            names, emails = [], []
            for entry in shared:
                r = resolve(entry, by_username, by_email, by_firstname)
                names.append(r["name"] if r else entry)
                if r and r["email"]:
                    emails.append(r["email"])
            if names:
                updates["SharedWithNames"] = names
            if emails:
                updates["SharedWithEmails"] = emails

        if updates:
            planned.append((item["PK"], item["SK"], updates))

    # ---- report -------------------------------------------------------------
    print(f"rows needing update : {len(planned)}")
    calls = sum(1 for pk, _, _ in planned if str(pk).startswith("c#"))
    print(f"   call records (c#) : {calls}")
    print(f"   list rows  (cls#) : {len(planned) - calls}\n")

    print("sample of planned changes:")
    for pk, _sk, upd in planned[:12]:
        label = str(pk)[:52]
        bits = ", ".join(f"{k}={v!r}" for k, v in upd.items())
        print(f"  {label:<54} {bits[:88]}")

    if unresolved:
        print(f"\nUNRESOLVABLE owners ({len(unresolved)} distinct) - left untouched:")
        for value, count in sorted(unresolved.items(), key=lambda x: -x[1])[:10]:
            print(f"  {value[:48]:<50} {count} row(s)")

    if not args.apply:
        print("\nPREVIEW ONLY - nothing written. Re-run with --apply to perform these updates.")
        return

    # ---- apply --------------------------------------------------------------
    print(f"\nApplying {len(planned)} updates ...")
    done = failed = 0
    for pk, sk, upd in planned:
        names = {f"#{i}": k for i, k in enumerate(upd)}
        values = {f":{i}": v for i, (_, v) in enumerate(upd.items())}
        expr = "SET " + ", ".join(f"#{i} = :{i}" for i in range(len(upd)))
        try:
            table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(PK)",
            )
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAILED {str(pk)[:46]}: {str(exc)[:70]}")

    print(f"\nupdated: {done} | failed: {failed}")
    print("Authorization fields (Owner / SharedWith) were not modified.")


if __name__ == "__main__":
    sys.exit(main())
