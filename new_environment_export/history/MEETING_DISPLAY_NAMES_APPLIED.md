# Meeting Owner & Shared-With Display Names — Resources Changed

_Applied: 2026-07-20 · Account 528757797189 · us-east-1_

**Scope:** showing **people's names** (owners, shared-with users) in the LMA web UI instead of raw
Cognito identifiers.

**Not in scope — separate workstreams:**
- The *bot's* display name inside a meeting → [VP_CHANGES_APPLIED.md](VP_CHANGES_APPLIED.md)
- Who is *allowed to see* a meeting → [PROJECT_ACCESS_STATUS.md](PROJECT_ACCESS_STATUS.md) and
  [BACKEND_INTEGRATION_SPEC.md](BACKEND_INTEGRATION_SPEC.md)

---

## 1. The Problem

The meetings list and detail view rendered raw identifiers:

| Column | Bound to | Displayed |
|---|---|---|
| Owner Name | `AgentId` | inconsistent — email, GUID, or even a meeting name |
| Owner Email | `Owner` | `microsoftentraid_7d48b272-2665-4ef5-ae4e-40b3459ce6f1` |
| Shared With | `SharedWith` | `[microsoftentraid_c51b9034-…]` |

`Owner` and `SharedWith` hold **Cognito usernames** because that is what the AppSync resolvers
compare for authorization (`$item.Owner == $ctx.identity.username`).

**They cannot be changed to emails or names.** The resolver receives no `email` claim — the frontend
authenticates with a Cognito **access token**, which never carries one. Storing emails in `Owner`
would break access for every user. (Same constraint documented in
[VP_DISPLAY_NAME_FIX.md §4b](VP_DISPLAY_NAME_FIX.md).)

**Solution:** keep the auth fields untouched; add *display-only* attributes alongside them.

```
Owner            = microsoftentraid_7d48b272-…      <- authorization (unchanged)
OwnerName        = "Bala Yogeswara Singh Bondili"   <- display
OwnerEmail       = "yogeswarsingh.bondili@…"        <- display
SharedWithNames  = ["Ashiesh Jaitly"]               <- display
SharedWithEmails = ["ashiesh.jaitly@…"]             <- display
```

### Data inconsistency discovered along the way

`Owner` was found stored in three formats:

| Format | Count | Source |
|---|---|---|
| `microsoftentraid_<oid>` | 157 | correct — the Cognito username |
| an email address | 9 | **a regression we introduced** — see §1a |
| a bare name (`yogi`, `venu`) | 93 | legacy, stopped 16 June (pre-existing) |

The email-owned records were an **access bug**: `Owner != ctx.identity.username`, so those users
could not see their own meetings. `VRSE Weekly Call` was owned by
`ashiesh.jaitly@snaddevelopers.com` whose username is `microsoftentraid_c51b9034-…`.

**This has now been fixed (§1a). All 18 affected rows repaired; 0 email-owned rows remain.**

---

## 1a. The Ownership Regression — Root Cause, Fix, Repair

### How ownership is set (`CallEventProcessor`)

```python
owner = get_owner_from_jwt(message.get("AccessToken"))
if owner is None:                                    # token missing/expired/invalid
    owner = message.get("AgentId", "system@lma.aws") # fall back to AgentId
```

`AgentId` traces back through the container to the `LMA_USER` env var:

```
LMA_USER  ->  START event AgentId  ->  owner  (when the token fails)
```

**Scheduled meetings never carry a token** (no user session when EventBridge fires), so the fallback
**always** fires — meaning `LMA_USER` alone decides ownership.

### The regression (self-inflicted)

On **2026-07-16**, the VP *display-name* fix set `LMA_USER` to the user's **email** (to feed the
participant name / intro). That value also flows into `AgentId` → `Owner`. So 8 scheduled meetings
created after that change were owned by an email instead of the username. One older join-now meeting
was affected separately (§ below). This is the classic "one variable serving two jobs" trap — the
container source even warns: `AgentId: details.lmaUser, // used as owner by CallEventProcessor`.

### The fix (VPScheduler, deployed 2026-07-20)

`resolve_vp_identity()` now returns **three** separate values, so a display change can never touch
ownership again:

| Env var | Value | Feeds |
|---|---|---|
| `LMA_USER` | the **Cognito username** | `AgentId` → `Owner` ✅ |
| `LMA_IDENTITY` | `<First>'s AI Assistant` | participant list |
| `INTRO_MESSAGE` | pre-expanded with the **full name** | spoken intro |

