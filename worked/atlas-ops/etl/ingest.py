#!/usr/bin/env python3
"""Atlas Ops — repeatable cross-system ingestion engine.

ONE deterministic builder for the whole graph. Reads a staging tree of live
pulls and emits graph.json + GRAPH_REPORT.md. Scaling out = drop more pulls
into the tree and re-run; no code changes.

Staging layout (all produced by the MCP pull stage — see PULL_RUNBOOK.md):
  _staging/
    bc_projects.json            # Atlas_BC list_projects           (backbone)
    acc_projects.json           # Atlas_Forma list_projects        (backbone)
    projects/<bcProjectId>/      # per-project DEPTH (optional, add as pulled)
      packages.json             #   {project:{id,name}, packages:{id:{name,div}}}
      bids.json                 #   Atlas_BC list_bids(project_id)      -> {results:[...]}
      invites.json              #   Atlas_BC list_invites(project_id)   -> {results:[...]}

Run:  PYTHONPATH=/home/user/graphify  .venv/bin/python etl/ingest.py
"""
import json, re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "_staging"
OUT = ROOT / "graphify-out"; OUT.mkdir(exist_ok=True)

GENERIC = {"the","at","casino","hotel","restaurant","building","pad","ti","tis","cd","cds","phase",
           "improvements","room","remodel","corridor","with","and","adjacent","sitework","copy",
           "future","re","pricing","g","u","of","golf","resort","cafe","bar","food","hall"}
MF = {1:"General Requirements",2:"Existing Conditions",3:"Concrete",4:"Masonry",5:"Metals",
      6:"Wood & Plastics",7:"Thermal & Moisture",8:"Openings",9:"Finishes",10:"Specialties",
      11:"Equipment",12:"Furnishings",13:"Special Construction",14:"Conveying",21:"Fire Suppression",
      22:"Plumbing",23:"HVAC",26:"Electrical",27:"Communications",28:"Electronic Safety",
      31:"Earthwork",32:"Exterior Improvements",33:"Utilities"}
SKIP = re.compile(r"template|scope template|sandbox|test project|component library|sub list|"
                  r"weekly meeting|field report|internal use|directroy|import", re.I)

def norm(s): return re.sub(r"\s+"," ", re.sub(r"[^a-z0-9 ]"," ", (s or "").lower())).strip()
def sig(s): return {t for t in norm(s).split() if t not in GENERIC and len(t) > 2}
def nid(p, s): return p + "_" + re.sub(r"[^a-z0-9]+","_", (s or "").lower()).strip("_")[:48]
def name_of(v): return (v.get("name") or v.get("displayName")) if isinstance(v, dict) else (v if isinstance(v,str) and v.strip() else None)
def major_div(d):
    m = re.search(r"(\d+)", d or ""); return int(m.group(1)) if m else None
def load_list(p):
    if not Path(p).exists(): return []
    d = json.loads(Path(p).read_text())
    return d if isinstance(d, list) else (d.get("results") or next((v for v in d.values() if isinstance(v,list)), []))

nodes, edges = {}, []
def N(i,l,f,s):
    if i not in nodes: nodes[i] = {"id":i,"label":l,"file_type":f,"source_file":s,"source_location":None,
                                   "source_url":None,"captured_at":None,"author":None,"contributor":None}
    return i
def E(s,t,rel,conf="EXTRACTED",sc=1.0,w=1.0,src="atlas-ops://etl"):
    edges.append({"source":s,"target":t,"relation":rel,"confidence":conf,"confidence_score":sc,
                  "source_file":src,"source_location":None,"weight":w})

# ---- backbone: BC + ACC projects, shared entities ----
bc = load_list(STAGING / "bc_projects.json")
bc_proj = []
for r in bc:
    nm = (r.get("name") or "").strip()
    if not nm or SKIP.search(nm): continue
    num, st, val = r.get("number") or "", r.get("state") or "", r.get("value")
    vs = f" · ${val/1e6:.1f}M" if isinstance(val,(int,float)) and val else ""
    pid = N(nid("bc", r.get("id") or nm), f"{nm}{(' ('+num+')') if num else ''} · BC{(' · '+st) if st else ''}{vs}",
            "document", "buildingconnected://projects")
    bc_proj.append((pid, nm))
    for fld, rel, kind in (("client","client_of","client"), ("architect","designed_by","architect")):
        x = name_of(r.get(fld))
        if x: E(pid, N(nid(kind,x), f"{x} ({kind})", "concept", f"buildingconnected://{kind}s"), rel)
    ms = name_of(r.get("marketSector"))
    if ms: E(pid, N(nid("sector",ms), f"{ms} (market sector)","concept","buildingconnected://sectors"), "in_sector")

