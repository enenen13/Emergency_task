"""Build the consolidated headache BERT notebook (sequential + multi-choice)."""
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

cells.append(cell(r"""# 頭痛BERT: 順次BERT vs 選択強化BERT vs 直接BERT

## 概要
頭痛プロトコル（`protocol.yaml` の `headache`）に対して、3種類のBERTモデルを実装・比較する。

- **モデル1：全BERT（順次）**
  yaml の分岐ノード数だけ専用BERTを用意し、「**分岐 → 選択肢 → 次の分岐**」のサイクルに従って順次（greedy）に推論する。
  予測が「次へ進む」以外の場合はその時点で終端し、後続のBERTは呼ばない。

- **モデル2：選択強化BERT（履歴持ちエージェント / 単一 MultipleChoice BERT）**
  単一のBERTで、yaml の「**分岐 → 選択肢**」サイクルを **エージェント的に** たどる。
  各ステップで BERT が見る state は次の3点セット:
  1. `[これまでの確認]` 過去の (分岐質問 → 自分が選んだ回答) 履歴
  2. `[現在の分岐]` 今居る分岐の質問
  3. `[患者の発話]` cos類似度で選定された関連会話ペア

  選択肢には「**→ 行き先**」が埋め込まれている（`はい → R2：くも膜下出血の疑い` / `いいえ → 次の確認: しびれや麻痺がありますか？` / `不明 → R3（保留）`）。
  ReAct / policy 的に、状態を更新しながらエッジを選んで進む。

- **モデル3：直接BERT（遷移図を使わない baseline）**
  会話全体を1本の文脈として読み、**遷移図を一切経由せず**に最終 triage（R2/R3/Y2）を直接 3クラス分類する単一BERT。
  遷移図ベース（モデル1・2）との比較対照。

データ前処理（ラリー分割・Sentence-LUKE ベクトル化・cos類似度による採用ペア選定）と各BERTの学習方法は既存ノートブック (`BERTの入力文選定.ipynb` / `頭痛BERT学習.ipynb`) を踏襲する。""", "markdown"))

cells.append(cell("# 1. データ準備", "markdown"))

cells.append(cell("""# ============================================================
# 環境判定 & セットアップ (Colab / ローカル どちらでも動く)
# ============================================================
import os, sys, subprocess

IN_COLAB = 'google.colab' in sys.modules
print(f'IN_COLAB = {IN_COLAB}')

if IN_COLAB:
    # ---- Colab: Drive をマウントし、不足パッケージを入れる ----
    from google.colab import drive
    drive.mount('/content/drive')

    # Colab に最初から入ってない可能性があるもの。-q で静かに。
    # transformers は 4.x にピン（5.x には MLukeTokenizer のバグあり）。
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                    'transformers==4.46.3', 'sentencepiece', 'fugashi',
                    'ipadic', 'unidic-lite', 'protobuf', 'tiktoken',
                    'pyyaml', 'graphviz', 'japanize-matplotlib'], check=True)

    # 自分のDriveに合わせて変更。CSV と protocol.yaml をここに置いてください。
    DATA_DIR = '/content/drive/MyDrive/NTCIR-19'
    CSV_PATH = f'{DATA_DIR}/headache_emergency_calls202605311132.csv'
    YAML_PATH = f'{DATA_DIR}/protocol.yaml'
else:
    # ---- ローカル ----
    BASE_DIR = 'C:/Users/hiyok/Desktop/Emergency_task'
    CSV_PATH = f'{BASE_DIR}/dataset/headache_emergency_calls202605311132.csv'
    YAML_PATH = f'{BASE_DIR}/transition_diagram/protocol.yaml'

print(f'CSV : {CSV_PATH}  (exists: {os.path.exists(CSV_PATH)})')
print(f'YAML: {YAML_PATH}  (exists: {os.path.exists(YAML_PATH)})')

# GPU 状態を確認
try:
    import torch
    print(f'CUDA available: {torch.cuda.is_available()}')
    if torch.cuda.is_available():
        print(f'  device: {torch.cuda.get_device_name(0)}')
except ImportError:
    pass"""))

cells.append(cell("""import pandas as pd
# 全件（162行）を学習に使う。CPU だと数十分〜時間かかるので、検証目的なら .head(N) で絞ること。
df = pd.read_csv(CSV_PATH)
print(f'rows: {len(df)}')
print('label分布:')
for col in ['痛み', 'しびれ', '振る舞い', 'トリアージ']:
    print(f'  {col}: {dict(df[col].value_counts().sort_index())}')
df.head()"""))

cells.append(cell("## 1.1. 会話をラリーごとに分割し、質問と回答のペアを作成", "markdown"))

cells.append(cell(r"""import re

def parse_conversation(conversation_text):
    dispatcher_turns = re.findall(r'Dispatcher:([^\n]*)', conversation_text)
    caller_turns = re.findall(r'Caller:([^\n]*)', conversation_text)

    qa_pairs = []
    min_len = min(len(dispatcher_turns), len(caller_turns))

    for i in range(min_len):
        question = dispatcher_turns[i].strip()
        answer = caller_turns[i].strip()
        if question and answer:
            qa_pairs.append({'質問': question, '回答': answer})

    return qa_pairs

df['qa_pairs'] = df['会話'].apply(parse_conversation)
df_expanded = df.explode('qa_pairs')
df_expanded['質問'] = df_expanded['qa_pairs'].apply(lambda x: x['質問'] if isinstance(x, dict) else None)
df_expanded['回答'] = df_expanded['qa_pairs'].apply(lambda x: x['回答'] if isinstance(x, dict) else None)

df_pairs = df_expanded.drop(columns=['会話', 'qa_pairs'])
df_pairs['ペア'] = df_pairs['質問'] + ' ' + df_pairs['回答']
df_pairs['ペア番号'] = df_pairs.groupby('id').cumcount()

ordered_columns = ['id', 'ペア番号', 'ペア', '痛み', 'しびれ', '振る舞い', 'トリアージ', '質問', '回答']
df_pairs = df_pairs[ordered_columns].reset_index(drop=True)
display(df_pairs.head(10))"""))

