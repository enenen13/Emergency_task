"""Build 頭痛BERT_アブレーション比較.ipynb

遷移図を辿る BERT の設計要素を 1 つずつ切り分けるアブレーション実験。
1 つの run_experiment(config) に統一し、config フラグで以下を出し分ける:

  M3 直接BERT      : head=direct                          (遷移図なし baseline)
  M1 順次BERT      : head=seqcls, share_encoder=False     (分岐ごと独立BERT)
  A  共有BERT      : head=seqcls, share_encoder=True, use_history=False
  A+履歴           : head=seqcls, share_encoder=True, use_history=True
  M2 選択強化BERT  : head=mc,     use_history=True, use_route=True

比較軸 (ablation):
  E1 共有       : M1 vs A          (パラメータ共有のデータ効率)
  E2 履歴       : A  vs A+履歴     (経路文脈の価値)
  E3 ヘッド/行先: A+履歴 vs M2     (MC + 行き先埋め込みの効果)
  E6 遷移図     : M3 vs それ以外   (構造を使う総合利得)

共通指標: 最終triage acc / macro-F1 / ノード単位acc(teacher-forced) /
          under-triage率 / 経路一致率
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
cells.append(cell(r"""# 頭痛BERT: 遷移図を辿るモデルの比較（ノード単位FT）

**タスク設定**：共通タスク（共通バイタル）は通過済みとし、頭痛プロトコル（`protocol.yaml` の `headache`）の
**3分岐ノードだけ** を対象に検証する。

**学習の枠組み（node-level supervision）**：
- 入力 = コサイン類似度＋閾値で選んだ「質問＋回答」ペア（採用ペア）
- ラベル = 各ノードの回答 **はい/いいえ/不明**（triage を直接学習しない）
- 最終 triage は、答えで **遷移図を歩いた結果として計算**で出す

> データは各ノードラベルが **54/54/54 で均衡**、かつ全ノードにラベルが付くため、
> ground-truth パス上だけでなく **全 162×3 = 486 例（均衡）** で学習する（`full_coverage=True`）。
> triage（R2=78/R3=78/Y2=6）は不均衡なので、直接当てに行く M3 より node-level の方が学習しやすい。

## 比較するモデル（ヘッド × 履歴 の 2×2 ＋ baseline）

| モデル | head | use_history | use_route | 位置づけ |
|---|---|---|---|---|
| **M3 直接BERT** | direct | – | – | 遷移図なし baseline |
| **A 共有BERT** | seqcls | False | – | ★本命：各質問を答える共有BERT |
| **A+履歴** | seqcls | True | – | 履歴が要るか検証 |
| **MC (履歴なし)** | mc | False | True | MC+行き先（履歴なし） |
| **M2 選択強化** | mc | True | True | MC+行き先＋履歴 |

## 比較軸
- **履歴の要否**：A vs A+履歴（seqcls）、MC vs M2（mc）→ 「履歴はいらないのか」を実証
- **ヘッドの違い**：A vs MC（履歴なし同士）→ 分類 vs MultipleChoice
- **遷移図の利得**：M3 vs それ以外

## 評価指標（accuracy だけにしない）
- **最終triage acc / macro-F1**：R3/R2/Y2 の不均衡に対応
- **ノード単位acc (teacher-forced)**：各分岐の はい/いいえ/不明 正解率（node-level FT の直接の評価）
- **under-triage率**：重症を軽症と読む割合（救急の安全指標）
- **経路一致率**：gold path と同じ経路を辿れたか（exposure bias の指標）

> split は **患者単位ランダム 7:3**（`test_size=0.3`）。閾値は既定 0.0（素の argmax）、重み付き loss は未使用。
> 162件と小さいので指標は test 集合で報告。結論前に複数 seed / CV 推奨。
""", "markdown"))

# ============================================================
# 1. 環境 & データ
# ============================================================
cells.append(cell("# 1. 環境セットアップ & データ準備", "markdown"))

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
                    'ipadic', 'unidic-lite', 'protobuf', 'tiktoken',
                    'pyyaml', 'japanize-matplotlib'], check=True)
    DATA_DIR = '/content/drive/MyDrive/NTCIR-19'
    CSV_PATH = f'{DATA_DIR}/headache_emergency_calls202605311132.csv'
    YAML_PATH = f'{DATA_DIR}/protocol.yaml'
else:
    BASE_DIR = 'C:/Users/hiyok/Desktop/Emergency_task'
    CSV_PATH = f'{BASE_DIR}/dataset/headache_emergency_calls202605311132.csv'
    YAML_PATH = f'{BASE_DIR}/transition_diagram/protocol.yaml'

print(f'CSV : {CSV_PATH}  (exists: {os.path.exists(CSV_PATH)})')
print(f'YAML: {YAML_PATH}  (exists: {os.path.exists(YAML_PATH)})')

import torch
print(f'CUDA available: {torch.cuda.is_available()}')"""))

