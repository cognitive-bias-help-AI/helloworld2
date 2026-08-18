# C4.3 Claim Cardinality Amendment

## Status and scope

This amendment supersedes only the earlier per-Slot Claim cardinality rationale in
`STATE_LIFECYCLE_v2_2.md` W10. It does not modify the Frozen schema, ReviewState,
Graph, Runtime, Store, evidence nodes, or report contract.

## Previous policy

The previous Domain policy allowed at most one canonical Claim per Slot. When two
or more eligible Semantic Units shared a Slot, it classified the condition as
ambiguous and materialized no Claim.

## Conflict with the semantic model

A Slot describes the role that information plays in the user's decision. A
Semantic Unit is one meaning-bearing part of the user's input. A Claim is one
independently evidence-reviewable external proposition. These are distinct
objects, so one Slot can naturally contain multiple independent propositions.

For example, `HBM 수요가 증가하고 공급은 부족하다` yields two S4 Semantic
Units: demand is increasing and supply is insufficient. Treating those two valid
propositions as ambiguity loses user intent and prevents both evidence reviews.

## Amended authority

Claim cardinality is bounded globally per review run, not uniquely per Slot.

A Slot may yield zero or more independently evidence-reviewable Claims. Each
eligible Semantic Unit may materialize at most one canonical Claim. The total
number of canonical verifiable Claims materialized in one review run must not
exceed `MAX_VERIFIABLE_CLAIMS=8`.

Multiple valid Claims sharing one Slot are not, by cardinality alone, an
ambiguity, a missing-information condition, or a conflict.

Capacity evaluation is atomic. If the global limit would be exceeded, no Claim
from that candidate batch is materialized. Silent truncation and implicit
prioritization are prohibited. No per-Slot magic limits are introduced.

## Frozen capability

The Frozen `Claim` validates each `claim_id`, `slot_id`, span, proposition,
provenance, and timestamp independently. It has no validator requiring `slot_id`
uniqueness. The ReviewStore identity boundary is `claim_id`, and the documented
DDL has a non-unique `(run_id, slot_id)` index. Therefore multiple Claims with the
same Slot are already valid without a Frozen change.

## Capacity and ordering

`MAX_VERIFIABLE_CLAIMS` remains 8, so the approved `4 * claim_count + 9` LLM
ceiling remains 41 at maximum capacity. Candidate order is derived from the
semantic projection's global span start, global span end, Slot, and stable
original index; LLM output order is not canonical authority.

Exact duplicate global spans are invalid candidate metadata and are rejected
before capacity evaluation. Text similarity and embedding-based deduplication are
outside this amendment.

## Downstream impact

n5 Query construction, n7 ClaimEvidence, n8 ClaimEvaluation, and n9 Finding
lineage are keyed by `claim_id`, not by Slot uniqueness. With the global maximum
unchanged, their worst-case Claim workload and State ID count do not increase.
Production code for those nodes is unchanged in this phase.

The report layer does not yet guarantee a lossless Slot-to-many-Claim hierarchy.
That requires a separate report contract and is not implied by this amendment.

## Runtime and migration

The pure planner does not read ReviewStore and does not create canonical Claims.
Future Runtime integration will supply the existing canonical verifiable Claim
count. Runtime routing of capacity exhaustion to `BUDGET_EXCEEDED`/n12 remains a
separate Graph amendment.

The amended Domain policy is not yet connected to production n3. Existing legacy
n3 already accepts a list of Claims and does not enforce Slot uniqueness, so no
canonical data or Store migration is required.
