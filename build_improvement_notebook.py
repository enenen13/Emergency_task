"""Build 頭痛BERT_選択強化_改善.ipynb

選択強化BERT (Model 2) の改善実験用。複数の config を切り替えて並列比較。
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

cells.append(cell(r"""# 頭痛BERT: 選択強化BERT 改善実験

## 概要
前ノートブック（`頭痛BERT_順次と選択強化.ipynb`）で MC-BERT (モデル2) が **Y2 を 0%** で外し、総 Accuracy が Model 1 に劣った診断結果を踏まえて、いくつかの改善案を **同じパイプラインを config で切替えて比較** する。

### 改善案
| 案 | やること | 想定効果 |
|---|---|---|
| **A**: threshold=0.0 | 信頼度閾値を外して常に argmax | R3への早期保留を消す → Y2 到達できるように |
| **B**: triage 重み付きlossで Y2優遇 | rare な triage（Y2）由来サンプルの損失を重く（`balanced` か手動倍率） | Y2 (6件) を学習が拾えるように |
| **C**: counterfactual aug | ground-truth で訪れない分岐の例も生成 | 後段分岐の学習例を 162×3 に増やす |
| **D**: max_len 拡張 | 128 → 256 | 履歴入り state を切り捨てない |
| **E**: 全部入り | A+B+C+D | 累積効果を確認 |

各実験は **同じ学習関数 `run_mc_experiment(config)`** を呼び、結果を1つの DataFrame に集約して比較する。""", "markdown"))

# ============================================================
# 1. 環境セットアップ
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

cells.append(cell("## 1.2 yaml → branch_table", "markdown"))

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
triage_decode = {0: 'R3', 1: 'R2', 2: 'Y2'}
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

# ペアのベクトル化
df_pairs['ペアのベクトル'] = df_pairs['ペア'].progress_apply(
    lambda x: model_luke.encode([x])[0].tolist() if pd.notna(x) else None
)

# 分岐質問のベクトル化 → cos類似度
branch_emb = {b['id']: model_luke.encode([b['question']])[0].tolist() for b in branch_table}
conv_emb = np.array(df_pairs['ペアのベクトル'].tolist())
for b in branch_table:
    e = np.array(branch_emb[b['id']]).reshape(1, -1)
    df_pairs[f'cos_sim_{b["id"]}'] = cosine_similarity(conv_emb, e).flatten()
print('vectorize OK')"""))

cells.append(cell(r"""# 採用ペア選定（gap閾値方式）
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

print('採用ペア選定 OK')
all_patient_ids = sorted(df_pairs['id'].unique())
print(f'patients: {len(all_patient_ids)}')"""))

# ============================================================
# 2. 共通ヘルパー
# ============================================================

cells.append(cell("# 2. 共通ヘルパー関数（state 構築・選択肢レンダリング）", "markdown"))

cells.append(cell(r"""# 選択肢の行き先（次の分岐 or 終端triage）を埋め込んだ文字列に
def render_choice_with_route(branch, choice):
    base = choice['text']
    if choice['action'] == 'terminal':
        sc = f'：{branch["suspected_condition"]}の疑い' if branch.get('suspected_condition') else ''
        return f'{base} → {choice["triage"]}{sc}'
    elif choice['action'] == 'next':
        next_b = next((x for x in branch_table if x['id'] == choice['next_id']), None)
        if next_b is not None:
            return f'{base} → 次の確認: 「{next_b["question"]}」'
        else:
            return f'{base} → 次の確認へ（{choice["next_id"]}）'
    else:
        return f'{base} → {choice["triage"]}（保留）'


# state 組み立て
def build_agent_context(branch, adopted_pairs, history, use_history=True):
    parts = []
    if use_history and history:
        hist = '\n'.join([f'- {h["question"]} → {h["choice_text"]}' for h in history])
        parts.append(f'[これまでの確認]\n{hist}')
    parts.append(f'[現在の分岐]\n{branch["question"]}')
    if adopted_pairs:
        parts.append(f'[患者の発話]\n{" ".join(adopted_pairs)}')
    return '\n\n'.join(parts)"""))

