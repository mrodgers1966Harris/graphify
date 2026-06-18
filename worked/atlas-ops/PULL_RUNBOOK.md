# Atlas Ops — pull runbook (live → staging tree)

The ingestion is two stages: **PULL** (call the Atlas MCP servers, write the staging tree) and
**BUILD** (`etl/ingest.py` normalizes the tree → graph). This file specifies the PULL stage so it's
reproducible by an agent loop now, and by the `atlas-graphify-ops` service later (which calls the same
MCP servers through the Atlas broker `api/atlas/mcp/client.js`).

All paths are under `worked/atlas-ops/_staging/` (gitignored — raw business data incl. vendor pricing).

## Backbone (once per refresh)

| Output | MCP call |
|---|---|
| `bc_projects.json` | `Atlas_BC.list_projects` |
| `acc_projects.json` | `Atlas_Forma.list_projects` (drop templates/test/sandbox; keep `{name,id}`) |
| `bc_opportunities.json` *(optional, vendor side)* | `Atlas_BC.list_opportunities` |

## Depth — per BC project that should carry bid detail

For each `projectId` from `bc_projects.json`, create `_staging/projects/<projectId>/`:

| Output | MCP call | Notes |
|---|---|---|
| `packages.json` | `Atlas_BC.list_project_bid_packages(project_id)` | reshape to `{"project":{"id","name"},"packages":{<pkgId>:{"name","div"}}}` (div = the package `number`, e.g. `Div 3`) |
| `bids.json` | `Atlas_BC.list_bids(project_id)` | keep `results[]` verbatim (uses `bidderCompanyId`, `leveledTotal`/`total`, `bidPackageId`) |
| `invites.json` | `Atlas_BC.list_invites(project_id)` | resolves `bidderCompany.id → name` for the bids |

`ingest.py` picks up every `projects/<id>/` dir automatically — add projects incrementally; no code change.

## Planned (P3 / P4)

| Layer | MCP calls | Adds |
|---|---|---|
| **ACC depth (P3)** | `Atlas_Forma.list_rfis` / `list_submittals` / `list_issues` / `get_budget_details` per `acc` project id | RFI/Submittal/Issue/Budget nodes on ACC projects |
| **SharePoint (P4)** | `Microsoft_365.sharepoint_search` / `sharepoint_folder_search` per project | Document nodes; merge the `precon-estimating` graph; dedup shared subs/architects across systems |

## Scale / cost note

Per-project depth pulls are large (Silverton: `bids` ~0.4 MB + `invites` ~1.3 MB). Pulling 60+ projects
interactively is impractical — the PULL stage is meant to run **inside the `atlas-graphify-ops` service**
(broker client + project loop + disk staging), not through a chat session. See
`atlas-oi/mcp/graphify-ops/README.md`.