cells.append(cell(r"""import pandas as pd
import re

df = pd.read_csv(CSV_PATH)
print(f'rows: {len(df)}')

def parse_conversation(conversation_text):
    dispatcher_turns = re.findall(r'Dispatcher:([^\n]*)', conversation_text)
    caller_turns = re.findall(r'Caller:([^\n]*)', conversation_text)
    qa_pairs = []
    for q, a in zip(dispatcher_turns, caller_turns):
        q, a = q.strip(), a.strip()
        if q and a:
            qa_pairs.append({'質問': q, '回答': a})
    return qa_pairs

df['qa_pairs'] = df['会話'].apply(parse_conversation)
df_e = df.explode('qa_pairs')
df_e['質問'] = df_e['qa_pairs'].apply(lambda x: x['質問'] if isinstance(x, dict) else None)
df_e['回答'] = df_e['qa_pairs'].apply(lambda x: x['回答'] if isinstance(x, dict) else None)
df_pairs = df_e.drop(columns=['会話', 'qa_pairs'])
df_pairs['ペア'] = df_pairs['質問'] + ' ' + df_pairs['回答']
df_pairs['ペア番号'] = df_pairs.groupby('id').cumcount()
df_pairs = df_pairs[['id', 'ペア番号', 'ペア', '痛み', 'しびれ', '振る舞い', 'トリアージ', '質問', '回答']].reset_index(drop=True)
print(f'pairs: {len(df_pairs)}')"""))

cells.append(cell("## 1.2 yaml → branch_table（頭痛プロトコルの分岐構造）", "markdown"))

cells.append(cell(r"""import yaml

with open(YAML_PATH, encoding='utf-8') as f:
    protocol = yaml.safe_load(f)
headache_proto = next(p for p in protocol['protocols'] if p['id'] == 'headache')

def is_branch_node(n):
    return 'choices' in n and not n.get('metadata_only', False)

branch_nodes = [n for n in headache_proto['nodes'] if is_branch_node(n)]
fallback_triage = headache_proto['fallback']['if_all_symptom_questions_negative']

def parse_choice(c):
    if c.get('triage'):
        return {'code': c['code'], 'text': c['text'], 'action': 'terminal', 'triage': c['triage']}
    if c.get('next'):
        return {'code': c['code'], 'text': c['text'], 'action': 'next', 'next_id': c['next']}
    return {'code': c['code'], 'text': c['text'], 'action': 'fallback', 'triage': fallback_triage}

branch_table = [{
    'id': n['id'],
    'question': n['question'],
    'suspected_condition': n.get('suspected_condition'),
    'choices': [parse_choice(c) for c in n['choices']]
} for n in branch_nodes]

branch_ids = {b['id'] for b in branch_table}
branch_to_label_col = {
    'headache_sudden_severe': '痛み',
    'headache_numbness_paralysis': 'しびれ',
    'headache_abnormal_behavior': '振る舞い',
}
LABEL_TO_CHOICE_CODE = {0: 'a', 1: 'b', 2: 'c'}
triage_decode = {0: 'R3', 1: 'R2', 2: 'Y2'}      # データのトリアージ値 → 文字列
TRIAGE_PRIORITY = {'R1': 6, 'R2': 5, 'R3': 4, 'Y1': 3, 'Y2': 2, 'G': 1}
print('branch_table:', [b['id'] for b in branch_table])
print('fallback:', fallback_triage)"""))

cells.append(cell("## 1.3 Sentence-LUKE で文ベクトル化 → cos類似度 → 採用ペア選定", "markdown"))

cells.append(cell(r"""from transformers import MLukeTokenizer, LukeModel
import torch
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from tqdm.notebook import tqdm
tqdm.pandas()


class SentenceLukeJapanese:
    def __init__(self, name, device=None):
        self.tokenizer = MLukeTokenizer.from_pretrained(name)
        self.model = LukeModel.from_pretrained(name)
        self.model.eval()
        if device is None:
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.device = torch.device(device)
        self.model.to(device)

    def _mean_pooling(self, out, mask):
        emb = out[0]
        m = mask.unsqueeze(-1).expand(emb.size()).float()
        return torch.sum(emb * m, 1) / torch.clamp(m.sum(1), min=1e-9)

    @torch.no_grad()
    def encode(self, sentences, batch_size=8):
        all_e = []
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            enc = self.tokenizer(batch, padding='longest', truncation=True, return_tensors='pt').to(self.device)
            out = self.model(**enc)
            all_e.extend(self._mean_pooling(out, enc['attention_mask']).to('cpu'))
        return torch.stack(all_e)


model_luke = SentenceLukeJapanese('sonoisa/sentence-luke-japanese-base-lite')

df_pairs['ペアのベクトル'] = df_pairs['ペア'].progress_apply(
    lambda x: model_luke.encode([x])[0].tolist() if pd.notna(x) else None
)

branch_emb = {b['id']: model_luke.encode([b['question']])[0].tolist() for b in branch_table}
conv_emb = np.array(df_pairs['ペアのベクトル'].tolist())
for b in branch_table:
    e = np.array(branch_emb[b['id']]).reshape(1, -1)
    df_pairs[f'cos_sim_{b["id"]}'] = cosine_similarity(conv_emb, e).flatten()
print('vectorize OK')"""))

