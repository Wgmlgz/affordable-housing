# Stage 7 — Area reconciliation and geometric calibration

## Exact listing constraints

From [listing.json](../../listing.json): total **52.1 m²**, combined living rooms **29.8 m²**, kitchen **7.3 m²**. Therefore all other interior space sums to **15.0 m²** (`52.1 − 29.8 − 7.3`). No separate room areas or measured wall lengths were supplied.

## One manually fitted realization

| Space | Model dimensions | Model area | Status |
|---|---:|---:|---|
| Living room | 4.25 × 4.00 m | 17.00 m² | Split of the known 29.8 m², assumed |
| Bedroom | 3.20 × 4.00 m | 12.80 m² | Split of the known 29.8 m², assumed |
| Kitchen | ≈2.439 × 2.993 m | 7.30 m² | Area known, sides assumed |
| Bathroom | 1.70 × 1.80 m | 3.06 m² | Assumed |
| Toilet | 0.90 × 1.333 m | 1.20 m² | Assumed |
| Wardrobe | 1.30 × 2.00 m | 2.60 m² | Assumed |
| Remaining corridor | Irregular connected polygon | 8.14 m² | Residual, assumed shape |
| **Total** |  | **52.10 m²** | Matches listing by construction |

The resulting *net-area canvas* is 7.45 m wide and `52.1 / 7.45 = 6.9933 m` deep. The drawn partitions have zero thickness and the exterior is a rectangle solely for this worked example. Real walls and measurement rules could change the gross outline. These side lengths must not be used for renovation or furniture purchasing.

## Visual constraints and priors

- The living room looks wider than the bedroom; a **17.0 / 12.8 m²** split is a plausible starting point. The photos do not distinguish it from, for example, **16.0 / 13.8 m²**. The admissible range in this example is living **15.5–18.5 m²**, bedroom **14.3–11.3 m²**, always summing to 29.8 m².
- The kitchen contains a cabinet run, freestanding stove and a small table. A roughly **2.4 × 3.0 m** rectangle satisfies its listed area and visible furnishing. The photos cannot prove which side is longer.
- A typical bath and internal door are useful only as broad priors. Their actual model and dimension have not been identified, so this example does **not** convert pixels to metres using them.
- The three listed areas have much higher reliability than any inferred dimension. Right angles, nonoverlapping rooms, door access and a continuous hall are additional geometric constraints.

## Manual optimization record

1. Fix the exact areas above, including the 15.0 m² residual.
2. Place the living and bedroom windows on exterior boundaries; place the kitchen window on an exterior or recess boundary.
3. Reserve a connected hall from the apartment entry to living room and kitchen.
4. Put bathroom, toilet and wardrobe on hall boundaries.
5. In A, put an opening on the bedroom–kitchen shared wall. In B, keep that wall closed and add a bedroom–hall opening plus a small hypothesized tiled hall spur.
6. Check that every space has a door or access opening, no rooms overlap, and the model areas sum to 52.1 m².

This is a **hand-optimized hypothesis**, not a claim that photogrammetry or a numerical optimizer recovered these coordinates. The machine-readable coordinates in stage 8 make this choice inspectable and editable.
