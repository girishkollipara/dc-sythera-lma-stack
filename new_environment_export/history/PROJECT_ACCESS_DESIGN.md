# Project‑Based Meeting Access — Design & Flow

**In one line:** let everyone on a project see all of that project's meetings (list + transcript,
past and live), without the per‑line copying that makes long meetings time out — and without
changing how today's sharing works.

This document is written flow‑first in plain language. The detailed resolver code is at the very
end (Part D) — skip it unless you're the one editing AppSync.

- **Part A — The idea** (read this)
- **Part B — The pieces we build** (read this)
- **Part C — The flows, step by step** (read this)
- **Part D — The exact AppSync changes** (implementation detail)

> **Status:** this design is now **implemented and deployed**. `PROJECT_ACCESS_STATUS.md` is the
> authoritative record of the live state. This document has been reconciled to the final decisions:
> the mirror is kept in sync by **dual-write** (not a change stream); segments are enriched inside
> the `addTranscriptSegment` resolver (the CallEventProcessor Lambda writes through that mutation);
> three subscriptions were changed (`onAddTranscriptSegment`, `onCreateCall`, `onUpdateCall`); and
> the live filter uses `ProjectId` only (per-meeting block is enforced on the read/refresh path).

---

# Part A — The idea

## A.1 The one new rule

Today LMA lets someone into a meeting if:

> **you own it, OR it was individually shared with you.**

We are adding one more way in:

> **…OR the meeting belongs to a project you're on (and you haven't been specifically blocked from
> that one meeting).**

Everything in this document exists only to make that sentence work.

## A.2 The final access rule (with the exceptions you asked for)

A person can see a meeting if **any** of these is true, checked in this order:

1. **They own it.** → allowed.
2. **It was individually shared with them** (the existing `SharedWith`). → allowed. *This always
   wins* — an explicit share can never be blocked by a project rule.
3. **They're on the meeting's project AND they're not on that meeting's block‑list.** → allowed.
4. Otherwise → not allowed.

This gives you the two controls you wanted:

- **Block someone from the whole project** → remove them from the project (they lose *all* its
  meetings). This is a *membership* change.
- **Block someone from just one meeting** → add them to that one meeting's block‑list (they keep
  every other project meeting). This is a *per‑meeting* exception.

And because rule #2 sits above the project rules, **an owner can still individually share any
meeting with anyone**, even someone blocked from the project — the explicit share wins.

## A.3 Why this is fast (the whole point)

To let a project member read a 2‑hour transcript, LMA checks **once** — "is this person on this
meeting's project?" — and if yes, shows the whole transcript. It does **not** stamp permission onto
each of the thousands of transcript lines. That single check is why long meetings stop timing out.

---

# Part B — The pieces we build

To answer the new rule, LMA needs two facts at the moment someone asks to see a meeting:

- **Fact 1 — Which project does this meeting belong to?**
- **Fact 2 — Which projects is this user on?**

So most of the work is just getting those two facts where LMA can read them, fast.

## B.1 Fact 1 lives on the meeting (a `ProjectId` tag)

When a meeting is linked to a project, your backend writes the **project's ID onto the meeting
record** inside LMA (a direct SDK write from your REST API). Now the meeting "knows" its project.
A meeting belongs to exactly **one** project, so `ProjectId` is a single value.

You already have the perfect trigger for this: **when a Virtual Participant joins**, your DynamoDB
stream Lambda receives the meeting ID + LMA CallId and records them in Mongo. That same flow is
where you also write the `ProjectId` onto the LMA meeting record.

## B.2 Fact 2 lives in a small lookup list (synced from Mongo)

Your projects and members live in **MongoDB**, and **AppSync cannot read MongoDB** (it has no
MongoDB connector). So we keep a **tiny copy** of just one fact — "*this user → these projects*" —
in a small **DynamoDB** table that AppSync *can* read in one quick lookup.

- Mongo stays your **source of truth**.
- Your **projects API dual-writes**: whenever it changes membership in Mongo, it writes the same fact
  to the mirror in the **same request** (see D.9). No sync Lambda, no change stream.
- Because the copy updates in the same request as the Mongo change, a newly added member sees the
  project **right away** — no waiting, no "log out and back in."

