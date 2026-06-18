# Graph Report - .  (2026-06-18)

## Corpus Check
- Corpus is ~14,058 words - fits in a single context window. You may not need a graph.

## Summary
- 128 nodes · 159 edges · 24 communities (10 shown, 14 thin omitted)
- Extraction: 75% EXTRACTED · 25% INFERRED · 0% AMBIGUOUS · INFERRED: 40 edges (avg confidence: 0.88)
- Token cost: 160,197 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Live Bid Pipeline & Packages|Live Bid Pipeline & Packages]]
- [[_COMMUNITY_Project Lifecycle & Phase Codes|Project Lifecycle & Phase Codes]]
- [[_COMMUNITY_SharePoint Precon Folder Structure|SharePoint Precon Folder Structure]]
- [[_COMMUNITY_Walk Church Concrete Bids|Walk Church Concrete Bids]]
- [[_COMMUNITY_Project Numbering Format & Rules|Project Numbering Format & Rules]]
- [[_COMMUNITY_Bid Risk Taxonomy|Bid Risk Taxonomy]]
- [[_COMMUNITY_Precon SOP Ownership|Precon SOP Ownership]]
- [[_COMMUNITY_Earthwork & Utilities Subs|Earthwork & Utilities Subs]]
- [[_COMMUNITY_Structural Steel Subs|Structural Steel Subs]]
- [[_COMMUNITY_Electrical Subs|Electrical Subs]]
- [[_COMMUNITY_QuickBooks Naming Rule|QuickBooks Naming Rule]]
- [[_COMMUNITY_Wood & Framing Sub|Wood & Framing Sub]]
- [[_COMMUNITY_Masonry Sub|Masonry Sub]]
- [[_COMMUNITY_Conveying  Elevator Sub|Conveying / Elevator Sub]]
- [[_COMMUNITY_Fire Suppression Sub|Fire Suppression Sub]]
- [[_COMMUNITY_Plumbing Sub|Plumbing Sub]]
- [[_COMMUNITY_HVAC Sub|HVAC Sub]]
- [[_COMMUNITY_Exterior Improvements Subs|Exterior Improvements Subs]]
- [[_COMMUNITY_CSI Division 07 — Thermal & Moisture  Roofing|CSI Division 07 — Thermal & Moisture / Roofing]]
- [[_COMMUNITY_CSI Division 08 — Openings (Doors, Glass, Shades)|CSI Division 08 — Openings (Doors, Glass, Shades)]]
- [[_COMMUNITY_CSI Division 09 — Finishes (Drywall, Plaster, Flooring, Tile)|CSI Division 09 — Finishes (Drywall, Plaster, Flooring, Tile)]]
- [[_COMMUNITY_CSI Division 10 — Specialties (Signage, Toilet Accessories)|CSI Division 10 — Specialties (Signage, Toilet Accessories)]]
- [[_COMMUNITY_CSI Division 11 — Equipment (Foodservice, Playgrounds)|CSI Division 11 — Equipment (Foodservice, Playgrounds)]]
- [[_COMMUNITY_CSI Division 28 — Electronic Safety & Security (Fire Alarm)|CSI Division 28 — Electronic Safety & Security (Fire Alarm)]]

## God Nodes (most connected - your core abstractions)
1. `Preconstruction File Structure SOP` - 14 edges
2. `Standard Project Number Format (YY-####-XX-XXX)` - 10 edges
3. `Silverton Ranch Casino & Hotel` - 10 edges
4. `Walk Church Concrete Package (CSI 03)` - 10 edges
5. `Project (schema concept)` - 9 edges
6. `Bid Package (schema concept)` - 9 edges
7. `Risk Type Taxonomy` - 9 edges
8. `Ryerson Concrete LLC (Walk Church concrete bid)` - 9 edges
9. `Walk Church` - 8 edges
10. `Nellis Concrete LLC (Walk Church concrete bid)` - 8 edges

## Surprising Connections (you probably didn't know these)
- `Walk Church` --references--> `Project Phase Machine (pre-bid/bidding/leveling/awarding/post-award)`  [INFERRED]
  forge_bid_book_state.json → SCHEMA_SPEC.md
