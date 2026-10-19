# Project Tagging — Race Condition and Missed `ProjectId` Tags

_Found: 2026-10-05 · Account 528757797189 · us-east-1 · Status: **open**, no fix applied yet_

**In one line:** when more than one person schedules an LMA bot for the same Teams meeting, only
one of the resulting LMA calls gets a `ProjectId`, and which one is decided by a sub-second race.
The other calls stay invisible to project members.

Related: [PROJECT_ACCESS_STATUS.md](PROJECT_ACCESS_STATUS.md) (how project access works),
[BACKEND_INTEGRATION_SPEC.md](BACKEND_INTEGRATION_SPEC.md) §7–8 (timing edge cases, reconcile job).

---

## 1. How a `ProjectId` is supposed to reach the EventSourcing table

Four hops, across two systems:

| # | System | Record | What happens |
|---|---|---|---|
| 1 | Synthera (Mongo) | `meetings` | A user's calendar event is synced in. The user links it to a project in the UI, which sets `project_ids` on **their** copy of the meeting. |
| 2 | Synthera (Mongo) | `lma_bot_tracking` + LMA `VirtualParticipant` table | A system job schedules one LMA bot per `meetings` document that has `lma_join = true`. |
| 3 | Synthera (Mongo) | `active_meetings_board` | When the meeting goes live, Synthera creates **one** board document per Teams meeting id, copies `project_ids` from the linked meeting record, and records the LMA call(s) on it. |
| 4 | AWS | `LMASA-MeetingEnrichment` Lambda → `EventSourcingTable` | On every new `c#` row, the Lambda looks up the board by `lma_call_id == CallId` and, if found, writes `ProjectId` onto the `c#` and `cls#` rows in one transaction. |

Project members can then see the meeting because the AppSync resolvers check
`ProjectId ∈ LMASA-ProjectMembership[user]` at read time.

Hop 4 is in [project_tagger.py](new_environment_export/lambda/enrichment_lambda_source/project_tagger.py).
The lookup is:

```python
doc = get_collection("active_meetings_board").find_one(
    {"lma_call_id": call_id},          # singular field only
    {"project_ids": 1, "_id": 0},
)
```

The Lambda is triggered **only by INSERT events** on `c#` / `cls#` rows (event-source-mapping
filter). It never runs again for a row once it has been inserted.

---

## 2. The incident — "Order to Cash (O2C) - Requirement Gathering", 2026-10-05

### 2.1 Setup

Three people were invited to the same Teams meeting (`platform_meeting_id = 298238043493289`).
Each one's calendar sync produced a separate `meetings` document, and each got its own bot.

| Person | Synthera `meeting_id` | `project_ids` on their copy | Bot scheduled (UTC) | LMA CallId suffix |
|---|---|---|---|---|
| Mihir Thakkar | `meeting_01M468SQBTWKDEC6SN22RGMV5Y` | `[project_01M35DE9AWVQK3HHBDM7MS1S1A]` (DeTect) | 15:22:04 | `…17-29-52-855` |
| Vamsi Solleti | `meeting_01M4690RE439BYES8VMCKN34MB` | `[]` | 15:22:08 | `…17-29-39-930` |
| Prasad Gudipaty | `meeting_01M468SXGBYP65DBK1AHGGJ3FF` | `[]` | 15:22:08 | `…17-29-32-046` |

Only Mihir linked the meeting to a project (his record's `updated_at` is 17:19:49, ten minutes
before start). The board document was built from his record, so the board carried the DeTect
project from the moment it was created.

### 2.2 Timeline (from the Lambda log, DynamoDB and Mongo)

