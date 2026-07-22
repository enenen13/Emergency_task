"""
vector_triage_eval.py — ベクトル入力 → トリアージ → 評価 だけの最小機能
=====================================================================
BERT推論や検索(cos)を一切行わず、「各ノードのcode（＝ベクトル）」を入力に、
protocol.yaml のルール探索で決定的にトリアージし、正解ラベルと突き合わせて F1/混同行列を出す。

3ステップ:
  (1) ベクトルCSV(1行=1通報, 列=ノードcode) → {bertノード:code}      … load_vectors()
  (2) vector_triage.vector_to_triage で main(VE/SE/LE) を確定        … run_triage()
  (3) 正解 xlsx(Case_id, Triage Label) と id 突き合わせ → F1/混同行列 … evaluate()

使い方(ローカル):
  python vector_triage_eval.py \
      --vec 評価用データ_label付け後.csv \
      --truth train/ja_dataset_v3.xlsx \
      --age train/df_validation_input_renamed.csv
"""
from __future__ import annotations
import argparse, csv, os, re
from collections import Counter

import pandas as pd
import triage_pipeline as tp
import vector_triage as vt
from feature_preprocess import load_records, summarize   # 前処理器（age/sex分離）


# ---------- (1) ベクトルCSV → recs[{id, age, sex, bert_pred}] ----------
# ★ベクトル入力装置(vector_triage)は変更しない。age/sex の分離は前処理器 feature_preprocess に委譲。
def load_vectors(vec_csv: str, age_csv: str | None = None):
    summarize(vec_csv, age_csv)                                   # 何をベクトル/メタ/無視にしたか
    return load_records(vec_csv, age_csv)


# ---------- (2) ベクトル → トリアージ ----------
def run_triage(recs):
    graph = tp.load_graph()                                        # yamlは1回だけ読む
    results = []
    for rec in recs:
        vec = vt.build_vector(rec['bert_pred'])                   # 入力装置はそのまま
        res = vt.vector_to_triage(vec, age=rec['age'], sex=rec.get('sex'), graph=graph)  # メタは別チャネル
        results.append({'id': rec['id'], 'age': rec['age'], 'main': res.main, 'sub1': res.sub1,
                        'common_completed': res.common_completed, 'common_stop': res.common_stop,
                        'completed': ','.join(res.completed_symptoms or []),
                        'furthest_broke': res.furthest_broke_symptom})
    print('main分布:', dict(Counter(r['main'] for r in results)),
          '/ 共通完了:', sum(r['common_completed'] for r in results), '/', len(results))
    return results


# ---------- (3) 評価（正解=xlsx の Triage Label）----------
def _norm_label(s):
    t = re.sub(r'[\s_\-]+', '', str(s)).lower()
    if t.startswith('very'):
        return 'VE'
    if t.startswith('semi'):
        return 'SE'
    if t.startswith('low'):
        return 'LE'
    return None


def load_truth(truth_xlsx: str):
    xls = pd.ExcelFile(truth_xlsx)
    sheet = 'ja' if 'ja' in xls.sheet_names else xls.sheet_names[0]
    dft = xls.parse(sheet)
    assert {'Case_id', 'Triage Label'} <= set(dft.columns), f'必要列なし: {list(dft.columns)}'
    truth = {}
    for _, r in dft.iterrows():
        lab = _norm_label(r['Triage Label'])
        if lab:
            truth[str(r['Case_id'])] = lab
    print('正解', len(truth), '件 分布:', {v: sum(x == v for x in truth.values()) for v in ['VE', 'SE', 'LE']})
    return truth


def evaluate(results, truth, out_csv='output/vector_triage_eval.csv'):
    pairs = [(str(r['id']), r['main']) for r in results if str(r['id']) in truth]
    y_true = [truth[i] for i, _ in pairs]
    y_pred = [p for _, p in pairs]
    print(f'突き合わせ {len(pairs)}/{len(results)} 件')
    other = sum(1 for p in y_pred if p not in ('VE', 'SE', 'LE'))
    if other:
        print(f'  ※ main が VE/SE/LE 以外(複数完了 等): {other}件 → 誤り計上')

    labels = ['VE', 'SE', 'LE']
    from sklearn.metrics import classification_report, confusion_matrix, f1_score
    print('\n===== F1 =====')
    for avg in ('macro', 'micro', 'weighted'):
        print(f'{avg:>8}-F1 :', round(f1_score(y_true, y_pred, labels=labels, average=avg, zero_division=0), 4))
    print('\n', classification_report(y_true, y_pred, labels=labels, zero_division=0))
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    print('混同行列 (行=正解, 列=予測) 順:', labels)
    print('        ' + '  '.join(f'{l:>4}' for l in labels))
    for lab, row in zip(labels, cm):
        print(f'  {lab:>4} | ' + '  '.join(f'{v:>4}' for v in row.tolist()))

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    res_by_id = {str(r['id']): r for r in results}
    with open(out_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'true_main', 'pred_main', 'correct', 'sub1', 'common_completed', 'common_stop', 'furthest_broke'])
        for i, p in pairs:
            r = res_by_id[i]
            w.writerow([i, truth[i], p, int(truth[i] == p), r['sub1'],
                        r['common_completed'], r['common_stop'], r['furthest_broke']])
    print('\n評価CSV →', out_csv, f'({len(pairs)}行)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vec', default='評価用データ_label付け後.csv')
    ap.add_argument('--truth', default='train/ja_dataset_v3.xlsx')
    ap.add_argument('--age', default='train/df_validation_input_renamed.csv')
    ap.add_argument('--out', default='output/vector_triage_eval.csv')
    a = ap.parse_args()

    recs = load_vectors(a.vec, a.age)          # (1)
    results = run_triage(recs)                  # (2)
    truth = load_truth(a.truth)
    evaluate(results, truth, a.out)             # (3)


if __name__ == '__main__':
    main()
