#!/usr/bin/env python3
"""Atlas Ops graph — Phase 2: BuildingConnected bid depth (Silverton pilot).

Adds the dense layer: per-project bid packages -> subcontractors -> CSI trades,
resolving sub names from invites (bidderCompany.id/name) and bid totals from bids.
Merges into the Phase 1 extract and rebuilds. Subs that bid multiple packages/
trades become hub nodes — and across projects (future batches) they become the
firm-wide "who bids what / where's coverage thin" intelligence.

Run:  PYTHONPATH=/home/user/graphify  .venv/bin/python etl/build_phase2.py
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "_staging"
OUT = ROOT / "graphify-out"

pkgs_doc = json.loads((STAGING / "silverton_packages.json").read_text())
proj = pkgs_doc["project"]
packages = pkgs_doc["packages"]
invites = json.loads((STAGING / "silverton_invites.json").read_text())
invites = invites.get("results", invites)
bids = json.loads((STAGING / "silverton_bids.json").read_text())
bids = bids.get("results", bids)

MF = {1:"General Requirements",2:"Existing Conditions",3:"Concrete",4:"Masonry",5:"Metals",
      6:"Wood & Plastics",7:"Thermal & Moisture",8:"Openings",9:"Finishes",10:"Specialties",
      11:"Equipment",12:"Furnishings",13:"Special Construction",14:"Conveying",21:"Fire Suppression",
      22:"Plumbing",23:"HVAC",26:"Electrical",27:"Communications",28:"Electronic Safety",
      31:"Earthwork",32:"Exterior Improvements",33:"Utilities"}
def major_div(div):
    m = re.search(r"(\d+)", div or "")
    return int(m.group(1)) if m else None

# company id -> name, from invites
comp_name = {}
for inv in invites:
    bc = inv.get("bidderCompany") or {}
    if bc.get("id"): comp_name[bc["id"]] = bc.get("name") or bc["id"]

# per-package: invited companies, and bids (company -> leveledTotal)
from collections import defaultdict
pkg_invited = defaultdict(set)
for inv in invites:
    bc = inv.get("bidderCompany") or {}
    if inv.get("bidPackageId") and bc.get("id"):
        pkg_invited[inv["bidPackageId"]].add(bc["id"])
pkg_bids = defaultdict(list)   # pkgId -> list[(companyId, leveledTotal)]
for b in bids:
    cid = b.get("bidderCompanyId")
    pk = b.get("bidPackageId")
    amt = b.get("leveledTotal") or b.get("total")
    if pk and cid:
        pkg_bids[pk].append((cid, amt))
        comp_name.setdefault(cid, cid)

# ---- merge into phase 1 extract ----
extract = json.loads((OUT / ".graphify_extract.json").read_text())
nodes = {n["id"]: n for n in extract["nodes"]}
edges = extract["edges"]

def add_node(nid_, label, ftype, src):
    if nid_ not in nodes:
        nodes[nid_] = {"id": nid_, "label": label, "file_type": ftype, "source_file": src,
                       "source_location": None, "source_url": None, "captured_at": None,
                       "author": None, "contributor": None}
    return nid_
def add_edge(s, t, rel, conf="EXTRACTED", score=1.0, w=1.0):
    edges.append({"source": s, "target": t, "relation": rel, "confidence": conf,
                  "confidence_score": score, "source_file": "buildingconnected://bids",
                  "source_location": None, "weight": w})

proj_node = "bc_" + proj["id"]            # matches Phase 1 BC project node id
if proj_node not in nodes:
    add_node(proj_node, f"{proj['name']} · BC", "document", "buildingconnected://projects")

n_sub, n_pkg, n_bidedge = set(), 0, 0
for pid, info in packages.items():
    div = info["div"]; mj = major_div(div)
    blist = pkg_bids.get(pid, [])
    amts = [a for _, a in blist if isinstance(a, (int, float)) and a]
    cov = f" · {len(blist)} bids" if blist else " · 0 bids"
    lohi = f" · ${min(amts)/1e6:.2f}M–${max(amts)/1e6:.2f}M" if amts else ""
    pkgn = add_node("pkg_" + pid, f"Silverton — {info['name']} ({div or 'misc'}){cov}{lohi}",
                    "document", "buildingconnected://packages")
    n_pkg += 1
    add_edge(proj_node, pkgn, "has_package")
    # CSI trade node
    if mj in MF:
        tn = add_node(f"trade_{mj:02d}", f"CSI {mj:02d} — {MF[mj]}", "concept", "csi://masterformat")
        add_edge(pkgn, tn, "in_trade")
    # subs that bid this package
    for cid, amt in blist:
        sn = add_node("sub_" + cid, f"{comp_name.get(cid, cid)} (subcontractor)", "concept",
                      "buildingconnected://companies")
        n_sub.add(cid)
        w = float(amt) if isinstance(amt, (int, float)) and amt else 1.0
        add_edge(pkgn, sn, "received_bid_from", w=w)
        n_bidedge += 1
        if mj in MF:
            add_edge(sn, f"trade_{mj:02d}", "bids_in_trade", "INFERRED", 0.85)

extract["nodes"] = list(nodes.values())
extract["edges"] = edges
(OUT / ".graphify_extract.json").write_text(json.dumps(extract, indent=2))
print(f"Silverton: {n_pkg} packages, {len(n_sub)} distinct subcontractors, {n_bidedge} bid edges")
print(f"MERGED extract: {len(nodes)} nodes, {len(edges)} edges")

# ---- rebuild ----
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json
detect = json.loads((OUT / ".graphify_detect.json").read_text())
G = build_from_json(extract, root=str(STAGING))
communities = cluster(G)
cohesion = score_all(G, communities)
gods = god_nodes(G)
surprises = surprising_connections(G, communities)
labels = {cid: "Community " + str(cid) for cid in communities}
questions = suggest_questions(G, communities, labels)
report = generate(G, communities, cohesion, labels, gods, surprises, detect,
                  {"input": 0, "output": 0}, ".", suggested_questions=questions)
(OUT / "GRAPH_REPORT.md").write_text(report)
to_json(G, communities, str(OUT / "graph.json"))
gl = {n["id"]: n["label"] for n in extract["nodes"]}
print(f"GRAPH: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities")
print("Top hubs:", [gl.get((x[0] if isinstance(x,(list,tuple)) else x)) for x in gods[:10]])