| Time (UTC) | Event | `lma_call_id` on the board at that instant | Result |
|---|---|---|---|
| 17:30:40.359 | Prasad's `c#` row inserted | board does not exist yet | — |
| 17:30:41.256 | Board document created (`created_by = mihir`, `project_ids = [DeTect]`) | Prasad's CallId | — |
| 17:30:42.845 | Lambda processes Prasad's insert | Prasad's CallId | **match → tagged** |
| 17:30:43.954 | Lambda processes Vamsi's insert | Prasad's CallId | no match → `no project for call_id` |
| 17:30:57.169 | Mihir's `c#` row inserted | Prasad's CallId | — |
| 17:30:57.720 | Lambda processes Mihir's insert | Prasad's CallId | no match → `no project for call_id` |
| 17:30:58.289 | Synthera syncs the board to Mihir's bot (`synced_at`) | **now Mihir's CallId** | 0.5 s too late, Lambda already ran |

### 2.3 Outcome

| LMA call | Owner | `ProjectId` on `c#` + `cls#` | Visible to DeTect project members? |
|---|---|---|---|
| `…17-29-32-046` | Prasad | `project_01M35DE9AWVQK3HHBDM7MS1S1A` | yes |
| `…17-29-39-930` | Vamsi | **missing** | no |
| `…17-29-52-855` | Mihir (the person who linked the project) | **missing** | no |

The board document today shows the full picture that the Lambda never saw:

```
lma_call_id  : "…17-29-52-855"                         # singular, last writer wins
lma_call_ids : ["…17-29-32-046", "…17-29-39-930", "…17-29-52-855"]   # all three
project_ids  : ["project_01M35DE9AWVQK3HHBDM7MS1S1A"]
```

---

## 3. The issues

### Issue 1 — Synthera overwrites a single `lma_call_id` on a shared board document

The board is **one document per Teams meeting**, but it has a **single** `lma_call_id` field.
Each bot's sync rewrites it. The field therefore means "the most recently synced bot", not "the
bot that belongs to the linked project". Any consumer that keys on it sees a moving target.

Synthera already maintains `lma_call_ids` (plural) with every session. The singular field is the
only thing wrong here.

### Issue 2 — The enrichment Lambda matches on the singular field only

`project_tagger.py` queries `{"lma_call_id": call_id}` and ignores `lma_call_ids`. Combined with
Issue 1, at most **one** of N sessions can ever match, and which one depends on sub-second
ordering between DynamoDB stream delivery and Synthera's sync writes.

### Issue 3 — INSERT-only trigger means a late link is never picked up

The Lambda runs once per row, at insert time. Anything that changes **after** that is invisible to it:

- the board document being created a second after the `c#` row (Prasad's case would have failed
  too if his insert had been processed 1.5 s earlier);
- a user linking the meeting to a project after the call started;
- Synthera rewriting `lma_call_id` (Issue 1).

[BACKEND_INTEGRATION_SPEC.md](BACKEND_INTEGRATION_SPEC.md) §8 calls for a periodic reconcile job
to repair exactly this. It has not been built.

### Issue 4 — One Teams meeting fans out to N LMA calls

Every invitee whose calendar is synced gets their own `meetings` document and their own bot. One
meeting produced three calls, three transcripts and three recordings. Project membership is
checked per call, so a project member who opens "the meeting" may land on an untagged copy and be
denied, while a tagged copy of the same meeting sits next to it. This is a product question as
much as a bug, but it is what makes Issues 1–3 visible.

---

## 4. Impact

- Project members cannot see any session that lost the race. In this incident that includes the
  session owned by the person who linked the project.
- The failure is **silent**: the Lambda logs `no project for call_id` at INFO level and returns
  success; nothing alerts.
- It fails **closed**, so nobody gains access they should not have. Only visibility is lost.
- Every multi-invitee meeting with `lma_join` enabled is exposed, not just this one.

---

## 5. Fixes

### 5.1 Immediate — tag the two missed sessions by hand

Write `ProjectId = project_01M35DE9AWVQK3HHBDM7MS1S1A` onto both rows of each session, one
`TransactWriteItems` per session, following §4 of the backend spec:

