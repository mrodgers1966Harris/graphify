# Atlas Ops — live cross-system knowledge graph (BC + ACC + SharePoint)

A graphify knowledge graph fed from Harris Associates' **live operational systems** — BuildingConnected
(BC), Autodesk Construction Cloud / Forma (ACC), and SharePoint — so Claude Code (and the Atlas agent
fleet) can answer questions across all three at once instead of digging through each silo.

Unlike the static `precon-estimating/` corpus, this graph is built by an **ETL that pulls live data via
the Atlas MCP servers** (`Atlas_BC`, `Atlas_Forma`, `Microsoft_365`), normalizes it, and emits a graph.

## The join is the entity web, not project IDs

The three systems barely overlap at the project level — BC is mostly the **precon/bidding** pipeline,
ACC is mostly **active construction**, and BC's `currentAccLinkedProjectId` FK is empty in practice.
What *does* connect them is the shared entity web: the same **subcontractors, architects, clients, and
CSI trades** recur across every pursuit and project. That web is the join, plus the handful of projects
that genuinely span pursuit → construction (Paiute, El Corazon, Decatur…).

## Phases

| Phase | Scope | Status |
|---|---|---|
| **P1** | BC + ACC project backbone + clients/architects/sectors + name-matched cross-system links | ✅ built |
| **P2** | BC depth: per-project bid packages → bids → **subcontractors** → CSI trades (the dense layer) | ⏳ next |
| **P3** | ACC depth: RFIs, submittals, issues, budgets per Forma project | ⏳ |
| **P4** | SharePoint docs + merge the `precon-estimating` graph; dedup shared entities across systems | ⏳ |
| **P5** | Operationalize: scheduled rebuild as the principal-scoped `atlas-graphify-ops` MCP server | ⏳ |

**Current graph (P1):** 122 nodes · 46 edges (66 BC projects, 28 ACC projects, 28 clients/architects/
sectors, 14 heuristic cross-system links). Deliberately sparse until P2 adds the recurring-sub hubs.

## Connect Claude Code to it

The graph is served over MCP by graphify. Point Claude Code at it:

```bash
pip install graphifyy           # provides the `graphify-mcp` server
# from the graphify repo root:
claude mcp add atlas-ops -- graphify-mcp "$(pwd)/worked/atlas-ops/graph.json"
```

…or drop the committed `.mcp.json` (in this folder) at your project root. Claude Code then gets these
tools and answers in natural language against the graph:

`query_graph` · `get_node` · `get_neighbors` · `get_community` · `god_nodes` · `graph_stats` · `shortest_path`

Verified working via `etl/test_connect.py` (a minimal MCP stdio client — lists tools, calls `graph_stats`
and `query_graph`).

## Build (one repeatable engine)

```bash
PYTHONPATH=<graphify-repo>  .venv/bin/python etl/ingest.py
```

`etl/ingest.py` is the whole pipeline: it reads the `_staging/` tree of live pulls and writes
`graph.json` + `GRAPH_REPORT.md`. **Scaling out is data, not code** — drop another
`_staging/projects/<bcProjectId>/{packages,bids,invites}.json` into the tree and re-run; the engine
picks it up automatically and the recurring-subcontractor hubs grow across projects.

The PULL stage (which MCP calls produce that tree) is specified in **`PULL_RUNBOOK.md`**. `_staging/` is
gitignored (raw business data incl. vendor pricing). At full scale the PULL + BUILD run inside the
`atlas-graphify-ops` service (see `atlas-oi/mcp/graphify-ops/`), not interactively.

**Current build:** 305 nodes · 398 edges — backbone (66 BC + 28 ACC projects + clients/architects/
sectors + 14 cross-system links) plus the Silverton depth pilot (40 packages → 124 subcontractors →
246 bids → CSI trades).

## Data sensitivity

`_staging/` holds raw BC/ACC pulls (vendor pricing, contacts) and is **gitignored**. The committed
`graph.json` holds entity labels (project names/numbers/values, clients) — treat it like the rest of the
private repo's business data. This graph is **not** wired to the agent fleet; exposing it to agents is the
gated P5 step (principal-scoped, per `atlas-oi/mcp/graphify/README.md` Phase 2).
