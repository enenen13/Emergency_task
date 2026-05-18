from pathlib import Path
import argparse
import os

import networkx as nx
import yaml


BASE_DIR = Path(__file__).resolve().parent
YAML_PATH = BASE_DIR / "protocol.yaml"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / ".cache"
MPL_CONFIG_DIR = BASE_DIR / ".matplotlib_cache"

os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_DIR))
CACHE_DIR.mkdir(exist_ok=True)
MPL_CONFIG_DIR.mkdir(exist_ok=True)

import matplotlib.pyplot as plt
from matplotlib import font_manager

plt.switch_backend("Agg")

JP_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\YuGothM.ttc"),
    Path(r"C:\Windows\Fonts\YuGothR.ttc"),
    Path(r"C:\Windows\Fonts\meiryo.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
]


def short(text, max_len=34):
    text = "" if text is None else str(text).replace("\n", " ")
    return text if len(text) <= max_len else text[:max_len] + "..."


def load_protocol(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_font_family():
    for font_path in JP_FONT_CANDIDATES:
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
            return font_manager.FontProperties(fname=str(font_path)).get_name()
    return "DejaVu Sans"


def protocol_start_nodes(data):
    starts = {}
    for protocol in data.get("protocols", []):
        protocol_id = protocol.get("id")
        if not protocol_id:
            continue
        if protocol.get("start_node"):
            starts[protocol_id] = protocol["start_node"]
            continue
        for node in protocol.get("nodes", []):
            if not node.get("metadata_only") and not node.get("transition_only"):
                starts[protocol_id] = node.get("id")
                break
    return starts


def add_node(graph, node_id, label=None, kind="question", group=None):
    if not node_id:
        return
    if node_id not in graph:
        graph.add_node(
            node_id,
            label=label or node_id,
            kind=kind,
            group=group or "",
        )
    elif label:
        graph.nodes[node_id]["label"] = label


def add_edge(graph, src, dst, label=""):
    if not src or not dst:
        return
    add_node(graph, dst)
    graph.add_edge(src, dst, label=short(label, 22))


def add_triage_edge(graph, src, triage, label="triage"):
    dst = f"triage:{triage}"
    add_node(graph, dst, label=triage, kind="triage")
    add_edge(graph, src, dst, label)


def resolve_protocol(protocol_id, starts):
    return starts.get(protocol_id, f"protocol:{protocol_id}")


def add_transitions_from_node(graph, node, starts, group=None):
    node_id = node.get("id")
    if not node_id:
        return

    question = node.get("question") or node.get("name") or node_id
    label = f"{node_id}\n{short(question)}"
    kind = "metadata" if node.get("metadata_only") else "question"
    add_node(graph, node_id, label=label, kind=kind, group=group)

    if node.get("next"):
        add_edge(graph, node_id, node["next"], "next")
    if node.get("route_to"):
        add_edge(graph, node_id, node["route_to"], "route")
    if node.get("route_to_protocol"):
        dst = resolve_protocol(node["route_to_protocol"], starts)
        add_edge(graph, node_id, dst, f"protocol:{node['route_to_protocol']}")
    if node.get("triage"):
        add_triage_edge(graph, node_id, node["triage"])

    for choice in node.get("choices", []) or []:
        choice_label = choice.get("text") or choice.get("value") or choice.get("code") or ""
        if choice.get("next"):
            add_edge(graph, node_id, choice["next"], choice_label)
        if choice.get("route_to"):
            add_edge(graph, node_id, choice["route_to"], choice_label)
        if choice.get("route_to_protocol"):
            dst = resolve_protocol(choice["route_to_protocol"], starts)
            add_edge(graph, node_id, dst, choice_label)
        if choice.get("triage"):
            add_triage_edge(graph, node_id, choice["triage"], choice_label)
        if choice.get("followup_metadata"):
            add_edge(graph, node_id, choice["followup_metadata"], "followup")

    for route in node.get("routes", []) or []:
        protocol_id = route.get("protocol")
        if protocol_id:
            dst = resolve_protocol(protocol_id, starts)
            label = protocol_id
            if route.get("age_condition"):
                label = f"{label}\n{route['age_condition']}"
            add_edge(graph, node_id, dst, label)


def build_graph(data, protocol_id=None, include_entry=True):
    graph = nx.DiGraph()
    starts = protocol_start_nodes(data)

    target_protocol = None
    if protocol_id:
        target_protocol = next(
            (
                protocol
                for protocol in data.get("protocols", []) or []
                if protocol.get("id") == protocol_id
            ),
            None,
        )

    if include_entry and not protocol_id:
        for node in data.get("entry_flow", []) or []:
            add_transitions_from_node(graph, node, starts, "entry_flow")
        for node in data.get("common_vitals", []) or []:
            add_transitions_from_node(graph, node, starts, "common_vitals")
        router = data.get("chief_complaint_router")
        if router:
            router = {"id": "chief_complaint_router", **router}
            add_transitions_from_node(graph, router, starts, "router")

    if target_protocol and "common_vitals" in (target_protocol.get("inherits") or []):
        for node in data.get("common_vitals", []) or []:
            add_transitions_from_node(graph, node, starts, "common_vitals")
        start = starts.get(protocol_id)
        if "route_to_symptom_inquiry" in graph and start:
            add_edge(graph, "route_to_symptom_inquiry", start, f"protocol:{protocol_id}")

    for protocol in data.get("protocols", []) or []:
        current_id = protocol.get("id")
        if protocol_id and current_id != protocol_id:
            continue
        for node in protocol.get("nodes", []) or []:
            add_transitions_from_node(graph, node, starts, current_id)

        followups = protocol.get("terminal_followup_chain", []) or []
        if followups:
            start = starts.get(current_id)
            if start:
                add_edge(graph, start, followups[0], "followup")
            for node_id in followups:
                add_node(graph, node_id, kind="metadata", group=current_id)
            for src, dst in zip(followups, followups[1:]):
                add_edge(graph, src, dst, "followup")

    return graph


def layered_layout(graph):
    roots = [n for n, degree in graph.in_degree() if degree == 0]
    preferred = [
        "intro_fire_or_emergency",
        "dispatch_location",
        "overview",
    ]
    roots = [n for n in preferred if n in graph] + [n for n in roots if n not in preferred]
    if not roots:
        roots = list(graph.nodes())[:1]

    level = {root: 0 for root in roots}
    queue = list(roots)
    while queue:
        src = queue.pop(0)
        for dst in graph.successors(src):
            next_level = level[src] + 1
            if dst not in level or next_level < level[dst]:
                level[dst] = next_level
                queue.append(dst)

    fallback_level = max(level.values(), default=0) + 1
    for node in graph.nodes:
        level.setdefault(node, fallback_level)

    by_level = {}
    for node, lv in level.items():
        by_level.setdefault(lv, []).append(node)

    pos = {}
    for lv, nodes in sorted(by_level.items()):
        nodes = sorted(nodes)
        width = max(len(nodes) - 1, 1)
        for i, node in enumerate(nodes):
            pos[node] = ((i - width / 2) * 3.6, -lv * 2.4)
    return pos


def draw_graph(graph, output, title, layout="layered"):
    output = Path(output)
    if not output.is_absolute():
        output = OUTPUT_DIR / output
    output.parent.mkdir(parents=True, exist_ok=True)

    font_family = get_font_family()
    plt.rcParams["font.family"] = [font_family]

    node_count = max(graph.number_of_nodes(), 1)
    width = min(max(node_count * 0.34, 18), 80)
    height = min(max(node_count * 0.25, 14), 60)
    plt.figure(figsize=(width, height))

    if layout == "spring":
        pos = nx.spring_layout(graph, seed=42, k=1.15)
    else:
        pos = layered_layout(graph)

    color_by_kind = {
        "question": "#DCEEFF",
        "metadata": "#ECECEC",
        "triage": "#FFD6D6",
    }
    node_colors = [
        color_by_kind.get(attrs.get("kind"), "#DCEEFF")
        for _, attrs in graph.nodes(data=True)
    ]
    labels = {node: attrs.get("label", node) for node, attrs in graph.nodes(data=True)}

    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colors,
        node_size=2700,
        edgecolors="#333333",
        linewidths=0.8,
    )
    nx.draw_networkx_edges(
        graph,
        pos,
        arrows=True,
        arrowstyle="->",
        arrowsize=17,
        width=1.1,
        edge_color="#555555",
        connectionstyle="arc3,rad=0.08",
    )
    nx.draw_networkx_labels(
        graph,
        pos,
        labels=labels,
        font_size=8,
        font_family=font_family,
    )
    nx.draw_networkx_edge_labels(
        graph,
        pos,
        edge_labels=nx.get_edge_attributes(graph, "label"),
        font_size=7,
        font_family=font_family,
    )

    plt.title(title, fontsize=14)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output, dpi=220, bbox_inches="tight")
    plt.close()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaml", default=YAML_PATH)
    parser.add_argument("--protocol", help="例: dyspnea, trauma, pediatric_nausea_vomiting")
    parser.add_argument("--output", default="transition_graph.png")
    parser.add_argument("--layout", choices=["layered", "spring"], default="layered")
    args = parser.parse_args()

    data = load_protocol(args.yaml)
    graph = build_graph(data, protocol_id=args.protocol)
    title = "119番通報 緊急度判定プロトコル 遷移図"
    if args.protocol:
        title += f" / {args.protocol}"

    output = draw_graph(graph, args.output, title, layout=args.layout)
    print(f"出力: {output}")
    print(f"nodes: {graph.number_of_nodes()}")
    print(f"edges: {graph.number_of_edges()}")


if __name__ == "__main__":
    main()
