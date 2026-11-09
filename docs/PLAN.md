# Change Impact Engine (flagship)

Approved 2026-11-09 (Jake).

## Context
Portfolio today: analytics work (Flight Delay, ALKEME), the MIS 331 database redo (queued), and the Hardened LLM
Container Platform (queued, a *skills* project for Docker/AWS/security). What's missing is the **flagship**: a real
business problem solved with an integration system. Standing rules from `career`: grows out of business consulting +
Digital Engineering IT; uses REST APIs, integration, event processing, digital-thread ideas, GovCloud-aware design;
NOT an AI wrapper, dashboard, generic website, broad Syndeia clone, or aviation/supply-chain-only product.

## Recommendation
**Change Impact Engine** — when an engineering change is proposed ("swap this part", "revise this requirement"),
answer in seconds: what else does it touch, who must approve, and what has to be re-tested.

- **Business problem:** at any manufacturer (aerospace, medical devices, automotive, industrial), a change sits across
  three systems that don't talk: requirements (ALM), parts/BOM (PLM), and test records. Impact analysis is done by hand
  in spreadsheets and meetings; missed links cause rework, failed audits and escapes. Mid-size makers can't afford
  enterprise digital-thread suites.
- **What it does:** a change request comes in as an event -> the engine walks the trace graph across the three systems ->
  returns affected requirements, parts, tests and owners -> opens an approval route -> writes an immutable audit trail
  (who approved what, against which baseline).
- **Why it fits Jake:** consulting story (process map of today's change board, cost of a missed link, recommendation),
  MIS story (systems of record, integration, governance), digital engineering vocabulary, without any work content.

## Architecture (v1)
```
mock ALM / PLM / Test services (FastAPI, seeded public-style sample data, each its own REST API + DB)
  -> adapters publish change events -> queue (Redis Streams locally, SQS/EventBridge on AWS)
  -> impact service: trace graph (Postgres + recursive queries), impact report API, approval workflow, audit log
  -> thin review page (one screen: change -> impact tree -> approve/reject). Not a dashboard.
Infra reuses the LLM project's controls: Docker Compose, hardened images, CI scan, Terraform, GovCloud-aware notes
(region-agnostic Terraform, no services unavailable in GovCloud, FIPS endpoints documented).
```

## Milestones (each a GitHub release + one jakemoore.dev write-up)
1. Business case: today's change process map, cost model, requirements, ERD (reuses MIS 331 skills).
2. Three mock systems of record with REST APIs and seeded data (an invented product, e.g. a small drone or infusion pump).
3. Trace graph + impact API (given a changed item, return everything downstream, with depth and owner).
4. Event flow: change events through the queue, idempotent consumers, retries, dead-letter queue.
5. Approval workflow + audit trail mapped to public configuration-management standards (EIA-649, ISO 10007).
6. AWS deploy with Terraform, then teardown; one 5-7 min video in the @JakeMooreEdu episode format.

## Order and timing
Finish the LLM container milestones 1-3 first (they give the Docker/CI base this reuses), then start this in
December 2026 so milestones 1-4 are live for spring 2027 interviews.

## Boundaries
- Nothing from Honeywell: no tools, data models, program names, process docs. Every design choice cites public
  standards or vendor docs. Site framing: "a personal project on engineering change management."
- Invented product and sample data only; no real company profiled.

## Verification (once built)
- Seed scenario: change one part -> impact report lists the expected requirements/tests (unit tests assert the set).
- Kill the consumer mid-stream -> no lost or duplicated events after restart.
- Audit log is append-only (update/delete attempt fails).
- `terraform apply` -> demo -> `terraform destroy`; AWS bill under the $10 budget alarm.