| Session | `c#` key (PK = SK) | `cls#` PK | `cls#` SK |
|---|---|---|---|
| Mihir | `c#Order to Cash (O2C) - Requirement Gathering-2026-10-05-17-29-52-855` | `cls#2026-10-05#s#04` | `ts#2026-10-05T17:30:57.169Z#id#Order to Cash (O2C) - Requirement Gathering-2026-10-05-17-29-52-855` |
| Vamsi | `c#Order to Cash (O2C) - Requirement Gathering-2026-10-05-17-29-39-930` | `cls#2026-10-05#s#04` | `ts#<CreatedAt of that c# row>#id#…17-29-39-930` |

Reversible with `REMOVE ProjectId` on the same keys.

### 5.2 Lambda — match the list, not the scalar (one-line change)

In `project_tagger.py`:

```python
doc = get_collection(_BOARD_COLLECTION).find_one(
    {"$or": [{"lma_call_id": call_id}, {"lma_call_ids": call_id}]},
    {"project_ids": 1, "_id": 0},
)
```

Mongo matches a scalar against array elements, so this finds the board for every session listed
in `lma_call_ids`. With this change all three O2C sessions would have been tagged. Redeploy via
`permanent_patches/lambda/enrichment_lambda/` and update the saved source there and in
`new_environment_export/`.

### 5.3 Synthera — stop treating `lma_call_id` as canonical

Either keep `lma_call_id` fixed to the first bot that syncs, or drop it in favour of
`lma_call_ids` everywhere. Any other consumer keyed on the singular field has the same race.

### 5.4 Reconcile job — close the timing gap for good

A scheduled job (hourly is fine) that:

1. reads every `active_meetings_board` document with non-empty `project_ids`;
2. for each CallId in `lma_call_ids`, reads the `c#` row and, if `ProjectId` is missing or
   different, writes it to `c#` + `cls#` in one transaction;
3. logs every repair so drift is visible.

This also covers "linked after the call started", which no stream trigger can.

### 5.5 Observability

Emit a CloudWatch metric or structured log on every `no project for call_id` outcome, and alarm
when the count per hour is non-zero for meetings whose board has `project_ids`. Today the miss is
indistinguishable from "meeting has no project".

---

## 6. How to verify after fixing

1. Schedule bots for one Teams meeting from **two** users; link the meeting to a project from
   only one of them.
2. Let the meeting start. Within seconds, both `c#` rows and both `cls#` rows must carry the same
   `ProjectId`. Check the Lambda log for two `project-tag: … → ProjectId=` lines and zero
   `no project` lines.
3. Log in as a project member who owns neither session. Both meetings must appear in the list,
   open, and show the transcript.
4. Link a third user's copy to the project **after** the meeting has started. After the next
   reconcile run, that session must be tagged too.

---

## 7. Evidence captured on 2026-10-05

Lambda log group `/aws/lambda/LMASA-MeetingEnrichment`, 17:30:40–17:30:58 UTC:

```
project-tag: c#…17-29-32-046 + cls#2026-10-05#s#04 / ts#2026-10-05T17:30:40.359Z#id#… → ProjectId=project_01M35DE9AWVQK3HHBDM7MS1S1A
project-tag: no project for call_id=Order to Cash (O2C) - Requirement Gathering-2026-10-05-17-29-39-930 — nothing to tag
project-tag: no project for call_id=Order to Cash (O2C) - Requirement Gathering-2026-10-05-17-29-52-855 — nothing to tag
```

Mongo `active_meetings_board` `_id 6ac3dec1daea179feb784f2b`: `created_at 17:30:41.256`,
`synced_at 17:30:58.289`, `lma_call_id = …52-855`, `lma_call_ids` = all three, `project_ids = [DeTect]`.

DynamoDB `c#…52-855` and `cls#2026-10-05#s#04 / ts#2026-10-05T17:30:57.169Z#id#…52-855`: no `ProjectId`.
DynamoDB `c#…32-046` and its `cls#` row: `ProjectId = project_01M35DE9AWVQK3HHBDM7MS1S1A`.