cells.append(cell(r"""## 1.2. protocol.yaml から頭痛プロトコルの分岐構造を読み込み

ここで以下を yaml から決定する：
- **BERTの数**（モデル1） = headache プロトコルの分岐ノード数
- **分岐→選択肢のサイクル**（両モデル共通） = 各分岐ノードの選択肢と次ノード/triage""", "markdown"))

cells.append(cell(r"""import yaml

with open(YAML_PATH, encoding='utf-8') as f:
    protocol = yaml.safe_load(f)

# headache プロトコルを抽出
headache_proto = next(p for p in protocol['protocols'] if p['id'] == 'headache')

# triage 判定のある choices を持つノード（metadata_only でないもの）を分岐ノードとする
def is_branch_node(node):
    return 'choices' in node and not node.get('metadata_only', False)

branch_nodes = [n for n in headache_proto['nodes'] if is_branch_node(n)]

print(f'頭痛プロトコルの分岐ノード数（= モデル1のBERT数）: {len(branch_nodes)}')
for i, n in enumerate(branch_nodes):
    print(f'\n[分岐 {i+1}] id={n["id"]}')
    print(f'  質問: {n["question"]}')
    for c in n['choices']:
        outcome = c.get('triage') or ('next:' + c.get('next', '')) or 'fallback'
        print(f'    {c["code"]}: {c["text"]}  ->  {outcome}')"""))

cells.append(cell(r"""# 分岐 → 選択肢サイクルを構造化（両モデルから参照する遷移テーブル）
fallback_triage = headache_proto['fallback']['if_all_symptom_questions_negative']

def parse_choice(choice):
    code = choice['code']
    text = choice['text']
    triage = choice.get('triage')
    next_id = choice.get('next')
    if triage:
        return {'code': code, 'text': text, 'action': 'terminal', 'triage': triage}
    elif next_id:
        return {'code': code, 'text': text, 'action': 'next', 'next_id': next_id}
    else:
        return {'code': code, 'text': text, 'action': 'fallback', 'triage': fallback_triage}

branch_table = []
for n in branch_nodes:
    branch_table.append({
        'id': n['id'],
        'question': n['question'],
        'suspected_condition': n.get('suspected_condition'),  # 行き先説明に使う
        'choices': [parse_choice(c) for c in n['choices']]
    })

# 表示
import pprint
pprint.pprint(branch_table, sort_dicts=False, width=120)"""))

cells.append(cell(r"""## 1.3. branch_table 上での着地集計と可視化

ロード済みのデータ（先頭10件）を、`branch_table` の **ground-truth ラベル**（`痛み` / `しびれ` / `振る舞い` カラム）に従って歩かせ、**どの triage に何件着地するか** を集計する。
- 分岐ノード（ひし形）には通過した件数を表示
- 終端 triage（角丸ボックス）には着地件数を表示
- エッジの太さは通過件数に比例（0件のエッジは破線で表示）
- `action='next'` の遷移先が `branch_table` 外（metadata_only ノードなど）の場合は yaml の `fallback.if_all_symptom_questions_negative` で着地扱い""", "markdown"))

cells.append(cell(r"""from graphviz import Digraph
from collections import defaultdict

# 集計に必要なラベル変換（モデル1セクションで再定義されるが、可視化を前段で完結させるため早期に置く）
branch_to_label_col = {
    'headache_sudden_severe': '痛み',
    'headache_numbness_paralysis': 'しびれ',
    'headache_abnormal_behavior': '振る舞い',
}
LABEL_TO_CHOICE_CODE = {0: 'a', 1: 'b', 2: 'c'}

# ===== 1) 各患者のグラウンドトゥルース・パスを辿って集計 =====
branch_ids = {b['id'] for b in branch_table}
all_patient_ids = sorted(df_pairs['id'].unique())

edge_count = defaultdict(int)        # (src, dst_node_id) -> 通過件数
terminal_count = defaultdict(int)    # triage -> 着地件数
branch_visits = defaultdict(int)     # branch_id -> 通過件数
patient_paths = []                   # (pid, terminal_triage, [(branch, code, dst)])

for pid in all_patient_ids:
    sub = df_pairs[df_pairs['id'] == pid]
    path = []
    current = branch_table[0]['id']
    terminal_triage = None
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        branch_visits[b['id']] += 1
        label_col = branch_to_label_col[b['id']]
        gt_label = int(sub[label_col].iloc[0])
        gt_code = LABEL_TO_CHOICE_CODE[gt_label]
        choice = next(c for c in b['choices'] if c['code'] == gt_code)
        if choice['action'] == 'next':
            target = choice['next_id']
            if target in branch_ids:
                edge_count[(b['id'], target)] += 1
                path.append((b['id'], gt_code, target))
                current = target
                continue
            else:
                # branch_table 外 → fallback で着地
                dst_key = f'TERM_{fallback_triage}'
                edge_count[(b['id'], dst_key)] += 1
                terminal_count[fallback_triage] += 1
                terminal_triage = fallback_triage
                path.append((b['id'], gt_code + '(→fb)', fallback_triage))
                break
        else:  # terminal / fallback
            terminal_triage = choice['triage']
            dst_key = f'TERM_{terminal_triage}'
            edge_count[(b['id'], dst_key)] += 1
            terminal_count[terminal_triage] += 1
            path.append((b['id'], gt_code, terminal_triage))
            break
    patient_paths.append((pid, terminal_triage, path))

# ===== 2) 集計サマリ =====
total = len(all_patient_ids)
print(f'対象患者数: {total}')
print('\n=== 着地 triage の集計 ===')
for t in sorted(terminal_count.keys()):
    print(f'  {t}: {terminal_count[t]} 件')

print('\n=== 各患者のパス ===')
for pid, t, path in patient_paths:
    arrow = ' → '.join([f"{src}/{code}" for src, code, _ in path] + [t])
    print(f'  {pid}: {arrow}')

# ===== 3) Graphviz で集計付き描画 =====
triage_fill = {
    'R1': '#ff6b6b', 'R2': '#ff8787', 'R3': '#ffa94d',
    'Y1': '#ffd43b', 'Y2': '#ffe066',
    'G':  '#a3e635',
}

# 全候補 terminal を列挙（0件のものも描画する）
all_terminals = set()
for b in branch_table:
    for c in b['choices']:
        if c['action'] in ('terminal', 'fallback'):
            all_terminals.add(c['triage'])
        elif c['action'] == 'next' and c['next_id'] not in branch_ids:
            all_terminals.add(fallback_triage)

g = Digraph('headache_branch_table_counts', format='png')
g.attr(rankdir='TB', fontname='Yu Gothic')
g.attr('node', fontname='Yu Gothic')
g.attr('edge', fontname='Yu Gothic')

# 分岐ノード（通過件数付き）
for b in branch_table:
    vis = branch_visits[b['id']]
    g.node(b['id'], label=f"{b['id']}\n[通過 n={vis}]\n{b['question']}",
           shape='diamond', style='filled', fillcolor='#dbe9ff', fontsize='10')

# 終端ノード（着地件数付き）
for t in sorted(all_terminals):
    cnt = terminal_count.get(t, 0)
    g.node(f'TERM_{t}', label=f'{t}\n着地 n={cnt}',
           shape='box', style='rounded,filled',
           fillcolor=triage_fill.get(t, '#cccccc'),
           fontsize='14', fontname='Yu Gothic Bold')

# エッジ（全構造を描く。通過件数で太さ／実線・破線を切替）
for b in branch_table:
    for c in b['choices']:
        if c['action'] == 'next':
            target = c['next_id']
            if target in branch_ids:
                dst = target
            else:
                dst = f'TERM_{fallback_triage}'
        else:
            dst = f'TERM_{c["triage"]}'
        cnt = edge_count.get((b['id'], dst), 0)
        label = f"{c['code']}: {c['text']}\n(n={cnt})"
        g.edge(
            b['id'], dst, label=label, fontsize='9',
            penwidth=str(1 + cnt * 1.2),
            style='solid' if cnt > 0 else 'dashed',
            color='black' if cnt > 0 else 'gray60',
        )

g  # ノートブック上で表示"""))

