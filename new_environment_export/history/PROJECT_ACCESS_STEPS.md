# Project Access — Steps I Need to Perform

This is the concrete build plan for *my* flow. No sync Lambda, no change stream, no extra
processor. My projects API **dual‑writes** to MongoDB and DynamoDB; I write `ProjectId` onto the
LMA records; I change the AppSync resolvers.

**The rule we're adding** (checked in order):

1. I own it → allowed.
2. It's individually shared with me (`SharedWith`) → allowed. *(explicit share always wins)*
3. I'm on the meeting's project **and** I'm not on that meeting's block‑list → allowed.
4. Otherwise → denied.

**Names used below**
- LMA table = `LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE`
- AppSync API = `lo3gpjswjjdqrh6xn5xkgipeme`
- New DynamoDB table = `LMASA-ProjectMembership` (its AppSync data source is named `ProjectMembership`)

> **Status:** the AppSync side (Step 3, and the resolver edits in Steps 4–5) is already **deployed
> and verified**. `PROJECT_ACCESS_STATUS.md` is the authoritative record of the live state. The
> remaining work is the backend data population (Steps 1–2) and the frontend selection (Step 5.3).

---

## Prerequisite — the user identifier I must use everywhere

The resolvers know a user as `ctx.identity.username`, which for my Entra‑federated users looks like
`microsoftentraid_<oid>` (the same string already in `Owner` / `SharedWith`). **Every place I key by
user (the `LMASA-ProjectMembership` table) must use this exact string** — not email, not my Mongo `_id`.

I already resolve this in `resolve_usernames()` in `index.py` (Cognito `list_users` filtered by
email → `Username`). I'll reuse that to get each user's Cognito username and store it on the Mongo
user record, so my dual‑write can key by it.

---

## STEP 1 — Write `ProjectId` (and block‑list) onto the LMA records

When a meeting is linked to a project, my backend writes onto **both** rows of that call in the LMA
table:

- `c#<CallId>`  (PK = SK = `c#<CallId>`)
- `cls#<date>#s#<shard>`  (the matching list row for that call)

Attributes I set:

| Attribute | Meaning |
|-----------|---------|
| `ProjectId` (String) | the project this meeting belongs to (one project per meeting) |
| `ExcludedUsers` (List of usernames) | optional per‑meeting block‑list (Step 4) |

**When I write it:** in my VP‑join flow (I already get the CallId there and write to Mongo) — the
same handler also does this DynamoDB write. A one‑time backfill script sets `ProjectId` on
already‑linked meetings.

Example (SDK `update_item`, run for both PK/SK pairs):

```python
# c# record
table.update_item(
    Key={"PK": f"c#{call_id}", "SK": f"c#{call_id}"},
    UpdateExpression="SET ProjectId = :p",
    ExpressionAttributeValues={":p": project_id},
)
# cls# record (I already know its PK/SK for this call)
table.update_item(
    Key={"PK": cls_pk, "SK": cls_sk},
    UpdateExpression="SET ProjectId = :p",
    ExpressionAttributeValues={":p": project_id},
)
```

> Unlink = `REMOVE ProjectId` on both rows.

---

## STEP 2 — Dual‑write membership to the `LMASA-ProjectMembership` table

Whenever a call hits my **projects API** that changes membership (create project, add/remove member),
my backend writes to **Mongo (as today) AND** this DynamoDB table — in the same request. No sync
Lambda.

### Table shape (keyed by USER — this is important)

I key by user so the list resolver needs only **one** lookup per request:

| Attribute | Type | Example |
|-----------|------|---------|
| `PK` | String | `u#microsoftentraid_7d48b272-…` |
| `ProjectIds` | String Set | `["proj_A", "proj_B"]` |
| `UpdatedAt` | String | ISO timestamp |

One row per user = the set of projects they belong to.

### What my dual‑write does