- `Gramercy Office Renovation – LJA Engineering` --implements--> `Project (schema concept)`  [INFERRED]
  forge_bid_book_state.json → SCHEMA_SPEC.md
- `High Five Fashion Show` --implements--> `Project (schema concept)`  [INFERRED]
  forge_bid_book_state.json → SCHEMA_SPEC.md
- `Majestic Phase 1` --implements--> `Project (schema concept)`  [INFERRED]
  forge_bid_book_state.json → SCHEMA_SPEC.md
- `Walk Church` --implements--> `Project (schema concept)`  [INFERRED]
  forge_bid_book_state.json → SCHEMA_SPEC.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Project Type Phase Codes Forming the Preconstruction Lifecycle** — raw_harris_bc_project_numbering_sop_phase_opp, raw_harris_bc_project_numbering_sop_phase_rom, raw_harris_bc_project_numbering_sop_phase_oc, raw_harris_bc_project_numbering_sop_phase_est, raw_harris_bc_project_numbering_sop_phase_bid, raw_harris_bc_project_numbering_sop_phase_awd [EXTRACTED 1.00]
- **Eleven Standard Preconstruction SharePoint Folders** — raw_harris_precon_folder_structure_folder_01, raw_harris_precon_folder_structure_folder_02, raw_harris_precon_folder_structure_folder_03, raw_harris_precon_folder_structure_folder_04, raw_harris_precon_folder_structure_folder_05, raw_harris_precon_folder_structure_folder_06, raw_harris_precon_folder_structure_folder_07, raw_harris_precon_folder_structure_folder_08, raw_harris_precon_folder_structure_folder_09, raw_harris_precon_folder_structure_folder_10, raw_harris_precon_folder_structure_folder_11 [EXTRACTED 1.00]
- **Four Segments Composing the Standard Project Number** — raw_harris_bc_project_numbering_sop_seg_year, raw_harris_bc_project_numbering_sop_seg_sequence, raw_harris_bc_project_numbering_sop_seg_office, raw_harris_bc_project_numbering_sop_seg_type [EXTRACTED 1.00]
- **Four subcontractors competing on the Walk Church concrete (CSI 03) package** — raw_proposals_text_nellis_concrete, raw_proposals_text_amc_concrete, raw_proposals_text_ryerson_concrete, raw_proposals_text_integrity_concrete, raw_proposals_text_concrete_division [INFERRED 0.95]
- **Atlas Bid Book hierarchy: Project to Division to Package to Bidder to Proposal** — raw_schema_spec_project, raw_schema_spec_csi_division, raw_schema_spec_bid_package, raw_schema_spec_bidder, raw_schema_spec_proposal [EXTRACTED 1.00]
- **Auto-derived bid-package risk type taxonomy** — raw_schema_spec_risk_no_bids, raw_schema_spec_risk_single_bid, raw_schema_spec_risk_wide_spread, raw_schema_spec_risk_low_coverage, raw_schema_spec_risk_long_lead, raw_schema_spec_risk_past_due_no_award, raw_schema_spec_risk_draft_unpublished [EXTRACTED 1.00]

## Communities (24 total, 14 thin omitted)

### Community 0 - "Live Bid Pipeline & Packages"
Cohesion: 0.12
Nodes (23): Gramercy Office Renovation – LJA Engineering, High Five Fashion Show, Majestic Phase 1, Forge Bid Book Next Actions, Silverton — Concrete Package, Silverton — Electrical Package, Silverton — Elevators Package, Silverton — HVAC Package (+15 more)

### Community 1 - "Project Lifecycle & Phase Codes"
Cohesion: 0.15
Nodes (17): ACC / Forma (Autodesk Construction Cloud project), BuildingConnected (preconstruction bid platform), AWD — Post-Bid / Awarded Phase, BID — Active Bid Phase, EST — Estimate Phase, OC — Opinion of Cost Phase, OPP — Opportunity Phase, ROM — Rough Order of Magnitude Phase (+9 more)

### Community 2 - "SharePoint Precon Folder Structure"
Cohesion: 0.13
Nodes (16): Preconstruction File Structure SOP, 01 RFP & Client Info, 02 Plans, 03 Subcontractor Out to Bid Documents, 05 Proposals, 06 Sub Bids, 07 Schedule | Logistics, 08 Existing Conditions (+8 more)

