"""Build 頭痛BERT_遷移型共有_vs_HeadacheBERT.ipynb

HeadacheBERT_painful_Finetuning.ipynb と「戦わせる」ための遷移型BERT。

- ファインチューニングする BERT は **1つ（共有）**。
  入力 = (分岐質問, 採用ペア) のペア → はい/いいえ/不明 を分類。
  質問を見て「どの選択肢が適切か」を選ぶだけのモデル。1本で3ノード全部を担う。
- データ・採用ペア選定は HeadacheBERT と同じ input_pairs.csv を使う。
  採用ペア列 _1/_2/_3 = 痛み / しびれ / 振る舞い ノード。
- run_052（Grid勝者）構成に固定:
  cl-tohoku/bert-base-japanese-v3 / stopwordsなし / lr5e-5 / ep10 / bs8 / max_len512 / seed42
- 評価軸を HeadacheBERT に合わせる:
  accuracy / precision_macro / recall_macro / f1_macro を 5-fold CV の mean±std、混同行列。
  CV は HeadacheBERT と同じ StratifiedKFold(5, shuffle, seed=42) を **ノードごと**に適用
  （痛みノードの fold は HeadacheBERT と一致 → 直接対決できる）。
"""
import json
import uuid


def cell(src, kind="code"):
    cid = str(uuid.uuid4())[:8]
    if isinstance(src, str):
        lines = src.split("\n")
        source = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    else:
        source = src
    base = {"cell_type": kind, "id": cid, "metadata": {}, "source": source}
    if kind == "code":
        base["execution_count"] = None
        base["outputs"] = []
    return base


cells = []

# ============================================================
# 0. 概要
# ============================================================
cells.append(cell(r"""# 頭痛BERT: 遷移型（共有1モデル） vs HeadacheBERT

`HeadacheBERT_painful_Finetuning.ipynb` と **同じ土俵で戦わせる** ための遷移型BERT。

## コンセプト
- **FTする BERT は 1 つ（共有）**。入力 = (分岐質問, 採用ペア) → **はい/いいえ/不明** を分類。
  「質問を見てどの選択肢が適切かを選ぶ」だけのモデルで、1本で 痛み/しびれ/振る舞い の3ノードを担う。
- HeadacheBERT は **痛みノード専用** の分類器（1ノードだけ）。
  → 「1本の共有モデルが、痛みノード専用モデルに勝てるか？」が対戦の主旨。おまけで しびれ/振る舞い も解ける。

## 評価軸（HeadacheBERT に合わせる）
- データ・採用ペア = HeadacheBERT と同じ `input_pairs.csv`（採用ペア列 `_1/_2/_3` = 痛み/しびれ/振る舞い）
- 指標 = **accuracy / precision_macro / recall_macro / f1_macro** の 5-fold CV **mean±std**、混同行列
- CV = HeadacheBERT と同じ `StratifiedKFold(5, shuffle, random_state=42)` を **ノードごと** に適用
  → 痛みノードの fold が HeadacheBERT と一致するので **直接比較可能**

## モデル構成（Grid勝者 run_052 に固定）
| 項目 | 値 |
|---|---|
| base model | `cl-tohoku/bert-base-japanese-v3` |
| stopwords | なし（生ペア） |
| learning_rate | 5e-5 |
| epochs | 10 |
| batch_size | 8 |
| max_length | 512 |
| seed | 42 |

**対戦相手 HeadacheBERT run_052（痛みノード）: accuracy 0.716 / f1_macro 0.712**
""", "markdown"))

# ============================================================
# 1. 環境 & データ
# ============================================================
cells.append(cell("# 1. 環境セットアップ & データ", "markdown"))

cells.append(cell("""# ============================================================
# 環境判定 & セットアップ (Colab / ローカル どちらでも動く)
# ============================================================
import os, sys, subprocess

IN_COLAB = 'google.colab' in sys.modules
print(f'IN_COLAB = {IN_COLAB}')

if IN_COLAB:
    from google.colab import drive
    drive.mount('/content/drive')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                    'transformers==4.46.3', 'sentencepiece', 'fugashi',
                    'ipadic', 'unidic-lite', 'protobuf', 'pyyaml',
                    'japanize-matplotlib'], check=True)
    DATA_DIR = '/content/drive/MyDrive/NTCIR-19'
    CSV_PATH = f'{DATA_DIR}/input_pairs.csv'
else:
    BASE_DIR = 'C:/Users/hiyok/Desktop/Emergency_task'
    CSV_PATH = f'{BASE_DIR}/dataset/input_pairs.csv'  # ローカルに置く場合

print(f'CSV : {CSV_PATH}  (exists: {os.path.exists(CSV_PATH)})')

import torch
print(f'CUDA available: {torch.cuda.is_available()}')"""))