The intro is expanded inside VPScheduler (new env var `INTRO_MESSAGE_TEMPLATE`) so the name reaches
the intro without passing through `LMA_USER`. Backup: `vp_fix_backups/VPScheduler_index_UPDATED.py`.
(Also recorded in [VP_CHANGES_APPLIED.md](VP_CHANGES_APPLIED.md), as it is a VP-naming change.)

### The repair (data, 2026-07-20)

All rows with an email `Owner` were rewritten to the correct Cognito username (resolved from email,
preferring the federated account). Both the `c#` and `cls#` row of each meeting were fixed.

```
found 18 rows (9 meetings x 2 records) | repaired 18 | skipped 0
rows still owned by an email: 0
VRSE Weekly Call -> Owner == Ashiesh's login -> True (access restored)
```

Backup: `owner-repair-backup.json`.

### Parked — join-now edge case (NOT fixed, low priority)

The join-now path passes a real access token, so `get_owner_from_jwt` normally succeeds and the owner
is correct **regardless** of `AgentId`. It only falls back to `AgentId` (= the email the frontend
sends as `userName`) when the **token is invalid** — which happens when the token is **already
expired at click time** (access tokens live 1 hour; a user with the app open >1h clicks with a stale
token). Only 1 record hit this.

**To close it (frontend, later):**
1. Send a **fresh token** on click → the token path succeeds (primary fix).
2. Optionally send the **Cognito username** as `userName` → makes the `AgentId` fallback safe too
   (belt-and-suspenders for the rare verification miss).

`Owner` precedence is **token first, `AgentId` only as fallback** — so a fresh token alone is
sufficient in practice.

---

## 2. Resources Changed (4)

### 2.1 DynamoDB data — backfill of existing meetings

| | |
|---|---|
| **Table** | `LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE` |
| **Rows updated** | **450** — 225 call records (`c#`) + 225 list rows (`cls#`) |
| **Attributes added** | `OwnerName`, `OwnerEmail`, and on 90 rows `SharedWithNames`, `SharedWithEmails` |
| **Script** | `backfill_display_names.py` (in this repo, `--apply` to write, preview by default) |
| **Result** | `updated: 450 | failed: 0` |

**Both records are updated deliberately** — the list row backs the meetings list, the call record
backs the detail view. Updating only one gives names in one place and GUIDs in the other.

**Transcript segments (`trs#`) are NOT touched** — ~34,944 rows. Nothing displays a segment's owner,
and writing them would repeat the per-line fan-out that project access was built to eliminate.

**Not resolvable:** 78 rows with legacy owners `yogi` (66) and `venu` (12). Left as-is by choice —
they render as those strings rather than blank. The frontend fallback chain (§3) covers them.

### 2.2 AppSync schema — four new fields

| | |
|---|---|
| **API** | `lo3gpjswjjdqrh6xn5xkgipeme` |
| **Types** | `Call` (detail) and `CallListItem` (list) |
| **Added** | `OwnerName: String`, `OwnerEmail: String`, `SharedWithNames: [String]`, `SharedWithEmails: [String]` |
| **Deploy** | `start_schema_creation` → `SUCCESS`, 102 types |
| **Backup** | `vp_fix_backups/schema-backup-20260720.graphql` |

**No resolver change was needed.** The response templates use `$util.toJson($ctx.result)`, so any
attribute present on the record flows straight through once the schema declares it.

Additive and backwards compatible — existing queries are unaffected.

### 2.3 Lambda `LMASA-MeetingEnrichment` — keeps future meetings correct

| | |
|---|---|
| **Runtime** | Python 3.12 · 256 MB · 60s timeout |
| **Source** | `enrichment_lambda/index.py` (in this repo) |
| **Trigger** | DynamoDB stream on the EventSourcing table |
| **Measured latency** | **3.8s cold, sub-second warm** |

Resolves `Owner` / `SharedWith` against Cognito and writes the display attributes back.

**Event source mapping** — `StartingPosition: LATEST`, `BatchSize: 100`,
`MaximumBatchingWindowInSeconds: 0`, `MaximumRetryAttempts: 2`, UUID
`a5940d5d-07ad-4f01-81ba-2bcb800eb832`.

**Filters (important):**

```json
{"eventName":["INSERT"],"dynamodb":{"NewImage":{"PK":{"S":[{"prefix":"c#"}]}}}}
{"eventName":["INSERT"],"dynamodb":{"NewImage":{"PK":{"S":[{"prefix":"cls#"}]}}}}
```