We call this small table the **access mirror** (`LMASA-ProjectMembership`). (You confirmed an extra
table is fine, so this is the chosen approach — instant access changes with no token‑refresh gap.)

> Why a copy instead of reading Mongo live? Because LMA checks this on *every* read. A fast local
> DynamoDB lookup beats calling Mongo (through a Lambda) on every request, and it keeps Mongo off
> the hot path.

## B.3 The block‑lists (your two exceptions)

- **Per‑meeting block** → a list called `ExcludedUsers` on the meeting record. Anyone in it is
  blocked from *that meeting's* project access (but not from an explicit individual share).
- **Whole‑project block** → simply removing the person from the project, i.e. updating the access
  mirror. No block‑list needed; they just stop being a member.

## B.4 Summary of what gets built

| # | Thing | Where | Who writes it |
|---|-------|-------|---------------|
| 1 | `ProjectId` on the meeting | LMA meeting records (`c#`, `cls#`) | Your REST API backend (via the VP‑join flow), at link time |
| 2 | Access mirror (`user → projects`) | New DynamoDB table `LMASA-ProjectMembership` | Your projects API (**dual-write**), on membership change |
| 3 | `ExcludedUsers` on the meeting | LMA meeting records (`c#`, `cls#`) | Your REST API backend, when blocking one person from one meeting |
| 4 | Enrich new segments (`ProjectId`) | Inside AppSync | The `addTranscriptSegment` resolver (D.8) |
| 5 | The new "OR project" rule | AppSync resolvers | One‑time resolver edit |

---

# Part C — The flows, step by step

Below, "the meeting record" means the two LMA rows for a call: the master row `c#<CallId>` and its
list row `cls#…`. They travel together.

## Flow 1 — Linking a meeting to a project (automatic, on VP join)

1. A Virtual Participant joins a meeting.
2. Your DynamoDB stream Lambda fires (as it does today) with the meeting ID + LMA CallId.
3. It records the link in Mongo (as today) **and** your backend writes `ProjectId = <that project>`
   onto the LMA meeting record (a direct SDK write).
4. Done. The meeting now belongs to a project. (One small write. No effect on anyone's access yet —
   the resolvers decide access at read time.)

## Flow 2 — Adding or removing a project member

1. Someone adds **Bala** to **Project A** in your app.
2. Your projects API writes it to **Mongo** and, in the **same request**, **dual-writes** the
   **access mirror**: Bala's row now lists Project A (or no longer does). One small write.
3. Done. Next time Bala reads, he sees (or stops seeing) all of Project A's meetings — immediately,
   no re‑login.

## Flow 3 — Blocking one person from one meeting

1. You want **Maria** (a Project A member) to *not* see meeting **X**.
2. Your app adds `Maria` to `ExcludedUsers` on X's meeting record (two small writes).
3. Done. Maria still sees every other Project A meeting; she just can't see X through the project.
   (If someone later individually shares X with her, she sees it again — explicit share wins.)

## Flow 4 — Individual sharing (unchanged)

The existing "share this meeting with this email" feature works **exactly** as before. It writes to
`SharedWith`, and rule #2 makes it always win. We are not touching it.

## Flow 5 — What happens when a member opens the app (reading)

This is where the two facts meet. When Bala opens LMA:

1. LMA looks up the **access mirror**: "what projects is Bala on?" → e.g. `[Project A, Project B]`.
2. **Meeting list:** LMA goes through the meetings and keeps the ones where **he owns it, OR it's
   shared with him, OR its `ProjectId` is one of his projects and he's not blocked from it.** →
   Project A and B meetings show up in his list.
3. **Opening a meeting's transcript:** LMA checks **once** — "is Bala on this meeting's project and
   not blocked?" If yes, it returns the **whole** transcript. If no, it falls back to the old check
   (does he own it / is it shared with him line‑by‑line).

Fast, and no permissions were copied anywhere.

## Flow 6 — A live meeting (real‑time transcript)

For live meetings, LMA pushes new transcript lines to subscribers. For a project member to receive
them live:

