# Live Meeting Subscriptions — Project-Access Fix

_Applied: 2026-07-21 · AppSync API `lo3gpjswjjdqrh6xn5xkgipeme` · us-east-1_

**Scope:** the AppSync **subscriptions** that stream a live meeting (transcript, status, list). Part of
the project-access workstream — see [PROJECT_ACCESS_STATUS.md](PROJECT_ACCESS_STATUS.md).

---

## 1. The Symptom

A user on more than a few projects saw **no live transcript** — the page only updated on manual
refresh. The WebSocket returned:

```
Filters combination exceed maximum limit 10 for subscription   (errorCode 400)
```

`nikethsai.avirneni` (10 projects) and `microsoftentraid_7d48b272` (13 projects) both hit it.

---

## 2. How Live Subscriptions Work (background)

- A **query** = pull once (what "refresh" uses). No filter-size limit.
- A **subscription** = a live WebSocket the server pushes new events down as they happen.

When you open a live meeting, the server runs a small resolver that builds a **filter** — a rule
deciding which events reach you. The filter is set **once, at subscribe time**, then every event is
checked against it.

**AppSync caps that filter:** the `in` operator holds **≤5 values**, and the total filter can have at
most **10 conditions**. These are hard limits.

---

## 3. Root Cause (two layers)

The three subscriptions built the filter as:

```js
or: [
  { Owner: { eq: me } },
  { SharedWith: { contains: me } },
  { ProjectId: { in: myProjects } }   // <-- the problem
]
```

**Layer 1 — the limit.** `myProjects` for a 13-project user produced a filter with 15 conditions,
over AppSync's cap → the **whole subscription was rejected**, so *nothing* streamed (not even for
meetings the user owned).

**Layer 2 — it never worked anyway.** Transcript segment events **carry no `ProjectId` field** (only
the `c#`/`cls#` records do). So `ProjectId in [...]` matched **nothing** — project members had never
actually received live transcript through it. The clause was pure dead weight whose only effect was
to overflow the filter.

> A first attempt chunked the list into groups of 5 to dodge the `in`-value cap. It didn't help — the
> **total-conditions** cap (10) still counts every value, and the clause was dead regardless. The
> real fix is below.

---

## 4. The Fix — Check Access in Code, Filter by CallId

The key realisation: `onAddTranscriptSegment(CallId)` is scoped to **one meeting**. So we don't need
the filter to reason about access across meetings. Instead:

1. **At subscribe time, check access to that one meeting — in code** (no size limit):
   owner OR shared OR (meeting's project ∈ my projects, and not blocked).
2. **If denied** → reject the subscription.
3. **If allowed** → set a trivially simple filter: `CallId = <that meeting>` — **one condition**,
   can never overflow.

```
OLD:  filter = (owner OR shared OR [up to 13 projects])   -> too big, rejected
NEW:  code:   am I allowed to see THIS meeting? (unlimited)
      filter: CallId eq <this meeting>                    -> one condition
```

The access decision moved **out of the size-limited filter** and **into unlimited code**. Projects
are checked in plain JavaScript, so 13 or 130 makes no difference.

**This also fixed a real bug:** project members now receive live transcript for meetings they're on
but don't own — which the dead `ProjectId` clause never delivered.

---

## 5. Resources Changed

### 5.1 New pipeline function — `GetCallForSub`

Fetches the meeting (`c#<CallId>`) at subscribe time and stashes `Owner`, `SharedWith`, `ProjectId`,
`ExcludedUsers` for the code check. Data source `CallEventSourcing`. VTL templates saved to
`vp_fix_backups/subscription-resolvers-v2/GetCallForSub.*.vtl`.

### 5.2 `Subscription.onAddTranscriptSegment` — rebuilt

Pipeline `GetUserProjectsJS → GetCallForSub`. Per-meeting access check in code, then
`setSubscriptionFilter({ CallId: { eq: callId } })`. Denies stranger and blocked-project-member;
allows owner / shared / project-member / admin.

### 5.3 `Subscription.onUpdateCall` — rebuilt

Same per-meeting logic (it also takes `CallId`).

### 5.4 `Subscription.onCreateCall` — simplified to owner/shared only

This one is **global** (no `CallId`) and fires when a meeting is first created — **before** any
`ProjectId` is assigned. Project filtering here was always meaningless, so it now filters by
`Owner`/`SharedWith` only (2 conditions, never overflows). Project members see brand-new meetings on
refresh, not live — acceptable and not a regression.

Source: `vp_fix_backups/subscription-resolvers-v2/`. Previous (chunked) versions in
`vp_fix_backups/subscription-resolvers-backup/`.

---

## 6. Verification

Validated with AppSync `evaluate-code` against the deployed logic, 7 scenarios:

| Caller | Result |
|---|---|
| owner (13 projects) | ALLOWED |
| project member, meeting's project in their 13 | **ALLOWED** (never worked before) |
| shared-with user | ALLOWED |
| stranger | DENIED |
| project member but on the meeting's block-list | DENIED |
| admin | ALLOWED |
| no `CallId` (fallback) | owner/shared filter |

> **Caveat:** subscriptions can only be *fully* proven over a real WebSocket, which can't be driven
> from the CLI. The resolver logic is validated exhaustively; the end-to-end confirmation is a 13+
> project user opening a live meeting and seeing transcript stream without refresh.

---

## 7. Ceiling — Gone

| | Before | After |
|---|---|---|
| Max projects before it breaks | 5, then 10 | **unlimited** (checked in code, not the filter) |

---

## 8. Known Separate Issue (NOT this fix)

`onUpdateVirtualParticipant` returns `"Not Authorized to access onUpdateVirtualParticipant"`. That is
a **different** subscription (the meeting-bot's status, not the transcript) with its own access rule,
untouched by this work. Investigate separately if a user should be seeing a bot's status but can't.

---

## 9. Drift

All four changes are **live CLI patches** to the AppSync API — reverted by a CloudFormation deploy of
`LMASA-AISTACK-4VKHCQQY3CJR` (the `AppSyncSchema` / resolver resources). Backups in
`vp_fix_backups/subscription-resolvers-v2/`. See the consolidation plan in
[MAKE_CHANGES_PERMANENT.md](MAKE_CHANGES_PERMANENT.md).