cells.append(cell(r"""import os, time, random
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                             f1_score, confusion_matrix)

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
SEED = 42

def set_seed(seed=SEED):
    random.seed(seed); np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

set_seed()
print('device:', DEVICE)

df = pd.read_csv(CSV_PATH)
print('rows:', len(df))
print('columns:', list(df.columns))"""))

cells.append(cell(r"""# ノード定義：採用ペア列 _1/_2/_3 = 痛み/しびれ/振る舞い
# 質問文は分岐点（ひし形）の質問（input_pairs を作った BERTの入力文選定.ipynb と同じ）
NODE_DEFS = [
    {'n': 1, 'label_col': '痛み',   'question': '激しい痛みが、起こりましたか？'},
    {'n': 2, 'label_col': 'しびれ', 'question': 'しびれや麻痺がありますか？'},
    {'n': 3, 'label_col': '振る舞い', 'question': '何か、いつもと違う振る舞いがありますか？（発症から3時間以内 ）'},
]

import re
def resolve_adopt_col(n):
    # '採用ペア...' で終わりが _n の列を拾う（基底名のゆらぎに頑健）
    cands = [c for c in df.columns if c.startswith('採用ペア') and c.rstrip().endswith(f'_{n}')]
    if not cands:
        raise KeyError(f'採用ペア_..._{n} 列が見つかりません。df.columns を確認してください。')
    return cands[0]

TEXT_COL = 'ペア'
for nd in NODE_DEFS:
    nd['adopt_col'] = resolve_adopt_col(nd['n'])
    assert nd['label_col'] in df.columns, f"ラベル列 {nd['label_col']} がありません"

print('=== ノード対応 ===')
for nd in NODE_DEFS:
    n_adopt = int(df[nd['adopt_col']].sum())
    print(f"  node{nd['n']} [{nd['label_col']}] 採用列={nd['adopt_col']}  採用ペア数={n_adopt}")
    print(f"     質問: {nd['question']}")"""))

cells.append(cell(r"""# 各ノードの学習例（採用ペアが True の行）を配列化
#   入力 = (分岐質問, 採用ペア)、ラベル = そのノードの 0/1/2 (はい/いいえ/不明)
node_data = {}
for nd in NODE_DEFS:
    sub = df[df[nd['adopt_col']] == True]
    node_data[nd['n']] = {
        'label_col': nd['label_col'],
        'question': nd['question'],
        'q': [nd['question']] * len(sub),
        'p': sub[TEXT_COL].astype(str).tolist(),
        'y': sub[nd['label_col']].astype(int).tolist(),
    }
    from collections import Counter
    print(f"node{nd['n']} [{nd['label_col']}]: {len(sub)} 例  label分布={dict(sorted(Counter(node_data[nd['n']]['y']).items()))}")

NUM_LABELS = 3"""))

# ============================================================
# 2. モデル設定 & 学習部品
# ============================================================
cells.append(cell("# 2. モデル設定（run_052）& 学習・評価部品（HeadacheBERT と同一機構）", "markdown"))

cells.append(cell(r"""# Grid勝者 run_052 に固定
CONFIG = {
    'model_name': 'cl-tohoku/bert-base-japanese-v3',
    'learning_rate': 5e-5,
    'num_epochs': 10,
    'batch_size': 8,
    'max_length': 512,
    'n_folds': 5,
}
# 対戦相手（HeadacheBERT run_052・痛みノード）の参照値
HEADACHE_REF = {'痛み': {'accuracy': 0.7159, 'f1_macro': 0.7116}}


class PairDataset(Dataset):
    # 入力を (質問, ペア) の2文として与える → BERT は [CLS] 質問 [SEP] ペア [SEP]
    def __init__(self, questions, pairs, labels, tokenizer, max_length):
        self.q, self.p, self.y = questions, pairs, labels
        self.tok, self.max_length = tokenizer, max_length

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        enc = self.tok(self.q[i], self.p[i], truncation=True, max_length=self.max_length,
                       padding='max_length', return_tensors='pt')
        item = {k: v.squeeze(0) for k, v in enc.items()}
        item['labels'] = torch.tensor(int(self.y[i]))
        return item


_tok_cache = {}
def get_tokenizer(name):
    if name not in _tok_cache:
        _tok_cache[name] = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
    return _tok_cache[name]


def build_model(name, tokenizer, num_labels=NUM_LABELS):
    model = AutoModelForSequenceClassification.from_pretrained(name, num_labels=num_labels)
    # tokenizer と vocab がずれるモデル（JMedRoBERTa 等）への保険
    if len(tokenizer) != model.config.vocab_size:
        model.resize_token_embeddings(len(tokenizer))
    return model.to(DEVICE)


def train_fold(train_q, train_p, train_y, cfg, seed=SEED):
    set_seed(seed)
    tok = get_tokenizer(cfg['model_name'])
    model = build_model(cfg['model_name'], tok)
    ds = PairDataset(train_q, train_p, train_y, tok, cfg['max_length'])
    dl = DataLoader(ds, batch_size=cfg['batch_size'], shuffle=True)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg['learning_rate'])
    model.train()
    for ep in range(cfg['num_epochs']):
        losses = []
        for batch in dl:
            batch = {k: v.to(DEVICE) for k, v in batch.items()}
            opt.zero_grad()
            out = model(**batch)
            out.loss.backward()
            opt.step()
            losses.append(out.loss.item())
    return model, tok


@torch.no_grad()
def predict(model, tok, questions, pairs, cfg):
    model.eval()
    ds = PairDataset(questions, pairs, [0] * len(questions), tok, cfg['max_length'])
    dl = DataLoader(ds, batch_size=cfg['batch_size'], shuffle=False)
    preds = []
    for batch in dl:
        batch.pop('labels', None)
        batch = {k: v.to(DEVICE) for k, v in batch.items()}
        logits = model(**batch).logits
        preds.extend(logits.argmax(-1).cpu().numpy().tolist())
    return preds


def metrics(y_true, y_pred):
    return {
        'accuracy': accuracy_score(y_true, y_pred),
        'precision_macro': precision_score(y_true, y_pred, average='macro', zero_division=0),
        'recall_macro': recall_score(y_true, y_pred, average='macro', zero_division=0),
        'f1_macro': f1_score(y_true, y_pred, average='macro', zero_division=0),
    }

print('setup OK / CONFIG =', CONFIG)"""))

