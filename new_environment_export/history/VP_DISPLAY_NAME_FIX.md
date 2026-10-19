# Virtual Participant Display Name — Diagnosis

_Last updated: 2026-07-20_

**What this document is:** *why* the Virtual Participant showed the wrong join name, and why the
obvious fixes fail. It is the reasoning record.

**For what was actually changed** (every resource, backup, and rollback), see
[VP_CHANGES_APPLIED.md](VP_CHANGES_APPLIED.md). That document is the source of truth for the live
state; this one explains how we got there.

There were **two independent bugs**, and the second only became visible once the first was fixed:

1. **The GUID** — the bot joined as `microsoftentraid_<guid>` instead of a person (§1–§5).
2. **The typing race** — Teams silently dropped characters from whatever name was typed (§6).

---

## Part One — The GUID

## 1. The Problem

A Virtual Participant joined showing:

```
LMA (microsoftentraid_5b3fe156-6142-4297-a633-dcb04244cd30)
```

instead of the person who scheduled it. The same value was also announced in the intro message, so
it was visible **and** audible.

> **The bot is not printing "Microsoft Entra ID" as a literal name.** It is printing the Cognito
> *username* of whoever scheduled it. Because the user pool is federated with Microsoft Entra ID,
> that username is a generated key — not an email.

## 2. Background: Two Facts That Cause This

| Term | What it means here |
|---|---|
| **Federated user** | Someone who signs in via Entra ID instead of a Cognito password. Cognito creates a local shadow account for them. |
| **Cognito username** | The primary key of that shadow account. For federated users Cognito generates it as `<provider>_<subject-id>`. Built to be unique and stable — **not** human-readable. |
| **Entra object ID (oid)** | The `<subject-id>` half. Entra identifies people by GUID, so the username inherits a GUID. |
| **Claims** | Key/value facts inside the login token. **Which claims exist depends on the token type** — see §4b. This is what made the first fix attempt fail. |
| **`$ctx.identity`** | How an AppSync resolver reads the caller. `$ctx.identity.username` → the GUID key. `$ctx.identity.claims` → the token's claims. |

## 3. Root Cause: One Value, Three Hops

The value is copied verbatim through three systems. Nothing transforms it. The bug is that its
*meaning* silently changes from "ownership key" to "display name" at hop 2.

**Hop 1 — AppSync resolver stamps the record** (`Mutation.createVirtualParticipant`):

```vtl
$util.qr($input.put("owner", $ctx.identity.username))
$util.qr($input.put("Owner", $ctx.identity.username))
```

Correct as an ownership key. Written to DynamoDB as
`owner = microsoftentraid_5b3fe156-…`.

**Hop 2 — VPScheduler copies it into the container environment** (the original `index.py` line 66):

```python
{'Name': 'LMA_USER', 'Value': new_image.get('owner', {}).get('S', '')},
```

**This is where the bug lived.** An ownership key becomes an identity label called `LMA_USER`.
Straight copy, no lookup.

**Hop 3 — The container interpolates it into the join name** (task definition, container `scribe`):

```
LMA_IDENTITY  = "LMA ({LMA_USER})"
INTRO_MESSAGE = "...invited by {LMA_USER} to join this meeting..."
```

Two placeholders, one variable — so the GUID surfaced twice.

## 4. Why the Obvious Fixes Don't Work

Recorded so nobody re-derives them.

### 4a. Strip the `microsoftentraid_` prefix — FAILS

The remainder is a **GUID**, not an email.

| Assumed stored value | Actual stored value |
|---|---|
| `microsoftentraid_tarun.kanakala@dimensioncg.com` | `microsoftentraid_5b3fe156-6142-4297-a633-dcb04244cd30` |

No string manipulation recovers an email, because the email is not in the value.

### 4b. Read `$ctx.identity.claims.email` in the resolver — FAILS

This was the **first fix attempted, and it did not work.** The frontend authenticates to AppSync
with a Cognito **access token**, not an ID token. Confirmed from `StrandsChatInterfaceFunction`'s
logged event:

```json
"identity": {"claims": {
  "token_use": "access",
  "scope": "phone openid profile email",
  "username": "microsoftentraid_8cd599c0-d8c6-45..."
}}
```

**Cognito access tokens never contain an `email` claim. Only ID tokens do.** So
`$ctx.identity.claims.get("email")` is always empty.

> **Trap:** the `scope` list contains `email`, which looks like proof the email is present. It isn't.
> Scope governs what the **userInfo endpoint** returns; it does not add claims to an access token.

The email attribute *does* exist in the user pool (`admin-get-user` returns it) and the SAML mapping
is correct. The data was never the problem — the **token** was.

### 4c. Change `owner` to the email — REJECTED (would break security)

`Owner` is compared against the caller's username to decide which participants a user may see
(`Query.listVirtualParticipants`):

```vtl
#if($item.Owner == $context.identity.username || $item.SharedWith.contains($context.identity.username))
```

Rewrite `Owner` to an email and that equality fails against every existing record — users lose sight
of their own participants.

> The ownership key and the display name are **two different jobs**. They need two different fields.

## 5. The Fix: Look It Up at Launch Time

Since the email is not in the token, it comes from the user pool. VPScheduler has the username
(`owner`), so it calls `cognito-idp:AdminGetUser` and reads the attributes directly.

This later grew into an enhancement — the display name became **`<First name>'s AI Assistant`**
rather than the bare email. VPScheduler now resolves **two** values per launch:

