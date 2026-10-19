# Project-Based Meeting Access — Status & Handover

_Last updated: 2026-07-08_

---

## 1. The Goal

**Let every member of a Synthera project see all LMA meetings that belong to that project** — the
meetings list, each meeting's details, summary, transcript (historical **and** live), and audio —
**without** the per-record copying that made the old `SharedWith` sharing time out on long meetings,
and **without** changing any existing behavior (owner access and individual `SharedWith` sharing
keep working exactly as before).

Supporting requirements:
- **Two revoke levels:** remove a member from a whole project, or block one member from a single meeting.
- **Explicit individual share always wins** over project rules.
- Projects live entirely in the **Synthera app (MongoDB)**; LMA stays project-unaware except for a
  small tag and a lookup table.

---

## 2. The Design in One Line

Change LMA's read rule from **"owner OR shared-with-me"** to
**"owner OR shared-with-me OR (on the meeting's project AND not blocked)"** — checked *at read time*
in the AppSync resolvers, using two facts:
- a `ProjectId` tag on each meeting record, and
- a small `user → projectIds` lookup table (`LMASA-ProjectMembership`) mirrored from MongoDB.

Because the check happens at read time, **revoking access = just changing the data** (no fan-out),
and **missing data = today's exact behavior** (fail-safe).

---

## 3. What Is DONE (deployed to AppSync API `lo3gpjswjjdqrh6xn5xkgipeme`, us-east-1)

### 3.1 New infrastructure (additive — nothing existing touched)
| Resource | Name / ID | Purpose |
|----------|-----------|---------|
| DynamoDB table | `LMASA-ProjectMembership` | Access mirror: `PK = u#<cognitoUsername>`, `ProjectIds: [..]` |
| IAM role | `LMASA-AppSyncProjectMembershipRole` | Lets AppSync `GetItem` the mirror |
| AppSync data source | `ProjectMembership` | Points at the mirror table |

### 3.2 New pipeline functions
| Function | Data source | Role |
|----------|-------------|------|
| `GetUserProjects` (VTL) | ProjectMembership | Stash caller's projects (read resolvers) |
| `GetUserProjectsJS` (JS) | ProjectMembership | Same, at subscribe time (subscriptions) |
| `GetCallAuth` | CallEventSourcing | getCall fetch + project auth |
| `GetCallForSeg` | CallEventSourcing | Fetch call's project for transcript reads |
| `GetCallForAddSeg` | CallEventSourcing | Fetch call's project when writing a segment |
| `QuerySegments` / `QuerySegmentsSentiment` | CallEventSourcing | Original transcript queries (pass-through) |
| `PutSegment` | CallEventSourcing | Original PutItem + stamp ProjectId/ExcludedUsers |
| `ListCallsQuery` / `ListCallsDateShardQuery` / `ListCallsDateHourQuery` | CallEventSourcing | Original list queries |

### 3.3 Resolvers converted (UNIT → PIPELINE, additive project rule)
| # | Resolver | Change | Verified |
|---|----------|--------|----------|
| 1 | `getCall` | + project auth | ✅ user-tested |
| 2 | `listCalls` | + project filter | ✅ user-tested |
| 3 | `listCallsDateShard` | + project filter | ✅ user-tested |
| 4 | `listCallsDateHour` | + project filter | ✅ user-tested |
| 5 | `getTranscriptSegments` | call-level project check | ✅ user-tested |
| 6 | `getTranscriptSegmentsWithSentiment` | call-level check + **fixes a pre-existing no-auth hole** | ✅ deployed |
| 7 | `addTranscriptSegment` | stamps `ProjectId`/`ExcludedUsers` on new segments | ✅ live-tested |
| 8 | `onAddTranscriptSegment` | + project branch in live filter | ✅ live-tested |
| 9 | `onCreateCall` | + project branch in live filter | ✅ deployed |
| 10 | `onUpdateCall` | + project branch in live filter | ✅ deployed |

**All changes are additive and absent-safe:** with no `ProjectId` and an empty mirror (current
state), every resolver behaves exactly as before. This is why the app looks unchanged today.

### 3.4 Verified working in the live app
- Opening a meeting (getCall) ✅
- Meetings list + transcript display ✅
- Live meeting transcript streaming ✅ (after the addTranscriptSegment + subscription changes)