These do two jobs:
1. **Exclude transcript segments** — ~98.6% of stream traffic. Without the filter the function would
   be invoked ~35,000 extra times.
2. **Prevent an infinite loop** — the function writes back to the same table, which re-fires the
   stream as a `MODIFY` event. Only `INSERT` matches, so its own writes are ignored.

**Failure behaviour:** every record is wrapped in try/except and errors are logged, never raised. A
missing display name must not stall the stream or force retries.

### 2.4 IAM role `LMASA-MeetingEnrichmentRole`

Least privilege — 5 scoped statements:

| Sid | Action | Resource |
|---|---|---|
| `Logs` | CreateLogGroup/Stream, PutLogEvents | account logs |
| `ReadStream` | GetRecords, GetShardIterator, DescribeStream, ListStreams | the table's stream only |
| `WriteDisplayFields` | `dynamodb:UpdateItem` | the table only |
| `ReadDirectory` | `cognito-idp:ListUsers` | pool `us-east-1_pOCRCo1Wx` only |
| `DecryptStream` | `kms:Decrypt`, `kms:DescribeKey` | key `1fed6af6-…` only |

> **`DecryptStream` was not obvious.** The table's stream is KMS-encrypted. Without it the mapping
> silently produced **zero invocations, zero errors, and no log group** — the only evidence was the
> mapping's `LastProcessingResult`:
> `PROBLEM: ... not authorized to perform: kms:Decrypt on ...key/1fed6af6-...`
> If a stream consumer ever appears dead, check there first.

---

## 3. Remaining Work — Frontend

The backend is complete. The UI will keep showing GUIDs until the frontend selects the new fields —
**GraphQL only returns fields explicitly requested.**

Full instructions: **[FRONTEND_CHANGES_REQUIRED.md](FRONTEND_CHANGES_REQUIRED.md)**

Summary:
1. Add `OwnerName`, `OwnerEmail`, `SharedWithNames`, `SharedWithEmails` to the `getCall` and
   `listCalls` queries.
2. Re-bind columns: Owner Name → `OwnerName`, Owner Email → `OwnerEmail`,
   Shared With → `SharedWithNames`.
3. **Keep requesting `Owner` / `SharedWith`** — still the permission fields.
4. Use a fallback chain, or the 78 legacy rows render blank:
   `call.OwnerName || call.OwnerEmail || call.Owner`

---

## 4. Rollback

| To undo | Action |
|---|---|
| Stop enriching new meetings | `delete_event_source_mapping` UUID `a5940d5d-07ad-4f01-81ba-2bcb800eb832` |
| Remove the function | delete Lambda `LMASA-MeetingEnrichment` + role `LMASA-MeetingEnrichmentRole` |
| Revert the schema | re-deploy `vp_fix_backups/schema-backup-20260720.graphql` |
| Remove the backfilled data | `REMOVE OwnerName, OwnerEmail, SharedWithNames, SharedWithEmails` on `c#`/`cls#` rows |

Everything is **additive**. `Owner` and `SharedWith` were never modified, so authorization is
unaffected in either direction — rolling back only removes display fields.

---

## 5. Drift

| Change | Reverted by |
|---|---|
| AppSync schema | CFN deploy of `LMASA-AISTACK-4VKHCQQY3CJR` (`AppSyncSchema` resource) |
| Lambda + role + mapping | **not** CFN-managed — created by CLI, survives stack deploys |
| Backfilled data | nothing — data survives deploys |

The schema is the fragile piece: a stack redeploy drops the four fields, and the frontend queries
would then fail with "cannot query field OwnerName". The Lambda and the data survive.

The durable fix is the fork (see [VP_CHANGES_APPLIED.md §4](VP_CHANGES_APPLIED.md)) — the schema
addition and the enrichment Lambda both belong in the CloudFormation source.

---

## 6. Known Gaps

**The email-owned meetings** (§1a) — **FIXED.** Root cause removed in VPScheduler, all 18 rows
repaired, 0 remain. The only residual is the parked **join-now stale-token edge case** (§1a) — a
frontend fix for later, low priority.

**The 93 legacy `yogi`/`venu` records** are unresolvable to a person. All predate 16 June, so the
90-day TTL removes them by mid-September; migrating them is likely not worth it.

**`AgentId` remains unreliable** — 107 emails, 80 usernames, 72 plain names, one holding a meeting
name. Nothing depends on it now that the UI can use `OwnerName`, but it should not be trusted.