acc_proj = []
for r in load_list(STAGING / "acc_projects.json"):
    nm = (r.get("name") or "").strip()
    if not nm or SKIP.search(nm): continue
    acc_proj.append((N(nid("acc", r.get("id") or nm), f"{nm} · ACC (active construction)","document","acc://projects"), nm))

xl = 0
for bpid, bn in bc_proj:
    bs = sig(bn)
    if not bs: continue
    for apid, an in acc_proj:
        a = sig(an)
        if a and (bs <= a or a <= bs or len(bs & a) >= 2):
            E(bpid, apid, "same_project_across_systems", "INFERRED", 0.85 if (bs<=a or a<=bs) else 0.75); xl += 1

# ---- depth: per-project bid packages -> subs -> trades ----
depth_projects, depth_subs, depth_bids = 0, set(), 0
for pdir in sorted((STAGING / "projects").glob("*")) if (STAGING / "projects").exists() else []:
    if not (pdir / "packages.json").exists(): continue
    doc = json.loads((pdir / "packages.json").read_text())
    proj, packages = doc["project"], doc["packages"]
    invites, bids = load_list(pdir / "invites.json"), load_list(pdir / "bids.json")
    cn = {}
    for inv in invites:
        c = inv.get("bidderCompany") or {}
        if c.get("id"): cn[c["id"]] = c.get("name") or c["id"]
    pbids = defaultdict(list)
    for b in bids:
        cid, pk, amt = b.get("bidderCompanyId"), b.get("bidPackageId"), (b.get("leveledTotal") or b.get("total"))
        if pk and cid: pbids[pk].append((cid, amt)); cn.setdefault(cid, cid)
    pnode = N(nid("bc", proj["id"]), f"{proj['name']} · BC", "document", "buildingconnected://projects")
    depth_projects += 1
    for pid_, info in packages.items():
        div, mj = info.get("div",""), major_div(info.get("div",""))
        bl = pbids.get(pid_, []); amts = [a for _,a in bl if isinstance(a,(int,float)) and a]
        lab = f"{proj['name'].split(' ')[0]} — {info['name']} ({div or 'misc'}) · {len(bl)} bids"
        lab += f" · ${min(amts)/1e6:.2f}M–${max(amts)/1e6:.2f}M" if amts else ""
        pk = N("pkg_"+pid_, lab, "document", "buildingconnected://packages")
        E(pnode, pk, "has_package")
        if mj in MF:
            tn = N(f"trade_{mj:02d}", f"CSI {mj:02d} — {MF[mj]}", "concept", "csi://masterformat")
            E(pk, tn, "in_trade")
        for cid, amt in bl:
            sn = N("sub_"+cid, f"{cn.get(cid,cid)} (subcontractor)", "concept", "buildingconnected://companies")
            depth_subs.add(cid); depth_bids += 1
            E(pk, sn, "received_bid_from", w=float(amt) if isinstance(amt,(int,float)) and amt else 1.0,
              src="buildingconnected://bids")
            if mj in MF: E(sn, f"trade_{mj:02d}", "bids_in_trade", "INFERRED", 0.85)

extract = {"nodes": list(nodes.values()), "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
(OUT/".graphify_extract.json").write_text(json.dumps(extract, indent=2))
detect = {"files": {"document": ["buildingconnected://projects","acc://projects","buildingconnected://packages"]},
          "total_files": 3, "total_words": 0, "scan_root": str(STAGING)}
(OUT/".graphify_detect.json").write_text(json.dumps(detect))
print(f"backbone: {len(bc_proj)} BC + {len(acc_proj)} ACC projects, {xl} cross-system links")
print(f"depth: {depth_projects} project(s), {len(depth_subs)} distinct subs, {depth_bids} bid edges")

from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json
G = build_from_json(extract, root=str(STAGING))
comm = cluster(G); coh = score_all(G, comm)
gods = god_nodes(G); surp = surprising_connections(G, comm)
labels = {c:"Community "+str(c) for c in comm}
qs = suggest_questions(G, comm, labels)
(OUT/"GRAPH_REPORT.md").write_text(generate(G, comm, coh, labels, gods, surp, detect, {"input":0,"output":0}, ".", suggested_questions=qs))
to_json(G, comm, str(OUT/"graph.json"))
print(f"GRAPH: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(comm)} communities")