### Community 3 - "Walk Church Concrete Bids"
Cohesion: 0.40
Nodes (13): Walk Church, AMC Concrete (Walk Church concrete bid), Walk Church Concrete Package (CSI 03), Exclusion: Caliche / Hard Rock Dig, Exclusion: Prevailing Wages, Integrity Concrete LLC (Walk Church concrete bid), Nellis Concrete LLC (Walk Church concrete bid), Ryerson Concrete LLC (Walk Church concrete bid) (+5 more)

### Community 4 - "Project Numbering Format & Rules"
Cohesion: 0.15
Nodes (13): Standard Project Number Format (YY-####-XX-XXX), Office Code AZ (Phoenix/Scottsdale, Arizona Operations), Office Code CA (California — future office), Office Code LV (Las Vegas, NV — Home Office), R-01 — Every BC project must receive a number, R-02 — Numbers assigned at creation, never changed, R-03 — Sequences tracked per year, per office, per type, R-05 — Projects that do not advance are not deleted (+5 more)

### Community 5 - "Bid Risk Taxonomy"
Cohesion: 0.38
Nodes (7): Risk: draft_unpublished (LOW), Risk: low_coverage (LOW), Risk: no_bids (HIGH), Risk: past_due_no_award (MEDIUM), Risk: single_bid (MEDIUM), Risk Type Taxonomy, Project Status Indicator (green/yellow/red)

### Community 6 - "Precon SOP Ownership"
Cohesion: 0.40
Nodes (6): BuildingConnected Project Numbering SOP, Jeff Contenta (VP Preconstruction, SOP Owner), Harris Preconstruction & Estimating Department, R-04 — Preconstruction owns the number register, Harris Live Pipeline Dashboard, Time-To-Hire / Weighted TTH metric (Value × Prob × 10%)

### Community 7 - "Earthwork & Utilities Subs"
Cohesion: 0.40
Nodes (5): CSI Division 31 — Earthwork, CSI Division 33 — Utilities, MTX (subcontractor), NDX (subcontractor), Reliant Construction LLC (subcontractor)

### Community 8 - "Structural Steel Subs"
Cohesion: 0.67
Nodes (3): Battleborn Steel (subcontractor), CSI Division 05 — Metals / Steel, LV Iron & Steel (subcontractor)

### Community 9 - "Electrical Subs"
Cohesion: 0.67
Nodes (3): CSI Division 26 — Electrical, Helix Electric (subcontractor), Jackson Electric (subcontractor)

## Knowledge Gaps
- **54 isolated node(s):** `Office Code LV (Las Vegas, NV — Home Office)`, `Office Code AZ (Phoenix/Scottsdale, Arizona Operations)`, `OPP — Opportunity Phase`, `EST — Estimate Phase`, `QuickBooks (accounting system)` (+49 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Standard Project Number Format (YY-####-XX-XXX)` connect `Project Numbering Format & Rules` to `Project Lifecycle & Phase Codes`, `SharePoint Precon Folder Structure`, `Precon SOP Ownership`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Why does `Preconstruction File Structure SOP` connect `SharePoint Precon Folder Structure` to `Project Lifecycle & Phase Codes`, `Precon SOP Ownership`?**
  _High betweenness centrality (0.074) - this node is a cross-community bridge._
- **Why does `Project Type Segment (three-letter phase code)` connect `Project Lifecycle & Phase Codes` to `Project Numbering Format & Rules`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `Walk Church Concrete Package (CSI 03)` (e.g. with `Silverton — Concrete Package` and `Walk Church`) actually correct?**
  _`Walk Church Concrete Package (CSI 03)` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `Project (schema concept)` (e.g. with `Gramercy Office Renovation – LJA Engineering` and `High Five Fashion Show`) actually correct?**
  _`Project (schema concept)` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Office Code LV (Las Vegas, NV — Home Office)`, `Office Code AZ (Phoenix/Scottsdale, Arizona Operations)`, `OPP — Opportunity Phase` to the rest of the system?**
  _61 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Live Bid Pipeline & Packages` be split into smaller, more focused modules?**
  _Cohesion score 0.1225296442687747 - nodes in this community are weakly interconnected._