# Project-Based Meeting Sharing — Backend Integration Spec

_Last updated: 2026-07-20 · Account 528757797189 · us-east-1_

**Who this is for:** developers working on the **Synthera / MongoDB backend**. This is the reference
for the changes your repos need.

**Status:** the AWS side (AppSync resolvers, membership table, IAM) is **already deployed and
verified working**. It is **inert** — it does nothing until your backend writes the two pieces of
data described below. Nothing here risks existing behaviour: until this data exists, meetings behave
exactly as they do today (owner + `SharedWith` only).

> **Verified 2026-07-20:** a real non-owner was granted access to a meeting purely by writing this
> data — no code changes. The mechanism works. See `PROJECT_ACCESS_STATUS.md` §9.

---

## 1. What You Are Building

LMA's access rule is now:

> **owner** OR **individually shared** OR **(on the meeting's project AND not blocked)**

The third clause needs **two facts**, and your backend owns both:

| # | Fact | Where it goes | When you write it |
|---|---|---|---|
| **A** | *"this meeting belongs to project X"* | `ProjectId` on the meeting's **2 records** | when a meeting is linked to a project |
| **B** | *"this user is on projects X, Y"* | a row in `LMASA-ProjectMembership` | when project membership changes |

Both must exist for a user to gain access. Either missing = no project access (fails safe).

---

## 2. The Identifier — Get This Right First

Everything is keyed by the **Cognito username**, *not* email, *not* your Mongo `_id`.

For Entra-federated users it looks like:

```
microsoftentraid_7d48b272-2665-4ef5-ae4e-40b3459ce6f1
```

This is the exact string in `ctx.identity.username`, and the same string already used in the
meetings' `Owner` and `SharedWith` fields.

**Required change:** store each user's Cognito username on your Mongo user document.

Resolve it once, from their email:

```python
resp = cognito.list_users(
    UserPoolId="us-east-1_pOCRCo1Wx",
    Filter=f'email = "{email}"',
    Limit=1,
)
cognito_username = resp["Users"][0]["Username"]   # microsoftentraid_<oid>
```

> This is the same pattern as `resolve_usernames()` in `index.py`. Do it once at user
> creation/sync and persist it — do not call Cognito on every request.

> ⚠️ **Some users have two Cognito accounts** (one federated `microsoftentraid_*`, one native with an
> email-style username). `list_users` by email may return either. Prefer the `microsoftentraid_*`
> one, since that is what federated logins present.

---

## 3. Fact B — Membership Mirror

### Table

```
LMASA-ProjectMembership          (us-east-1, on-demand)
arn:aws:dynamodb:us-east-1:528757797189:table/LMASA-ProjectMembership
```

**Key schema:** `PK` (String) — **partition key only, no sort key.**

### Item shape

```json
{
  "PK": "u#microsoftentraid_7d48b272-2665-4ef5-ae4e-40b3459ce6f1",
  "ProjectIds": ["proj_aeccar", "proj_anatomy"]
}
```

- `PK` = literal `u#` + the Cognito username
- `ProjectIds` = **the user's complete project list** (a String Set or List)

### Rules

1. **Write the FULL set every time — never append/remove incrementally.** Rewriting the whole list
   makes the operation idempotent and self-healing: a replay or retry can never corrupt it.
2. **Write MongoDB first, then the mirror.** Mongo is the source of truth; the mirror is a derived
   cache. If the mirror write fails, you have a stale cache — recoverable. The reverse is not.
3. **Trigger on every membership change:** member added, member removed, user deleted, project
   deleted.
4. **Removing a user's last project:** write `ProjectIds: []` (or delete the row). Both are safe —
   the resolver treats a missing row as "no projects."

```python
table.put_item(Item={
    "PK": f"u#{cognito_username}",
    "ProjectIds": current_project_ids,     # the complete list, from Mongo
})
```

### One-time backfill

Iterate every existing project member and write their row. Idempotent, so safe to re-run.

---

## 4. Fact A — Tag the Meeting

### Table

```
LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE
```

### ⚠️ A meeting is TWO records — you must write BOTH

| Record | Purpose | Read by |
|---|---|---|
| **call record** (`c#…`) | the meeting itself — summary, recording | `getCall` (opening a meeting) |
| **list row** (`cls#…`) | index entry for browsing by date | `listCalls` (the meetings list) |

**Each resolver only reads its own record.** So a partial write produces broken, confusing states:

| Tagged | User experience |
|---|---|
| both ✅ | appears in list **and** opens — correct |
| only call record | **invisible in the list**, but opens by direct link |
| only list row | **appears in list**, but "access denied" on open |

Both partial states are silent — no error explains them.

### Key construction

**Call record** — PK and SK are identical:

```
PK = "c#" + CallId
SK = "c#" + CallId
```

**List row** — derived from the call's `CreatedAt` (ISO-8601, e.g. `2026-07-15T15:21:23.135Z`):

```python
date  = created_at[0:10]                    # "2026-07-15"
hour  = int(created_at[11:13])              # 15
shard = f"{hour // 4:02d}"                  # "03"   (6 shards/day, 4 hours each)

list_pk = f"cls#{date}#s#{shard}"           # "cls#2026-07-15#s#03"
list_sk = f"ts#{created_at}#id#{call_id}"   # "ts#2026-07-15T15:21:23.135Z#id#<CallId>"
```

> This mirrors exactly what the `createCall` resolver does. `shardsInDay = 6`, so
> `shardDivider = 24/6 = 4` and `hourShard = hour / 4` (integer division).
>
> **Do not scan to find the list row** — it is fully calculable from `CreatedAt` + `CallId`.
> Read the call record first to get its `CreatedAt`.

### Write both rows in ONE transaction

**This is the most important rule in this document.**

```python
dynamodb.transact_write_items(TransactItems=[
    {"Update": {
        "TableName": TABLE,
        "Key": {"PK": {"S": f"c#{call_id}"}, "SK": {"S": f"c#{call_id}"}},
        "UpdateExpression": "SET ProjectId = :p",
        "ExpressionAttributeValues": {":p": {"S": project_id}},
        "ConditionExpression": "attribute_exists(PK)",
    }},
    {"Update": {
        "TableName": TABLE,
        "Key": {"PK": {"S": list_pk}, "SK": {"S": list_sk}},
        "UpdateExpression": "SET ProjectId = :p",
        "ExpressionAttributeValues": {":p": {"S": project_id}},
        "ConditionExpression": "attribute_exists(PK)",
    }},
])
```

Both succeed or neither does — a half-tagged meeting becomes impossible.

> **Precedent:** the existing `createCall` resolver already writes these same two rows with
> `TransactWriteItems`. Follow that pattern.

The `ConditionExpression` guards against tagging a meeting whose records don't exist (see §7).

### Unlinking

Same transaction, using `REMOVE ProjectId` on both rows.

### One project per meeting

`ProjectId` is a single string. A meeting cannot belong to two projects without a schema change.

---

## 5. Optional — Per-Meeting Block List

To block one user from **one** meeting while they keep the rest of the project:

```
ExcludedUsers = ["microsoftentraid_<oid>", ...]     # Cognito usernames
```

Write it to **both rows**, same transaction rule.

> An explicit `SharedWith` share **overrides** a project block — that precedence is intentional.

---

## 6. Frontend Change (small but required for live streaming)

Add **`ProjectId`** to the selection set of the `onAddTranscriptSegment` subscription and the
transcript queries.

- **Without it:** project members see live meetings only **after a refresh**.
- **With it:** they receive **real-time** streaming.

`Owner` and `SharedWith` are unchanged, so adding this later breaks nothing.

---

## 7. Edge Cases You Must Handle

**Meetings with no records.** Many completed meetings have **no call record at all** — the bot joined
but was never admitted to the meeting, so nothing was transcribed. There is nothing to tag.
Your "Link to Project" flow must handle this (the `ConditionExpression` above will fail cleanly).

**90-day TTL.** All meeting records auto-delete **90 days** after creation (`ExpiresAfter`
attribute). Consequences:
- Project members can only ever see meetings from the **last 90 days**.
- A backfill can only tag meetings that still exist.
- Tagging does **not** extend the lifetime.

**Recurring meetings.** One Teams meeting ID maps to **many** sessions, each with its own `CallId`
and its own records. Link **per session**, not per meeting ID.

**Timing.** A meeting's records only exist **after** the call starts. If you tag at
schedule-time, the records won't be there yet — tag when the meeting is linked *and* exists, or
re-tag after creation.

---

## 8. Reconcile Job (Required for Robustness)

The mirror is a derived cache with no automatic repair. A dual-write can fail silently and leave
wrong access in place indefinitely.

**Build a periodic job (e.g. hourly/nightly) that:**

1. Rebuilds every membership row from Mongo (full-set writes — idempotent).
2. Audits meeting tags: for each meeting with a `ProjectId` on its call record, verify the list row
   has the **same** `ProjectId` — and vice versa. Report/repair mismatches.
3. Removes membership rows for users no longer in Mongo.

Item 2 is your safety net if anything ever writes these records outside your normal path.

---

## 9. How to Verify It Works

1. Pick a meeting **you do not own** and are **not shared on**.
2. Tag it with a project (both rows).
3. Add yourself to that project in the mirror.
4. The meeting should now appear in your list **and** open, with transcript and summary.
5. Remove your membership row → access disappears on next read.

This exact test was performed on 2026-07-20 and passed.

---

## 10. Known Limitation (Not Your Problem to Fix, But Be Aware)

`listCalls` uses a DynamoDB **`Scan`**, not a `Query`, and filters afterwards. The table is ~98.6%
transcript rows, so it reads ~100 items to surface ~0.7 meetings — roughly **27 pages to show 20
meetings**, and a page can return **zero meetings with a non-null `nextToken`**.

**Your frontend must treat "empty page + nextToken" as "keep paging"**, or project meetings will
appear to be missing.

The real fix is a sparse **GSI on `ProjectId`**, which must be added via CloudFormation
(the table is managed by stack `LMASA-AISTACK-4VKHCQQY3CJR`). Tracked separately.

---

## 11. Quick Reference

```
# Identifier
cognito_username = "microsoftentraid_<entra-oid>"        # NOT email

# Membership  (table: LMASA-ProjectMembership)
PK          = "u#" + cognito_username
ProjectIds  = [ ...complete list... ]                    # full-set write

# Meeting tag (table: LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE)
call record : PK = SK = "c#" + CallId
list row    : PK = "cls#{date}#s#{hour//4:02d}"
              SK = "ts#{CreatedAt}#id#{CallId}"
attribute   : ProjectId = "<project id>"                 # BOTH rows, ONE transaction

# Optional block
ExcludedUsers = [cognito_username, ...]                  # BOTH rows

# Order
1. write MongoDB (source of truth)
2. write DynamoDB mirror / tag
3. reconcile job repairs drift
```
