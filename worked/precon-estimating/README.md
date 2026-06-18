# Harris Preconstruction & Estimating Corpus

A real-world business-document corpus: the Harris Associates (Las Vegas general
contractor) **Preconstruction & Estimating** knowledge base, pulled from SharePoint
and turned into a navigable knowledge graph. Tests graphify on mixed prose SOPs +
structured JSON data + an HTML dashboard — no source code.

## Source

`HarrisAiTaskforce › Digital Innovation Lab › Estimating › Estimating and Precon Documents`
(Harris Associates M365 / SharePoint).

Scope: **structured + authored documents only.** The folder also holds ~70 scanned
subcontractor bid PDFs (~140 MB) and a 915 KB `bid_book_data.json`; those were
intentionally excluded. The scanned PDFs' analytic content (who bid what, on which
package, for how much) is already captured in `forge_bid_book_state.json` and the
roster, so the graph is built from the lean, high-signal sources rather than OCR'ing
binaries. To pull the full per-bid scope text later: `/graphify add` the PDFs.

## Corpus (7 files · ~14,058 words)

```
raw/
├── Harris_BC_Project_Numbering_SOP.html  — BuildingConnected project-numbering SOP
│                                            (YY-####-XX-XXX, office codes, OPP→AWD phases, rules R-01..R-08)
├── Harris_Precon_Folder_Structure.html   — standard 11-folder SharePoint precon structure
├── Harris_Live_Pipeline_v4_7.html        — live BC pipeline dashboard (5 active projects)
├── SCHEMA_SPEC.md                         — Atlas Bid Book normalized data model
│                                            (Project → CSI Division → Package → Bidder → Proposal → Leveling/Risk/Award)
├── forge_bid_book_state.json             — live snapshot: 5 projects, $60.7M, Silverton leveling by package
├── proposals_text.json                    — 4 fully-extracted Walk Church concrete bids (CSI 03)
└── sub_bids_list.txt                      — full subcontractor roster by CSI division
```

## How to run

```bash
pip install graphifyy
graphify install                 # Claude Code (or --platform codex / opencode / ...)
```

Then open your AI assistant in this directory and type:

```
/graphify ./raw
```

Ask questions against the built graph:

```
/graphify query "Which subcontractors bid the Walk Church concrete package and how do their totals compare?"
/graphify query "How does a project move from OPP to AWD, and where does the number come from?"
/graphify path "Silverton Ranch Casino & Hotel" "Bid Risk Taxonomy"
```

## What to expect

- **128 nodes · 159 edges · 24 communities** (75% EXTRACTED · 25% INFERRED · 0% AMBIGUOUS)
- **God nodes:** `Preconstruction File Structure SOP`, `Standard Project Number Format (YY-####-XX-XXX)`,
  `Silverton Ranch Casino & Hotel`, `Walk Church Concrete Package (CSI 03)`, `Project (schema concept)`,
  `Bid Package (schema concept)`, `Risk Type Taxonomy`
- **Major communities:** Live Bid Pipeline & Packages · Project Lifecycle & Phase Codes ·
  SharePoint Precon Folder Structure · Walk Church Concrete Bids · Project Numbering Format & Rules ·
  Bid Risk Taxonomy · Precon SOP Ownership · plus one tight cluster per CSI trade (Steel, Electrical,
  Masonry, HVAC, Plumbing, Elevators, Earthwork/Utilities, …)
- **Surprising connection:** the live pipeline projects (`Walk Church`, `Majestic Phase 1`, `Gramercy`,
  `High Five`) all `implement` the `Project (schema concept)` defined in `SCHEMA_SPEC.md` — the dashboard
  data and the data-model spec describe the same entities from two different files.
- **Hyperedges:** the six OPP→AWD phase codes forming the precon lifecycle; the eleven standard SharePoint
  folders; the four segments of the project number; the four subs competing on Walk Church concrete; the
  Bid Book hierarchy levels; the seven auto-derived risk types.

Outputs in this directory: `graph.json` (GraphRAG-ready), `GRAPH_REPORT.md` (audit report),
`graph.html` (interactive, open in any browser), `manifest.json`. See `review.md` for an honest evaluation.
