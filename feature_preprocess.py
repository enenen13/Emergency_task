"""
feature_preprocess.py — 前処理器：検証用CSV → 「ベクトル」＋「メタデータ(年齢/性別)」を分離
==============================================================================
■ 目的
  検証のために 評価用データ_label付け後.csv へ後付けした 年齢/性別 は、本来ベクトルに
  含まれない外部情報。これらを **ベクトル入力装置（vector_triage / NODE_ORDER / build_vector）
  を一切変えずに** 切り離し、age/sex の別チャネルとして下流へ渡す前処理層。

■ 方針
  ・ベクトル列 = triage_pipeline.NODE_MAP のキー(=yamlノードid)に一致する列のみ。
    → メタ列(年齢/性別)は「ベクトルには絶対に混ぜない」。既存のベクトル生成はそのまま。
  ・メタ列が無いCSV（本番相当の“素の入力”）でも動く：
      age/sex は None。外部 age_csv(id,年齢[,性別]) があればそこから補完する。
  ・入力CSVに混在していても、下流の bert_pred には node 列だけが渡る（メタは混入しない）。

■ 使い方
    from feature_preprocess import load_records
    recs = load_records('評価用データ_label付け後.csv')      # メタはCSV内から自動分離
    # recs[i] = {'id','bert_pred':{bertノード:code},'age','sex','_used_cols','_skipped'}
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

import pandas as pd
import triage_pipeline as tp

NODE_MAP = tp.NODE_MAP

# メタ列（ベクトルに含めない外部情報）の別名。ここに一致する列は age/sex 側へ回す。
META_ALIASES: Dict[str, tuple] = {
    'age': ('年齢', 'age', 'Age', 'AGE'),
    'sex': ('性別', 'sex', 'Sex', 'gender', 'Gender'),
}
# ベクトルでも下流メタでもない、無視してよい付帯列（会話原文など）
IGNORE_COLS = ('Conversation', 'conversation', 'Summary', 'summary')


def _pick(colnames, aliases) -> Optional[str]:
    for a in aliases:
        if a in colnames:
            return a
    return None


def split_columns(columns) -> Tuple[List[str], Dict[str, Optional[str]], List[str]]:
    """列名を (ベクトル列, メタ列マップ{age:列名,sex:列名}, 未対応列) に分類する。"""
    cols = list(columns)
    node_cols = [c for c in cols if c in NODE_MAP]                 # ★ベクトルはNODE_MAP一致のみ
    meta_map = {k: _pick(cols, al) for k, al in META_ALIASES.items()}
    meta_cols = {v for v in meta_map.values() if v}
    skipped = [c for c in cols
               if c not in node_cols and c not in meta_cols
               and c not in ('id',) and c not in IGNORE_COLS]
    return node_cols, meta_map, skipped


def _to_code(v):
    v = str(v).strip()
    if v == '':
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _clean_meta(v):
    """メタ値の空文字/NaN を None に。年齢は数値、性別は文字列のまま(男/女はそのまま使える)。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == '' or s.lower() == 'nan':
        return None
    return s


def load_records(vec_csv: str,
                 age_csv: Optional[str] = None,
                 df: Optional[pd.DataFrame] = None) -> List[dict]:
    """検証CSV → recs。ベクトル(bert_pred)とメタ(age/sex)を分離して返す。
       ・age/sex は CSV内メタ列を最優先。無ければ age_csv(id,年齢[,性別])から補完。
       ・どちらも無ければ age=sex=None（＝素の入力想定でもそのまま動く）。"""
    if df is None:
        df = pd.read_csv(vec_csv, dtype=str).fillna('')
    assert 'id' in df.columns, 'CSVに id 列が必要です'

    node_cols, meta_map, skipped = split_columns(df.columns)

    # 外部メタ（CSVにメタ列が無い/欠損のときのフォールバック）
    ext_age, ext_sex = {}, {}
    if age_csv:
        try:
            g = pd.read_csv(age_csv, dtype=str)
            if 'id' in g.columns:
                a_col = _pick(g.columns, META_ALIASES['age'])
                s_col = _pick(g.columns, META_ALIASES['sex'])
                if a_col:
                    ext_age = {str(i): _clean_meta(v) for i, v in zip(g['id'], g[a_col])}
                if s_col:
                    ext_sex = {str(i): _clean_meta(v) for i, v in zip(g['id'], g[s_col])}
        except FileNotFoundError:
            pass

    recs = []
    for _, r in df.iterrows():
        rid = str(r['id'])
        # --- ベクトル側（node列だけ。メタは混入しない） ---
        bp = {}
        for c in node_cols:
            cd = _to_code(r[c])
            if cd is None:
                continue
            bp[NODE_MAP[c]] = cd                                   # yamlノード列 → bertノード
        # --- メタ側（別チャネル） ---
        age = _clean_meta(r[meta_map['age']]) if meta_map['age'] else None
        sex = _clean_meta(r[meta_map['sex']]) if meta_map['sex'] else None
        if age is None:
            age = ext_age.get(rid)
        if sex is None:
            sex = ext_sex.get(rid)
        recs.append({'id': rid, 'bert_pred': bp, 'age': age, 'sex': sex,
                     '_used_cols': len(node_cols), '_skipped': skipped})
    return recs


def summarize(vec_csv: str, age_csv: Optional[str] = None) -> None:
    """分離結果の要約（何をベクトルに使い、何をメタに回し、何を無視したか）。"""
    df = pd.read_csv(vec_csv, dtype=str).fillna('')
    node_cols, meta_map, skipped = split_columns(df.columns)
    print(f'[前処理器] {vec_csv}: {len(df)}行 / 全{len(df.columns)}列')
    print(f'  ベクトル列(NODE_MAP一致): {len(node_cols)}')
    print(f'  メタ列(別チャネル)      : age={meta_map["age"]}  sex={meta_map["sex"]}')
    print(f'  無視した付帯列          : {skipped or "なし"}')


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else '評価用データ_label付け後.csv'
    summarize(path)
    recs = load_records(path)
    r0 = recs[0]
    print(f'例: id={r0["id"]} bert_pred数={len(r0["bert_pred"])} age={r0["age"]} sex={r0["sex"]}')