1. Each **new** transcript line is stamped, as it's written, with the meeting's `ProjectId`. The
   segment writer (the **CallEventProcessor Lambda**) writes every line **through the
   `addTranscriptSegment` mutation**, so we stamp it **inside that resolver** — it reads the meeting
   record and copies `ProjectId` onto the line (D.8). One small lookup per line, no bulk copying, and
   it catches every write path without touching the upstream Lambda.
2. The member's live subscription is set to receive lines where **they own it, OR it's shared with
   them, OR the line's project is one of theirs.**
3. So project members watch the transcript stream in real time.

> **Per-meeting block in live:** the live filter uses `ProjectId` only — it does **not** apply
> `ExcludedUsers`. Per-meeting blocking is enforced on the read/refresh path (Flow 5), so a blocked
> member watching live is cut off on their **next refresh**. This keeps the live filter simple and
> avoids missing-field edge cases.

---

# Part D — The exact AppSync changes (implementation detail)

> Skip this part unless you're editing the AppSync resolvers. Everything here is **additive**: we
> only ever *append* an "OR project" branch. The existing Owner/SharedWith/Admin logic is left
> exactly as it is, so nothing that works today can break.

All changes are in AppSync API `lo3gpjswjjdqrh6xn5xkgipeme`.

## D.1 The access mirror table

New DynamoDB table **`LMASA-ProjectMembership`** (owned by **your** stack — never the LMA table;
its AppSync data source is named `ProjectMembership`):

| Attribute | Type | Example |
|-----------|------|---------|
| `PK` | String | `u#microsoftentraid_7d48b272-2665-4ef5-ae4e-40b3459ce6f1` |
| `ProjectIds` | String Set | `["proj_A", "proj_B"]` |
| `UpdatedAt` | String | ISO timestamp |

- One row per user; lookup is a single `GetItem`.
- **Critical:** the `PK` must use the exact `cognito:username` the resolver sees as
  `ctx.identity.username` (e.g. `microsoftentraid_<oid>`) — **not** email, **not** Mongo `_id`.
  Store that Cognito username on each Synthera user so your sync can key the mirror correctly.
  If this is wrong, members silently won't match (it fails *closed* — safe, but broken).
- **How to get the Cognito username:** you already do this in `resolve_usernames()` in `index.py` —
  it calls Cognito `list_users` with `Filter='email = "<email>"'` and reads `Username`. Reuse that
  to map each Mongo user → their Cognito username (ideally store it on the user once, at
  signup/first login).

## D.2 New attributes on LMA meeting records

- `ProjectId` (string, single) — the meeting's project. Written on `c#` + `cls#` at link time by
  your **REST API backend** (direct SDK write — Flow 1).
- `ExcludedUsers` (list of usernames) — per‑meeting block‑list. Written on `c#` + `cls#` by your
  REST API backend (Flow 3).
- For **live** delivery, new transcript segments must carry `ProjectId` so the subscription filter
  can match. The segment writer (the **CallEventProcessor Lambda**) writes every line **through the
  `addTranscriptSegment` mutation**, so we enrich the segment **inside that resolver** — it reads the
  `c#` record and merges `ProjectId` into the stored item and returned payload (see D.8). Historical
  segments are **not** stamped and **not** backfilled; they're covered by the call‑level check in D.5.

## D.3 Shared building block: a "get my projects" function

Add an AppSync **data source** pointing at the access mirror, and one reusable **function**
`GetUserProjects`:

- **request:** `GetItem` with key `PK = u#${ctx.identity.username}`
- **response:** stash the result and continue:
  ```
  #set($ids = $util.defaultIfNull($ctx.result.ProjectIds, []))
  $util.qr($ctx.stash.put("myProjectIds", $ids))
  {}
  ```
- Absent‑safe: no mirror row → `myProjectIds = []` → project branch never matches → today's
  behavior.

Each read resolver below becomes a **pipeline** that runs `GetUserProjects` first, then its existing
logic, then applies the appended OR.

## D.4 `getCall` (pipeline: GetUserProjects → existing GetItem)

