"""
age_logic.py  — 機能1: 分岐における「年齢（＋性別）との論理集合」の計算
==============================================================

protocol.yaml の分岐には年齢が絡むものが2種類ある。

【Aタイプ】回答そのものを年齢から導出できる質問
    「40歳以上ですか？」等。age>=40 なら「はい／以上」の選択肢を自動で埋める。
    対象例: chest_age_40 / common_cold_sweat_age_subquestion / syncope_age_40_subquestion

【Bタイプ】年齢(＋性別)の論理集合で “その分岐が適用されるか” が決まるゲート
    router の age_condition: 'age >= 16' / 'age < 16' / 'age >= 16 OR age unknown'
    ノード質問の (65才以上の場合) / (女性で12歳以上の場合、男性65歳以上の場合) …
    → eval_age_condition / eval_age_sex_gate / applicable_protocols で評価する。

年齢は「数値」でも「導入の年齢カテゴリ(①〜⑥)」でも受ける（parse_age）。
"""

from __future__ import annotations

import re
from typing import Optional, List, Dict, Any

# 導入プロトコルの年齢カテゴリ（PDF p.35）→ 代表年齢（下限）
AGE_CATEGORY_LOWER = {
    '1': 0, '①': 0,      # 4歳以下（乳幼児）
    '2': 5, '②': 5,      # 5-14歳（小児）
    '3': 15, '③': 15,    # 15-39歳（青年）
    '4': 40, '④': 40,    # 40-69歳（壮年）
    '5': 70, '⑤': 70,    # 70歳以上（高齢）
    '6': 85, '⑥': 85,    # 85歳以上（超高齢）
}


def parse_age(age: Any) -> Optional[int]:
    """数値/数字文字列/年齢カテゴリ(①〜⑥,1〜6)/None を、比較用の代表年齢(下限)に正規化。
       不明は None を返す。"""
    if age is None:
        return None
    if isinstance(age, (int, float)):
        return int(age)
    s = str(age).strip()
    if s in AGE_CATEGORY_LOWER:
        return AGE_CATEGORY_LOWER[s]
    m = re.search(r'\d+', s)
    return int(m.group()) if m else None


def age_ge(age: Any, n: int) -> Optional[bool]:
    a = parse_age(age)
    return None if a is None else a >= n


def age_lt(age: Any, n: int) -> Optional[bool]:
    a = parse_age(age)
    return None if a is None else a < n


# ---------------------------------------------------------------------------
# Bタイプ: router の age_condition 文字列を評価
# ---------------------------------------------------------------------------
def eval_age_condition(cond: str, age: Any) -> bool:
    """'age >= 16' / 'age < 16' / 'age >= 16 OR age unknown' を評価。
       年齢不明(None)は、'unknown' を含む条件のみ True、その他は False。"""
    if not cond:
        return True
    c = str(cond).lower()
    a = parse_age(age)
    # 'OR age unknown'
    if a is None:
        return ('unknown' in c)
    ok = False
    for m in re.finditer(r'age\s*(>=|<=|>|<|==)\s*(\d+)', c):
        op, n = m.group(1), int(m.group(2))
        val = {'>=': a >= n, '<=': a <= n, '>': a > n, '<': a < n, '==': a == n}[op]
        # 条件内は素朴に「どれか一致(OR)」で判定（このYAMLの age_condition は単一 or unknown 併記のみ）
        ok = ok or val
    return ok if re.search(r'age\s*[<>=]', c) else True