cells.append(cell(r"""# 採用ペア選定（cos類似度の gap 閾値方式・既存ロジック踏襲）
threshold = 0.02

def select_pairs_by_gap(g, col, th=0.02):
    sgi = g.sort_values(by=col, ascending=False)
    sgt = sgi.reset_index(drop=True)
    adopted = pd.Series(False, index=range(len(sgt)))
    if len(sgt) > 0:
        adopted.iloc[0] = True
    if len(sgt) <= 1:
        return adopted.set_axis(sgi.index).reindex(g.index)
    diffs = sgt[col].diff() * -1
    for i in range(1, len(diffs)):
        if diffs.iloc[i] > th:
            adopted.iloc[i:] = False
            break
        else:
            adopted.iloc[i] = True
    return adopted.set_axis(sgi.index).reindex(g.index)

for b in branch_table:
    ad = f'採用ペア_{b["id"]}'
    df_pairs[ad] = False
    for pid, group in df_pairs.groupby('id'):
        adoption = select_pairs_by_gap(group, f'cos_sim_{b["id"]}', threshold)
        df_pairs.loc[group.index, ad] = adoption

all_patient_ids = sorted(df_pairs['id'].unique())
print(f'採用ペア選定 OK / patients: {len(all_patient_ids)}')"""))

# ============================================================
# 2. 共通ヘルパー
# ============================================================
cells.append(cell("# 2. 共通ヘルパー（state 構築・選択肢レンダリング）", "markdown"))

cells.append(cell(r"""# 選択肢に「→ 行き先」を埋め込む（use_route=True のとき使用）
def render_choice_with_route(branch, choice):
    base = choice['text']
    if choice['action'] == 'terminal':
        sc = f'：{branch["suspected_condition"]}の疑い' if branch.get('suspected_condition') else ''
        return f'{base} → {choice["triage"]}{sc}'
    elif choice['action'] == 'next':
        next_b = next((x for x in branch_table if x['id'] == choice['next_id']), None)
        if next_b is not None:
            return f'{base} → 次の確認: 「{next_b["question"]}」'
        return f'{base} → 次の確認へ（{choice["next_id"]}）'
    return f'{base} → {choice["triage"]}（保留）'


# state（context）組み立て。フラグで構成要素を ON/OFF。
#   use_node_question : [現在の分岐] を入れるか（共有エンコーダがノードを識別するのに必須）
#   use_history       : [これまでの確認] を入れるか
def build_context(branch, adopted_pairs, history, use_node_question=True, use_history=True):
    parts = []
    if use_history and history:
        hist = '\n'.join([f'- {h["question"]} → {h["choice_text"]}' for h in history])
        parts.append(f'[これまでの確認]\n{hist}')
    if use_node_question:
        parts.append(f'[現在の分岐]\n{branch["question"]}')
    if adopted_pairs:
        parts.append(f'[患者の発話]\n{" ".join(adopted_pairs)}')
    if not parts:
        parts.append('(発話なし)')
    return '\n\n'.join(parts)


def adopted_pairs_of(pid, bid):
    ad = f'採用ペア_{bid}'
    return df_pairs[(df_pairs['id'] == pid) & (df_pairs[ad] == True)]['ペア'].tolist()


# under-triage 判定（予測が正解より緊急度が低い＝危険側の誤り）
def is_under_triage(true_t, pred_t):
    return TRIAGE_PRIORITY.get(pred_t, 0) < TRIAGE_PRIORITY.get(true_t, 0)


# ground-truth ラベルで歩いた gold path（分岐 → choice code の列）
def gold_path(pid, sub):
    path = []
    current = branch_table[0]['id']
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        gt_code = LABEL_TO_CHOICE_CODE[int(sub[branch_to_label_col[b['id']]].iloc[0])]
        path.append((b['id'], gt_code))
        ch = next(c for c in b['choices'] if c['code'] == gt_code)
        if ch['action'] == 'next' and ch.get('next_id') in branch_ids:
            current = ch['next_id']
            continue
        break
    return path"""))

# ============================================================
# 3. 学習例の生成
# ============================================================
cells.append(cell(r"""# 3. 学習例の生成（ノード単位）

各患者・各ノードについて 1 例を作る。入力 context = [現在の分岐質問] + [採用ペア]（+履歴）、
label = そのノードの回答 index (0=はい/1=いいえ/2=不明)。

- **full_coverage=True**（既定）: 全 (患者 × 3ノード) を例化 → 162×3=486 例、各ノード 54/54/54 で均衡。
  全ノードにラベルが付いているので、gold path が途中終端しても後段ノードを学習できる。
- **full_coverage=False**: ground-truth パス上の訪問ノードだけ（従来方式）。

履歴は頭痛の直列チェーン（痛み→しびれ→振る舞い）に沿った gold prefix を使う。""", "markdown"))