cells.append(cell(r"""# 2. ペアと分岐質問のベクトル化 & cos類似度

Sentence-LUKE で「会話の各ラリー（ペア）」と「yaml の分岐質問」をベクトル化し、cos類似度を計算する。
これにより、各分岐質問にどのラリーが対応しているかを抽出する。""", "markdown"))

cells.append(cell(r"""from transformers import MLukeTokenizer, LukeModel
import torch


class SentenceLukeJapanese:
    def __init__(self, model_name_or_path, device=None):
        self.tokenizer = MLukeTokenizer.from_pretrained(model_name_or_path)
        self.model = LukeModel.from_pretrained(model_name_or_path)
        self.model.eval()
        if device is None:
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.device = torch.device(device)
        self.model.to(device)

    def _mean_pooling(self, model_output, attention_mask):
        token_embeddings = model_output[0]
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

    @torch.no_grad()
    def encode(self, sentences, batch_size=8):
        all_embeddings = []
        for batch_idx in range(0, len(sentences), batch_size):
            batch = sentences[batch_idx:batch_idx + batch_size]
            encoded_input = self.tokenizer(batch, padding='longest', truncation=True, return_tensors='pt').to(self.device)
            model_output = self.model(**encoded_input)
            sentence_embeddings = self._mean_pooling(model_output, encoded_input['attention_mask']).to('cpu')
            all_embeddings.extend(sentence_embeddings)
        return torch.stack(all_embeddings)


MODEL_NAME = 'sonoisa/sentence-luke-japanese-base-lite'
model_luke = SentenceLukeJapanese(MODEL_NAME)"""))

cells.append(cell(r"""from tqdm.notebook import tqdm
tqdm.pandas()

# ペアのベクトル化
df_pairs['ペアのベクトル'] = df_pairs['ペア'].progress_apply(
    lambda x: model_luke.encode([x])[0].tolist() if pd.notna(x) else None
)
display(df_pairs[['id', 'ペア番号', 'ペア', 'ペアのベクトル']].head())"""))

cells.append(cell(r"""# 各分岐質問のベクトル化（yaml から取得した branch_table を参照）
branch_question_embeddings = {}
for b in branch_table:
    emb = model_luke.encode([b['question']])[0].tolist()
    branch_question_embeddings[b['id']] = emb
    print(f'埋め込み生成: {b["id"]}  (dim={len(emb)})')"""))

cells.append(cell(r"""from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

conversation_embeddings = np.array(df_pairs['ペアのベクトル'].tolist())

# 分岐ごとに cos 類似度カラムを追加
for b in branch_table:
    emb_np = np.array(branch_question_embeddings[b['id']]).reshape(1, -1)
    df_pairs[f'cos_sim_{b["id"]}'] = cosine_similarity(conversation_embeddings, emb_np).flatten()

cos_cols = [f'cos_sim_{b["id"]}' for b in branch_table]
display(df_pairs[['id', 'ペア番号', 'ペア'] + cos_cols])"""))

cells.append(cell(r"""# 3. 採用ペアの選定（既存ロジック踏襲）

cos類似度の降順に並べ、隣接する類似度の差が閾値を超えたら以降を不採用とする。""", "markdown"))

