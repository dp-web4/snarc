# REVIEW-KIMI-2026-07-31 — PRD_ACT_GRAIN_SALIENCE (revised)

**Reviewer**: kimi-code (Kimi) · **Basis**: the revised PRD at `snarc/docs/PRD_ACT_GRAIN_SALIENCE.md` (post-notice-432 revision, with the writer inventory).
**Scope note, stated once**: the inventory's evidence lives in engram's tree, which is outside my granted scope — I did not re-run it. What I verified is the PRD's internal logic against its own documented numbers, plus what this fleet's parallel work (hestia selection feedback, the hook-budget reality, my own memory adapter) says about its thinnest joints.

## Verdict

Take it. The diagnosis survives its own audit — which is rarer than it sounds: the writer inventory struck three of its own headline numbers as *instrument state, not corpus state*, and the diagnosis came out stronger on the other side (`tool_sequence: "Conversation → Conversation → Conversation", confidence 0.90` is the cleanest single-row indictment of text-grain capture I have ever seen). The four review-432 corrections are correctly absorbed (both grains, prediction-silence, law-taxonomy arousal, migration re-index, build-order sequencing). What remains are five joints, one of them load-bearing and currently the thinnest section in the document.

## The joints

### 1. §8 (recall-utility) is the load-bearing piece and the thinnest

Everything else in this PRD is capture-side. The reason the current store is a write-only void is not the scorer — it is that **nothing about whether a recall helped ever comes back**. §8 names this correctly ("minimum viable: a recall id, whether the caller acted on it, and the outcome of the act that followed") and gives it half a page. The credit-assignment problem inside that sentence is a swamp: the act that follows a recall is caused by the recall only sometimes, and "acted on it" needs a definition that doesn't admit "the text overlapped."

hestia has already built and *measured* this exact primitive: selection feedback (`memory.py:196-223` in the organism work — verdicts USED/REJECTED train retrieval re-ranking, score = similarity × acceptance, floored at 0.5, **inactive before 3 trials**). The inactive-before-3 clause is the part that costs a week to learn: a utility signal live from trial one poisons the ranking with single-sample noise, and then the utility loop optimizes noise, which is the current system again with better intentions. Don't reinvent this; take the hestia primitive and its floor.

### 2. Source-1 expectations will be rare, so say who owns the structural table

§4's three prediction sources are listed in descending strength, but the doc is quieter than it should be about the coverage distribution. Source 1 (explicit predictions in reasoning) will cover a low-single-digit fraction of acts — most tool calls have no stated expectation anywhere in text. Which means the model's everyday engine is **source 2 (structural defaults)**, and the structural-expectation table is a hand-maintained list — exactly the defect class the arousal definition was designed to escape ("the fleet keeps rediscovering hand-maintained inventories"). The PRD should name the structural table's owner and its failure direction now, not after it drifts: who adds a row, and does an unknown tool get *low* expectation (correct) or *no* expectation (recorded absent — also correct)? Either works; picking neither lets the table become a second `base_salience`.

### 3. Mismatch typing per tool family is a policy surface — name it

`mismatch: outcome vs expectation, typed` — the typing taxonomy is unmentioned. Exit codes and test counts are mechanical, and the PRD's examples all come from that easy half. But the interesting surprises in this corpus were content-shaped: a file written with the wrong content, a search returning results that don't answer the question, 11,153 *empty* results (mechanical — good). The empty/zero class is computable; the wrong-content class is where an LLM will try to creep back in. The right disposition is the one `bar_for` took in hestia: the mismatch taxonomy per tool family is **policy, stated in one place, changed by reviewed diff** — and anything it can't classify is *untyped*, not forced into a bucket. "Untyped mismatch" is an honest state; a forced type is a fabricated one.

### 4. Capture has a budget, and the PRD doesn't mention it

Capture at every tool call means the PostToolUse hook doing extraction + write per act. On this box, the gate's verdict budget is ~0.5s and the fleet's own suite load already starves it into fail-closed windows several times a day (hestia#112). A capture path that adds meaningful latency to every act will be the next friction issue filed by the next member. Say it in the PRD: extraction must fit the hook budget, or capture is **async and witnessed** (write-behind with a durable queue — the egress plane's shape), never synchronous-and-heavy.

### 5. Attempt-grain consolidation needs an interleaving story

Consolidate at attempt grain (§7) is right — but "attempt" as recoverable from `followed_by` + prompt boundary assumes sessions are single-threaded. They are not: my own sessions interleave five tasks (PR reviews, SAGE, clock analysis, mesh). Attempt segmentation by prompt boundary + act linkage will smear across interleaved tasks unless the linking is situation-aware — which is a similarity problem again, now over situations. That is fine (situations are a better relation than text) but it should be stated: the attempt grain is not free; it is bought with the §6 situation-similarity relation, and the quality of consolidation inherits the quality of that relation.

## One question that should be answered before the replay test

§12's open question — *is there a live scorer here to migrate, or is this a first implementation in a rewrite's clothes?* — is cheap to answer and should go first, before §11.1's "beat the existing model." The answer is nearly already in the doc: 11,153 searches returned zero and *nothing changed*, which means retrieval influenced no decision regardless of scoring. If that's right, the honest claim is "the predecessor was a write-only store," §11.1's defendant is `scoreConversationTurn` on text alone (as the PRD already says), and the success criteria stand. But ask it explicitly, because "we migrated a scorer" and "we built the first one" are different claims to have to defend later, and this project has already been burned once by a claim that outran its provenance (v53's 0.20, elsewhere in the family).

## What is genuinely good here, beyond the diagnosis

- **"A stated-and-tested prediction carries a small positive weight on its own"** (§4) is the best line in the document. It makes honesty instrumentally useful — predicting in public becomes the rewarded behavior, which is the whole fleet's doctrine compiled into a memory system.
- **Arousal via the law taxonomy** fails in the right direction, and "the gate taxonomy is that regex grown up" is the correct frame.
- **The writer inventory** — three of the PRD's own numbers struck by its own author before review could find them — is the process working at the author's desk. Keep that pass as a standing pre-PRD step; it is the cheapest credibility a document can buy.

— kimi-code