cells.append(cell(r"""def build_step_examples(use_node_question=True, use_history=False, use_route=True,
                       full_coverage=True):
    examples = []
    for pid, sub in df_pairs.groupby('id'):
        # 各ノードの gold choice
        gold = {}
        for b in branch_table:
            code = LABEL_TO_CHOICE_CODE[int(sub[branch_to_label_col[b['id']]].iloc[0])]
            gold[b['id']] = next(c for c in b['choices'] if c['code'] == code)

        if full_coverage:
            # 全ノードを順に例化（履歴は gold prefix）
            history = []
            for b in branch_table:
                ap = adopted_pairs_of(pid, b['id'])
                context = build_context(b, ap, history, use_node_question, use_history)
                ch_texts = [render_choice_with_route(b, c) if use_route else c['text']
                            for c in b['choices']]
                gt_idx = next(i for i, c in enumerate(b['choices']) if c['code'] == gold[b['id']]['code'])
                examples.append({'patient_id': pid, 'branch_id': b['id'],
                                 'context': context, 'choices': ch_texts, 'label': gt_idx})
                history.append({'branch_id': b['id'], 'question': b['question'],
                                'choice_text': gold[b['id']]['text']})
        else:
            # gold path を歩いて訪問ノードだけ
            history = []
            current = branch_table[0]['id']
            while True:
                b = next(x for x in branch_table if x['id'] == current)
                ap = adopted_pairs_of(pid, b['id'])
                context = build_context(b, ap, history, use_node_question, use_history)
                ch_texts = [render_choice_with_route(b, c) if use_route else c['text']
                            for c in b['choices']]
                gt_idx = next(i for i, c in enumerate(b['choices']) if c['code'] == gold[b['id']]['code'])
                examples.append({'patient_id': pid, 'branch_id': b['id'],
                                 'context': context, 'choices': ch_texts, 'label': gt_idx})
                gc = gold[b['id']]
                history.append({'branch_id': b['id'], 'question': b['question'],
                                'choice_text': gc['text']})
                if gc['action'] == 'next' and gc.get('next_id') in branch_ids:
                    current = gc['next_id']
                    continue
                break
    return pd.DataFrame(examples)


# 患者単位ランダム split 7:3（全実験で固定）
from sklearn.model_selection import train_test_split
TRAIN_IDS, TEST_IDS = train_test_split(all_patient_ids, test_size=0.3, random_state=42)
TEST_SET = set(TEST_IDS)
print(f'train患者={len(TRAIN_IDS)}, test患者={len(TEST_IDS)}')

_demo = build_step_examples()
print(f'例数(full_coverage)={len(_demo)} / branch分布={dict(_demo["branch_id"].value_counts())}')
print('label分布(全体):', dict(_demo["label"].value_counts().sort_index()))"""))

# ============================================================
# 4. 学習・推論の共通部品
# ============================================================
cells.append(cell("# 4. 学習・推論の共通部品", "markdown"))

cells.append(cell(r"""from transformers import (BertJapaneseTokenizer, BertForSequenceClassification,
                          BertForMultipleChoice)
from torch.utils.data import TensorDataset, DataLoader
from torch.optim import AdamW
from sklearn.metrics import accuracy_score, f1_score
import torch.nn.functional as F

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
BERT_NAME = 'cl-tohoku/bert-base-japanese-whole-word-masking'
print(f'device: {device}')

DEFAULT_CONFIG = {
    'name': 'unnamed',
    'head': 'seqcls',            # 'seqcls' / 'mc' / 'direct'
    'share_encoder': True,       # seqcls のみ: True=共有1本 / False=分岐ごと独立
    'use_node_question': True,   # context に [現在の分岐] を入れるか
    'use_history': False,        # 既定で履歴なし（node-level FT はマルコフ的）
    'use_route': True,           # mc のみ: 選択肢に行き先を埋め込むか
    'full_coverage': True,       # 全(患者×ノード)を学習例に（486例・均衡）
    'max_len': 128,
    'epochs': 3,
    'lr': 2e-5,
    'batch_size': 4,
    'threshold': 0.0,            # 信頼度がこれ未満なら R3 で安全側終端（0=無効）
}


# ---- seqcls エンコード ----
def encode_seqcls(texts, labels, tokenizer, max_len):
    enc = tokenizer(list(texts), padding='max_length', truncation=True,
                    max_length=max_len, return_tensors='pt')
    return TensorDataset(enc['input_ids'], enc['attention_mask'], torch.tensor(list(labels)))


def train_seqcls(train_texts, train_labels, num_labels, cfg):
    tok = BertJapaneseTokenizer.from_pretrained(BERT_NAME)
    model = BertForSequenceClassification.from_pretrained(
        BERT_NAME, num_labels=num_labels, attn_implementation='eager').to(device)
    opt = AdamW(model.parameters(), lr=cfg['lr'])
    dl = DataLoader(encode_seqcls(train_texts, train_labels, tok, cfg['max_len']),
                    batch_size=cfg['batch_size'], shuffle=True)
    for ep in range(cfg['epochs']):
        model.train()
        tot = 0
        for ids, am, lab in dl:
            ids, am, lab = ids.to(device), am.to(device), lab.to(device)
            opt.zero_grad()
            out = model(ids, attention_mask=am, labels=lab)
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += out.loss.item()
    return model, tok


@torch.no_grad()
def seqcls_predict(model, tok, text, n_labels, max_len):
    model.eval()
    enc = tok([text], padding='max_length', truncation=True,
              max_length=max_len, return_tensors='pt').to(device)
    probs = F.softmax(model(**enc).logits[0, :n_labels], dim=-1).cpu().numpy()
    idx = int(probs.argmax())
    return idx, float(probs[idx])


# ---- mc エンコード ----
NUM_CHOICES = max(len(b['choices']) for b in branch_table)

def encode_mc(df_split, tokenizer, max_len):
    ids_all, am_all, labs = [], [], []
    for _, row in df_split.iterrows():
        cs = list(row['choices'])
        while len(cs) < NUM_CHOICES:
            cs.append('')
        enc = tokenizer([row['context']] * NUM_CHOICES, cs,
                        padding='max_length', truncation=True,
                        max_length=max_len, return_tensors='pt')
        ids_all.append(enc['input_ids'])
        am_all.append(enc['attention_mask'])
        labs.append(row['label'])
    return TensorDataset(torch.stack(ids_all), torch.stack(am_all), torch.tensor(labs))


def train_mc(mc_train, cfg):
    tok = BertJapaneseTokenizer.from_pretrained(BERT_NAME)
    model = BertForMultipleChoice.from_pretrained(BERT_NAME, attn_implementation='eager').to(device)
    opt = AdamW(model.parameters(), lr=cfg['lr'])
    dl = DataLoader(encode_mc(mc_train, tok, cfg['max_len']),
                    batch_size=cfg['batch_size'], shuffle=True)
    for ep in range(cfg['epochs']):
        model.train()
        for ids, am, lab in dl:
            ids, am, lab = ids.to(device), am.to(device), lab.to(device)
            opt.zero_grad()
            out = model(input_ids=ids, attention_mask=am, labels=lab)
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
    return model, tok


@torch.no_grad()
def mc_predict(model, tok, context, rendered_choices, max_len):
    model.eval()
    n_valid = len(rendered_choices)
    cs = list(rendered_choices)
    while len(cs) < NUM_CHOICES:
        cs.append('')
    enc = tok([context] * NUM_CHOICES, cs, padding='max_length', truncation=True,
              max_length=max_len, return_tensors='pt')
    ids = enc['input_ids'].unsqueeze(0).to(device)
    am = enc['attention_mask'].unsqueeze(0).to(device)
    probs = F.softmax(model(input_ids=ids, attention_mask=am).logits[0, :n_valid], dim=-1).cpu().numpy()
    idx = int(probs.argmax())
    return idx, float(probs[idx])"""))

