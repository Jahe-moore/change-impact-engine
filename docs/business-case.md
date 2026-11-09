# Business case

## The problem
A medical-device maker changes its product constantly: a supplier discontinues a sensor, firmware gets a fix, a
requirement tightens after a field complaint. Each change has to be reviewed for impact before it is approved:
which requirements does it touch, which assemblies, which verification tests must run again, and who has to sign off.
FDA design controls (21 CFR 820) and configuration-management practice (EIA-649, ISO 10007) both expect that review to
be traceable.

The trouble is that the answer lives in three systems that don't talk to each other:

| System | Holds | Owned by |
|---|---|---|
| Requirements tool (ALM) | what the product must do | systems engineering |
| PLM / bill of materials | what the product is made of | mechanical and electrical |
| Test management | how each requirement is proven | verification and validation |

## How it works today
1. An engineer files a change request with a description.
2. The change board coordinator emails the leads of each area.
3. Each lead searches their own system and replies with what they think is affected, usually in a spreadsheet.
4. The coordinator merges the answers, schedules a review meeting and chases approvals.
5. Someone later updates each system by hand.

**What breaks:** the links between systems live in people's heads and spreadsheets. A missed link means a test that
should have been re-run wasn't, which surfaces in an audit or, worse, in the field.

## The fix
Put the cross-system links in one place (a trace graph), and let a change in any system of record trigger impact
analysis automatically. The engine returns every affected item with the reason it is affected, routes the change to
exactly the owners of those items plus configuration management, and records every step in a tamper-evident audit
trail.

## Impact (illustrative model; every input is an estimate, change them to fit a real company)
| Input | Estimate |
|---|---|
| Engineering changes per month | 60 (720 a year) |
| People-hours to assess one change by hand | 6 |
| People-hours with the engine (review the generated report) | 1 |
| Loaded engineering cost per hour | $75 |
| Share of manual assessments that miss an affected test | 2% |

| Result | Calculation | Value |
|---|---|---|
| Hours saved per year | 720 x (6 - 1) | 3,600 hours |
| Cost saved per year | 3,600 x $75 | $270,000 |
| Missed re-tests avoided per year | 720 x 2% | about 14 |

The hours are the visible saving. The 14 missed re-tests are the real risk: each is a potential audit finding or
field issue, and the engine finds them by walking the links instead of relying on memory.

## Recommendation
Start with the trace graph and impact report alone, read-only, on one product line. It needs no change to how anyone
works, and it shows within a month whether the generated impact matches what the change board finds by hand. Add
automatic routing and the audit trail once the links are trusted.