cells.append(cell(r"""threshold = 0.02

def select_pairs_by_similarity_gap(group_df, cos_sim_col_name, threshold=0.1):
    sorted_group_indexed = group_df.sort_values(by=cos_sim_col_name, ascending=False)
    sorted_group_temp = sorted_group_indexed.reset_index(drop=True)
    adopted_temp = pd.Series(False, index=range(len(sorted_group_temp)))
    if len(sorted_group_temp) > 0:
        adopted_temp.iloc[0] = True
    if len(sorted_group_temp) <= 1:
        return adopted_temp.set_axis(sorted_group_indexed.index).reindex(group_df.index)
    diffs = sorted_group_temp[cos_sim_col_name].diff() * -1
    for i in range(1, len(diffs)):
        if diffs.iloc[i] > threshold:
            adopted_temp.iloc[i:] = False
            break
        else:
            adopted_temp.iloc[i] = True
    return adopted_temp.set_axis(sorted_group_indexed.index).reindex(group_df.index)


for b in branch_table:
    cos_sim_col = f'cos_sim_{b["id"]}'
    adopted_col = f'採用ペア_{b["id"]}'
    df_pairs[adopted_col] = False
    for pid, group in df_pairs.groupby('id'):
        adoption = select_pairs_by_similarity_gap(group, cos_sim_col, threshold)
        df_pairs.loc[group.index, adopted_col] = adoption

show_cols = ['id', 'ペア番号', 'ペア']
for b in branch_table:
    show_cols.append(f'cos_sim_{b["id"]}')
    show_cols.append(f'採用ペア_{b["id"]}')
display(df_pairs[show_cols])"""))

cells.append(cell(r"""# 4. モデル1：全BERT（順次実行）

- **BERTの数** = `len(branch_table)`（= yaml から取得した頭痛プロトコルの分岐ノード数）
- 各分岐に対し、その分岐の **採用ペア** を入力としてラベル（はい/いいえ/不明）を予測する独立BERTを学習
- 推論時は branch_table を **頭から順に走査** し、各分岐BERTを呼び出して softmax 信頼度が閾値以上なら採用。「次へ進む（=いいえ）」と判定された場合のみ次の分岐へ。それ以外（はい→triage / 不明→R3）はその時点で終端し、後続BERTは呼ばない。

学習時のラベル付与（既存データに準拠）：
- `痛み` カラム → `headache_sudden_severe`
- `しびれ` カラム → `headache_numbness_paralysis`
- `振る舞い` カラム → `headache_abnormal_behavior`

各カラムの値 0/1/2 は yaml の選択肢 a/b/c（はい/いいえ/不明）に対応する。""", "markdown"))

cells.append(cell(r"""# どの分岐ノードに、データのどの label カラムを使うかのマッピング
branch_to_label_col = {
    'headache_sudden_severe': '痛み',
    'headache_numbness_paralysis': 'しびれ',
    'headache_abnormal_behavior': '振る舞い',
}

# データのラベル 0=はい(a), 1=いいえ(b), 2=不明(c)
LABEL_TO_CHOICE_CODE = {0: 'a', 1: 'b', 2: 'c'}
CHOICE_CODE_TO_LABEL = {'a': 0, 'b': 1, 'c': 2}"""))

cells.append(cell("## 4.1. 各分岐に対して専用BERTを学習", "markdown"))

cells.append(cell(r"""import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from transformers import BertJapaneseTokenizer, BertForSequenceClassification
import torch
from torch.utils.data import TensorDataset, DataLoader
from torch.optim import AdamW
from sklearn.metrics import accuracy_score, f1_score

max_len = 128
epochs = 3
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

def train_one_epoch(model, dataloader, optimizer):
    model.train()
    total_loss = 0
    for batch in dataloader:
        b_ids, b_mask, b_labels = [b.to(device) for b in batch]
        optimizer.zero_grad()
        out = model(b_ids, attention_mask=b_mask, labels=b_labels)
        out.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        total_loss += out.loss.item()
    return total_loss / len(dataloader)

def evaluate_model(model, dataloader):
    model.eval()
    val_loss, all_preds, all_labels = 0, [], []
    with torch.no_grad():
        for batch in dataloader:
            b_ids, b_mask, b_labels = [b.to(device) for b in batch]
            out = model(b_ids, attention_mask=b_mask, labels=b_labels)
            val_loss += out.loss.item()
            preds = torch.argmax(out.logits, dim=1).flatten()
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(b_labels.cpu().numpy())
    return (val_loss / len(dataloader),
            accuracy_score(all_labels, all_preds),
            f1_score(all_labels, all_preds, average='weighted', zero_division=0))


per_branch_bert = {}  # branch_id -> {'model', 'tokenizer', 'label_encoder', ...}

for b in branch_table:
    bid = b['id']
    label_col = branch_to_label_col[bid]
    adopted_col = f'採用ペア_{bid}'

    filtered = df_pairs[df_pairs[adopted_col] == True]
    grouped = filtered.groupby('id').agg(
        input_text=('ペア', lambda x: ' '.join(x)),
        label=(label_col, 'first'),
    ).reset_index()

    le = LabelEncoder()
    grouped['encoded'] = le.fit_transform(grouped['label'])
    num_labels = grouped['encoded'].nunique()
    print(f'\n=== [{bid}] label_col={label_col}, num_labels={num_labels} ===')

    X_train, X_test, y_train, y_test, id_train, id_test = train_test_split(
        grouped['input_text'], grouped['encoded'], grouped['id'],
        test_size=1/3, random_state=42,
        stratify=grouped['encoded'] if num_labels > 1 else None
    )

    tokenizer = BertJapaneseTokenizer.from_pretrained('cl-tohoku/bert-base-japanese-whole-word-masking')
    enc_train = tokenizer(X_train.tolist(), padding='max_length', truncation=True, max_length=max_len, return_tensors='pt')
    enc_test  = tokenizer(X_test.tolist(),  padding='max_length', truncation=True, max_length=max_len, return_tensors='pt')

    train_ds = TensorDataset(enc_train['input_ids'], enc_train['attention_mask'], torch.tensor(y_train.tolist()))
    test_ds  = TensorDataset(enc_test['input_ids'],  enc_test['attention_mask'],  torch.tensor(y_test.tolist()))
    train_dl = DataLoader(train_ds, batch_size=4, shuffle=True)
    test_dl  = DataLoader(test_ds,  batch_size=4, shuffle=False)

    model = BertForSequenceClassification.from_pretrained(
        'cl-tohoku/bert-base-japanese-whole-word-masking',
        num_labels=num_labels, attn_implementation='eager'
    ).to(device)
    opt = AdamW(model.parameters(), lr=2e-5)

    for ep in range(epochs):
        tr_loss = train_one_epoch(model, train_dl, opt)
        va_loss, acc, f1 = evaluate_model(model, test_dl)
        print(f'  Epoch {ep+1}: train_loss={tr_loss:.4f}, test_loss={va_loss:.4f}, acc={acc:.4f}, f1={f1:.4f}')

    per_branch_bert[bid] = {
        'model': model,
        'tokenizer': tokenizer,
        'label_encoder': le,
        'test_patient_ids': id_test.tolist(),
        'train_patient_ids': id_train.tolist(),
    }"""))

