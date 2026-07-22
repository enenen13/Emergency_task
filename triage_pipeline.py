"""
triage_pipeline.py
==================
119番トリアージ推論パイプライン（protocol.yaml をルールで探索する決定的エンジン）。

■ このモジュールがやること
  1. transition_diagram/protocol.yaml を読み、ノードをフラットに索引化する。
  2. 「回答（各質問ノードの選択肢）＋年齢」を入力に、
       - 共通フロー（entry_flow → common_vitals → 症候別へ）を辿る
       - 21+1 症候プロトコルをそれぞれ辿る
     を行い、各々の「完了 / 途切れ」を判定する。
  3. ユーザー指定の合成ロジックで最終トリアージを決める：
       ┌ 共通完了 かつ ≥1症状完了 → 完了症状のトリアージ（複数なら最重症＋その旨）
       │ 共通完了 かつ 症状全て途切れ → VE（途切れVE）
       │ 共通途切れ かつ ≥1症状完了 → VE（途切れVE, どの症状が完了したか付記）
       └ 共通途切れ かつ 症状全て途切れ → VE（両方途切れ）
     出力＝メイン(VE/SE/LE) / サブ1(R/Y/G 根拠 or 途切れVE) / サブ2(詳細) / 参考(最遷移症状)。

■ トリアージ対応（protocol.yaml labels より）
     R1/R2/R3 = 赤 = Very Emergency (VE)
     Y1/Y2    = 黄 = Semi Emergency (SE)
     G        = 緑 = Low Emergency  (LE)

■ 回答(answers)の表し方
     answers: dict[node_id, choice]     choice は protocol.yaml の choices[].code（'a','b','c',…）
     ・BERT(verify07)の出力 code は 0=非該当 / 1..N=選択肢インデックス。
       → bert_code_to_choice(node, code) で 1→'a', 2→'b', … に変換（0=非該当は None＝回答なし＝途切れ要因）。
     ・BERTの学習ノード名（例 '02_asthma_history'）と protocol.yaml のノードID（例 'common_breathing'）は
       一致しない。両者の対応は NODE_MAP（下部）に定義する。※ここは要・実データ整合（下記「未確定点」）。

■ 未確定点（実運用前に要調整）
     ・NODE_MAP（yamlノードID ↔ BERTノード）の網羅。protocol.yaml は判定の一部を common_vitals 側に
       持つ症候があり（例 dyspnea）、BERTの症候別ノードと1:1でない。対応表を詰める必要がある。
     ・各症候プロトコルの「入口ノード」。ここでは nodes[0] を入口とみなす（必要なら PROTOCOL_ENTRY で上書き）。

決定ロジック（decide）と探索エンジン（traverse）は完成・検証済み。BERT配線とNODE_MAPが差し込み口。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

try:
    import age_logic          # 機能1: 年齢との論理集合（Aタイプ回答の自動導出）
except Exception:             # age_logic が無くても本体は動く
    age_logic = None

# ---------------------------------------------------------------------------
# トリアージ・ラベル対応
# ---------------------------------------------------------------------------
CAT = {'R1': 'VE', 'R2': 'VE', 'R3': 'VE', 'Y1': 'SE', 'Y2': 'SE', 'G': 'LE'}
MAIN_NAME = {'VE': 'Very Emergency', 'SE': 'Semi Emergency', 'LE': 'Low Emergency'}
# 重症度の並び（先頭ほど重症）。「最重症」判定に使う。
PRIORITY_ORDER = ['R1', 'R2', 'R3', 'Y1', 'Y2', 'G']

COMMON_DONE_IDS = ('route_to_symptom_inquiry', 'chief_complaint_router')  # ここに到達＝共通完了


# ---------------------------------------------------------------------------
# 1. protocol.yaml の読み込みとグラフ化
# ---------------------------------------------------------------------------
@dataclass
class ProtocolGraph:
    raw: dict
    index: Dict[str, dict]                 # node_id -> node（entry_flow / common_vitals / 各protocolのnodes）
    entry_start: str                       # 共通フローの入口ノードID
    protocol_ids: List[str]                # 症候プロトコルID一覧
    protocol_nodes: Dict[str, List[str]]   # protocol_id -> その症候のノードID列
    global_rules: List[dict]

    def node(self, nid: str) -> Optional[dict]:
        return self.index.get(nid)


# 症候プロトコルの入口ノードを明示したい場合の上書き（未指定は nodes[0]）
PROTOCOL_ENTRY: Dict[str, str] = {}


def load_graph(path: str = None) -> ProtocolGraph:
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        tdir = os.path.join(here, 'transition_diagram')
        # ★学習ラベルの由来である spreadsheet版yaml（不明などの正規回答肢を完全収録）を優先。
        #   protocol.yaml は不明肢が欠落しており、code=不明(=k) が choices[k-1] 不在で
        #   None(=該当なし=途切れ)に落ちてしまう（label0のみ該当なしが正）。最新の
        #   protocol_spreadsheet_aligned*.yaml があればそれを既定にし、無ければ protocol.yaml。
        import glob as _glob
        _aligned = sorted(_glob.glob(os.path.join(tdir, 'protocol_spreadsheet_aligned*.yaml')))
        path = _aligned[-1] if _aligned else os.path.join(tdir, 'protocol.yaml')
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    index: Dict[str, dict] = {}
    for node in raw.get('entry_flow', []):
        index[node['id']] = node
    for node in raw.get('common_vitals', []):
        index[node['id']] = node
    # 症候別への案内 / ルーターも索引に入れておく（完了判定の到達先）
    router = raw.get('chief_complaint_router')
    if isinstance(router, dict) and 'id' in router:
        index[router['id']] = router

    protocol_nodes: Dict[str, List[str]] = {}
    protocol_ids: List[str] = []
    for proto in raw.get('protocols', []):
        pid = proto['id']
        protocol_ids.append(pid)
        ids = []
        for node in proto.get('nodes', []):
            index[node['id']] = node
            ids.append(node['id'])
        protocol_nodes[pid] = ids

    entry_start = raw['entry_flow'][0]['id']
    return ProtocolGraph(raw=raw, index=index, entry_start=entry_start,
                         protocol_ids=protocol_ids, protocol_nodes=protocol_nodes,
                         global_rules=raw.get('global_priority_rules', []))


# ---------------------------------------------------------------------------
# 2. 回答アダプタ（BERT code → yaml choice）
# ---------------------------------------------------------------------------
_LETTERS = 'abcdefghij'

# 学習ラベル→yaml choice code リマップ（07_21データが yaml の並びと一致しないノード）。
# 07_21 の一部ノードは yaml choices と label の順序がズレて学習されており
# （選択肢の意味圧縮や c↔d の入れ替わり）、位置対応 choices[k-1] では triage がズレる。
# 全07_21データのキーワード検証で確認した正しい対応へ明示変換する（はい=1/いいえ=2 等は不変）。
COMMON_LABEL_REMAP: Dict[str, Dict[int, str]] = {
    # overview: label選択肢は4つ(1:CPA/2:けいれん/3:主訴分類/4:不明)だが yaml choices は5つ
    # (a,b外傷,c convulsion,chief_complaint_classification,d)で並びがズレる。位置変換だと
    # code3→'c'(convulsion) に誤爆し、通常主訴が全て convulsion へ直行→共通バイタルを飛ばす。
    # code_label に合わせて明示変換する（3=主訴分類→chief_complaint_classification で共通続行）。
    'overview':             {1: 'a', 2: 'c', 3: 'chief_complaint_classification', 4: 'd'},
    'common_breathing':     {3: 'f', 4: 'g'},   # 3=呼吸が苦しそう(R2) / 4=不明(R3)
    'common_conversation':  {3: 'i'},           # 3=不明(R3)
    # 注: hematemesis_amount 等は protocol.yaml に選択肢dが無い＝yaml版ズレ（下記）。
    #     pipeline を spreadsheet版yamlに切替えれば {3:'d',4:'c'} で整合する。
}


def bert_code_to_choice(code: int, node: Optional[dict] = None) -> Optional[str]:
    """BERTの分類 code(0..N) を yaml の choice code へ。
    0=非該当 → None（＝回答なし＝その質問では辺を選べない＝途切れ要因）。

    ★ 学習ラベル規約: code=k は「そのノードの choices の k番目」（node_questions の
       yaml_choices 順＝yaml choices 順）。したがって node（yamlノードdict）を渡せば
       choices[k-1] の“実コード”を返すのが正しい（例: overview の 4番目='chief_complaint_classification'、
       palpitation の 3番目='b' 等、a,b,c…の連番でないノードでも正しく対応）。
    node 省略時のみ後方互換で 'a','b',… の位置レター変換にフォールバックする。"""
    if code is None or int(code) <= 0:
        return None
    # 共通ノードは学習データが yaml の選択肢を意味で圧縮しているため、
    # 位置対応(choices[k-1])では triage がズレる。yaml choice へ明示リマップする。
    if node is not None:
        _rm = COMMON_LABEL_REMAP.get(node.get('id'))
        if _rm and int(code) in _rm:
            return _rm[int(code)]
    idx = int(code) - 1
    if node is not None and 'choices' in node:
        choices = node['choices']
        return str(choices[idx]['code']) if 0 <= idx < len(choices) else None
    return _LETTERS[idx] if idx < len(_LETTERS) else None


# yamlノードID -> BERTノードキー の対応表。
# transition_diagram/node_map.json（ノード名suffix一致で自動生成・107件）を読み込む。
# 無ければ空（＝全yaml分岐が未回答＝途切れ）。手修正はJSONを直すか下のdictを上書き。
NODE_MAP: Dict[str, str] = {}


def _load_node_map(path: str = None) -> Dict[str, str]:
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, 'transition_diagram', 'node_map.json')
    if os.path.exists(path):
        import json
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


NODE_MAP.update(_load_node_map())


_AGE_FLAGS_CACHE = None


def _age_flags() -> Dict[str, dict]:
    """dictionary/age_flags.json を1回だけ読み込む（無ければ空）。"""
    global _AGE_FLAGS_CACHE
    if _AGE_FLAGS_CACHE is None:
        try:
            _AGE_FLAGS_CACHE = age_logic.load_age_flags() if age_logic is not None else {}
        except Exception:
            _AGE_FLAGS_CACHE = {}
    return _AGE_FLAGS_CACHE


def build_answers_from_bert(bert_pred: Dict[str, int],
                            graph: Optional['ProtocolGraph'] = None,
                            age: Any = None,
                            sex: Optional[str] = None) -> Dict[str, str]:
    """BERT予測 {bert_node: code} を yaml探索用 {yaml_node_id: choice} へ変換する。
    NODE_MAP を yaml→bert 方向で走査するので、1つのBERTノードが複数のyamlノードに
    対応していても（例 00_common_cold_sweat → cold_sweat と age_subquestion）両方に配れる。
    ★ graph を渡すと各yamlノードの choices を使って code→実コードを正しく対応させる
       （a,b,c…連番でないノードのズレを解消）。graph 省略時は位置レター変換にフォールバック。"""
    flags = _age_flags()
    out: Dict[str, str] = {}
    for yaml_node, bert_node in NODE_MAP.items():
        if bert_node not in bert_pred:
            continue
        node = graph.node(yaml_node) if graph is not None else None
        code = bert_pred[bert_node]
        # 年齢/性別が関わる分岐は resolve_age_branch で論理計算（方針B: triage時に適用）
        if node is not None and age_logic is not None and yaml_node in flags:
            res = age_logic.resolve_age_branch(node, flags[yaml_node], code, age, sex)
            if not res.get('applies', True):
                continue                       # ゲート不成立 → この分岐はスキップ（回答なし）
            ch = res.get('resolved_code')
        else:
            ch = bert_code_to_choice(code, node)
        if ch is not None:
            out[yaml_node] = ch
    return out


# ---------------------------------------------------------------------------
# 3. トラバーサル（グラフ探索）
# ---------------------------------------------------------------------------
@dataclass
class Walk:
    path: List[str]                 # 通過したノードID列
    completed: bool = False         # 完了（triage終端 or 指定完了ノードに到達）
    broke_off: bool = False         # 途切れ（回答なしで辺を選べず停止）
    triage: Optional[str] = None    # 到達したトリアージ（R1..G）※あれば
    route: Optional[str] = None     # route_to_protocol / route_to の行き先
    stop: str = ''                  # 停止理由
    transitions: int = 0            # 遷移数（＝どれだけ深く辿れたか）


def traverse(graph: ProtocolGraph, start_id: str, answers: Dict[str, str],
             complete_ids: tuple = (), max_steps: int = 400) -> Walk:
    """start_id から answers に従って辺を辿る。**トリアージの重症度比較は一切しない。**
       ・choices ノード：answers[node] の choice を選ぶ。無ければ node['next']（無ければ conditional は
         先頭choiceのnextでスキップ）へ。
       ・triage を持つ終端に到達 → その終端のトリアージで確定（比較しない）。
       ・triage/next に効く質問が「未回答で進めない」→ 途切れ（broke_off）。
       ・accumulate_triage（しびれ等）：triageを確定させず acc に積んで next へ継続。
         メタ収集の末端(発症時刻など)まで辿れたら **acc の最後に記録した症状トリアージ** を採用（比較ではなく経路順）。"""
    path: List[str] = []
    acc: List[str] = []          # accumulate_triage で記録した（確定させていない）トリアージ
    nid = start_id
    seen = set()

    def _end_here(stop: str) -> Walk:
        # メタ末端などに到達した終点。記録済み症状トリアージ(acc)の最後を採る。無ければ途切れ。
        final = acc[-1] if acc else None
        return Walk(path=path, completed=final is not None, broke_off=final is None,
                    triage=final, stop=stop, transitions=len(path))

    while nid and len(path) < max_steps:
        if nid in complete_ids:
            return Walk(path=path + [nid], completed=True, stop=f'reached:{nid}',
                        triage=(acc[-1] if acc else None), transitions=len(path))
        node = graph.node(nid)
        if node is None:
            return Walk(path=path, broke_off=True, stop=f'missing_node:{nid}', transitions=len(path))
        if nid in seen:
            return _end_here(f'loop:{nid}')
        seen.add(nid)
        path.append(nid)

        # ノード自体が triage 終端
        if 'triage' in node and 'choices' not in node:
            return Walk(path=path, completed=True, triage=node['triage'],
                        stop='triage_node', transitions=len(path))

        if 'choices' in node:
            ans = answers.get(nid)
            chosen = None
            if ans is not None:
                for c in node['choices']:
                    if str(c.get('code')) == str(ans):
                        chosen = c
                        break
            if chosen is None:
                # 回答なし：ノード既定next → conditional なら先頭choiceのnextでスキップ
                nxt = node.get('next')
                if not nxt and node.get('conditional_on') and node['choices']:
                    nxt = node['choices'][0].get('next')
                if nxt:
                    nid = nxt
                    continue
                # 進めない。この質問が triage/分岐に効くなら「途切れ」、メタ収集の末端なら終点。
                affecting = any(('triage' in c or 'next' in c or 'route_to' in c or 'route_to_protocol' in c)
                                for c in node['choices'])
                if affecting:
                    return Walk(path=path, broke_off=True, triage=None,
                                stop=f'no_answer:{nid}', transitions=len(path))
                return _end_here(f'leaf_end:{nid}')
            if 'triage' in chosen:
                # accumulate_triage + next → 記録して継続。それ以外はこの終端で確定（比較しない）。
                if chosen.get('accumulate_triage') and chosen.get('next'):
                    acc.append(chosen['triage'])
                    nid = chosen['next']
                    continue
                return Walk(path=path, completed=True, triage=chosen['triage'],
                            stop='triage_choice', transitions=len(path))
            if 'route_to_protocol' in chosen:
                return Walk(path=path, completed=True, route=chosen['route_to_protocol'],
                            triage=(acc[-1] if acc else None), stop='route_to_protocol', transitions=len(path))
            if 'route_to' in chosen:
                return Walk(path=path, completed=True, route=chosen['route_to'],
                            triage=(acc[-1] if acc else None), stop='route_to', transitions=len(path))
            nxt = chosen.get('next') or node.get('next')
            if not nxt:
                return _end_here(f'choice_leaf_end:{nid}')     # 選択肢に行き先なし＝収集末端
            nid = nxt
            continue

        # choices なし（metadata_only / transition_only / 単純ノード）→ 既定 next
        nxt = node.get('next')
        if not nxt:
            return _end_here(f'deadend:{nid}')
        nid = nxt
    return _end_here('max_steps')


def traverse_common(graph: ProtocolGraph, answers: Dict[str, str]) -> Walk:
    """共通フロー（導入＋共通バイタル）を辿る。route_to_symptom_inquiry / router 到達で完了。"""
    return traverse(graph, graph.entry_start, answers, complete_ids=COMMON_DONE_IDS)


def traverse_symptom(graph: ProtocolGraph, protocol_id: str, answers: Dict[str, str]) -> Walk:
    """症候プロトコルを入口から辿る。triage終端到達で完了。"""
    entry = PROTOCOL_ENTRY.get(protocol_id)
    if entry is None:
        ids = graph.protocol_nodes.get(protocol_id, [])
        if not ids:
            return Walk(path=[], broke_off=True, stop=f'no_nodes:{protocol_id}')
        entry = ids[0]
    return traverse(graph, entry, answers)


# ---------------------------------------------------------------------------
# 4. 合成判定ロジック（ユーザー仕様）
# ---------------------------------------------------------------------------
@dataclass
class TriageResult:
    # ===== メイン出力 =====
    main: str                       # 'VE' | 'SE' | 'LE'（複数完了時のみ '複数完了'）
    main_name: str                  # 'Very Emergency' など
    # ===== サブ出力 =====
    sub1: str                       # 'R1'..'G' or '途切れVE(該当なし)'
    sub2: str                       # 詳細ステータス（共通完了かつ症状完了1つ 等）
    reason: str = ''                # どうしてそうなったか（決定経路の説明）
    # ===== 内訳 =====
    common_completed: Optional[bool] = None   # 共通フローが症候別へ到達したか
    common_stop: str = ''                     # 共通フローの停止理由
    completed_symptoms: List[str] = field(default_factory=list)   # 完了した症候
    broke_symptoms: List[dict] = field(default_factory=list)      # 途切れた症候 [{protocol,transitions,stop}]
    furthest_symptom: Optional[str] = None       # 全症候で最も遷移できたもの
    furthest_transitions: int = 0
    furthest_broke_symptom: Optional[str] = None # ★途切れた中で最も遷移が長かった症候
    furthest_broke_transitions: int = 0
    global_override: Optional[str] = None        # global_priority_rules 発火時のルールID
    note: str = ''

    def __str__(self) -> str:
        fb = (f' / 途切れ最長={self.furthest_broke_symptom}({self.furthest_broke_transitions})'
              if self.furthest_broke_symptom else '')
        return f'[{self.main_name}] sub1={self.sub1} | {self.sub2}{fb}'

    def report(self) -> str:
        """予測時に一括表示する詳細レポート。"""
        L = []
        L.append(f'■ メイン: {self.main_name}  (main={self.main})')
        L.append(f'■ サブ1(根拠コード): {self.sub1}')
        L.append(f'■ なぜ: {self.reason}')
        L.append(f'■ 詳細: {self.sub2}')
        if self.common_completed is not None:
            L.append(f'■ 共通フロー: {"完了(症候別へ到達)" if self.common_completed else "途切れ"}  [{self.common_stop}]')
        if self.completed_symptoms:
            L.append(f'■ 完了した症候: {", ".join(self.completed_symptoms)}')
        if self.furthest_broke_symptom:
            L.append(f'■ 途切れた中で最長遷移: {self.furthest_broke_symptom}（{self.furthest_broke_transitions}遷移）')
        if self.broke_symptoms:
            top = sorted(self.broke_symptoms, key=lambda s: -s['transitions'])[:5]
            L.append('■ 途切れ症候(遷移数降順): '
                     + ', '.join(f"{s['protocol']}({s['transitions']})" for s in top))
        if self.global_override:
            L.append(f'■ 最優先ルール発火: {self.global_override}')
        return '\n'.join(L)


def decide(common_completed: bool,
           symptom_results: List[dict]) -> TriageResult:
    """ユーザー指定の4ケース合成ロジック。**トリアージの重症度比較で1つに絞ることはしない。**
    最優先は「共通＋症状が途切れなく完了したか」。
    symptom_results: [{'protocol':str, 'completed':bool, 'triage':str|None, 'transitions':int}, ...]
    """
    completed = [s for s in symptom_results if s.get('completed') and s.get('triage')]
    furthest = max(symptom_results, key=lambda s: s.get('transitions', 0), default=None) \
        if symptom_results else None
    fs_name = furthest['protocol'] if furthest else None
    fs_tr = furthest['transitions'] if furthest else 0

    # ── ケース1：共通完了 かつ ≥1症状完了 ──
    if common_completed and completed:
        names = [s['protocol'] for s in completed]
        if len(completed) == 1:
            s = completed[0]
            main = CAT.get(s['triage'], 'VE')
            return TriageResult(main=main, main_name=MAIN_NAME[main], sub1=s['triage'],
                                sub2=f"共通完了かつ症状完了1つ({s['protocol']}:{s['triage']})",
                                furthest_symptom=fs_name, furthest_transitions=fs_tr,
                                completed_symptoms=names)
        # 複数完了：どれが重いかの比較で1つに絞らず、事実を列挙して出す。
        listed = ', '.join(f"{s['protocol']}:{s['triage']}" for s in completed)
        return TriageResult(main='複数完了', main_name='複数症状完了(要判断・比較なし)',
                            sub1=' / '.join(s['triage'] for s in completed),
                            sub2=f'共通完了かつ症状完了{len(completed)}つ({listed}) ※単一に絞らない',
                            furthest_symptom=fs_name, furthest_transitions=fs_tr,
                            completed_symptoms=names)

    # ── ケース2：共通完了 かつ 症状全て途切れ → VE ──
    if common_completed and not completed:
        return TriageResult('VE', MAIN_NAME['VE'], '途切れVE(該当なし)',
                            '共通完了＋症状全て途切れ',
                            furthest_symptom=fs_name, furthest_transitions=fs_tr)

    # ── ケース3：共通途切れ かつ ≥1症状完了 → VE（どの症状が完了したか付記）──
    if (not common_completed) and completed:
        names = [f"{s['protocol']}:{s['triage']}" for s in completed]
        return TriageResult('VE', MAIN_NAME['VE'], '途切れVE(該当なし)',
                            '共通途切れ＋症状完了(' + ', '.join(names) + ')',
                            furthest_symptom=fs_name, furthest_transitions=fs_tr,
                            completed_symptoms=[s['protocol'] for s in completed])

    # ── ケース4：共通途切れ かつ 症状全て途切れ → VE ──
    return TriageResult('VE', MAIN_NAME['VE'], '途切れVE(該当なし)',
                        '両方途切れ(共通途切れ＋症状途切れ)',
                        furthest_symptom=fs_name, furthest_transitions=fs_tr)


# ---------------------------------------------------------------------------
# 5. エンドツーエンド（yaml探索 → 合成判定）
# ---------------------------------------------------------------------------
def match_global_rules(text: str, rules: List[dict]) -> Optional[dict]:
    """通報テキストに global_priority_rules のキーワードが当たれば最高優先度のルールを返す。"""
    hits = [r for r in rules
            if any(kw in (text or '') for kw in r.get('match_any', []))]
    if not hits:
        return None
    return sorted(hits, key=lambda r: -r.get('priority', 0))[0]


def run_triage(graph: ProtocolGraph,
               answers: Dict[str, str],
               age: Optional[int] = None,
               sex: Optional[str] = None,
               transcript_text: str = '',
               symptoms: Optional[List[str]] = None) -> TriageResult:
    """入力（回答＋年齢＋通報テキスト）から最終トリアージ＋内訳を一括で出す。
       symptoms=None なら全21+1症候を試す（ユーザー方針）。"""
    # 機能1: 年齢との論理集合 — Aタイプ age ノード（「N歳以上ですか？」）を年齢から自動充填
    if age is not None and age_logic is not None:
        answers = age_logic.augment_answers_with_age(answers, age, graph.index)

    # 0) global_priority_rules（CPA等）の即時override
    rule = match_global_rules(transcript_text, graph.global_rules)
    if rule is not None and rule.get('triage'):
        tri = rule['triage']
        main = CAT.get(tri, 'VE')
        return TriageResult(main, MAIN_NAME[main], tri, f'最優先ルール({rule.get("id")})で即時確定',
                            reason=f'通報文が最優先ルール「{rule.get("id")}」に一致 → {tri} 即時確定',
                            global_override=rule.get('id'))

    # 1) 共通フロー
    cw = traverse_common(graph, answers)
    if cw.triage is not None:
        main = CAT.get(cw.triage, 'VE')
        return TriageResult(main, MAIN_NAME[main], cw.triage,
                            f'共通フロー内で確定({cw.path[-1]})',
                            reason=f'共通フローの {cw.path[-1]} で {cw.triage} が確定',
                            common_completed=False, common_stop=cw.stop)
    # 共通完了 = 症候別ルーティングに到達したか。route_to_symptom_inquiry/router 到達(reached:)に加え、
    # overview 等から症候別プロトコルへ直行する route_to_protocol / route_to も「共通は途切れず症候へ渡った」
    # とみなす（protocol.yaml の正規ルート。これを途切れ扱いにすると症候別トリアージが全て安全側VEに潰れる）。
    common_completed = cw.stop.startswith('reached:') or cw.stop.startswith('route_to')

    # 2) 全症候を辿る（回答があった症候だけ完了/遷移とみなすゲート付）
    if symptoms is not None:
        target = symptoms
    else:
        # router_age: 年齢で不適用の症候（成人↔小児）を除外する（例 age<16 は adult_* を除外）
        _routes = (graph.raw.get('chief_complaint_router') or {}).get('routes', [])
        _disallow = set()
        if _routes and age_logic is not None:
            _allowed = set(age_logic.applicable_protocols(_routes, age))
            _disallow = {r['protocol'] for r in _routes if r.get('protocol') not in _allowed}
        target = [p for p in graph.protocol_ids if p not in _disallow]
    sym_results = []
    for pid in target:
        w = traverse_symptom(graph, pid, answers)
        engaged = any(n in answers for n in w.path)
        sym_results.append({'protocol': pid,
                            'completed': bool(engaged and w.triage is not None and not w.broke_off),
                            'triage': w.triage if engaged else None,
                            'transitions': w.transitions if engaged else 0,
                            'broke': bool(engaged and w.broke_off),
                            'stop': w.stop})

    # 3) 合成判定（メイン/サブ）
    res = decide(common_completed, sym_results)

    # 4) 内訳を詰める：共通状態・途切れ症候・「途切れた中で最長遷移」・reason
    res.common_completed = common_completed
    res.common_stop = cw.stop
    broke = [s for s in sym_results if s['broke'] and s['transitions'] > 0]
    res.broke_symptoms = [{'protocol': s['protocol'], 'transitions': s['transitions'], 'stop': s['stop']}
                          for s in broke]
    fb = max(broke, key=lambda s: s['transitions'], default=None)
    res.furthest_broke_symptom = fb['protocol'] if fb else None
    res.furthest_broke_transitions = fb['transitions'] if fb else 0

    completed = [s for s in sym_results if s['completed']]
    if common_completed and completed:
        if len(completed) == 1:
            res.reason = f'共通完了 → {completed[0]["protocol"]} が完了({completed[0]["triage"]}) をそのまま採用'
        else:
            res.reason = (f'共通完了 → {len(completed)}症候が完了。比較で1つに絞らず全て提示')
    elif common_completed and not completed:
        res.reason = '共通は完了したが全症候が途切れ → 判断根拠不足のため安全側で VE'
    elif (not common_completed) and completed:
        res.reason = '共通が途切れ → 症候は完了したが共通が未確立のため安全側で VE'
    else:
        res.reason = '共通も症候も途切れ → 判断根拠なしのため安全側で VE'
    return res


# ---------------------------------------------------------------------------
# 6. 予測時パイプライン入口（BERT予測 → トリアージ）
# ---------------------------------------------------------------------------
def predict_triage(graph: ProtocolGraph,
                   bert_pred: Optional[Dict[str, int]] = None,
                   answers: Optional[Dict[str, str]] = None,
                   age: Optional[int] = None,
                   sex: Optional[str] = None,
                   transcript_text: str = '') -> TriageResult:
    """通報1件を判定する入口。
       ・bert_pred: {bert_node: code}（verify07モデルの出力）を渡すと NODE_MAP 経由で回答へ変換。
       ・answers: yaml回答 {yaml_node: choice} を直接渡してもよい（テスト用）。
       両方省略時は空（＝全途切れ→VE）。"""
    ans = dict(answers) if answers else {}
    if bert_pred:
        ans.update(build_answers_from_bert(bert_pred, graph, age=age, sex=sex))
    return run_triage(graph, ans, age=age, sex=sex, transcript_text=transcript_text)


def predict_answers_with_model(model, tok, node_pairs: Dict[str, str],
                               node_questions: Dict[str, str],
                               max_length: int = 160, device: str = 'cpu') -> Dict[str, int]:
    """verify07 の final_model で {bert_node: pair_text} → {bert_node: code} を推論する。
       node_questions は final_model/node_questions.json の 'node_questions'（BERTノード名キー）。
       ※torch/transformers が要る。モデル未学習ならこの関数は使わず bert_pred を直接渡す。"""
    import torch as _t
    model.eval()
    out = {}
    with _t.no_grad():
        for node, pair in node_pairs.items():
            q = node_questions.get(node)
            if q is None:
                continue
            enc = tok(q, pair, truncation=True, max_length=max_length, return_tensors='pt').to(device)
            out[node] = int(model(**enc).logits.argmax(-1).cpu())
    return out


# ---------------------------------------------------------------------------
# デモ / 自己テスト
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print('=== 決定ロジック decide() の4ケース検証（合成入力）===')
    tests = [
        ('①共通完了×症状1完了(SE)', True,
         [{'protocol': 'headache', 'completed': True, 'triage': 'Y1', 'transitions': 5},
          {'protocol': 'dyspnea', 'completed': False, 'triage': None, 'transitions': 1}]),
        ('①共通完了×症状2完了(比較せず両方出す)', True,
         [{'protocol': 'chest_pain_nontraumatic', 'completed': True, 'triage': 'R2', 'transitions': 4},
          {'protocol': 'headache', 'completed': True, 'triage': 'Y2', 'transitions': 6}]),
        ('②共通完了×症状全途切れ', True,
         [{'protocol': 'headache', 'completed': False, 'triage': None, 'transitions': 3},
          {'protocol': 'dyspnea', 'completed': False, 'triage': None, 'transitions': 2}]),
        ('③共通途切れ×症状完了', False,
         [{'protocol': 'headache', 'completed': True, 'triage': 'G', 'transitions': 5}]),
        ('④共通途切れ×症状全途切れ', False,
         [{'protocol': 'headache', 'completed': False, 'triage': None, 'transitions': 2}]),
    ]
    for name, common_ok, sr in tests:
        r = decide(common_ok, sr)
        print(f'  {name:32s} -> {r}')

    print('\n=== 実 protocol.yaml で共通フロー探索（合成answers）===')
    g = load_graph()
    print(f'  索引ノード数={len(g.index)} / 症候数={len(g.protocol_ids)} / 入口={g.entry_start}')
    # 共通バイタルを「異常なし」で症候別へ到達させる回答
    common_answers = {
        'intro_fire_or_emergency': 'a',                 # 救急
        'overview': 'chief_complaint_classification',   # 主訴分類へ
        'observation': 'a',                             # 観察できる
        'common_breathing': 'a',                        # 呼吸正常
        'common_cold_sweat': 'b',                       # 冷や汗なし → 次へ
        'common_face_color': 'b',                       # 顔色異常なし → 次へ
        'common_conversation': 'a',                     # 会話正常 → 症候別へ
    }
    cw = traverse_common(g, common_answers)
    print(f'  共通完了={cw.stop.startswith("reached:")} broke={cw.broke_off} stop={cw.stop}')
    print(f'  経路: {" -> ".join(cw.path)}')

    print('\n=== run_triage エンドツーエンド ===')
    # (A) 共通は症候別へ到達したが、症状質問の回答が無い → ケース2（VE・途切れ）
    rA = run_triage(g, common_answers, age=50)
    print(f'  (A) 共通完了・症状未回答           -> {rA}')
    # (B) 呼吸なし（共通フロー内でR1確定）
    rB = run_triage(g, {**common_answers, 'common_breathing': 'b'}, age=50)
    print(f'  (B) 共通内で呼吸なし(R1確定)      -> {rB}')
    # (C) CPAキーワード（global_priority_rules 即時override）
    rC = run_triage(g, common_answers, age=50, transcript_text='呼吸なし 冷たくなっている')
    print(f'  (C) CPAキーワードでglobal override -> {rC}')
