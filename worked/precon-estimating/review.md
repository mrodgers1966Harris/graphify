# Graphify Evaluation — Harris Preconstruction & Estimating Corpus (2026-06-18)

**Corpus:** 7 files · ~14,058 words · Harris Associates Preconstruction & Estimating
documents from SharePoint (2 HTML SOPs, 1 HTML pipeline dashboard, 1 Markdown schema
spec, 2 JSON data files, 1 text roster). No source code.
**Pipeline:** semantic extraction (host LLM via two parallel subagents) → graph_builder →
clusterer → analyzer → reporter. No AST stage (no code), no Gemini key.
**Result:** 128 nodes · 159 edges · 24 communities · 75% EXTRACTED / 25% INFERRED / 0% AMBIGUOUS.

---

## What the graph captures well

- **The numbering SOP is fully modeled.** The `YY-####-XX-XXX` format decomposes into its four
  segments (year / sequence / office / type) as a hyperedge, the six phase codes (OPP, ROM, OC,
  EST, BID, AWD) form a lifecycle hyperedge, the three office codes (LV/AZ/CA) and eight operating
  rules (R-01..R-08) are all present and correctly attributed to `Jeff Contenta` and the
  Preconstruction department.
- **Cross-document linkage is the standout.** The biggest payoff is that the live dashboard
  (`forge_bid_book_state.json`) and the data-model spec (`SCHEMA_SPEC.md`) describe the *same*
  entities: each pipeline project (`Walk Church`, `Majestic Phase 1`, `Gramercy`, `High Five`,
  `Silverton`) is linked by an `implements` edge to the `Project (schema concept)`. A reader of
  either file alone would miss that the spec is the model the dashboard instantiates.
- **Trade structure emerges cleanly.** Without being told the MasterFormat hierarchy, clustering
  produced one tight community per CSI trade (Steel, Electrical, Masonry, HVAC, Plumbing, Elevators,
  Earthwork/Utilities, Exterior Improvements) — each pairing the division with the subs that bid it.
  These are the highest-cohesion communities in the graph (0.67 for the 3-node trade clusters).
- **The four competing Walk Church concrete bids** are correctly grouped (community cohesion 0.40),
  tied to the same CSI-03 package, and joined by `semantically_similar_to` edges — exactly the
  "competing bids for one scope" relationship you'd want to query.

## Honest limitations

- **The corpus is small (~14K words) — it fits in a single context window.** The report says so
  up front. The graph's value here is less about token compression and more about making the
  cross-file structure (spec ↔ dashboard ↔ bids ↔ SOP) explicit and queryable.
- **54 isolated / weakly-connected nodes and 14 thin (<3 node) communities.** Many CSI divisions
  appear only as a division + a single sub, because only the *current* round's low bidders are in
  the lean sources. The full `bid_book_data.json` (excluded by design) would densify these — every
  invited bidder, contact, and line item would attach to its package. Right now the trade clusters
  are real but sparse.
- **Office codes, QuickBooks, and several phase nodes are isolated.** They're referenced once in
  prose and never co-occur with the entities they govern, so they read as leaf facts rather than hubs.
- **25% of edges are INFERRED (avg confidence 0.88).** The `implements`/`shares_data_with` links
  between the dashboard projects and the schema concepts are model-reasoned, not stated verbatim in
  either file. They are correct here, but they are the edges a reviewer should sanity-check first —
  the report's "Suggested Questions" section flags them explicitly.

## Scores

| Dimension | Score | Key finding |
|-----------|-------|-------------|
| Node/entity quality | 8/10 | SOP, schema, projects, subs, CSI divisions all captured; money/dates correctly kept as attributes, not nodes |
| Edge accuracy | 7/10 | 75% EXTRACTED; the 25% INFERRED spec↔dashboard links are right and are the most useful edges |
| Community quality | 8/10 | Trade clusters and the four thematic doc communities are crisp; long tail of thin single-sub communities |
| Surprising connections | 7/10 | The spec-instantiation link is a genuine cross-file insight; some flagged "surprises" are the same pattern repeated per project |
| God nodes | 9/10 | The two SOPs, the schema Project/Package concepts, Silverton, and the Walk Church concrete package are exactly the right hubs |
| Overall usefulness | 8/10 | Answers real precon questions (numbering, folder structure, who-bid-what, risk taxonomy) directly from the graph |

**Overall: 7.8/10.** A strong result for a lean, mixed prose-plus-data corpus. The single biggest
lever for improvement is ingesting the full `bid_book_data.json` (or OCR'ing the ~70 bid PDFs) to
turn the sparse per-trade clusters into a dense bidder/line-item graph.