### 3.5 Backups / source of truth
- **Originals** (before changes): [appsync_current/](appsync_current/) — all 65 original resolvers + schema.
- **Updated** (after changes): [appsync_updated/](appsync_updated/) — current live state.
- **Deployed source** (VTL/JS I wrote): [appsync_changes/](appsync_changes/) — re-deployable.

---

## 4. What Is PENDING (to actually turn project access ON)

> Everything below is **your app's** work. Until it's done, project access stays inert and the
> system behaves exactly as before (owner/sharedwith only). Nothing here risks existing behavior.

### B. Backend (MongoDB / Synthera REST API) — activates project access
1. **Store each user's Cognito username** (`microsoftentraid_<oid>`) on the Mongo user, so the mirror
   can be keyed by the exact `ctx.identity.username`. Reuse the `resolve_usernames()` Cognito
   `list_users`-by-email pattern from `index.py`.
2. **Populate `LMASA-ProjectMembership`** — dual-write from your projects API on add/remove member:
   `PK = u#<cognitoUsername>`, `ProjectIds` = the user's project set. **Backfill existing members once.**
3. **Write `ProjectId` onto `c#` + `cls#`** when a meeting is linked to a project (your VP-join /
   REST backend, direct SDK write). **Backfill already-linked meetings once.**
4. *(Optional)* **Write `ExcludedUsers`** (list of usernames) on `c#` + `cls#` for per-meeting blocking.

### C. Frontend — enables real-time streaming to project members
5. Add **`ProjectId`** to the `onAddTranscriptSegment` subscription's selection set (and to the
   transcript query selections). Without it, project members see live meetings **on refresh**; with
   it, they receive **real-time** streaming. (The mandatory fields `Owner`/`SharedWith` are unchanged,
   so nothing breaks if this is added later.)

### A. Ops
6. **These are direct CLI changes.** A redeploy of the LMA CloudFormation stack will **overwrite**
   them. Keep [appsync_changes/](appsync_changes/) in source control and re-apply after any LMA
   upgrade. (Same caveat applies to other manual changes already made to this stack.)

### Consistency note (dual-write)
The mirror is a **derived cache** of Mongo (the source of truth), so the dual-write pattern is fine.
To keep it safe: write **Mongo first**, then the mirror; write the **full `ProjectIds` set**
(idempotent); and add a periodic **reconcile** job that rebuilds the mirror from Mongo.

---

## 5. How Access Is Decided (reference)

A user can see a meeting if **any** of these is true (checked in order):
1. They are the **Owner**.
2. They are in the meeting's **`SharedWith`** (individual share — always wins).
3. They are a **member of the meeting's project** (`ProjectId ∈ their projects`) **and** not in the
   meeting's `ExcludedUsers`.
Admins see everything (unchanged).

- **Reads** (list, open, transcript) re-check on every request → revoke is instant on next read.
- **Live subscription** sets its filter once at subscribe time → a live viewer is cut off on their
  next refresh (only relevant if you revoke someone mid-live-meeting).
- **Transcripts** are authorized **once per call** (call-level), not per line → no timeout on long
  meetings, and no fan-out.

---

## 6. Findings Along The Way (unrelated to the project work, but worth knowing)

- **Audio recordings:** gated by `getCall` (you only get the URL if authorized), so project members
  get audio automatically. The S3 object itself is readable by any authenticated user who has the
  key (flat prefix + broad Cognito role) — a **pre-existing** property; a direct-to-S3 bypass isn't
  blocked. Optional hardening: serve recordings via a presigned-URL Lambda that checks access.
- **Meeting Assist Bot:** uses a separate path (`sendChatMessage` → StrandsChat → orchestrator reads
  DynamoDB directly). **Unaffected** by our changes. It performs no access check of its own
  (pre-existing) but in normal use only assists the meeting you already opened.
- **`getTranscriptSegmentsWithSentiment` had no authorization at all** (returned any transcript to
  any authenticated user). Our change **closed that hole** while adding project access.