# ============================================================
# 3. 学習データ生成（2モード）
# ============================================================

cells.append(cell("# 3. 学習データ生成（2モード）\n\n- **groundtruth**: ground-truth ラベルで歩いたパス上のステップのみ\n- **counterfactual**: 全 (患者 × 分岐) を「もしここに居たら」想定で例化（履歴は ground-truth の prefix）", "markdown"))

cells.append(cell(r"""def build_examples_groundtruth(df_pairs, branch_table, use_history=True, use_route=True):
    # ground-truth パスを歩いた各ステップのみ例化
    examples = []
    for pid, sub in df_pairs.groupby('id'):
        history = []
        current = branch_table[0]['id']
        while True:
            b = next(x for x in branch_table if x['id'] == current)
            ad = f'採用ペア_{b["id"]}'
            ap = sub[sub[ad] == True]['ペア'].tolist()
            context = build_agent_context(b, ap, history, use_history)
            ch_texts = [render_choice_with_route(b, c) if use_route else c['text']
                        for c in b['choices']]
            gt_label = int(sub[branch_to_label_col[b['id']]].iloc[0])
            gt_code = LABEL_TO_CHOICE_CODE[gt_label]
            gt_idx = next(i for i, c in enumerate(b['choices']) if c['code'] == gt_code)
            gt_choice = b['choices'][gt_idx]
            examples.append({
                'patient_id': pid, 'branch_id': b['id'],
                'context': context, 'choices': ch_texts, 'label': gt_idx,
            })
            history.append({
                'branch_id': b['id'],
                'question': b['question'],
                'choice_text': gt_choice['text'],
            })
            if gt_choice['action'] == 'next':
                target = gt_choice['next_id']
                if target in branch_ids:
                    current = target
                    continue
                break
            break
    return pd.DataFrame(examples)


def build_examples_counterfactual(df_pairs, branch_table, use_history=True, use_route=True):
    # 全 (患者 × 分岐) を例化。history は ground-truth prefix を使う。
    # 途中でground-truthが止まる分岐については、空履歴 OR ground-truth履歴を使い分け。
    examples = []
    for pid, sub in df_pairs.groupby('id'):
        # まず ground-truth パスを記録（各分岐到達時点のhistoryスナップショットを保存）
        gt_history_at = {}  # branch_id -> history snapshot
        history = []
        current = branch_table[0]['id']
        visited = set()
        while True:
            gt_history_at[current] = list(history)
            visited.add(current)
            b = next(x for x in branch_table if x['id'] == current)
            gt_label = int(sub[branch_to_label_col[b['id']]].iloc[0])
            gt_code = LABEL_TO_CHOICE_CODE[gt_label]
            gt_choice = next(c for c in b['choices'] if c['code'] == gt_code)
            history.append({
                'branch_id': b['id'],
                'question': b['question'],
                'choice_text': gt_choice['text'],
            })
            if gt_choice['action'] == 'next' and gt_choice['next_id'] in branch_ids:
                current = gt_choice['next_id']
            else:
                break

        # 全分岐に対し例化（ground-truthで訪問しなかった分岐もカバー）
        for b in branch_table:
            bid = b['id']
            ad = f'採用ペア_{bid}'
            ap = sub[sub[ad] == True]['ペア'].tolist()
            # historyスナップショット：訪問済みなら ground-truth history、未訪問なら空
            hist_snapshot = gt_history_at.get(bid, [])
            context = build_agent_context(b, ap, hist_snapshot, use_history)
            ch_texts = [render_choice_with_route(b, c) if use_route else c['text']
                        for c in b['choices']]
            gt_label = int(sub[branch_to_label_col[bid]].iloc[0])
            gt_code = LABEL_TO_CHOICE_CODE[gt_label]
            gt_idx = next(i for i, c in enumerate(b['choices']) if c['code'] == gt_code)
            examples.append({
                'patient_id': pid, 'branch_id': bid,
                'context': context, 'choices': ch_texts, 'label': gt_idx,
            })
    return pd.DataFrame(examples)


# 動作確認
ex_gt = build_examples_groundtruth(df_pairs, branch_table)
ex_cf = build_examples_counterfactual(df_pairs, branch_table)
print(f'ground-truth examples: {len(ex_gt)}')
print(f'counterfactual examples: {len(ex_cf)}')
print('branch分布 (groundtruth):', dict(ex_gt['branch_id'].value_counts()))
print('branch分布 (counterfactual):', dict(ex_cf['branch_id'].value_counts()))"""))