```vtl
#if ( $ctx.error )
  $util.error($ctx.error.message, $ctx.error.type)
#end

#set($isCognitoUser = true)
#if($context.identity.userArn && $context.identity.userArn.contains(":assumed-role/"))
  #set($isCognitoUser = false)
#end

## ---- ADDED: project access (blocked users don't get the project path) ----
#set($myProjects = $util.defaultIfNull($ctx.stash.myProjectIds, []))
#set($excluded   = $util.defaultIfNull($context.result.ExcludedUsers, []))
#set($inProject  = $context.result.ProjectId
                   && $myProjects.contains($context.result.ProjectId)
                   && !$excluded.contains($context.identity.username))

#if($isCognitoUser
    && !$context.identity.groups.contains("Admin")
    && $context.result.Owner != $context.identity.username
    && !$context.result.SharedWith.contains($context.identity.username)
    && !$inProject)                       ## <-- ADDED: only widens who passes
      $util.error("User $context.identity.username is not authorized", "AuthorizationError")
#else
  $utils.toJson($context.result)
#end
```

## D.5 `getTranscriptSegments` (pipeline: GetUserProjects → GetCall → existing query)

Authorize at the **call level** so we never touch individual lines:

- **Function 1** `GetUserProjects` → stashes `myProjectIds`.
- **Function 2** `GetCall` (LMA table): `GetItem` `PK=SK=c#${ctx.args.callId}`; stash the call's
  project + block‑list:
  ```
  $util.qr($ctx.stash.put("callProjectId", $ctx.result.ProjectId))
  $util.qr($ctx.stash.put("callExcluded",  $util.defaultIfNull($ctx.result.ExcludedUsers, [])))
  {}
  ```
- **Function 3** = the **existing** segment query (unchanged).
- **Response (after) template:**

```vtl
#if ( $ctx.error )
  $util.error($ctx.error.message, $ctx.error.type)
#end

#set($myProjects  = $util.defaultIfNull($ctx.stash.myProjectIds, []))
#set($callProject = $ctx.stash.callProjectId)
#set($callExcl    = $util.defaultIfNull($ctx.stash.callExcluded, []))
#set($isProjectMember = $callProject
                        && $myProjects.contains($callProject)
                        && !$callExcl.contains($context.identity.username))   ## <-- ADDED

#if($ctx.identity.groups.contains("Admin") || $isProjectMember)
  ## Admins (as today) + allowed project members get the whole transcript, no per-line filter
  $util.toJson({"TranscriptSegments": $ctx.result.items, "nextToken": $ctx.result.nextToken})
#else
  ## ---- UNCHANGED: today's exact per-line Owner/SharedWith filter ----
  ## (an individual share still shows those lines here — explicit share wins)
  #set($myResults = [])
  #foreach($item in $context.result.items)
    #if($item.Owner == $context.identity.username || $item.SharedWith.contains($context.identity.username))
      #set($added = $myResults.add($item))
    #end
  #end
  $util.toJson({"TranscriptSegments": $myResults, "nextToken": $ctx.result.nextToken})
#end
```

> **`getTranscriptSegmentsWithSentiment` gets the same treatment** (identical pipeline + call-level
> check, output key `TranscriptSegmentsWithSentiment`). It additionally **gains authorization it
> never had** — it previously returned any meeting's sentiment transcript to any authenticated user;
> this change closes that pre-existing hole while adding project access.

## D.6 `listCalls`, `listCallsDateShard`, `listCallsDateHour` (pipeline: GetUserProjects → existing query)

All three share the same response filter — append one OR:

```vtl
#if ( $ctx.error )
  $util.error($ctx.error.message, $ctx.error.type)
#end

#set($myProjects = $util.defaultIfNull($ctx.stash.myProjectIds, []))        ## <-- ADDED
#set($me = $context.identity.username)

#if(!$context.identity.groups.contains("Admin"))
  #set($myResults = [])
  #foreach($item in $context.result.items)
      #set($excl = $util.defaultIfNull($item.ExcludedUsers, []))
      #if($item.Owner == $me
          || $item.SharedWith.contains($me)
          || ($item.ProjectId && $myProjects.contains($item.ProjectId) && !$excl.contains($me)))  ## <-- ADDED
          #set($added = $myResults.add($item))
      #end
  #end
  $util.toJson({"Calls": $myResults, "nextToken": $ctx.result.nextToken})
#else
  $util.toJson({"Calls": $ctx.result.items, "nextToken": $ctx.result.nextToken})
#end
```

This is what makes project meetings **appear in each member's list**.