cells.append(cell(r"""## 4.2. yaml の「分岐 → 選択肢」サイクルで順次推論

`branch_table` を頭から順に走査。各分岐で対応BERTを呼び、softmax 信頼度が閾値以上なら採用、未満なら R3（=不明）で安全側に終端。
「いいえ（=次へ）」が選ばれた場合のみ次の分岐BERTを呼ぶ。""", "markdown"))

cells.append(cell(r"""import torch.nn.functional as F

threshold_confidence = 0.5  # softmax 信頼度の閾値

def sequential_predict(patient_id, df_pairs, branch_table, per_branch_bert,
                       threshold_confidence=0.5):
    # yaml の分岐→選択肢サイクルに従って順次推論し、最終 triage を返す。
    trace = []
    for b in branch_table:
        bid = b['id']
        adopted_col = f'採用ペア_{bid}'
        patient_pairs = df_pairs[(df_pairs['id'] == patient_id) & (df_pairs[adopted_col] == True)]
        input_text = ' '.join(patient_pairs['ペア'].tolist())

        bundle = per_branch_bert[bid]
        model, tokenizer, le = bundle['model'], bundle['tokenizer'], bundle['label_encoder']
        model.eval()

        enc = tokenizer([input_text], padding='max_length', truncation=True,
                        max_length=max_len, return_tensors='pt').to(device)
        with torch.no_grad():
            logits = model(**enc).logits
        probs = F.softmax(logits, dim=-1)[0].cpu().numpy()
        pred_idx_in_encoded = int(probs.argmax())
        confidence = float(probs[pred_idx_in_encoded])
        decoded_label = int(le.inverse_transform([pred_idx_in_encoded])[0])

        if confidence < threshold_confidence:
            # 信頼度不足 → 安全側で R3
            trace.append({'branch': bid, 'predicted_label': decoded_label,
                          'confidence': confidence, 'action': 'low_confidence',
                          'triage': 'R3'})
            return 'R3', trace

        choice_code = LABEL_TO_CHOICE_CODE[decoded_label]
        choice = next(c for c in b['choices'] if c['code'] == choice_code)

        trace.append({'branch': bid, 'predicted_label': decoded_label,
                      'confidence': confidence, 'choice': choice['text'],
                      'action': choice['action']})

        if choice['action'] in ('terminal', 'fallback'):
            return choice['triage'], trace
        # action == 'next' なら次の分岐BERTへ
    # 全部 'next' で抜けた場合 → fallback
    return fallback_triage, trace


# 全患者で推論し結果を表
all_patient_ids = sorted(df_pairs['id'].unique())
seq_results = []
triage_decode = {0: 'R3', 1: 'R2', 2: 'Y2'}  # データのトリアージ値 0/1/2 → 文字列
for pid in all_patient_ids:
    pred_triage, trace = sequential_predict(pid, df_pairs, branch_table, per_branch_bert,
                                            threshold_confidence=threshold_confidence)
    true_triage_code = df_pairs[df_pairs['id'] == pid]['トリアージ'].iloc[0]
    seq_results.append({
        'id': pid,
        '真のトリアージ': triage_decode.get(int(true_triage_code), str(true_triage_code)),
        'モデル1予測': pred_triage,
        'trace': trace,
    })

seq_df = pd.DataFrame(seq_results)
display(seq_df[['id', '真のトリアージ', 'モデル1予測']])

# 詳細トレースの表示
for r in seq_results:
    print(f'\n[id={r["id"]}] 真={r["真のトリアージ"]} / 予測={r["モデル1予測"]}')
    for step in r['trace']:
        print('   ', step)"""))

cells.append(cell(r"""# 5. モデル2：選択強化BERT（履歴持ちエージェント / 単一 MultipleChoice BERT）

1つの BERT を **policy ネットワーク** として使い、`branch_table` の上を **状態を更新しながら歩く** エージェントを実装する。

各ステップで BERT が見る state（context）:

```
[これまでの確認]
- {過去の分岐の質問1} → {自分が選んだ回答1}
- {過去の分岐の質問2} → {自分が選んだ回答2}
- ...

[現在の分岐]
{今いる分岐の質問}

[患者の発話]
{この分岐に紐づく cos類似採用ペア}
```

**選択肢** は「→ 行き先」入り（次の分岐の質問文 or 終端 triage を埋め込み）。

学習時も推論時も BERT は **常に同じ形式の state** を見る → 「policy」として一貫した振る舞いになる。
学習データは「ground-truth ラベルでパスを歩きながら各ステップを記録」して作る。""", "markdown"))

cells.append(cell("## 5.1. 選択肢テキスト & 状態の組み立て関数", "markdown"))