# ============================================================
# 4. 実験関数
# ============================================================

cells.append(cell("# 4. 実験関数 `run_mc_experiment(config)`\n\n複数の改善案を **同じ関数の config 引数で切替** して比較する。", "markdown"))

cells.append(cell(r"""from transformers import BertJapaneseTokenizer, BertForMultipleChoice
from torch.utils.data import TensorDataset, DataLoader
from torch.optim import AdamW
from torch.nn import CrossEntropyLoss
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score
import torch.nn.functional as F

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
print(f'device: {device}')

DEFAULT_CONFIG = {
    'name': 'baseline',
    'use_history': True,
    'use_route_in_choice': True,
    'data_mode': 'groundtruth',      # 'groundtruth' or 'counterfactual'
    # 'triage_weights':
    #   None       : 重みなし（CrossEntropyLoss そのまま）
    #   'balanced' : sklearn 風の自動計算  total / (n_classes * count)
    #   dict       : 手動指定（例: {'R2': 1.0, 'R3': 1.0, 'Y2': 5.0}）
    'triage_weights': None,
    'max_len': 128,
    'epochs': 3,
    'lr': 2e-5,
    'batch_size': 4,
    'threshold_confidence': 0.5,
}


def resolve_triage_weights(spec, train_patient_ids, patient_triage):
    # 設定値からサンプル単位重みの辞書 (triage -> weight) を確定させる
    if spec is None:
        return None
    if isinstance(spec, dict):
        return dict(spec)
    if spec == 'balanced':
        # sklearn の compute_class_weight('balanced') と同じ式
        from collections import Counter
        cnt = Counter(patient_triage[pid] for pid in train_patient_ids)
        total = sum(cnt.values())
        n = len(cnt)
        return {t: total / (n * c) for t, c in cnt.items()}
    raise ValueError(f'unknown triage_weights spec: {spec}')


def encode_mc_batch(df_split, tokenizer, max_len, num_choices,
                    sample_weights=None):
    # patient_id, label, choices, context は df_split から取り出してテンソル化
    # sample_weights は df_split の行に1対1対応した float list
    ids_all, am_all, labs, ws = [], [], [], []
    for i, (_, row) in enumerate(df_split.iterrows()):
        ctx = row['context']
        cs = list(row['choices'])
        while len(cs) < num_choices:
            cs.append('')
        enc = tokenizer([ctx] * num_choices, cs,
                        padding='max_length', truncation=True,
                        max_length=max_len, return_tensors='pt')
        ids_all.append(enc['input_ids'])
        am_all.append(enc['attention_mask'])
        labs.append(row['label'])
        ws.append(sample_weights[i] if sample_weights is not None else 1.0)
    return (torch.stack(ids_all), torch.stack(am_all),
            torch.tensor(labs), torch.tensor(ws, dtype=torch.float))


def mc_agent_step(model, tokenizer, context, choice_texts, max_len, num_choices):
    n_valid = len(choice_texts)
    cs = list(choice_texts)
    while len(cs) < num_choices:
        cs.append('')
    enc = tokenizer([context] * num_choices, cs,
                    padding='max_length', truncation=True,
                    max_length=max_len, return_tensors='pt')
    ids = enc['input_ids'].unsqueeze(0).to(device)
    mask = enc['attention_mask'].unsqueeze(0).to(device)
    model.eval()
    with torch.no_grad():
        logits = model(input_ids=ids, attention_mask=mask).logits
    probs = F.softmax(logits[0, :n_valid], dim=-1).cpu().numpy()
    pred_idx = int(probs.argmax())
    return pred_idx, float(probs[pred_idx])


def mc_agent_predict(patient_id, df_pairs, branch_table, model_mc, tokenizer_mc,
                     config, num_choices):
    history = []
    trace = []
    current = branch_table[0]['id']
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        ad = f'採用ペア_{b["id"]}'
        ap = df_pairs[(df_pairs['id'] == patient_id) & (df_pairs[ad] == True)]['ペア'].tolist()
        context = build_agent_context(b, ap, history, config['use_history'])
        ch_texts = [render_choice_with_route(b, c) if config['use_route_in_choice'] else c['text']
                    for c in b['choices']]
        pred_idx, conf = mc_agent_step(model_mc, tokenizer_mc, context, ch_texts,
                                       config['max_len'], num_choices)
        if conf < config['threshold_confidence']:
            trace.append({'branch': b['id'], 'pred_idx': pred_idx, 'conf': conf,
                          'action': 'low_conf', 'triage': 'R3'})
            return 'R3', trace
        choice = b['choices'][pred_idx]
        trace.append({'branch': b['id'], 'pred_idx': pred_idx, 'conf': conf,
                      'choice': choice['text'], 'action': choice['action']})
        history.append({'branch_id': b['id'], 'question': b['question'],
                        'choice_text': choice['text']})
        if choice['action'] in ('terminal', 'fallback'):
            return choice['triage'], trace
        target = choice.get('next_id')
        if target in branch_ids:
            current = target
            continue
        return fallback_triage, trace
    return fallback_triage, trace


def run_mc_experiment(config):
    cfg = {**DEFAULT_CONFIG, **config}
    print(f'\n{"="*60}\nExperiment: {cfg["name"]}\nconfig: {cfg}\n{"="*60}')

    # データ生成
    if cfg['data_mode'] == 'counterfactual':
        mc_df = build_examples_counterfactual(df_pairs, branch_table,
                                              cfg['use_history'], cfg['use_route_in_choice'])
    else:
        mc_df = build_examples_groundtruth(df_pairs, branch_table,
                                           cfg['use_history'], cfg['use_route_in_choice'])
    print(f'examples: {len(mc_df)}')

    # patient_id で train/test 分割
    tr_ids, te_ids = train_test_split(all_patient_ids, test_size=1/3, random_state=42)
    mc_tr = mc_df[mc_df['patient_id'].isin(tr_ids)].reset_index(drop=True)
    mc_te = mc_df[mc_df['patient_id'].isin(te_ids)].reset_index(drop=True)
    print(f'train={len(mc_tr)}, test={len(mc_te)}')

    NUM_CHOICES = max(len(b['choices']) for b in branch_table)
    tokenizer = BertJapaneseTokenizer.from_pretrained('cl-tohoku/bert-base-japanese-whole-word-masking')

    # patient -> 真triage の対応（重み計算に使う）
    patient_triage_code = df_pairs.groupby('id')['トリアージ'].first().to_dict()
    patient_triage = {pid: triage_decode[int(c)] for pid, c in patient_triage_code.items()}

    # triage_weights を確定
    tw = resolve_triage_weights(cfg['triage_weights'], tr_ids, patient_triage)
    if tw is not None:
        print(f'triage_weights: {tw}')

    # 各サンプルの重み（その患者の真triageの重み）
    train_sample_w = [tw[patient_triage[pid]] for pid in mc_tr['patient_id']] if tw else None
    # 検証時の loss 集計用には重みなしで OK
    test_sample_w = None

    tr_ids_t, tr_am_t, tr_lab_t, tr_w_t = encode_mc_batch(
        mc_tr, tokenizer, cfg['max_len'], NUM_CHOICES, train_sample_w)
    te_ids_t, te_am_t, te_lab_t, te_w_t = encode_mc_batch(
        mc_te, tokenizer, cfg['max_len'], NUM_CHOICES, test_sample_w)
    tr_dl = DataLoader(TensorDataset(tr_ids_t, tr_am_t, tr_lab_t, tr_w_t),
                       batch_size=cfg['batch_size'], shuffle=True)
    te_dl = DataLoader(TensorDataset(te_ids_t, te_am_t, te_lab_t, te_w_t),
                       batch_size=cfg['batch_size'], shuffle=False)

    model = BertForMultipleChoice.from_pretrained(
        'cl-tohoku/bert-base-japanese-whole-word-masking', attn_implementation='eager'
    ).to(device)
    optimizer = AdamW(model.parameters(), lr=cfg['lr'])
    loss_fn = CrossEntropyLoss(reduction='none')  # サンプル単位の loss を取得

    for ep in range(cfg['epochs']):
        model.train()
        total_loss = 0
        for ids, am, lab, w in tr_dl:
            ids, am, lab, w = ids.to(device), am.to(device), lab.to(device), w.to(device)
            optimizer.zero_grad()
            out = model(input_ids=ids, attention_mask=am)
            losses = loss_fn(out.logits, lab)        # [batch]
            loss = (losses * w).mean()               # 重み付き平均
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
        # eval（test時は重みなしの平均でOK）
        model.eval()
        all_preds, all_labs = [], []
        with torch.no_grad():
            for ids, am, lab, _w in te_dl:
                ids, am, lab = ids.to(device), am.to(device), lab.to(device)
                logits = model(input_ids=ids, attention_mask=am).logits
                all_preds.extend(logits.argmax(-1).cpu().numpy())
                all_labs.extend(lab.cpu().numpy())
        acc = accuracy_score(all_labs, all_preds)
        f1 = f1_score(all_labs, all_preds, average='weighted', zero_division=0)
        print(f'  Epoch {ep+1}: train_loss={total_loss/len(tr_dl):.4f}, test_acc={acc:.4f}, f1={f1:.4f}')

    # 全患者で agent 推論
    results = []
    for pid in all_patient_ids:
        pred_triage, trace = mc_agent_predict(pid, df_pairs, branch_table, model, tokenizer,
                                              cfg, NUM_CHOICES)
        true_code = df_pairs[df_pairs['id'] == pid]['トリアージ'].iloc[0]
        results.append({
            'id': pid,
            '真': triage_decode.get(int(true_code), str(true_code)),
            '予測': pred_triage,
            'in_test': pid in te_ids,
        })
    res_df = pd.DataFrame(results)
    res_df['正解'] = res_df['真'] == res_df['予測']
    overall = res_df['正解'].mean()
    by_triage = res_df.groupby('真')['正解'].mean()
    print(f'\n>>> {cfg["name"]} overall acc = {overall:.4f}')
    print(by_triage)
    return {
        'config': cfg,
        'results': res_df,
        'overall_acc': overall,
        'by_triage': by_triage.to_dict(),
    }"""))

