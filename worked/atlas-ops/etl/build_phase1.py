#!/usr/bin/env python3
"""Atlas Ops graph — Phase 1: BuildingConnected + ACC project backbone.

Deterministic ETL (no LLM). Reads the staged live pulls and emits a graphify
extraction JSON, then builds the graph. The cross-system join is the shared
entity web (clients, architects, market sectors) plus heuristic name-matched
project links between BC (precon/bidding) and ACC/Forma (active construction).

Run:  PYTHONPATH=/home/user/graphify  .venv/bin/python etl/build_phase1.py
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # worked/atlas-ops
STAGING = ROOT / "_staging"
OUT = ROOT / "graphify-out"
OUT.mkdir(exist_ok=True)

GENERIC = {"the","at","casino","hotel","restaurant","building","pad","ti","tis","cd","cds",
           "phase","improvements","room","remodel","corridor","with","and","adjacent","sitework",
           "copy","future","re","pricing","g","u","of","golf","resort","cafe","bar","food","hall"}

def norm(s): return re.sub(r"\s+"," ", re.sub(r"[^a-z0-9 ]"," ", (s or "").lower())).strip()
def sig(s): return {t for t in norm(s).split() if t not in GENERIC and len(t) > 2}
def nid(prefix, s): return prefix + "_" + re.sub(r"[^a-z0-9]+","_", (s or "").lower()).strip("_")[:48]

def name_of(v):
    if isinstance(v, dict): return v.get("name") or v.get("displayName")
    return v if isinstance(v, str) and v.strip() else None

def load_list(path):
    d = json.loads(Path(path).read_text())
    if isinstance(d, list): return d
    return next((v for v in d.values() if isinstance(v, list)), [])

bc = load_list(STAGING / "bc_projects.json")
acc = json.loads((STAGING / "acc_projects.json").read_text())

SKIP = re.compile(r"template|scope template|sandbox|test project|component library|sub list|"
                  r"weekly meeting|field report|internal use|directroy|import", re.I)

nodes, edges = {}, []
def add_node(nid_, label, ftype, src):
    if nid_ not in nodes:
        nodes[nid_] = {"id": nid_, "label": label, "file_type": ftype, "source_file": src,
                       "source_location": None, "source_url": None, "captured_at": None,
                       "author": None, "contributor": None}
    return nid_
def add_edge(s, t, rel, conf="EXTRACTED", score=1.0, src="atlas-ops://etl"):
    edges.append({"source": s, "target": t, "relation": rel, "confidence": conf,
                  "confidence_score": score, "source_file": src, "source_location": None, "weight": 1.0})

# ---- BC projects (precon / bidding side) ----
bc_proj = []
for r in bc:
    name = (r.get("name") or "").strip()
    if not name or SKIP.search(name): continue
    num = r.get("number") or ""
    state = r.get("state") or ""
    val = r.get("value")
    valstr = f" · ${val/1e6:.1f}M" if isinstance(val,(int,float)) and val else ""
    pid = nid("bc", (r.get("id") or name))
    label = f"{name}" + (f" ({num})" if num else "") + f" · BC{(' · '+state) if state else ''}{valstr}"
    add_node(pid, label, "document", "buildingconnected://projects")
    bc_proj.append((pid, name, r))
    for fld, rel in (("client","client_of"), ("architect","designed_by")):
        nm = name_of(r.get(fld))
        if nm:
            kind = "client" if fld=="client" else "architect"
            ent = add_node(nid(kind, nm), f"{nm} ({kind})", "concept", f"buildingconnected://{kind}s")
            add_edge(pid, ent, rel)
    ms = name_of(r.get("marketSector"))
    if ms:
        sec = add_node(nid("sector", ms), f"{ms} (market sector)", "concept", "buildingconnected://sectors")
        add_edge(pid, sec, "in_sector")

# ---- ACC / Forma projects (active construction side) ----
acc_proj = []
for r in acc:
    name = (r.get("name") or "").strip()
    if not name or SKIP.search(name): continue
    pid = nid("acc", r.get("id") or name)
    add_node(pid, f"{name} · ACC (active construction)", "document", "acc://projects")
    acc_proj.append((pid, name))

# ---- Cross-system links: BC project <-> ACC project by name (heuristic, INFERRED) ----
xlinks = 0
for bpid, bname, _ in bc_proj:
    bs = sig(bname)
    if not bs: continue
    for apid, aname in acc_proj:
        as_ = sig(aname)
        if not as_: continue
        inter = bs & as_
        contained = bs <= as_ or as_ <= bs
        if contained or len(inter) >= 2:
            score = 0.85 if contained else 0.75
            add_edge(bpid, apid, "same_project_across_systems", "INFERRED", score)
            xlinks += 1

extract = {"nodes": list(nodes.values()), "edges": edges, "hyperedges": [],
           "input_tokens": 0, "output_tokens": 0}
(OUT / ".graphify_extract.json").write_text(json.dumps(extract, indent=2))
detect = {"files": {"document": ["buildingconnected://projects","acc://projects"]},
          "total_files": 2, "total_words": 0, "scan_root": str(STAGING)}
(OUT / ".graphify_detect.json").write_text(json.dumps(detect))

print(f"BC projects: {len(bc_proj)} | ACC projects: {len(acc_proj)} | "
      f"clients/architects/sectors+: {len(nodes)-len(bc_proj)-len(acc_proj)} | "
      f"cross-system name links: {xlinks}")
print(f"TOTAL nodes: {len(nodes)} | edges: {len(edges)}")

# ---- Build the graph ----
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json

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
print(f"GRAPH: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities")
gl = {n['id']: n['label'] for n in extract['nodes']}
print("God nodes:", [gl.get((x[0] if isinstance(x,(list,tuple)) else x), x) for x in gods[:8]])