cells.append(cell(r"""# 選択肢に「→ 行き先」情報を埋め込む
# 例:
#   はい → R2: くも膜下出血の疑い
#   いいえ → 次の確認: しびれや麻痺がありますか？
#   不明 → R3: 保留
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

# エージェントの state を組み立てる
def build_agent_context(branch, adopted_pairs, history):
    parts = []
    if history:
        # これまで歩いた経路（質問とその選択）を全部見せる
        hist_lines = '\n'.join([f'- {h["question"]} → {h["choice_text"]}' for h in history])
        parts.append(f'[これまでの確認]\n{hist_lines}')
    parts.append(f'[現在の分岐]\n{branch["question"]}')
    if adopted_pairs:
        parts.append(f'[患者の発話]\n{" ".join(adopted_pairs)}')
    return '\n\n'.join(parts)

# 動作確認
print('=== 選択肢テキスト（行き先入り）===')
for b in branch_table:
    print(f'\n[{b["id"]}] {b["question"]}')
    for c in b['choices']:
        print(f'  {c["code"]}: {render_choice_with_route(b, c)}')

print('\n=== state 例（branch 2 へ進んだ場合）===')
demo_hist = [{
    'question': branch_table[0]['question'],
    'choice_text': branch_table[0]['choices'][1]['text'],  # 「いいえ」
}]
print(build_agent_context(branch_table[1], ['しびれは無いと言ってます'], demo_hist))"""))

cells.append(cell(r"""# ground-truth ラベルでパスを歩き、各ステップを学習例として記録する
mc_examples = []  # {'patient_id', 'branch_id', 'context', 'choices', 'label'}

for pid, sub in df_pairs.groupby('id'):
    history = []
    current = branch_table[0]['id']
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        bid = b['id']
        label_col = branch_to_label_col[bid]
        adopted_col = f'採用ペア_{bid}'
        adopted_pairs = sub[sub[adopted_col] == True]['ペア'].tolist()

        # state（履歴 + 現在分岐 + 発話）
        context = build_agent_context(b, adopted_pairs, history)

        # 選択肢（行き先入り）
        choice_texts = [render_choice_with_route(b, c) for c in b['choices']]
        code_to_idx = {c['code']: i for i, c in enumerate(b['choices'])}

        # ground truth の選択
        gt_label = int(sub[label_col].iloc[0])
        gt_code = LABEL_TO_CHOICE_CODE[gt_label]
        gt_idx = code_to_idx[gt_code]
        gt_choice = b['choices'][gt_idx]

        mc_examples.append({
            'patient_id': pid,
            'branch_id': bid,
            'context': context,
            'choices': choice_texts,
            'label': gt_idx,
        })

        # 履歴に記録して次へ
        history.append({
            'branch_id': bid,
            'question': b['question'],
            'choice_text': gt_choice['text'],
        })

        if gt_choice['action'] == 'next':
            target = gt_choice['next_id']
            if target in branch_ids:
                current = target
                continue
            else:
                # branch_table 外（metadata_only など）→ ループ脱出
                break
        else:  # terminal / fallback
            break

mc_df = pd.DataFrame(mc_examples)
print(f'MC エージェント学習例数（patientsごとに ground-truthパスを歩いた合計）= {len(mc_df)}')
print(f'patient数: {mc_df["patient_id"].nunique()}, ステップ数の分布:')
print(mc_df.groupby('patient_id').size().value_counts().sort_index())
display(mc_df.head(10))"""))

cells.append(cell("## 5.2. BertForMultipleChoice の学習", "markdown"))

cells.append(cell(r"""from transformers import BertForMultipleChoice

# 患者IDで train/test 分割（同じ患者は train か test のどちらか一方のみ）
train_ids, test_ids = train_test_split(all_patient_ids, test_size=1/3, random_state=42)
mc_train = mc_df[mc_df['patient_id'].isin(train_ids)].reset_index(drop=True)
mc_test  = mc_df[mc_df['patient_id'].isin(test_ids)].reset_index(drop=True)
print(f'mc_train={len(mc_train)}, mc_test={len(mc_test)}')

NUM_CHOICES = max(len(b['choices']) for b in branch_table)
print(f'NUM_CHOICES = {NUM_CHOICES}')

tokenizer_mc = BertJapaneseTokenizer.from_pretrained('cl-tohoku/bert-base-japanese-whole-word-masking')


def encode_mc_batch(df_split, tokenizer, max_len=128):
    # BertForMultipleChoice 用に [batch, num_choices, seq_len] にエンコード
    input_ids_all, attn_all, labels = [], [], []
    for _, row in df_split.iterrows():
        ctx = row['context']
        choices = list(row['choices'])
        while len(choices) < NUM_CHOICES:
            choices.append('')
        enc = tokenizer(
            [ctx] * NUM_CHOICES, choices,
            padding='max_length', truncation=True, max_length=max_len, return_tensors='pt'
        )
        input_ids_all.append(enc['input_ids'])
        attn_all.append(enc['attention_mask'])
        labels.append(row['label'])
    input_ids_all = torch.stack(input_ids_all)   # [B, C, L]
    attn_all = torch.stack(attn_all)             # [B, C, L]
    labels = torch.tensor(labels)
    return input_ids_all, attn_all, labels


train_ids_t, train_attn_t, train_lab_t = encode_mc_batch(mc_train, tokenizer_mc, max_len)
test_ids_t,  test_attn_t,  test_lab_t  = encode_mc_batch(mc_test,  tokenizer_mc, max_len)
print('train shape:', train_ids_t.shape, 'test shape:', test_ids_t.shape)

train_dl_mc = DataLoader(TensorDataset(train_ids_t, train_attn_t, train_lab_t),
                         batch_size=4, shuffle=True)
test_dl_mc  = DataLoader(TensorDataset(test_ids_t,  test_attn_t,  test_lab_t),
                         batch_size=4, shuffle=False)

model_mc = BertForMultipleChoice.from_pretrained(
    'cl-tohoku/bert-base-japanese-whole-word-masking', attn_implementation='eager'
).to(device)
optimizer_mc = AdamW(model_mc.parameters(), lr=2e-5)


def train_mc(model, dataloader, optimizer):
    model.train()
    total = 0
    for ids, mask, lab in dataloader:
        ids, mask, lab = ids.to(device), mask.to(device), lab.to(device)
        optimizer.zero_grad()
        out = model(input_ids=ids, attention_mask=mask, labels=lab)
        out.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        total += out.loss.item()
    return total / len(dataloader)

def eval_mc(model, dataloader):
    model.eval()
    val_loss, preds, gold = 0, [], []
    with torch.no_grad():
        for ids, mask, lab in dataloader:
            ids, mask, lab = ids.to(device), mask.to(device), lab.to(device)
            out = model(input_ids=ids, attention_mask=mask, labels=lab)
            val_loss += out.loss.item()
            preds.extend(out.logits.argmax(dim=-1).cpu().numpy().tolist())
            gold.extend(lab.cpu().numpy().tolist())
    return (val_loss / len(dataloader),
            accuracy_score(gold, preds),
            f1_score(gold, preds, average='weighted', zero_division=0))


for ep in range(epochs):
    tr_loss = train_mc(model_mc, train_dl_mc, optimizer_mc)
    va_loss, acc, f1 = eval_mc(model_mc, test_dl_mc)
    print(f'[MC-BERT] Epoch {ep+1}: train_loss={tr_loss:.4f}, test_loss={va_loss:.4f}, acc={acc:.4f}, f1={f1:.4f}')"""))