# ============================================================
# 5. 遷移図トラバーサル + 指標
# ============================================================
cells.append(cell("# 5. 遷移図トラバーサル（greedy）と評価指標", "markdown"))

cells.append(cell(r"""# choose(branch, context, rendered_choices) -> (choice_idx, confidence)
# を受け取り、遷移図を greedy に辿って最終 triage と経路を返す。
def traverse(pid, cfg, choose):
    history, pred_path = [], []
    current = branch_table[0]['id']
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        ap = adopted_pairs_of(pid, b['id'])
        context = build_context(b, ap, history, cfg['use_node_question'], cfg['use_history'])
        rendered = [render_choice_with_route(b, c) if cfg['use_route'] else c['text']
                    for c in b['choices']]
        idx, conf = choose(b, context, rendered)
        if cfg['threshold'] > 0 and conf < cfg['threshold']:
            pred_path.append((b['id'], 'low_conf'))
            return 'R3', pred_path
        ch = b['choices'][idx]
        pred_path.append((b['id'], ch['code']))
        history.append({'branch_id': b['id'], 'question': b['question'],
                        'choice_text': ch['text']})
        if ch['action'] in ('terminal', 'fallback'):
            return ch['triage'], pred_path
        if ch.get('next_id') in branch_ids:
            current = ch['next_id']
            continue
        return fallback_triage, pred_path


def compute_metrics(res_df, node_records):
    # res_df: id / 真 / 予測 / in_test / 経路一致
    # node_records: list of {branch_id, correct, in_test}  (teacher-forced node判定)
    te = res_df[res_df['in_test']]
    overall = (te['真'] == te['予測']).mean()
    macro_f1 = f1_score(te['真'], te['予測'], average='macro', zero_division=0)
    under = te.apply(lambda r: is_under_triage(r['真'], r['予測']), axis=1).mean()
    path_match = te['経路一致'].mean()
    nd = pd.DataFrame(node_records)
    node_acc = nd[nd['in_test']]['correct'].mean() if len(nd) else float('nan')
    by_triage = (te.assign(c=te['真'] == te['予測']).groupby('真')['c'].mean().to_dict())
    by_node = (nd[nd['in_test']].groupby('branch_id')['correct'].mean().to_dict()
               if len(nd) else {})
    return {
        'overall_acc': overall, 'macro_f1': macro_f1, 'under_triage': under,
        'path_match': path_match, 'node_acc': node_acc,
        'by_triage': by_triage, 'by_node': by_node,
    }"""))

# ============================================================
# 6. run_experiment 本体
# ============================================================
cells.append(cell("# 6. `run_experiment(config)` — 5 モデルを config で出し分け", "markdown"))

