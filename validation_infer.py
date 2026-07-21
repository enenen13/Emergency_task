"""
validation_infer.py — 機能: 検証入力(df_validation_input) 1行 → 全分岐をモデル予測 → ベクトル化
============================================================================
verify07 で学習したモデル(A or B)を使い、`train/df_validation_input*.csv` の
ワイド形式（1行=1通報、各 `<症状>_質問N文`=質問A / `<症状>_質問Nペア`=会話B）を
全ノード分推論して bert_pred(={node:code}) → ベクトルにする。

・列形式は自動判定：
    - renamed形式（推奨）：列名が yaml_node_id（例 'palpitation_heart_history'=質問文）＋
      '<id>_pairs'（会話ペア）。NODE_MAP で yaml_node_id → BERTキーへ。
    - 旧形式：'<症状>_質問N文' / '<症状>_質問Nペア'。transition_diagram/validation_column_map.json で解決。
・質問A: mode='A' は 文（=yaml質問）そのもの。mode='B' は node_questions_B の質問＋選択肢。
・年齢論理集合(機能1): 「N歳以上ですか？」ノードは年齢から code を上書き（数値: >=N→1(はい) / <N→2(いいえ) / 不明→モデル値のまま）。
  ※ Bタイプの router 年齢ゲートは、後段 vector_to_triage(vector, age) に age を渡すことで適用される。
・出力: age, bert_pred, vector, diagnostics（列別 予測code）。

predict_fn(A:str, B:str)->int を差し替え可能にしてあるので、実モデル未接続でもモック検証できる。
"""
from __future__ import annotations
import os, re, json, csv
from typing import Dict, List, Callable, Any, Optional

import triage_pipeline as tp   # NODE_MAP（yaml_node_id -> BERTキー）を使う

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_json(rel):
    with open(os.path.join(_HERE, rel), encoding='utf-8') as f:
        return json.load(f)


COLMAP = _load_json('transition_diagram/validation_column_map.json')['columns']   # 旧形式: base -> {node,pair_col,bun_col}
NODE_Q_A = _load_json('dictionary/node_questions_A.json')['questions']
NODE_Q_B = _load_json('dictionary/node_questions_B.json')['questions']

_AGE_Q_RE = re.compile(r'(\d+)\s*[歳才]以上ですか')


def _age_num(age: Any) -> Optional[int]:
    if age is None:
        return None
    s = str(age).strip()
    m = re.search(r'\d+', s)
    return int(m.group()) if m else None


def prompt_for(node: str, bun: str, mode: str) -> str:
    """モデルに渡す質問A。mode='A'→文そのまま（=yaml質問）。mode='B'→node_questions_B の質問＋選択肢。"""
    if mode == 'B':
        e = NODE_Q_B.get(node, {})
        if e.get('question'):
            return e['question'] + (e.get('prompt_suffix') or '')
    # mode A（または B辞書に無い場合）は文（yaml質問）を使う
    return bun or NODE_Q_A.get(node, {}).get('question', '')


def _uses_nodeid_columns(fieldnames) -> bool:
    """renamed形式（列名=yaml_node_id と '<id>_pairs'）かどうかを判定。"""
    fs = set(fieldnames or [])
    return any((y in fs) and (f'{y}_pairs' in fs) for y in tp.NODE_MAP)


def _iter_cols(row):
    """(yaml_node, bert_node, bun_col, pair_col) を列挙。
       renamed形式（列名=yaml_node_id）優先、無ければ旧COLMAP形式。"""
    if _uses_nodeid_columns(row.keys()):
        for yaml_node, bert_node in tp.NODE_MAP.items():
            pair_col = f'{yaml_node}_pairs'
            if pair_col in row:                      # CSVにその列がある分だけ
                yield yaml_node, bert_node, yaml_node, pair_col
    else:
        for base, info in COLMAP.items():
            yield base, info['node'], info['bun_col'], info['pair_col']


def infer_row(row: Dict[str, str],
              predict_fn: Callable[[str, str], int],
              mode: str = 'A') -> Dict[str, Any]:
    """検証CSVの1行 → {age, bert_pred, diagnostics}。predict_fn(A,B)->code。
       列形式は自動判定: renamed（列名=yaml_node_id / '<id>_pairs'）優先、無ければ旧 症状_質問N。"""
    age = row.get('年齢')
    age_n = _age_num(age)
    bert_pred: Dict[str, int] = {}
    diag: List[Dict[str, Any]] = []
    for col_id, node, bun_col, pair_col in _iter_cols(row):
        bun = str(row.get(bun_col, '') or '').strip()
        pair = str(row.get(pair_col, '') or '').strip()
        if not pair:
            continue                       # その質問の列が空（＝聞いていない）→スキップ
        A = prompt_for(node, bun, mode)
        code = int(predict_fn(A, pair))
        src = 'model'
        # --- 年齢論理集合: 「N歳以上ですか？」は年齢から上書き ---
        m = _AGE_Q_RE.search(bun)
        if m and age_n is not None:
            code = 1 if age_n >= int(m.group(1)) else 2      # 1=はい/以上, 2=いいえ/未満
            src = 'age_logic'
        bert_pred[node] = code
        diag.append({'col': col_id, 'node': node, 'code': code, 'src': src})
    return {'id': row.get('id'), 'age': age, 'bert_pred': bert_pred, 'diagnostics': diag}


def row_to_vector(row: Dict[str, str],
                  predict_fn: Callable[[str, str], int],
                  mode: str = 'A'):
    """1行 → (vector, age, info)。vector_triage.build_vector を使用。"""
    import vector_triage as vt
    r = infer_row(row, predict_fn, mode)
    vec = vt.build_vector(r['bert_pred'])
    return vec, r['age'], r


def load_validation(csv_path: str) -> List[Dict[str, str]]:
    with open(csv_path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))