## D.7 Subscriptions: onAddTranscriptSegment, onCreateCall, onUpdateCall

Make each a **pipeline** subscription resolver: Function 1 = `GetUserProjectsJS` (a JS function that
stashes `myProjectIds` at subscribe time), then the response appends the project branch to the filter:

```js
export function response(ctx) {
  const { selectionSetList } = ctx.info;
  // guard UNCHANGED — do NOT require ProjectId here, or the current frontend breaks
  if (!selectionSetList.includes('Owner') || !selectionSetList.includes('SharedWith')) {
    util.unauthorized();
  }

  const { groups } = ctx.identity;
  if (groups === undefined || !groups.includes("Admin")) {
    const me = ctx.identity.username;
    const myProjects = ctx.stash.myProjectIds || [];         // <-- ADDED (from GetUserProjectsJS)
    const or = [
      { Owner:      { eq: me } },
      { SharedWith: { contains: me } },                      // explicit share wins (own branch)
    ];
    if (myProjects.length > 0) {                             // <-- ADDED: project branch
      or.push({ ProjectId: { in: myProjects } });
    }
    extensions.setSubscriptionFilter(util.transform.toSubscriptionFilter({ or }));
  }
  return null;
}
```

Requirements for live matching:
- New segments/calls must carry `ProjectId` in the pushed payload (D.2 / D.8).
- The **client subscription should select `ProjectId`** so the filter can match it — **not enforced**
  (until added, project members just see live meetings on refresh).
- **Per-meeting block (`ExcludedUsers`) is not in the live filter** — it's enforced on the
  read/refresh path (D.4–D.6).
- Subscription filters are fixed when a client subscribes, so membership changes take effect on the
  next (re)subscribe — i.e. a page refresh/reconnect. Fine for live meetings.
- (`onShareMeetings` was **not** changed — only these three subscriptions.)

## D.8 Enrich new segments inside `addTranscriptSegment` (for live delivery)

The segment writer (the **CallEventProcessor Lambda**) writes every line through the
`addTranscriptSegment` mutation, so we stamp `ProjectId` onto each new segment **inside that
resolver** — one chokepoint that catches all write paths, no upstream Lambda change. Convert
`addTranscriptSegment` from a single `PutItem` into a **pipeline**:

- **Function 1** `GetCallForSegment` (LMA table): `GetItem` `PK=SK=c#${ctx.args.input.CallId}`, then
  stash the two fields:
  ```
  $util.qr($ctx.stash.put("callProjectId", $ctx.result.ProjectId))
  $util.qr($ctx.stash.put("callExcluded",  $util.defaultIfNull($ctx.result.ExcludedUsers, [])))
  {}
  ```
- **Function 2** = the existing `PutItem`, but merge the stashed fields into the input first so they
  are both **stored** on the segment and **returned** in the payload (the payload is what feeds the
  `onAddTranscriptSegment` subscription filter):
  ```
  #if($ctx.stash.callProjectId)
    $util.qr($ctx.args.input.put("ProjectId", $ctx.stash.callProjectId))
  #end
  #if($ctx.stash.callExcluded && $ctx.stash.callExcluded.size() > 0)
    $util.qr($ctx.args.input.put("ExcludedUsers", $ctx.stash.callExcluded))
  #end
  ## ...then the existing PutItem body, unchanged...
  ```
  (`ProjectId` is what the live filter matches on. `ExcludedUsers` on the segment is not used by the
  live filter — per-meeting block is enforced on the read/refresh path — so stamping it is optional.)

Notes / trade‑offs:
- This adds **one `GetItem` per segment write**. It's tiny and fast, but it's on the live ingestion
  path. If volume is a concern: enable AppSync **caching** on Function 1 keyed by `CallId`, or stamp
  only **final** (non‑partial) segments, or defer this whole section to **Phase 2** (project members
  then see the meeting on refresh via D.5 instead of live).
- Purely additive: if the `c#` record has no `ProjectId`, nothing is added and the segment behaves
  exactly as today.

## D.9 Keeping the mirror in sync with Mongo

Membership lives in Mongo; your **projects API dual-writes** the mirror in the same request that
changes Mongo (chosen approach — no sync Lambda, no change stream):

