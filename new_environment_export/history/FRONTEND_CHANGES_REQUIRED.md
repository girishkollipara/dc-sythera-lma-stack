# Frontend Changes Required

_Last updated: 2026-07-20 · AppSync API `lo3gpjswjjdqrh6xn5xkgipeme` · us-east-1_

Two independent changes:

| # | What | Where it shows |
|---|---|---|
| **1** | Show real names instead of `microsoftentraid_…` | Meetings list + meeting detail |
| **2** | Bot joins as `Bala's AI Assistant`, not `LMA (email)` | Inside the Teams/Zoom meeting |

---

# CHANGE 1 — Show Names Instead of GUIDs

## The problem

The meetings list renders raw identifiers:

| Column | Currently bound to | Shows |
|---|---|---|
| Owner Name | `AgentId` | sometimes an email, sometimes `microsoftentraid_…`, sometimes a meeting name |
| Owner Email | `Owner` | `microsoftentraid_7d48b272-2665-4ef5-ae4e-40b3459ce6f1` |
| Shared With | `SharedWith` | `[microsoftentraid_c51b9034-…]` |

`Owner` and `SharedWith` hold **Cognito usernames** because that is what the server compares for
authorization. `AgentId` is unreliable — it holds three different formats across records.

## What the backend has already done

Four new fields are **live on the API now** and populated on all 259 existing meetings:

```
OwnerName          "Bala Yogeswara Singh Bondili"
OwnerEmail         "yogeswarsingh.bondili@dimensioncg.com"
SharedWithNames    ["Ashiesh Jaitly"]
SharedWithEmails   ["ashiesh.jaitly@snaddevelopers.com"]
```

They exist on both the `Call` type (detail view) and `CallListItem` type (list view).

> `Owner` / `SharedWith` are **unchanged** — they still hold Cognito usernames and are still what the
> server authorizes against. Do **not** stop sending them, and do **not** use the new fields for any
> permission logic.

## Frontend change

**GraphQL only returns fields you explicitly select** — the data is there, but your query must ask
for it.

### 1. `getCall` query — add four fields

```graphql
query Query($callId: ID!) {
  getCall(CallId: $callId) {
    CallId
    AgentId
    Owner                 # keep - used for ownership logic
    SharedWith            # keep - used for ownership logic
    OwnerName             # ADD
    OwnerEmail            # ADD
    SharedWithNames       # ADD
    SharedWithEmails      # ADD
    CallSummaryText
    CreatedAt
    Status
    RecordingUrl
    TotalConversationDurationMillis
    # ...rest unchanged
  }
}
```

### 2. `listCalls` query — add the same four fields

`CallListItem` carries them too, so the list view needs no extra round-trip.

```graphql
query ListCalls($startDateTime: AWSDateTime, $endDateTime: AWSDateTime) {
  listCalls(startDateTime: $startDateTime, endDateTime: $endDateTime) {
    Calls {
      CallId
      Owner
      SharedWith
      OwnerName             # ADD
      OwnerEmail            # ADD
      SharedWithNames       # ADD
      SharedWithEmails      # ADD
      CreatedAt
      # ...rest unchanged
    }
    nextToken
  }
}
```

### 3. Re-bind the table columns

| Column | Change from | Change to |
|---|---|---|
| Owner Name | `AgentId` | `OwnerName` |
| Owner Email | `Owner` | `OwnerEmail` |
| Shared With | `SharedWith` | `SharedWithNames` (join with `", "`) |

### 4. Fallback (important)

A few records cannot be resolved (legacy owners stored as `yogi` / `venu`, ~78 rows). Those have no
`OwnerName`, so always fall back:

```js
const ownerLabel = call.OwnerName || call.OwnerEmail || call.Owner;
const sharedLabel = (call.SharedWithNames?.length ? call.SharedWithNames : call.SharedWith) ?? [];
```

Never render a bare `OwnerName` without the fallback, or those rows show blank.

## Also worth fixing while you're here

**Paging.** `listCalls` uses a DynamoDB `Scan` and filters afterwards, so a page can legitimately
return **zero meetings with a non-null `nextToken`**. Treat that as "keep paging", not "no results" —
otherwise meetings appear to be missing.