```
LMA_USER     = email                    -> intro message ("...invited by ...")
LMA_IDENTITY = "<First>'s AI Assistant" -> participant list
```

Display-name formula (function `resolve_vp_identity`), in order:

1. Cognito `given_name`, **first word only** — Entra stores middle names too, so
   `Bala Yogeswara Singh` → `Bala`.
2. else the email's local part before the first `.` — `ashiesh.jaitly@…` → `Ashiesh`.
3. capitalise the first letter only — `vamsikrishna` → `Vamsikrishna` (keeps `McDonald` intact).
4. if nothing resolves → `LMA (<user>)`, the original format.

Tested against all 37 pool users: **0 blanks** (30 via `given_name`, 7 via the email fallback).

> The exact deployed code, the supporting env var, and the IAM grant are documented in
> [VP_CHANGES_APPLIED.md §2.2–§2.5](VP_CHANGES_APPLIED.md). The AppSync resolver *also* writes an
> `ownerEmail` attribute, but it is currently inert — access tokens carry no email claim, so it is
> only future-proofing for a possible switch to ID tokens.

### Why already-scheduled meetings needed a separate backfill

VPScheduler freezes the whole `ecs:RunTask` request — including `LMA_USER` — into the EventBridge
schedule **at creation time**. EventBridge replays that JSON verbatim. So fixing the code did **not**
fix the 14 meetings already booked; their JSON was frozen with the GUID. They were rewritten
in place (see [VP_CHANGES_APPLIED.md §2.9](VP_CHANGES_APPLIED.md)).

---

## Part Two — The Typing Race

## 6. Teams Silently Drops Characters

Once the email reached the container correctly, a **second, unrelated bug** surfaced inside the
browser. It had been hidden all along: the `LMA (` prefix was acting as a sacrificial buffer,
absorbing the loss so the email survived. Removing the prefix exposed it.

### Symptom

| Name the container tried to type | What Teams displayed | Lost |
|---|---|---|
| `LMA (yogeswarsingh.bondili@dimensioncg.com)` | `Lyogeswarsingh.bondili@dimensioncg.com)` | `MA (` (4) |
| `yogeswarsingh.bondili@dimensioncg.com` | `y` | everything after `y` (36) |
| `Bala's AI Assistant` | `BI Assistant` | `ala's A` (7) |

### Cause

The container types the name in **character-by-character** and never checks the result. Teams'
anonymous pre-join is a React app that floats a transient shroud over the input **while it finishes
hydrating** — and any keystrokes typed during that window are silently swallowed. `waitForSelector`
resolves when the element *exists*, not when it is *ready*, so typing starts too early.

The loss is a **duration, not a character count**: however long the shroud sits there, that many
keystrokes die. The first character lands, a middle chunk vanishes, the tail appends to the first —
which is why the amount lost differs every run. No fixed prefix could ever have absorbed that
reliably.

### The fix

Type, **read the field back, and retype if it doesn't match**, up to 6 attempts. The project uses
**Puppeteer** (`puppeteer ^24.22.1`), so there is no atomic `fill()` — the verify-and-retry loop is
the only reliable approach. If all attempts fail it logs and **joins anyway** — a wrong name beats a
lost recording.

This required rebuilding the container image, because the typing code is compiled into it. The full
build method, the two-line diff, and the image tags are in
[VP_CHANGES_APPLIED.md §2.6](VP_CHANGES_APPLIED.md).

### Proof

The bug reproduced live and corrected in the same meeting:

```
+40.2s  Entering name.
+47.0s  Display name mismatch (expected "Bala's AI Assistant", got "BI Assistant") - retrying 1/6.
+48.1s  Display name entered correctly on attempt 2.
```

A `mismatch` line is the fix **working** — the bug happened and was caught. Before the patch the same
truncation happened silently, because nothing was checking.

Verified on **Teams** (retry fired, corrected on attempt 2) and **Zoom** (clean on attempt 1 — no
hydration shroud, which is why Zoom was never visibly broken). `webex.ts` / `chime.ts` were not
patched — Teams and Zoom only.

---

## 7. Related Observation (Not Fixed)

`scheduledFor` is always `NULL` in the VP table:

```
"meetingTime":{"N":1784215200}, "scheduledFor":{"NULL":null}
```

`$util.math.multiply($ctx.args.input.meetingTime, 1000)` overflows 32-bit int on epoch seconds, so
`epochMilliSecondsToISO8601` receives null. Pre-existing and unrelated. Harmless for launching —
VPScheduler uses `meetingTime`, not `scheduledFor`.

---

## 8. Current State & Open Items

Both bugs are fixed on both meeting paths (scheduled and join-now). The precise resource-by-resource
state, rollback map, and drift warnings live in **[VP_CHANGES_APPLIED.md](VP_CHANGES_APPLIED.md)**.

The headline open items, in short:

- **Everything is a live CLI patch** — a CloudFormation deploy, or a re-run of the container
  CodeBuild project, reverts it. The durable fix is to **fork LMA at the 0.3.2 tag** and publish from
  your own bucket. Upstream `main` already fixes the typing bug, but migrated Puppeteer → Playwright,
  so its file cannot be dropped into 0.3.2.
- **Name formats differ** between paths (`Bala's AI Assistant` vs `LMA (email)`) — both correct,
  unifying them is deferred to the fork.