- **Add member to a project** → update that user's row to include the project:
  ```python
  membership.update_item(
      Key={"PK": f"u#{cognito_username}"},
      UpdateExpression="ADD ProjectIds :p SET UpdatedAt = :t",
      ExpressionAttributeValues={":p": {project_id}, ":t": now_iso},  # :p is a string SET
  )
  ```
- **Remove member** → `DELETE ProjectIds :p` for that user's row.
- **Create/rename project** → no membership change, nothing to write here (I keep project details in
  Mongo).

Because it's written in the same request as the Mongo change, access is **instant** — the member
refreshes and sees the project. Writing the full set (ADD/DELETE, not overwrite) makes re‑runs safe.

> Why keyed by user and not by project: `listCalls` scans many meetings across possibly many
> projects. With a user‑keyed row, the resolver does **one** `GetItem` (my projects) and filters in
> memory. A project‑keyed table would force a lookup per meeting. So: **user → projects**.

---

## STEP 3 — Change the AppSync resolvers (the read paths)

### 3.0 One‑time setup

- Add an AppSync **data source** `ProjectMembership` → the new DynamoDB table.
- Add a reusable **function** `GetUserProjects` (attached to `ProjectMembership`):
  - request: `GetItem` `PK = u#${ctx.identity.username}`
  - response: stash the set and continue
    ```
    #set($ids = $util.defaultIfNull($ctx.result.ProjectIds, []))
    $util.qr($ctx.stash.put("myProjectIds", $ids))
    {}
    ```
- Each resolver below becomes a **pipeline** that runs `GetUserProjects` first.

> Absent‑safe: no row → `myProjectIds = []` → project rule never matches → today's exact behavior.

### 3.1 `listCalls`, `listCallsDateShard`, `listCallsDateHour` — show project meetings in the list

Pipeline: `GetUserProjects` → existing query. In the response filter, append one OR:

```vtl
#set($myProjects = $util.defaultIfNull($ctx.stash.myProjectIds, []))
#set($me = $context.identity.username)

#if(!$context.identity.groups.contains("Admin"))
  #set($myResults = [])
  #foreach($item in $context.result.items)
      #set($excl = $util.defaultIfNull($item.ExcludedUsers, []))
      #if($item.Owner == $me
          || $item.SharedWith.contains($me)
          || ($item.ProjectId && $myProjects.contains($item.ProjectId) && !$excl.contains($me)))
          #set($added = $myResults.add($item))
      #end
  #end
  $util.toJson({"Calls": $myResults, "nextToken": $ctx.result.nextToken})
#else
  $util.toJson({"Calls": $ctx.result.items, "nextToken": $ctx.result.nextToken})
#end
```

### 3.2 `getCall` — open a project meeting (this also unlocks summary + audio URL)

Pipeline: `GetUserProjects` → existing `GetItem`. Response auth (only the `&& !$inProject` and the
two `#set`s are new):

```vtl
#set($isCognitoUser = true)
#if($context.identity.userArn && $context.identity.userArn.contains(":assumed-role/"))
  #set($isCognitoUser = false)
#end

#set($myProjects = $util.defaultIfNull($ctx.stash.myProjectIds, []))
#set($excluded   = $util.defaultIfNull($context.result.ExcludedUsers, []))
#set($inProject  = $context.result.ProjectId
                   && $myProjects.contains($context.result.ProjectId)
                   && !$excluded.contains($context.identity.username))

#if($isCognitoUser
    && !$context.identity.groups.contains("Admin")
    && $context.result.Owner != $context.identity.username
    && !$context.result.SharedWith.contains($context.identity.username)
    && !$inProject)
      $util.error("User $context.identity.username is not authorized", "AuthorizationError")
#else
  $utils.toJson($context.result)
#end
```

### 3.3 `getTranscriptSegments` — read the whole transcript (checked once, no per‑line copying)