```js
while (result.Calls.length === 0 && result.nextToken) {
  result = await fetchNextPage(result.nextToken);
}
```

**Empty-transcript meetings.** Many completed meetings have no call record at all — the bot joined
but was never admitted, so nothing was transcribed. `getCall` returns nothing and the UI currently
shows *"failed to get call details - please try again later"*, which is misleading. Prefer:
*"No transcript was recorded for this meeting."*

---

# CHANGE 2 — Join Now: Bot Display Name

## The problem

The two ways of starting a bot produce different names in the meeting:

| Path | Name shown in Teams/Zoom |
|---|---|
| **Scheduled meeting** | `Bala's AI Assistant` ✅ |
| **Join Now** | `LMA (yogeswarsingh.bondili@dimensioncg.com)` ❌ |

**Why.** Join Now calls the Step Function directly. It sets only `LMA_USER` from what the frontend
sends, and never sets `LMA_IDENTITY` — so the container falls back to its default template
`LMA ({LMA_USER})`. The scheduled path resolves a proper display name in the VPScheduler Lambda.

## Current Join Now payload

The state machine reads these from `$.data`:

```
meetingPlatform  meetingID  meetingPassword  meetingName
userName         <- becomes LMA_USER  (currently the user's email)
accessToken      idToken   rereshToken        (note: existing typo in the field name)
virtualParticipantId
```

## Frontend change — send one extra field

```diff
  {
    "data": {
      "meetingPlatform": "TEAMS",
      "meetingID": "215854814724985",
      "meetingPassword": "…",
      "meetingName": "Sprint Planning",
      "userName": "yogeswarsingh.bondili@dimensioncg.com",
+     "userDisplayName": "Bala's AI Assistant",
      "accessToken": "…",
      "idToken": "…",
      "rereshToken": "…",
      "virtualParticipantId": "…"
    }
  }
```

- **Keep `userName` as the email.** It feeds the spoken intro message
  (*"…invited by yogeswarsingh.bondili@… to join this meeting"*). Do not change it.
- **`userDisplayName`** becomes the name shown in the participant list.

### How to build `userDisplayName`

Use the **same rule as the backend**, so both paths agree:

```
1. Cognito `given_name`, FIRST WORD only
     "Bala Yogeswara Singh"  ->  "Bala"
2. else the email's local part before the first "."
     "ashiesh.jaitly@…"      ->  "Ashiesh"
3. capitalise the first letter only
     "vamsikrishna"          ->  "Vamsikrishna"
4. append "'s AI Assistant"
     ->  "Bala's AI Assistant"
```

You already send `idToken` in this payload, and **ID tokens contain `given_name`** — so the frontend
can read it directly from the decoded token. (Note: the *access* token does **not** carry it.)

If nothing resolves, send `LMA ({email})` to match today's behaviour rather than an empty string.

## Backend change — ALREADY DEPLOYED (2026-07-20)

The Step Function now reads the field and passes it to the container as `LMA_IDENTITY`.

It is guarded, so it is safe **whether or not** you send the field:

```
Identity Choice
  ├── userDisplayName present  ->  RunTaskWithIdentity   (uses your value)
  └── userDisplayName absent   ->  RunTask               (original behaviour, unchanged)
```

Both the Join Now path (`RunTask`) and this state machine's own scheduling path
(`CreateSchedule`) are covered.

> **No coordination needed.** The backend is live and backwards compatible — today every request
> takes the original path. The moment you start sending `userDisplayName`, the guard routes to the
> identity branch automatically. Ship whenever you are ready; there is no breaking window.

---

# Summary

| Change | Frontend work | Backend status |
|---|---|---|
| Add 4 fields to `getCall` query | small | ✅ live |
| Add 4 fields to `listCalls` query | small | ✅ live |
| Re-bind 3 table columns | small | ✅ live |
| Fallback chain for unresolved owners | small | n/a |
| Handle empty page + `nextToken` | small | n/a (pre-existing) |
| Send `userDisplayName` on Join Now | small | ✅ live (guarded, backwards compatible) |

Nothing here changes authorization. `Owner` and `SharedWith` remain the permission fields and must
keep being requested.