# ============================================================
# 3. 5-fold CV（HeadacheBERT と同じ fold）
# ============================================================
cells.append(cell(r"""# 3. 5-fold CV — 共有1モデルを全ノードの train で学習、ノードごとに評価

各ノードに `StratifiedKFold(5, shuffle, seed=42)` を適用（HeadacheBERT と同設定）。
- fold f の学習データ = 3ノードの train 分割を**プール**（1本の共有モデル）
- fold f の評価 = 各ノードの test 分割で個別に測定（**痛みノードの fold は HeadacheBERT と一致**）""", "markdown"))

cells.append(cell(r"""# ノードごとに同設定の StratifiedKFold を作る（痛みノードは HeadacheBERT と同じ分割になる）
splits = {}
for nd in NODE_DEFS:
    d = node_data[nd['n']]
    skf = StratifiedKFold(n_splits=CONFIG['n_folds'], shuffle=True, random_state=SEED)
    splits[nd['n']] = list(skf.split(d['p'], d['y']))
print('fold split 準備完了')"""))

cells.append(cell(r"""fold_records = []          # 各 fold × 各ノード（+pooled）の指標
pooled_true_all, pooled_pred_all = [], []   # 混同行列用（全fold累積・pooled）
node_true_all = {nd['n']: [] for nd in NODE_DEFS}
node_pred_all = {nd['n']: [] for nd in NODE_DEFS}

for f in range(CONFIG['n_folds']):
    # --- 学習データ（3ノードの train をプール）---
    tr_q, tr_p, tr_y = [], [], []
    for nd in NODE_DEFS:
        d = node_data[nd['n']]
        train_idx, _ = splits[nd['n']][f]
        tr_q += [d['q'][i] for i in train_idx]
        tr_p += [d['p'][i] for i in train_idx]
        tr_y += [d['y'][i] for i in train_idx]
    print(f'\n===== fold {f+1}/{CONFIG["n_folds"]}  train例数={len(tr_y)} =====')
    t0 = time.time()
    model, tok = train_fold(tr_q, tr_p, tr_y, CONFIG)
    print(f'  trained in {time.time()-t0:.1f}s')

    # --- 評価（ノードごと）---
    pooled_true, pooled_pred = [], []
    for nd in NODE_DEFS:
        d = node_data[nd['n']]
        _, test_idx = splits[nd['n']][f]
        q = [d['q'][i] for i in test_idx]
        p = [d['p'][i] for i in test_idx]
        y = [d['y'][i] for i in test_idx]
        pr = predict(model, tok, q, p, CONFIG)
        m = metrics(y, pr)
        fold_records.append({'fold': f + 1, 'node': nd['label_col'], **m})
        print(f'    [{nd["label_col"]}] acc={m["accuracy"]:.3f} f1={m["f1_macro"]:.3f} (n={len(y)})')
        pooled_true += y; pooled_pred += pr
        node_true_all[nd['n']] += y; node_pred_all[nd['n']] += pr
    pm = metrics(pooled_true, pooled_pred)
    fold_records.append({'fold': f + 1, 'node': 'pooled', **pm})
    print(f'    [pooled] acc={pm["accuracy"]:.3f} f1={pm["f1_macro"]:.3f}')
    pooled_true_all += pooled_true; pooled_pred_all += pooled_pred

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

fold_df = pd.DataFrame(fold_records)
print('\n=== fold別 記録 ===')
display(fold_df)"""))