# ============================================================
# 5. 実験を順次実行
# ============================================================

cells.append(cell("# 5. 実験を順次実行\n\n各 config を `run_mc_experiment` に渡して結果を集める。", "markdown"))

cells.append(cell(r"""# 実験 config 一覧
experiments = [
    {
        'name': 'baseline (履歴+行き先, threshold=0.5)',
    },
    {
        'name': 'A: threshold=0.0 (閾値なし)',
        'threshold_confidence': 0.0,
    },
    {
        'name': 'B-auto: balanced (≒Y2 9倍)',
        'triage_weights': 'balanced',
    },
    {
        'name': 'B-3x: Y2 を3倍',
        'triage_weights': {'R2': 1.0, 'R3': 1.0, 'Y2': 3.0},
    },
    {
        'name': 'B-9x: Y2 を9倍（balanced と同じくらい）',
        'triage_weights': {'R2': 1.0, 'R3': 1.0, 'Y2': 9.0},
    },
    {
        'name': 'C: counterfactual aug',
        'data_mode': 'counterfactual',
    },
    {
        'name': 'D: max_len=256',
        'max_len': 256,
    },
    {
        'name': 'E: 全部入り (A+B-balanced+C+D)',
        'threshold_confidence': 0.0,
        'triage_weights': 'balanced',
        'data_mode': 'counterfactual',
        'max_len': 256,
    },
]

all_results = []
for exp_cfg in experiments:
    out = run_mc_experiment(exp_cfg)
    all_results.append(out)"""))