Pipeline: `GetUserProjects` → **GetCall** (new: read the `c#` record for its `ProjectId` +
`ExcludedUsers`) → existing segment query.

- New `GetCall` function response (stash the call's project + block‑list):
  ```
  $util.qr($ctx.stash.put("callProjectId", $ctx.result.ProjectId))
  $util.qr($ctx.stash.put("callExcluded",  $util.defaultIfNull($ctx.result.ExcludedUsers, [])))
  {}
  ```
- Final response template:
  ```vtl
  #set($myProjects  = $util.defaultIfNull($ctx.stash.myProjectIds, []))
  #set($callProject = $ctx.stash.callProjectId)
  #set($callExcl    = $util.defaultIfNull($ctx.stash.callExcluded, []))
  #set($isProjectMember = $callProject
                          && $myProjects.contains($callProject)
                          && !$callExcl.contains($context.identity.username))

  #if($ctx.identity.groups.contains("Admin") || $isProjectMember)
    $util.toJson({"TranscriptSegments": $ctx.result.items, "nextToken": $ctx.result.nextToken})
  #else
    ## unchanged: today's per-line Owner/SharedWith filter (explicit share still shows here)
    #set($myResults = [])
    #foreach($item in $context.result.items)
      #if($item.Owner == $context.identity.username || $item.SharedWith.contains($context.identity.username))
        #set($added = $myResults.add($item))
      #end
    #end
    $util.toJson({"TranscriptSegments": $myResults, "nextToken": $ctx.result.nextToken})
  #end
  ```

### 3.4 `getTranscriptSegmentsWithSentiment` — same as 3.3 (and closes a pre‑existing hole)

⚠️ This resolver **has no authorization today** — its response returns `$ctx.result.items`
unfiltered, so any authenticated user can read any meeting's sentiment transcript, bypassing even
`SharedWith`. Apply the **same pipeline + call‑level project check as 3.3**, and add the per‑line
Owner/SharedWith fallback it's currently missing:

```vtl
#set($myProjects  = $util.defaultIfNull($ctx.stash.myProjectIds, []))
#set($callProject = $ctx.stash.callProjectId)
#set($callExcl    = $util.defaultIfNull($ctx.stash.callExcluded, []))
#set($isProjectMember = $callProject && $myProjects.contains($callProject)
                        && !$callExcl.contains($context.identity.username))

#if($ctx.identity.groups.contains("Admin") || $isProjectMember)
  $util.toJson({"TranscriptSegmentsWithSentiment": $ctx.result.items, "nextToken": $ctx.result.nextToken})
#else
  #set($myResults = [])
  #foreach($item in $context.result.items)
    #if($item.Owner == $context.identity.username || $item.SharedWith.contains($context.identity.username))
      #set($added = $myResults.add($item))
    #end
  #end
  $util.toJson({"TranscriptSegmentsWithSentiment": $myResults, "nextToken": $ctx.result.nextToken})
#end
```

> Behavior note: this *adds* auth where there was none, so unauthorized users lose access they were
> never meant to have. Owner / shared / project members / Admin are unaffected. Confirm whether the
> UI uses this field before flipping it.

**After Step 3, project members can:** see project meetings in the list, open them, read the full
transcript, read the summary (`CallSummaryText` comes from `getCall`), and get the recording URL
(also from `getCall`).

---

## STEP 4 — (optional) Per‑meeting block: hide one meeting from one member

My backend adds the user to `ExcludedUsers` on that meeting's `c#` + `cls#` rows:

```python
table.update_item(
    Key={"PK": f"c#{call_id}", "SK": f"c#{call_id}"},
    UpdateExpression="SET ExcludedUsers = list_append(if_not_exists(ExcludedUsers, :empty), :u)",
    ExpressionAttributeValues={":u": [cognito_username], ":empty": []},
)
# repeat for the cls# row
```

The resolvers in Step 3 already honor it. Removing the person from the list restores access.
**Whole‑project block** = just remove them from the project (Step 2 delete) → they lose all its
meetings.

---

## STEP 5 — Live real‑time streaming to project members

Steps 1–4 cover list + open + read (on load/refresh). This step adds the **live** push so project
members watch new transcript lines appear in real time during an in‑progress meeting. Still **no new
Lambda** — two additive resolver edits.

Why it's needed (recap): a live subscription can't look anything up per line. It only matches
**fields on the line itself**. So each new line must carry a small `ProjectId` **tag** (not user
names), and each subscriber's filter is set once (at subscribe time) to accept their projects.

### 5.1 Stamp `ProjectId` (+ `ExcludedUsers`) on each new segment — `addTranscriptSegment`

Turn the `addTranscriptSegment` resolver from a single `PutItem` into a **pipeline**:

- **Function A — `GetCallForSegment`** (data source = LMA table): read the meeting record and stash
  its project + block‑list.
  - request: `GetItem` `PK = SK = c#${ctx.args.input.CallId}`
  - response (tolerant — if the `c#` row/field is missing, stash stays empty and nothing is stamped):
    ```
    $util.qr($ctx.stash.put("callProjectId", $ctx.result.ProjectId))
    $util.qr($ctx.stash.put("callExcluded",  $util.defaultIfNull($ctx.result.ExcludedUsers, [])))
    {}
    ```
- **Function B — the existing `PutItem`**, with a few lines prepended to merge the stashed values
  into the item (so they are both **stored** and **returned in the payload** — the payload is what
  the subscription filter sees):
  ```
  #if($ctx.stash.callProjectId)
    $util.qr($ctx.args.input.put("ProjectId", $ctx.stash.callProjectId))
  #end
  #if($ctx.stash.callExcluded && $ctx.stash.callExcluded.size() > 0)
    $util.qr($ctx.args.input.put("ExcludedUsers", $ctx.stash.callExcluded))
  #end
  ## ...the existing PutItem body stays exactly as-is below...
  ```
  (This mirrors how the resolver already does `$ctx.args.input.put("CreatedAt", …)`.)

Result: every new line now carries a `ProjectId` tag. **Additive & absent‑safe** — if the meeting
has no `ProjectId`, nothing is added and the line behaves exactly as today.

> Cost note: this adds one `GetItem` per line written (partials included). A `GetItem` is ~a few ms
> and effectively free (~$0.25 per million reads), so an hour‑long meeting is negligible. If you ever
> want to trim it, stamp only **final** (non‑partial) segments — project members would then get final
> lines live (a second or two behind partials).

### 5.2 Set the live filter to accept my projects — `onAddTranscriptSegment`

Make this subscription a **pipeline** too, so it can look up my projects **once, at subscribe time**:

- **Function A — `GetUserProjectsJS`** (data source = `ProjectMembership`, APPSYNC_JS):
  ```js
  import { util } from '@aws-appsync/utils';
  export function request(ctx) {
    return { operation: 'GetItem',
             key: util.dynamodb.toMapValues({ PK: `u#${ctx.identity.username}` }) };
  }
  export function response(ctx) {
    if (ctx.error) { ctx.stash.myProjectIds = []; return {}; }
    ctx.stash.myProjectIds = (ctx.result && ctx.result.ProjectIds) ? ctx.result.ProjectIds : [];
    return {};
  }
  ```
- **Resolver response** — add the project branch to the existing filter (only the `myProjects` line
  and the `if` block are new):
  ```js
  export function response(ctx) {
    const { selectionSetList } = ctx.info;
    // guard unchanged — do NOT require ProjectId here, or the current frontend breaks
    if (!selectionSetList.includes('Owner') || !selectionSetList.includes('SharedWith')) {
      util.unauthorized();
    }
    const { groups } = ctx.identity;
    if (groups === undefined || !groups.includes("Admin")) {
      const me = ctx.identity.username;
      const myProjects = ctx.stash.myProjectIds || [];            // <-- ADDED
      const or = [
        { Owner:      { eq: me } },
        { SharedWith: { contains: me } },
      ];
      if (myProjects.length > 0) {                                // <-- ADDED
        or.push({ ProjectId: { in: myProjects } });
      }
      extensions.setSubscriptionFilter(util.transform.toSubscriptionFilter({ or }));
    }
    return null;
  }
  ```

> **Per-meeting block is NOT in the live filter.** We deliberately kept the live filter to
> `ProjectId in myProjects` (no `ExcludedUsers`) to avoid missing-field edge cases. A blocked member
> watching live is cut off on their **next refresh** (the read path in 3.2–3.4 enforces the block).

### 5.3 Client change (enables live streaming to project members)

The frontend's `onAddTranscriptSegment` subscription should **select `ProjectId`** in its GraphQL
selection set — the live filter can only match a field that's present in the payload. This is **not
enforced** (the mandatory guard still only requires `Owner`/`SharedWith`), so nothing breaks if it's
added later. Until it's added, project members simply see live meetings **on refresh**.

### 5.4 Live list updates — `onCreateCall`, `onUpdateCall`

Apply the same project branch to these two subscription filters so a new/updated project meeting
pops into members' lists without a refresh. Edge to know: a brand‑new call may not have `ProjectId`
yet (you link it on VP join, slightly after creation), so members receive it via the `onUpdateCall`
event once `ProjectId` is set — the meeting still appears live, just on the update rather than the
create.

**After Step 5:** project members see the meeting appear, open it, read the full transcript, **and
watch new lines stream in live** — with only a `ProjectId` tag on each line, no user names.

---

## Rollout order

1. Store each user's Cognito username (Prerequisite).
2. Create `LMASA-ProjectMembership` table (empty — harmless).
3. Turn on the dual‑write in my projects API (Step 2) + backfill existing members.
4. Write `ProjectId` on link (Step 1) + backfill already‑linked meetings.
5. Add data source + `GetUserProjects` function (Step 3.0).
6. Flip `listCalls*`, `getCall`, `getTranscriptSegments` to pipeline with the OR (Steps 3.1–3.3).
   → project members can now see the list, open meetings, and read transcripts (on load/refresh).
7. Live streaming (Step 5): stamp segments (5.1) → update `onAddTranscriptSegment` filter (5.2) →
   frontend selects `ProjectId`/`ExcludedUsers` (5.3) → live list updates `onCreateCall`/`onUpdateCall`
   (5.4). → project members now watch lines live and see meetings appear/update live.
8. (optional) Step 4 per‑meeting blocking.

Any resolver misbehaving → revert it to its old (UNIT) version → instant return to today's behavior.

---

## Test checklist

Project **P**, members {Owner O, Member M, Non‑member N}, meeting **X** linked to P:

- **List:** M sees X; N does not; O unchanged.
- **Open + transcript + summary + audio:** all work for M (full transcript returns fast even for a
  1‑hour meeting); N is denied.
- **Add member:** add N to P → N refreshes → sees X immediately.
- **Whole‑project block:** remove M from P → M refreshes → loses all P meetings.
- **Per‑meeting block:** add M to X's `ExcludedUsers` → M loses X only.
- **Explicit share wins:** individually share X with a blocked user → they see X again.
- **Existing individual sharing:** still works exactly as before.

---

## Not changed / already understood (for reference)

- **Meeting Assist Bot** — different path (`sendChatMessage` → StrandsChat Lambda → orchestrator
  reads DynamoDB directly). **Not affected** by these changes; works as today. It performs no access
  check of its own (pre‑existing), but in normal use it only assists a meeting you already opened.
- **Audio (.wav)** — access is gated at `getCall` (you only get the URL if authorized), so project
  members get it automatically. The S3 object itself is readable by any authenticated user who has
  the key — a pre‑existing property, not introduced here.