cells.append(cell("## 5.3. yaml の「分岐 → 選択肢」サイクルで遷移図をたどる", "markdown"))

cells.append(cell(r"""def mc_agent_step(model, tokenizer, context, choice_texts, max_len=128):
    # 1ステップ: context（履歴入りstate）と選択肢から1つを選ぶ
    n_valid = len(choice_texts)
    cs = list(choice_texts)
    while len(cs) < NUM_CHOICES:
        cs.append('')
    enc = tokenizer(
        [context] * NUM_CHOICES, cs,
        padding='max_length', truncation=True, max_length=max_len, return_tensors='pt'
    )
    ids = enc['input_ids'].unsqueeze(0).to(device)
    mask = enc['attention_mask'].unsqueeze(0).to(device)
    model.eval()
    with torch.no_grad():
        logits = model(input_ids=ids, attention_mask=mask).logits
    valid_logits = logits[0, :n_valid]
    probs = F.softmax(valid_logits, dim=-1).cpu().numpy()
    pred_idx = int(probs.argmax())
    return pred_idx, float(probs[pred_idx])


def mc_agent_predict(patient_id, df_pairs, branch_table, model_mc, tokenizer_mc,
                     threshold_confidence=0.5):
    # 履歴を保持しながら遷移図を歩く（学習時と同じ state 形式）
    trace = []
    history = []  # 過去ステップの (branch_id, question, choice_text)
    current = branch_table[0]['id']
    while True:
        b = next(x for x in branch_table if x['id'] == current)
        adopted_col = f'採用ペア_{b["id"]}'
        adopted_pairs = df_pairs[(df_pairs['id'] == patient_id)
                                 & (df_pairs[adopted_col] == True)]['ペア'].tolist()
        context = build_agent_context(b, adopted_pairs, history)
        choice_texts = [render_choice_with_route(b, c) for c in b['choices']]

        pred_idx, conf = mc_agent_step(model_mc, tokenizer_mc, context, choice_texts)

        if conf < threshold_confidence:
            trace.append({'branch': b['id'], 'predicted_idx': pred_idx, 'confidence': conf,
                          'action': 'low_confidence', 'triage': 'R3'})
            return 'R3', trace

        choice = b['choices'][pred_idx]
        trace.append({'branch': b['id'], 'predicted_idx': pred_idx, 'confidence': conf,
                      'choice': choice['text'], 'action': choice['action']})
        # 履歴更新
        history.append({
            'branch_id': b['id'],
            'question': b['question'],
            'choice_text': choice['text'],
        })

        if choice['action'] in ('terminal', 'fallback'):
            return choice['triage'], trace
        # next の場合は次の分岐へ
        target = choice.get('next_id')
        if target in branch_ids:
            current = target
            continue
        else:
            # branch_table 外 → fallback
            return fallback_triage, trace
    return fallback_triage, trace


mc_results = []
for pid in all_patient_ids:
    pred_triage, trace = mc_agent_predict(pid, df_pairs, branch_table, model_mc, tokenizer_mc,
                                          threshold_confidence=threshold_confidence)
    true_triage_code = df_pairs[df_pairs['id'] == pid]['トリアージ'].iloc[0]
    mc_results.append({
        'id': pid,
        '真のトリアージ': triage_decode.get(int(true_triage_code), str(true_triage_code)),
        'モデル2予測': pred_triage,
        'trace': trace,
    })

mc_results_df = pd.DataFrame(mc_results)
display(mc_results_df[['id', '真のトリアージ', 'モデル2予測']].head(20))

# 数件だけ詳細trace
print('\n=== サンプル trace（最初の5患者）===')
for r in mc_results[:5]:
    print(f'\n[id={r["id"]}] 真={r["真のトリアージ"]} / 予測={r["モデル2予測"]}')
    for step in r['trace']:
        print('   ', step)"""))

cells.append(cell(r"""# 6. モデル3：直接BERT（遷移図を使わない baseline）

会話全体を1本の文脈として読み、**遷移図を一切使わず**に最終 triage（R2/R3/Y2）を直接 3クラス分類する単一BERT。
遷移図ベース（モデル1・2）と比較することで、「遷移図を経由する設計の利得」が見える対照モデル。

- 入力: 患者の会話ペア全部を連結
- 出力: トリアージ（0:R3, 1:R2, 2:Y2）
- 構造: `BertForSequenceClassification(num_labels=3)`""", "markdown"))

