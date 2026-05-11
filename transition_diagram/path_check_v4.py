"""
v0.3.2 検査: terminal_followup_chain と followup_metadata を含めたフル到達性検査
"""
import yaml

with open("/home/claude/work/protocol.yaml", encoding="utf-8") as f:
    d = yaml.safe_load(f)

nodes = {}
for n in d["entry_flow"]: nodes[n["id"]] = n
for n in d["common_vitals"]: nodes[n["id"]] = n
nodes["chief_complaint_router"] = d["chief_complaint_router"]

protocol_starts = {}
protocol_followups = {}
for p in d["protocols"]:
    for n in p["nodes"]: nodes[n["id"]] = n
    if p.get("start_node"):
        protocol_starts[p["id"]] = p["start_node"]
    else:
        for n in p["nodes"]:
            if not n.get("metadata_only") and not n.get("transition_only"):
                protocol_starts[p["id"]] = n["id"]; break
    if p.get("terminal_followup_chain"):
        protocol_followups[p["id"]] = p["terminal_followup_chain"]

def walk():
    seen = set()
    stack = ["intro_fire_or_emergency"]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in nodes: continue
        seen.add(nid)
        node = nodes[nid]
        if nid == "chief_complaint_router":
            for r in node.get("routes", []):
                t = r.get("protocol")
                if t in protocol_starts: stack.append(protocol_starts[t])
                # terminal_followup も到達対象に
                if t in protocol_followups:
                    for fu in protocol_followups[t]: stack.append(fu)
            continue
        if "next" in node and isinstance(node["next"], str): stack.append(node["next"])
        for ch in node.get("choices") or []:
            if "next" in ch: stack.append(ch["next"])
            if "route_to_protocol" in ch and ch["route_to_protocol"] in protocol_starts:
                stack.append(protocol_starts[ch["route_to_protocol"]])
                if ch["route_to_protocol"] in protocol_followups:
                    for fu in protocol_followups[ch["route_to_protocol"]]:
                        stack.append(fu)
            if "followup_metadata" in ch:
                stack.append(ch["followup_metadata"])
    return seen

reachable = walk()
all_ids = set(nodes.keys())
unreachable = sorted(all_ids - reachable)

real_unreach = [x for x in unreachable if not nodes[x].get("metadata_only") and not nodes[x].get("transition_only")]
meta_unreach = [x for x in unreachable if nodes[x].get("metadata_only") or nodes[x].get("transition_only")]

print(f"全ノード数: {len(all_ids)}")
print(f"到達可能ノード: {len(reachable)}")
print(f"到達不能ノード: {len(unreachable)}")
print()
print(f"=== 非メタ到達不能: {len(real_unreach)}件 ===")
for n in real_unreach: print(f"  - {n}")
print()
print(f"=== メタ☎ 到達不能: {len(meta_unreach)}件 ===")
for n in meta_unreach: print(f"  ☎ {n}")
print()

# === synthetic_node 残存チェック ===
print("=== synthetic_node 残存チェック ===")
synth_count = 0
for p in d["protocols"]:
    for n in p["nodes"]:
        if n.get("synthetic_node"):
            synth_count += 1
            print(f"  残存: {p['id']} / {n['id']}")
if synth_count == 0:
    print("  ✅ synthetic_node はゼロ件（全てPDF直接の構造に置換済み）")
print()

# === overview 構造の最終確認 ===
print("=== overview の最終構造 ===")
overview = next(n for n in d["entry_flow"] if n["id"]=="overview")
for ch in overview["choices"]:
    code = ch.get("code")
    parts = []
    if ch.get("triage"): parts.append(f"triage={ch['triage']}")
    if ch.get("next"): parts.append(f"next={ch['next']}")
    if ch.get("route_to_protocol"): parts.append(f"route_to_protocol={ch['route_to_protocol']}")
    if ch.get("matches_rule"): parts.append(f"matches_rule={ch['matches_rule']}")
    if ch.get("pdf_cell_blank"): parts.append("pdf_cell_blank=True")
    print(f"  {code:35s}: {' | '.join(parts)}")
