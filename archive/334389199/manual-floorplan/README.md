# Manual floor-plan reconstruction: Cian 334389199

This is a worked, reproducible example of reconstructing a **hypothesis** from the archived listing photos. It is not a measured floor plan, cadastral drawing, or building permit. All measurements below are either stated in the listing or explicitly marked as assumptions.

| Stage | Artifact | Result |
|---|---|---|
| 1. Inputs and photo grouping | [01-input/photo-index.md](01-input/photo-index.md) | Every one of the 30 photos assigned a role |
| 2. Visible evidence | [02-observations/room-observations.md](02-observations/room-observations.md) | Room shells, openings, fixtures, missing views |
| 3. Cross-photo matching | [03-matches/matches.md](03-matches/matches.md) | Repeated walls, objects, floor finishes, doorways |
| 4. Building research | [04-building/references.md](04-building/references.md) | Possible series-family leads; no verified matching plan found |
| 5. Connectivity | [05-topology/adjacency.md](05-topology/adjacency.md) | Proven and inferred room connections |
| 6. Candidate layouts | [06-candidates/candidates.md](06-candidates/candidates.md) | Two competing explanations of the bedroom exit |
| 7. Geometry and scale | [07-calibration/geometry.md](07-calibration/geometry.md) | Area reconciliation and assumed dimensions |
| 8. Plans | [08-output/candidate-A.svg](08-output/candidate-A.svg), [candidate-B.svg](08-output/candidate-B.svg), [plan.json](08-output/plan.json) | Editable vector drawings and machine-readable example |
| 9. Review | [09-review/open-questions.md](09-review/open-questions.md) | What can and cannot be resolved from this input |

The SVGs are **schematic net-area diagrams**. Relative compass direction, external wall shape, partition thickness, individual room dimensions, and exact opening positions remain unverified. The colors show a manually chosen arrangement that satisfies the three known area totals, not a photogrammetric result.

Evidence sources: [archived listing data](../listing.json) and [the 30 archived images](../images/). External building references are linked in stage 4. No archived listing files were changed for this example.