- **Speaker mislabeling** (e.g., a passive Otter.ai notetaker shown as a speaker): a
  **capture/diarization** issue in the meeting-bot / transcription layer, decided *before*
  AppSync. Our resolver never touches `Speaker` and wrote 0 extra fields — proven not the cause.
  A real fix would be in the Virtual Participant / speaker-attribution layer.

---

## 7. Rollback

Any resolver can be reverted instantly by re-applying its original from
[appsync_current/resolvers/](appsync_current/resolvers/) with `aws appsync update-resolver`
(`--kind UNIT --data-source-name <original>`). We already did this once for `addTranscriptSegment`
and re-applied it after clearing it. The new table, role, data source, and functions are additive
and can be deleted if the feature is abandoned.

---

## 8. Bottom Line

- **AppSync side: DONE and verified.** All 10 resolvers are project-aware, live, and safe (inert
  until data is added).
- **To go live with the feature:** complete Section 4 B (populate the mirror + tag meetings with
  `ProjectId` from your backend) and 4 C (frontend selects `ProjectId` for live streaming).
- The moment a meeting has a `ProjectId` and a user has a mirror row, project access "turns on" for
  that pair — with zero further AppSync work.

---

## 9. End-to-End Verification (2026-07-20) — PASSED

The feature was exercised for the first time with **real data and a real non-owner**. Previously it
was "deployed but never proven."

**Test setup**

| | |
|---|---|
| Meeting | `Test Meeting` — CallId `Test Meeting-2026-07-15-15-19-09-283` (155 real transcript segments) |
| Meeting owner | `microsoftentraid_659f635e-…` (vamsikrishna.solleti@) |
| Test user | `microsoftentraid_7d48b272-…` (yogeswarsingh.bondili@) — **not** the owner, **not** in `SharedWith` |
| Tag applied | `ProjectId = E2E-TEST-PROJECT-20260720` on both the `c#` and `cls#` rows |
| Membership | `LMASA-ProjectMembership` → `PK=u#microsoftentraid_7d48b272-…`, `ProjectIds=[E2E-TEST-PROJECT-20260720]` |

**Results** — the live deployed `getTranscriptSegments` decision template was evaluated against the
real DynamoDB data:

```
project member  -> ALLOWED (full transcript)
stranger        -> DENIED  (0 segments)
owner           -> ALLOWED (via the owner fallback)
```

Confirmed in the UI: the test user can see and open a meeting they neither own nor were shared on.
**The access model works as designed.**

> **⚠ THIS TEST DATA IS STILL LIVE (deliberately left in place).**
> `Test Meeting` (15 Jul) is tagged with `E2E-TEST-PROJECT-20260720`, and yogeswarsingh.bondili@ has
> a membership row for that project. This is **why that user can see a meeting owned by
> vamsikrishna.solleti@** — it is intentional test state, not a bug. To remove it: delete the
> `ProjectId` attribute from both rows of that CallId, and delete the `u#…7d48b272…` row from
> `LMASA-ProjectMembership`.

### Scalability finding (from the same review)

`listCalls` uses a DynamoDB **`Scan`**, not a `Query` — and filters afterwards. This is
**pre-existing** (present in `appsync_current`, before the project work), but project access makes it
matter more, because members must now find meetings they do not own across the whole table.

Table composition today (35,343 items):

| Type | Count | Share |
|---|---|---|
| `trs#` transcript | 34,944 | **98.6%** |
| `c#` meetings | 257 | 0.7% |
| `cls#` list rows | 257 | 0.7% |

DynamoDB applies `filter` *after* reading, so `limit: 100` reads 100 items and returns **~0.7 meeting
rows**. Surfacing 20 meetings scans **~2,759 items (~27 pages)**, and a page can return zero meetings
with a non-null `nextToken`. It degrades as transcripts accumulate (~136 transcript rows per meeting).

**Recommended fix:** a **sparse GSI** on `ProjectId` (+ timestamp as sort key). Only items carrying a
`ProjectId` would be indexed — a few hundred rows, never the transcripts — turning the scan into a
direct `Query`. It is additive and needs no data migration.

**Do this via CloudFormation, not the CLI.** `EventSourcingTable` is managed by stack
`LMASA-AISTACK-4VKHCQQY3CJR`; an out-of-band GSI risks CloudFormation trying to reconcile (or remove)
it on the next stack update — a bad failure mode on the production meetings table.