# ============================================================
# 6. 比較
# ============================================================

cells.append(cell("# 6. 改善案ごとの比較", "markdown"))

cells.append(cell(r"""# 比較テーブル
rows = []
for r in all_results:
    row = {'name': r['config']['name'], 'overall_acc': r['overall_acc']}
    row.update({f'acc_{k}': v for k, v in r['by_triage'].items()})
    rows.append(row)

compare_df = pd.DataFrame(rows)
display(compare_df)

# Y2 がどう変化したかに注目
print('\n=== Y2正解率の変化 ===')
for r in all_results:
    y2 = r['by_triage'].get('Y2', 0.0)
    print(f'  {r["config"]["name"]:50s}: Y2 = {y2:.2%}')"""))

cells.append(cell(r"""# 可視化
import matplotlib.pyplot as plt
import japanize_matplotlib

fig, ax = plt.subplots(1, 2, figsize=(14, 6))

# overall
xs = [r['config']['name'] for r in all_results]
ys = [r['overall_acc'] for r in all_results]
ax[0].barh(xs, ys, color='steelblue')
ax[0].set_xlabel('overall accuracy')
ax[0].set_title('全体 Accuracy')
ax[0].set_xlim(0, 1)
for i, v in enumerate(ys):
    ax[0].text(v + 0.01, i, f'{v:.3f}', va='center')

# triage別
triages = ['R2', 'R3', 'Y2']
x_pos = range(len(all_results))
width = 0.25
for i, t in enumerate(triages):
    vals = [r['by_triage'].get(t, 0) for r in all_results]
    ax[1].bar([p + width*i for p in x_pos], vals, width=width, label=t)
ax[1].set_xticks([p + width for p in x_pos])
ax[1].set_xticklabels([r['config']['name'] for r in all_results], rotation=45, ha='right')
ax[1].set_ylabel('accuracy')
ax[1].set_title('triage別 Accuracy')
ax[1].legend()
ax[1].set_ylim(0, 1.05)

plt.tight_layout()
plt.show()"""))