# ---------------------------------------------------------------------------
# Bタイプ(論理集合): 「(女性で12歳以上の場合、男性65歳以上の場合)」等を評価
# ---------------------------------------------------------------------------
def eval_age_sex_gate(text: str, age: Any, sex: Optional[str] = None) -> Optional[bool]:
    """質問文中の年齢(＋性別)ゲートを論理集合として評価する。
       例:「(女性で12歳以上の場合、男性65歳以上の場合)」= (女∧age>=12) ∨ (男∧age>=65)
          「(65才以上の場合)」= age>=65 ／「(40才以上)」= age>=40
       戻り: True=適用 / False=非適用 / None=判定不能(年齢不明など)。"""
    if not text:
        return True
    a = parse_age(age)
    sx = (sex or '').strip()
    # 性別つき複合（女性で N歳以上、男性 M歳以上）
    fem = re.search(r'女性?で?\s*(\d+)\s*[歳才]以上', text)
    male = re.search(r'男性?で?\s*(\d+)\s*[歳才]以上', text)
    if fem and male:
        nf, nm = int(fem.group(1)), int(male.group(1))
        if a is None:
            return None
        female = sx.startswith(('女', 'f', 'F'))
        male_ = sx.startswith(('男', 'm', 'M'))
        if female:
            return a >= nf
        if male_:
            return a >= nm
        # 性別不明 → どちらかを満たせば適用（安全側）
        return (a >= nf) or (a >= nm)
    # 単純な「N才/歳以上」ゲート
    m = re.search(r'(\d+)\s*[歳才]以上', text)
    if m:
        return age_ge(a, int(m.group(1)))
    m2 = re.search(r'(\d+)\s*[歳才]未満', text)
    if m2:
        return age_lt(a, int(m2.group(1)))
    return True


# ---------------------------------------------------------------------------
# Aタイプ: 「N歳以上ですか？」ノードの回答を年齢から導出
# ---------------------------------------------------------------------------
_AGE_QUESTION_RE = re.compile(r'(\d+)\s*[歳才]以上ですか')


def is_age_question_node(node: dict) -> Optional[int]:
    """そのノードが「N歳以上ですか？」を“直接聞いている”なら閾値Nを返す。違えば None。
       ※「(65才以上の場合) …ますか」のような“ゲート”(Bタイプ)や、たまたま選択肢に
         「以上/未満」を含む別質問は対象外。判定は質問文の「N歳以上ですか」に限定する。"""
    q = str(node.get('question', ''))
    m = _AGE_QUESTION_RE.search(q)
    return int(m.group(1)) if m else None


def derive_age_choice(node: dict, age: Any) -> Optional[str]:
    """Aタイプ age ノードの choice code を年齢から返す（age不明なら None＝答えない）。"""
    thr = is_age_question_node(node)
    if thr is None:
        return None
    ge = age_ge(age, thr)
    if ge is None:
        return None
    # 「以上/はい」側 と「未満/いいえ」側の choice code を拾う
    yes = no = None
    for c in node.get('choices', []):
        t = str(c.get('text', ''))
        if ('以上' in t) or ('はい' in t):
            yes = c.get('code')
        elif ('未満' in t) or ('いいえ' in t):
            no = c.get('code')
    return (yes if ge else no)


# ---------------------------------------------------------------------------
# 応用: 年齢で症候プロトコルを絞る / Aタイプ回答を一括投入
# ---------------------------------------------------------------------------
def applicable_protocols(routes: List[dict], age: Any) -> List[str]:
    """router.routes の age_condition を評価し、その年齢で適用可能な protocol id を返す。"""
    out = []
    for r in routes:
        cond = r.get('age_condition')
        if cond is None or eval_age_condition(cond, age):
            out.append(r['protocol'])
    return out


def augment_answers_with_age(answers: Dict[str, str], age: Any, index: Dict[str, dict]) -> Dict[str, str]:
    """graph の全ノードを見て、Aタイプの age ノード（「N歳以上ですか？」）を年齢から埋める。
       数値年齢は BERT の推測より確実なので、既存回答があっても**上書き**する。
       返り値は新しい dict（元の answers は破壊しない）。"""
    out = dict(answers)
    for nid, node in index.items():
        ch = derive_age_choice(node, age)
        if ch is not None:
            out[nid] = ch          # 年齢が権威（上書き）
    return out