cells.append(cell(r"""def run_experiment(config, train_ids=None, test_ids=None):
    cfg = {**DEFAULT_CONFIG, **config}
    # fold 指定がなければグローバルの 7:3 split を使う
    train_ids = TRAIN_IDS if train_ids is None else train_ids
    test_ids = TEST_IDS if test_ids is None else test_ids
    test_set = set(test_ids)
    print(f'\n{"="*64}\n[{cfg["name"]}]  head={cfg["head"]} share={cfg["share_encoder"]} '
          f'hist={cfg["use_history"]} route={cfg["use_route"]}\n{"="*64}')

    # ---------- M3: 直接BERT（遷移図なし） ----------
    if cfg['head'] == 'direct':
        direct = df_pairs.groupby('id').agg(
            text=('ペア', lambda x: ' '.join(x)), label=('トリアージ', 'first')).reset_index()
        tr = direct[direct['id'].isin(train_ids)]
        model, tok = train_seqcls(tr['text'], tr['label'].astype(int), 3, cfg)
        rows = []
        for _, r in direct.iterrows():
            idx, _ = seqcls_predict(model, tok, r['text'], 3, cfg['max_len'])
            rows.append({'id': r['id'], '真': triage_decode[int(r['label'])],
                         '予測': triage_decode[idx], 'in_test': r['id'] in test_set,
                         '経路一致': np.nan})
        res_df = pd.DataFrame(rows)
        m = compute_metrics(res_df, [])
        m.update({'name': cfg['name'], 'results': res_df})
        _report(m)
        return m

    # ---------- 学習例を生成 ----------
    ex = build_step_examples(cfg['use_node_question'], cfg['use_history'],
                             cfg['use_route'], cfg['full_coverage'])
    ex_tr = ex[ex['patient_id'].isin(train_ids)].reset_index(drop=True)

    # ---------- 学習 ----------
    if cfg['head'] == 'mc':
        model, tok = train_mc(ex_tr, cfg)
        def choose(b, context, rendered):
            return mc_predict(model, tok, context, rendered, cfg['max_len'])
        def choose_example(row):
            b = next(x for x in branch_table if x['id'] == row['branch_id'])
            return mc_predict(model, tok, row['context'], row['choices'], cfg['max_len'])[0]

    elif cfg['share_encoder']:                      # A / A+履歴
        model, tok = train_seqcls(ex_tr['context'], ex_tr['label'], 3, cfg)
        def choose(b, context, rendered):
            return seqcls_predict(model, tok, context, len(b['choices']), cfg['max_len'])
        def choose_example(row):
            b = next(x for x in branch_table if x['id'] == row['branch_id'])
            return seqcls_predict(model, tok, row['context'], len(b['choices']), cfg['max_len'])[0]

    else:                                           # M1: 分岐ごと独立BERT
        per_branch = {}
        for bid, g in ex_tr.groupby('branch_id'):
            per_branch[bid] = train_seqcls(g['context'], g['label'], 3, cfg)
        def choose(b, context, rendered):
            mdl, tk = per_branch[b['id']]
            return seqcls_predict(mdl, tk, context, len(b['choices']), cfg['max_len'])
        def choose_example(row):
            mdl, tk = per_branch[row['branch_id']]
            b = next(x for x in branch_table if x['id'] == row['branch_id'])
            return seqcls_predict(mdl, tk, row['context'], len(b['choices']), cfg['max_len'])[0]

    # ---------- ノード単位 acc（teacher-forced：学習例の context で各分岐を独立判定） ----------
    node_records = []
    for _, row in ex.iterrows():
        pred_idx = choose_example(row)
        node_records.append({'branch_id': row['branch_id'],
                             'correct': int(pred_idx == row['label']),
                             'in_test': row['patient_id'] in test_set})

    # ---------- 最終 triage（greedy トラバーサル） ----------
    rows = []
    for pid in all_patient_ids:
        sub = df_pairs[df_pairs['id'] == pid]
        pred_triage, pred_path = traverse(pid, cfg, choose)
        gpath = gold_path(pid, sub)
        rows.append({
            'id': pid,
            '真': triage_decode[int(sub['トリアージ'].iloc[0])],
            '予測': pred_triage,
            'in_test': pid in test_set,
            '経路一致': int(pred_path == gpath),
        })
    res_df = pd.DataFrame(rows)
    m = compute_metrics(res_df, node_records)
    m.update({'name': cfg['name'], 'results': res_df})
    _report(m)
    return m


def _report(m):
    print(f'  overall_acc = {m["overall_acc"]:.3f} | macro_f1 = {m["macro_f1"]:.3f} '
          f'| under_triage = {m["under_triage"]:.3f} '
          f'| node_acc = {m["node_acc"] if m["node_acc"]==m["node_acc"] else float("nan"):.3f} '
          f'| path_match = {m["path_match"]:.3f}')
    print(f'  by_triage = {{' + ', '.join(f"{k}:{v:.2f}" for k, v in m["by_triage"].items()) + '}}')"""))

# ============================================================
# 7. 実験実行
# ============================================================
cells.append(cell("# 7. モデルを実行（ヘッド × 履歴 の 2×2 ＋ baseline）\n\n履歴の要否（A vs A+履歴、MC vs M2）とヘッド（A vs MC）を切り分ける。", "markdown"))

cells.append(cell(r"""experiments = [
    {'name': 'M3 直接BERT (遷移図なし)',
     'head': 'direct'},
    {'name': 'A 共有BERT (履歴なし)',
     'head': 'seqcls', 'use_history': False, 'use_route': False},
    {'name': 'A+履歴',
     'head': 'seqcls', 'use_history': True, 'use_route': False},
    {'name': 'MC (履歴なし+行先)',
     'head': 'mc', 'use_history': False, 'use_route': True},
    {'name': 'M2 選択強化 (履歴+行先)',
     'head': 'mc', 'use_history': True, 'use_route': True},
]

all_results = [run_experiment(c) for c in experiments]"""))

