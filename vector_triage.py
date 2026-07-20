"""
vector_triage.py — 機能2: 「~90-100 長のベクトル」→ トリアージ変換
==================================================================

各質問ノードのBERT予測（code 0=非該当 / 1..N=選択肢）を、**固定順のベクトル**にまとめ、
それをトリアージに変換する。ベクトルの各次元 = 1つのBERT質問ノードの回答code。

  NODE_ORDER  … ベクトルの並び（BERT判定ノードを固定順に）。長さ ≒ 90-100。
  build_vector(bert_pred)        {bert_node: code} → [code, code, ...]
  vector_to_bertpred(vector)     ベクトル → {bert_node: code}
  vector_to_triage(vector, age)  ベクトル＋年齢 → TriageResult（機能1の年齢論理集合も内部で適用）

トリアージ変換の実体は triage_pipeline のルール探索（protocol.yaml）。
ベクトルは「全ノードの回答をまとめた入力表現」で、これを決定的にトリアージへ落とす。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any

import triage_pipeline as tp


def get_node_order(node_map: Optional[Dict[str, str]] = None) -> List[str]:
    """ベクトルの次元順（＝トリアージ判定に使うBERTノードを固定順に）。
       NODE_MAP のBERT側 unique を昇順で。長さがベクトル長になる。"""
    nm = node_map if node_map is not None else tp.NODE_MAP
    return sorted(set(nm.values()))


# ベクトルの標準スキーマ（モジュール読み込み時に確定）
NODE_ORDER: List[str] = get_node_order()
VECTOR_LEN: int = len(NODE_ORDER)


def build_vector(bert_pred: Dict[str, int], node_order: Optional[List[str]] = None) -> List[int]:
    """{bert_node: code} → 固定順ベクトル。未取得ノードは 0（非該当/未取得）。"""
    order = node_order or NODE_ORDER
    return [int(bert_pred.get(n, 0)) for n in order]


def vector_to_bertpred(vector: List[int], node_order: Optional[List[str]] = None) -> Dict[str, int]:
    """ベクトル → {bert_node: code}。長さは NODE_ORDER と一致している前提。"""
    order = node_order or NODE_ORDER
    if len(vector) != len(order):
        raise ValueError(f'ベクトル長 {len(vector)} が NODE_ORDER {len(order)} と不一致')
    return {n: int(c) for n, c in zip(order, vector)}


# 導入フローの構造化入力（BERTノードでない質問）の既定値。
#   救急通報である・観察可能、を既定に。必要に応じて base_answers で上書き/追加する。
DEFAULT_BASE_ANSWERS = {
    'intro_fire_or_emergency': 'a',                 # 救急
    'overview': 'chief_complaint_classification',   # 主訴分類へ（overviewはBERTにもあるが導入既定として）
}


def vector_to_triage(vector: List[int],
                     age: Any = None,
                     sex: Optional[str] = None,
                     transcript_text: str = '',
                     base_answers: Optional[Dict[str, str]] = None,
                     graph: Optional['tp.ProtocolGraph'] = None,
                     node_order: Optional[List[str]] = None) -> 'tp.TriageResult':
    """★機能2の本体：ベクトル(＋年齢) → トリアージ。
       ・vector をBERT予測に戻し、NODE_MAPでyaml回答へ、年齢論理集合(機能1)を適用して探索する。
       ・base_answers: 導入フローの構造化入力（救急?/観察可否など、BERTでない質問）。
         省略時は DEFAULT_BASE_ANSWERS。ベクトル由来の回答が優先される。"""
    g = graph or tp.load_graph()
    pred = vector_to_bertpred(vector, node_order)
    base = dict(DEFAULT_BASE_ANSWERS if base_answers is None else base_answers)
    return tp.predict_triage(g, bert_pred=pred, answers=base, age=age, transcript_text=transcript_text)


def describe_vector(node_order: Optional[List[str]] = None) -> List[dict]:
    """ベクトル各次元の説明（index, bert_node, 対応するyamlノード）。ノートでの確認用。"""
    order = node_order or NODE_ORDER
    inv = {}
    for yaml_node, bert_node in tp.NODE_MAP.items():
        inv.setdefault(bert_node, []).append(yaml_node)
    return [{'index': i, 'bert_node': n, 'yaml_nodes': inv.get(n, [])}
            for i, n in enumerate(order)]


if __name__ == '__main__':
    print(f'VECTOR_LEN = {VECTOR_LEN}')
    print('先頭10次元:')
    for d in describe_vector()[:10]:
        print(f"  [{d['index']:3d}] {d['bert_node']:34s} <- {d['yaml_nodes']}")
    # デモ：しびれ サインb→既往c(不明)=R3 のベクトルを作って判定
    g = tp.load_graph()
    bp = {'14_numbness_stroke_symptoms': 2, '14_numbness_stroke_history': 3}
    vec = build_vector(bp)
    print(f'\nベクトル長={len(vec)}  非ゼロ={[(NODE_ORDER[i], v) for i, v in enumerate(vec) if v]}')
    # 共通は別途 answers で通す必要があるため、ここでは症候部分のみのデモ
    r = vector_to_triage(vec, age=70)
    print(r.report())