cells.append(cell(r"""## 6.1. 学習データ準備と学習""", "markdown"))

cells.append(cell(r"""# 患者ごとに「会話ペア全部の連結」を作る
direct_data = df_pairs.groupby('id').agg(
    input_text=('ペア', lambda x: ' '.join(x)),
    label=('トリアージ', 'first'),
).reset_index()
print(f'直接BERT 学習対象: {len(direct_data)} 患者')

le_direct = LabelEncoder()
direct_data['encoded'] = le_direct.fit_transform(direct_data['label'])
n_labels_direct = direct_data['encoded'].nunique()
print(f'クラス数: {n_labels_direct} / 分布: {dict(direct_data["label"].value_counts().sort_index())}')

X_d_tr, X_d_te, y_d_tr, y_d_te, id_d_tr, id_d_te = train_test_split(
    direct_data['input_text'], direct_data['encoded'], direct_data['id'],
    test_size=1/3, random_state=42,
    stratify=direct_data['encoded'] if n_labels_direct > 1 else None
)
print(f'train={len(X_d_tr)}, test={len(X_d_te)}')

tokenizer_direct = BertJapaneseTokenizer.from_pretrained('cl-tohoku/bert-base-japanese-whole-word-masking')
enc_tr = tokenizer_direct(X_d_tr.tolist(), padding='max_length', truncation=True, max_length=max_len, return_tensors='pt')
enc_te = tokenizer_direct(X_d_te.tolist(), padding='max_length', truncation=True, max_length=max_len, return_tensors='pt')

tr_ds_d = TensorDataset(enc_tr['input_ids'], enc_tr['attention_mask'], torch.tensor(y_d_tr.tolist()))
te_ds_d = TensorDataset(enc_te['input_ids'], enc_te['attention_mask'], torch.tensor(y_d_te.tolist()))
tr_dl_d = DataLoader(tr_ds_d, batch_size=4, shuffle=True)
te_dl_d = DataLoader(te_ds_d, batch_size=4, shuffle=False)

model_direct = BertForSequenceClassification.from_pretrained(
    'cl-tohoku/bert-base-japanese-whole-word-masking',
    num_labels=n_labels_direct, attn_implementation='eager'
).to(device)
optimizer_direct = AdamW(model_direct.parameters(), lr=2e-5)

for ep in range(epochs):
    tl = train_one_epoch(model_direct, tr_dl_d, optimizer_direct)
    vl, acc, f1 = evaluate_model(model_direct, te_dl_d)
    print(f'[直接BERT] Epoch {ep+1}: train_loss={tl:.4f}, test_loss={vl:.4f}, acc={acc:.4f}, f1={f1:.4f}')"""))

cells.append(cell(r"""## 6.2. 直接BERTの推論（全患者）""", "markdown"))

cells.append(cell(r"""def direct_predict(patient_id, df_pairs, model, tokenizer, le, max_len=128):
    patient_pairs = df_pairs[df_pairs['id'] == patient_id]['ペア'].tolist()
    text = ' '.join(patient_pairs)
    enc = tokenizer([text], padding='max_length', truncation=True, max_length=max_len, return_tensors='pt').to(device)
    model.eval()
    with torch.no_grad():
        logits = model(**enc).logits
    pred_idx = int(logits.argmax(-1)[0])
    pred_label = int(le.inverse_transform([pred_idx])[0])
    return triage_decode.get(pred_label, str(pred_label))

direct_results = []
for pid in all_patient_ids:
    pred = direct_predict(pid, df_pairs, model_direct, tokenizer_direct, le_direct, max_len)
    true_code = df_pairs[df_pairs['id'] == pid]['トリアージ'].iloc[0]
    direct_results.append({
        'id': pid,
        '真のトリアージ': triage_decode.get(int(true_code), str(true_code)),
        'モデル3予測': pred,
    })
direct_results_df = pd.DataFrame(direct_results)
display(direct_results_df.head(20))"""))

cells.append(cell(r"""# 7. 3モデルの比較

同じ全患者を3つの設計で解いた結果を並べる：
- **モデル1**：分岐ごとの専用BERT × 順次（greedy）traversal
- **モデル2**：単一 MC-BERT（選択肢に行き先情報を埋め込み）× 同じ traversal
- **モデル3**：直接BERT — 遷移図を使わず会話全体から triage 直接分類（baseline）""", "markdown"))

cells.append(cell(r"""compare_df = (
    seq_df[['id', '真のトリアージ', 'モデル1予測']]
    .merge(mc_results_df[['id', 'モデル2予測']], on='id')
    .merge(direct_results_df[['id', 'モデル3予測']], on='id')
)
compare_df['モデル1正解'] = compare_df['真のトリアージ'] == compare_df['モデル1予測']
compare_df['モデル2正解'] = compare_df['真のトリアージ'] == compare_df['モデル2予測']
compare_df['モデル3正解'] = compare_df['真のトリアージ'] == compare_df['モデル3予測']
display(compare_df)

m1_acc = compare_df['モデル1正解'].mean()
m2_acc = compare_df['モデル2正解'].mean()
m3_acc = compare_df['モデル3正解'].mean()
print(f'\nモデル1（全BERT 順次・遷移図あり）         Accuracy: {m1_acc:.4f}')
print(f'モデル2（選択強化BERT 単一・遷移図あり）   Accuracy: {m2_acc:.4f}')
print(f'モデル3（直接BERT 単一・遷移図なし）       Accuracy: {m3_acc:.4f}')

# triage 別の正解率
print('\n=== triage別正解率 ===')
print(compare_df.groupby('真のトリアージ')[['モデル1正解', 'モデル2正解', 'モデル3正解']].mean())"""))


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

out_path = r"c:/Users/hiyok/Desktop/Emergency_task/頭痛BERT_順次と選択強化.ipynb"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print("OK:", out_path)
print("cells:", len(cells))