- On **add member**: `ADD ProjectIds :p` to the mirror row `u#<cognitoUsername>`.
- On **remove member**: `DELETE ProjectIds :p` from that user's row.
- Write the **full set** (idempotent) so re-runs and one-time backfills are safe.
- Map the Mongo user → Cognito username as in D.1.

> Consistency: the mirror is a **derived cache** of Mongo. Write **Mongo first**, then the mirror,
> and add a periodic **reconcile** that rebuilds the mirror from Mongo to heal any missed dual-write.
> (A change stream is a valid alternative, but dual-write was chosen for simplicity.)

---

# Part E — Rollout, safety, testing

## E.1 Safe rollout order (each step does nothing until the next uses it)

1. **Identity mapping** — store each user's Cognito username (needed by the mirror). (D.1)
2. **Create the access mirror table** — empty, harms nothing. (D.1)
3. **Sync membership → mirror** — dual-write from your projects API populates rows. (Flow 2 / D.9)
4. **Write `ProjectId` on link** — from your REST API backend; plus a one‑time backfill for
   already‑linked meetings (updates `c#`+`cls#` only — cheap). (Flow 1)
5. **Enrich new segments** via the `addTranscriptSegment` resolver. Harmless until read. (D.8)
   *(Optional / Phase 2 — skip if deferring live streaming to project members.)*
6. **Add the data source + `GetUserProjects` function.** (D.3)
7. **Flip the read resolvers** to pipeline with the appended OR. (D.4–D.6) ← project access goes live.
8. **Flip the subscription filters.** (D.7) ← live delivery to project members.

If anything looks wrong after step 7 or 8, revert that one resolver to its old (UNIT) version → an
instant return to today's behavior.

## E.2 Why nothing else breaks

| If missing… | Result |
|-------------|--------|
| `ProjectId` on a meeting | project branch is false → today's Owner/SharedWith behavior |
| A user's mirror row | `myProjectIds = []` → project branch never matches → today's behavior |
| `ExcludedUsers` | treated as empty list → no one is blocked → normal project access |
| Wrong identity mapping | member just doesn't match → fails **closed** (never opens access wrongly) |

Existing Owner / `SharedWith` / Admin logic is **appended to, never replaced**. Individual sharing
is untouched, and there's no clobbering because project access lives in its own fields.

## E.3 Test checklist

Project **P**, members {Owner O, Member M, Non‑member N}, meeting **X** linked to P:

- **List:** M sees X; N does not; O unchanged.
- **Open call / transcript:** allowed for M (returns the **whole** transcript, fast, even for a
  1‑hour meeting); denied/empty for N.
- **Live:** with X in progress, M receives new lines; N does not.
- **Add member:** add N to P → N refreshes → sees X immediately (no re‑login).
- **Whole‑project block:** remove M from P → M refreshes → loses all P meetings.
- **Per‑meeting block:** add M to X's `ExcludedUsers` → M loses X only, keeps other P meetings.
- **Explicit share wins:** individually share X with a blocked user → they see X again.
- **Individual share of a non‑project meeting:** still works exactly as before.
- **Admin:** unchanged (sees everything).

---

# Part F — Confirmed decisions

These were open questions; now settled:

1. **One meeting belongs to exactly one project.** → single `ProjectId` on the meeting (no list).
2. **Membership lives in MongoDB.** → your **projects API dual-writes** the access mirror in the
   same request (Flow 2 / D.9). No sync Lambda / change stream. Mongo stays the source of truth.
3. **Cognito username** = the federated ID the resolver sees as `ctx.identity.username`, e.g.
   `microsoftentraid_<oid>` — the same string already in `SharedWith`/`Owner`. Map each Mongo user
   to it the same way `resolve_usernames()` in `index.py` already does (Cognito `list_users` by
   email); store it on the user so the mirror can be keyed by it. (D.1)
4. **The segment writer is the CallEventProcessor Lambda, and it writes every line through the
   `addTranscriptSegment` mutation.** → new segments are enriched with `ProjectId` **inside that
   resolver** (D.8) — one chokepoint that catches all write paths, with no upstream Lambda change.
5. **Linking and blocking are done by your REST API backend** via direct AWS SDK writes to the LMA
   table — **not** through new AppSync mutations.

Nothing left blocking — the build order is in Part E.