cells.append(cell(r"""# 観察ポイントの整理（自由記述）

print(\"\"\"
==== 改善案の効果まとめ ====

A. threshold=0.0
   → 信頼度に関わらず常に argmax を採用。
   → R3への早期保留が消える分、Y2のような fallback triage に到達できるようになる。
   → ただし R3 で「保留」していた case が他に流れるため、R3 acc は下がる可能性。

B. class weight
   → 出現頻度の少ない選択肢の損失を重くする。
   → Y2 を産む「いいえ」の連続を学びやすくなる。

C. counterfactual augmentation
   → ground-truthで歩かない分岐も学習例に入れる。
   → 後段分岐 (numbness_paralysis, abnormal_behavior) の学習データが3倍以上に増える。
   → 履歴は ground-truth prefix を使うので「ある人がここに辿り着いたら何を選ぶか」を学べる。

D. max_len=256
   → 履歴+発話を切り捨てずに済む。長文patient向け。
   → 学習時間とメモリは増える。

E. 全部入り
   → 効果が打ち消し合うこともあるので、個別に効くものを併用したいだけ取捨選択する。
\"\"\")"""))

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

out_path = r"c:/Users/hiyok/Desktop/Emergency_task/頭痛BERT_選択強化_改善.ipynb"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print('OK:', out_path)
print('cells:', len(cells))
