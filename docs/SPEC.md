# Change Impact Engine — v1 spec (2026-11-09)

Single source for the code, the site write-up and the video. If the code changes, this changes.

## Product used in the demo (invented)
**IP-200 infusion pump**, made by a fictional medical-device maker. No real company. Medical devices are a good fit:
FDA design controls (21 CFR 820) require traceability between requirements, design and verification, and every
design change needs documented impact review and approval.

## Three mock systems of record (each its own REST router, its own tables)
| System | Prefix | Holds | Real-world equivalent |
|---|---|---|---|
| Requirements | `/alm` | `REQ-*` requirements, parent/derived links | ALM / requirements tool |
| Parts | `/plm` | `PRT-*` parts and assemblies, BOM (parent contains child) | PLM / BOM |
| Tests | `/tests` | `TST-*` verification tests | Test management |

Every item has: `id`, `title`, `owner` (a role, e.g. "Firmware Lead"), `revision`.

## Trace links (the digital thread)
| Link | Meaning |
|---|---|
| `REQ -> REQ` `derives` | child requirement derived from parent |
| `REQ -> PRT` `satisfied_by` | part implements requirement |
| `REQ -> TST` `verified_by` | test verifies requirement |
| `PRT -> PRT` `contains` | assembly contains part (BOM) |
| `TST -> PRT` `exercises` | test runs on that part |

## Impact rules (what a change touches)
Change to a **part**: its parent assemblies (up the BOM, recursively), requirements it satisfies, tests that exercise it
or any of those parent assemblies, and tests that verify the affected requirements.
Change to a **requirement**: its derived child requirements (recursively), parts that satisfy any of them, tests that
verify any of them.
Each impacted item is returned with `depth` (hops from the changed item) and `reason` (the link it came through).
Implemented as a recursive SQL query over one `trace_link` table.

## Change request lifecycle
`POST /changes` (target item + description) -> state `submitted` -> impact computed -> approvers assigned ->
`in_review` -> each approver `approve` / `reject` -> `approved` (all approve) or `rejected` (any reject).
**Approvers** = the owner of the changed item, the distinct owners of every impacted item, plus the `Configuration Manager` (always).
Every affected test is listed as a **re-test required** item on the approved change.

## Events (outbox pattern)
Systems of record don't call the engine directly. A change writes an `item.changed` event to an `outbox` table in the
same transaction. A worker (`python -m cie.worker`, or `POST /admin/process-events`) reads the outbox:
- **Idempotent:** each event id is recorded in `processed_event`; a repeat is skipped, so a crash and replay never
  double-processes.
- **Retries:** a failing event is retried up to 3 times, then moved to `dead_letter` with the error.
In v1 the outbox is a SQLite table; the roadmap swaps it for SQS/EventBridge without changing the consumer logic.

## Audit trail
`audit_log` is append-only: SQLite triggers abort any UPDATE or DELETE. Each row stores `prev_hash` and `hash`
(SHA-256 of prev_hash + row content), so editing history breaks the chain. `GET /audit/verify` re-walks the chain.
Mapped to configuration-management practice (EIA-649 / ISO 10007: identify, control, status-account, audit).

## Review page
`GET /` one screen: pick a change -> impact tree grouped by system -> approvers with status -> approve/reject.
Not a dashboard.

## v1 scope vs roadmap
| v1 (tonight) | Roadmap |
|---|---|
| FastAPI, SQLite, outbox + worker, triggers, pytest, Dockerfile | Postgres, SQS/EventBridge, Terraform on AWS (GovCloud-aware), OIDC auth, CI with scan/SBOM |