# ============================================================
# 8. 比較
# ============================================================
cells.append(cell("# 8. 比較テーブルと ablation の読み取り", "markdown"))

cells.append(cell(r"""rows = []
for r in all_results:
    rows.append({
        'model': r['name'],
        'overall_acc': round(r['overall_acc'], 3),
        'macro_f1': round(r['macro_f1'], 3),
        'Y2_acc': round(r['by_triage'].get('Y2', 0.0), 3),   # ★ Y2 を学べたかに注目
        'under_triage↓': round(r['under_triage'], 3),
        'node_acc': round(r['node_acc'], 3) if r['node_acc'] == r['node_acc'] else None,
        'path_match': round(r['path_match'], 3),
    })
compare_df = pd.DataFrame(rows)
display(compare_df)

def acc_of(name):
    return next(r['overall_acc'] for r in all_results if r['name'] == name)
def y2_of(name):
    return next(r['by_triage'].get('Y2', 0.0) for r in all_results if r['name'] == name)

print('\n=== 比較の読み取り ===')
print(f'履歴(seqcls): A={acc_of("A 共有BERT (履歴なし)"):.3f} → A+履歴={acc_of("A+履歴"):.3f} '
      f'(Δ={acc_of("A+履歴")-acc_of("A 共有BERT (履歴なし)"):+.3f})')
print(f'履歴(mc)    : MC={acc_of("MC (履歴なし+行先)"):.3f} → M2={acc_of("M2 選択強化 (履歴+行先)"):.3f} '
      f'(Δ={acc_of("M2 選択強化 (履歴+行先)")-acc_of("MC (履歴なし+行先)"):+.3f})')
print(f'ヘッド(履歴なし): A={acc_of("A 共有BERT (履歴なし)"):.3f} → MC={acc_of("MC (履歴なし+行先)"):.3f} '
      f'(Δ={acc_of("MC (履歴なし+行先)")-acc_of("A 共有BERT (履歴なし)"):+.3f})')
print(f'遷移図      : M3={acc_of("M3 直接BERT (遷移図なし)"):.3f} vs 遷移図あり最良='
      f'{max(acc_of(n) for n in ["A 共有BERT (履歴なし)","A+履歴","MC (履歴なし+行先)","M2 選択強化 (履歴+行先)"]):.3f}')
print('Y2_acc:', {r["name"]: round(r["by_triage"].get("Y2", 0.0), 2) for r in all_results})"""))

cells.append(cell(r"""# 可視化（指標を横並び）
import matplotlib.pyplot as plt
import japanize_matplotlib

names = [r['name'] for r in all_results]
metrics = ['overall_acc', 'macro_f1', 'under_triage', 'path_match']
titles = ['overall acc ↑', 'macro F1 ↑', 'under-triage 率 ↓', '経路一致率 ↑']

fig, axes = plt.subplots(1, 4, figsize=(20, 5))
for ax, mkey, title in zip(axes, metrics, titles):
    vals = [r[mkey] for r in all_results]
    colors = ['salmon' if mkey == 'under_triage' else 'steelblue' for _ in vals]
    ax.barh(names, vals, color=colors)
    ax.set_title(title)
    ax.set_xlim(0, 1)
    for i, v in enumerate(vals):
        if v == v:
            ax.text(min(v + 0.01, 0.9), i, f'{v:.2f}', va='center', fontsize=9)
    ax.invert_yaxis()
plt.tight_layout()
plt.show()"""))

cells.append(cell(r"""# 結果をCSVへ
import os
os.makedirs('output', exist_ok=True)
compare_df.to_csv('output/ablation_summary.csv', index=False, encoding='utf-8-sig')

# 患者単位の全予測も残す（誤判定の追跡用）
merged = all_results[0]['results'][['id', '真', 'in_test']].copy()
for r in all_results:
    merged = merged.merge(r['results'][['id', '予測']].rename(columns={'予測': r['name']}), on='id')
merged.to_csv('output/ablation_per_patient.csv', index=False, encoding='utf-8-sig')
print('saved: output/ablation_summary.csv, output/ablation_per_patient.csv')
display(merged.head(20))"""))