# ============================================================
# 4. 集計 & 対戦
# ============================================================
cells.append(cell("# 4. 集計（mean±std）と HeadacheBERT との対戦", "markdown"))

cells.append(cell(r"""# ノード（+pooled）ごとに mean±std
agg = (fold_df.groupby('node')[['accuracy', 'precision_macro', 'recall_macro', 'f1_macro']]
       .agg(['mean', 'std']))
display(agg)

# 痛みノードで HeadacheBERT run_052 と直接対決
trans_pain = fold_df[fold_df['node'] == '痛み']
my_acc_m, my_acc_s = trans_pain['accuracy'].mean(), trans_pain['accuracy'].std()
my_f1_m, my_f1_s = trans_pain['f1_macro'].mean(), trans_pain['f1_macro'].std()

print('\n' + '=' * 56)
print('  痛みノード 対戦（5-fold CV, mean±std）')
print('=' * 56)
print(f'  HeadacheBERT (run_052・痛み専用)  : acc {HEADACHE_REF["痛み"]["accuracy"]:.3f} / '
      f'f1 {HEADACHE_REF["痛み"]["f1_macro"]:.3f}')
print(f'  遷移型BERT (共有1本・本ノートブック): acc {my_acc_m:.3f}±{my_acc_s:.3f} / '
      f'f1 {my_f1_m:.3f}±{my_f1_s:.3f}')
diff = my_f1_m - HEADACHE_REF["痛み"]["f1_macro"]
print(f'  → f1_macro 差分: {diff:+.3f}  ({"遷移型の勝ち" if diff > 0 else "HeadacheBERTの勝ち"})')

os.makedirs('output', exist_ok=True)
agg.to_csv('output/transition_vs_headache_summary.csv', encoding='utf-8-sig')
fold_df.to_csv('output/transition_vs_headache_folds.csv', index=False, encoding='utf-8-sig')
print('\nsaved: output/transition_vs_headache_summary.csv, _folds.csv')"""))

cells.append(cell(r"""# 混同行列（全fold累積・ノード別 + pooled）
import matplotlib.pyplot as plt
try:
    import japanize_matplotlib  # noqa
except ImportError:
    pass

LABEL_NAMES = ['はい', 'いいえ', '不明']

def plot_cm(ax, y_true, y_pred, title):
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])
    im = ax.imshow(cm, cmap='Blues')
    ax.set_xticks(range(3)); ax.set_yticks(range(3))
    ax.set_xticklabels(LABEL_NAMES); ax.set_yticklabels(LABEL_NAMES)
    ax.set_xlabel('pred'); ax.set_ylabel('true'); ax.set_title(title)
    for i in range(3):
        for j in range(3):
            ax.text(j, i, cm[i, j], ha='center', va='center',
                    color='white' if cm[i, j] > cm.max() / 2 else 'black')

fig, axes = plt.subplots(1, 4, figsize=(20, 4.5))
for ax, nd in zip(axes[:3], NODE_DEFS):
    plot_cm(ax, node_true_all[nd['n']], node_pred_all[nd['n']], f'{nd["label_col"]}ノード')
plot_cm(axes[3], pooled_true_all, pooled_pred_all, 'pooled (3ノード)')
plt.tight_layout()
plt.show()"""))

cells.append(cell(r"""## 5. 解釈メモ

- **対戦の主眼**：痛みノードで `HeadacheBERT(run_052・痛み専用)` vs `遷移型(共有1本)`。
  共有モデルは3ノードを1本で担うので、痛み専用に**勝つ/同等なら**「1本で十分」という強い主張になる。
- **fold が一致**：痛みノードは HeadacheBERT と同じ StratifiedKFold(seed=42) なので、acc/f1 を直接並べられる。
- **しびれ/振る舞い**：HeadacheBERT は未対応。共有モデルはここも同時に出せる＝適用範囲で優位。

> 注意：CV は採用ペア**例単位**の分割（HeadacheBERT に合わせた）。同一患者の複数ペアが train/test に跨りうる点も HeadacheBERT と同条件。患者リークを厳密に排除したい場合は患者単位 split に変更する。""", "markdown"))

# ============================================================
# 書き出し
# ============================================================
nb = {
    "cells": cells,
    "metadata": {
        "colab": {"provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out_path = r"c:/Users/hiyok/Desktop/Emergency_task/頭痛BERT_遷移型共有_vs_HeadacheBERT.ipynb"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print('OK:', out_path)
print('cells:', len(cells))