cells.append(cell(r"""## 9. 解釈メモ

- **履歴の要否**：A vs A+履歴、MC vs M2。node-level supervision は「各質問を独立に答える」マルコフ的タスクなので、原理的には履歴不要のはず。Δがほぼ 0 か負なら「履歴はいらない」を実証できる（max_len 圧迫や入力多様化で**むしろ悪化**する可能性も）。
- **ヘッドの違い**：A（分類）vs MC（MultipleChoice+行き先）。頭痛は全ノードが はい/いいえ/不明 の均一ラベルなので分類ヘッドで足りる。MC の利点は選択肢数が変わる**他症候へのスケール時**に出る。
- **遷移図の利得**：M3（遷移図なし）との差。triage が不均衡（Y2=6）なので M3 は Y2 をほぼ学べない一方、node-level は各ノード均衡で学べる。**accuracy が同等でも under-triage 率・経路一致率・ノード単位 acc で遷移図ありが優れる**ことを示せると論文として強い。
- **full_coverage の効果**：全 486 例で学習すると後段ノード（しびれ/振る舞い）の例が増え、深い分岐の確信度が立つ → 前ノートブックで Y2 が 0% になった「深い分岐のデータ希少」問題が緩和されるはず（`Y2_acc` で確認）。

> 注意：162件・7:3 単一 split のため数値は分散が大きい。結論を出す前に **複数 seed か 5-fold CV** で平均±標準偏差を取ること（`random_state` を振って `run_experiment` を回す）。
> ノード単位 acc は `by_node` に分岐別で入っているので、`for r in all_results: print(r["name"], r["by_node"])` で「どの分岐が弱いか」を確認できる。""", "markdown"))

# ============================================================
# 10. 5-fold 交差検証（初期の遷移型BERT）
# ============================================================
cells.append(cell(r"""# 10. 5-fold 交差検証（初期の遷移型BERT）

**遷移型BERT**＝頭痛確定ノードから3分岐（急な痛み→しびれ→振る舞い）を1本の共有BERTで辿るモデル
（node-level FT・履歴なし）。これを **患者単位 5-fold CV** で検証し、mean±std を出す。

- Y2 が 6 件と少ないので **StratifiedKFold（triage で層化）** で各 fold に散らす
- baseline として M3 直接BERT も同じ fold で CV
- 各 fold の test 集合の指標を集計（最終triage acc / macro-F1 / under-triage / 経路一致 / ノード単位acc）""", "markdown"))

cells.append(cell(r"""from sklearn.model_selection import StratifiedKFold

# 患者単位の triage（層化キー）
PATIENT_TRIAGE = [triage_decode[int(df_pairs[df_pairs['id'] == pid]['トリアージ'].iloc[0])]
                  for pid in all_patient_ids]


def run_cv(config, n_splits=5, seed=42):
    ids = np.array(all_patient_ids)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    folds = []
    for k, (tr, te) in enumerate(skf.split(ids, PATIENT_TRIAGE)):
        train_ids, test_ids = ids[tr].tolist(), ids[te].tolist()
        print(f'\n##### {config["name"]} — fold {k+1}/{n_splits} '
              f'(train={len(train_ids)}, test={len(test_ids)})')
        m = run_experiment({**config, 'name': f'{config["name"]} [f{k+1}]'},
                           train_ids=train_ids, test_ids=test_ids)
        folds.append(m)
    keys = ['overall_acc', 'macro_f1', 'under_triage', 'path_match', 'node_acc']
    agg = {}
    for key in keys:
        vals = [f[key] for f in folds if f[key] == f[key]]   # NaN を除外
        agg[key] = (float(np.mean(vals)), float(np.std(vals))) if vals else (float('nan'), float('nan'))
    print(f'\n{"="*50}\n=== {config["name"]} 5-fold CV (mean±std) ===')
    for key in keys:
        mu, sd = agg[key]
        print(f'  {key:14s}: {mu:.3f} ± {sd:.3f}')
    return {'name': config['name'], 'folds': folds, 'agg': agg}"""))

cells.append(cell(r"""# 検証する config（遷移型BERT本命 ＋ M3 baseline）
TRANS_CFG = {'name': '遷移型BERT (共有/履歴なし)',
             'head': 'seqcls', 'use_history': False, 'use_route': False}
M3_CFG = {'name': 'M3 直接BERT', 'head': 'direct'}

cv_trans = run_cv(TRANS_CFG, n_splits=5, seed=42)
cv_m3 = run_cv(M3_CFG, n_splits=5, seed=42)"""))

cells.append(cell(r"""# CV サマリ（mean ± std）
def cv_row(cv):
    row = {'model': cv['name']}
    for key, (mu, sd) in cv['agg'].items():
        row[key] = f'{mu:.3f} ± {sd:.3f}'
    return row

cv_summary = pd.DataFrame([cv_row(cv_trans), cv_row(cv_m3)])
display(cv_summary)

import os
os.makedirs('output', exist_ok=True)
cv_summary.to_csv('output/cv_summary.csv', index=False, encoding='utf-8-sig')

# fold ごとの overall_acc も残す
fold_rows = []
for cv in (cv_trans, cv_m3):
    for k, f in enumerate(cv['folds']):
        fold_rows.append({'model': cv['name'], 'fold': k + 1,
                          'overall_acc': round(f['overall_acc'], 3),
                          'macro_f1': round(f['macro_f1'], 3),
                          'under_triage': round(f['under_triage'], 3),
                          'node_acc': round(f['node_acc'], 3) if f['node_acc'] == f['node_acc'] else None})
fold_df = pd.DataFrame(fold_rows)
fold_df.to_csv('output/cv_folds.csv', index=False, encoding='utf-8-sig')
print('saved: output/cv_summary.csv, output/cv_folds.csv')
display(fold_df)"""))

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

out_path = r"c:/Users/hiyok/Desktop/Emergency_task/頭痛BERT_アブレーション比較.ipynb"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print('OK:', out_path)
print('cells:', len(cells))
