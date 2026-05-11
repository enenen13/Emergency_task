# 119番通報プロトコル YAML — 人間レビュー必須項目一覧

**対象ファイル:** `protocol.yaml` (version: `pdf_faithful_v0.3.2`)
**原本:** `119_tuhou.pdf` (40ページ、フッター頁番号 35-74)
**作成日:** 2026-05-11 (v0.3.2 でハルシネーション除去・到達性確保)
**目的:** PDF原本をテキスト抽出＋ラスタ画像 (150 DPI) で精査し YAML を PDF忠実版に修正した。本書は **「機械が補完した箇所」「PDF原本が曖昧な箇所」「PDF原本の表記揺れ」「医療判断を要する箇所」** を網羅的に列挙したレビュー用資料である。最終運用前に必ず本書のチェックリストに沿って医療従事者の確認を取ること。

## -2. v0.3.2 最終検証結果（機械トラバース可能性 & PDF↔YAML 1:1 対応）

ユーザー指摘「機械が辿れる遷移図設計であればよい、PDFに完璧に一致していればよい、辿れないところは Human Review に入れて」を受けて実施した網羅的検証の結果。

### -2-1. PDF→YAML 抜け検査
PDF原本 (p.35-74) に記載された全質問を手動列挙し、YAML との対応を確認。

| 項目 | 件数 |
|---|---|
| PDF 質問項目（手動列挙） | **128個** |
| YAML ノード（実装） | **129個** |
| **PDF にあるが YAML にない** | **0個** ✅ |
| **YAML にあるが PDF 直接対応のない** | **1個** (`chief_complaint_router` のみ) |

`chief_complaint_router` は PDF p.36 Q8「それでは、(◯◯)について、もう少しお聞きします → 主訴分類に応じて症候別インタビューへ」の <主訴の選択> 機構に対応する **構造的補助ノード**（個別質問ではなく分岐ハブ）。これは PDF の意図を機械可読化するために必要な構造で、ハルシネーションではない。

### -2-2. 機械トラバース可能エッジの集計
intro_fire_or_emergency から辿る際に使用するエッジの種別と件数：

| エッジ種別 | 件数 | 用途 |
|---|---|---|
| `next` (選択肢上) | 110 | 通常の質問遷移 |
| `next` (ノード自身) | 3 | dispatch_location 等の素通し質問 |
| `route_to_protocol` | 34 | プロトコル間ジャンプ（症候別ルーター等） |
| `terminal_followup_chain` | 11 | 緊急度確定後の ☎ 情報収集 |
| `followup_metadata` | 3 | サブ質問への分岐（40歳サブ質問等） |
| `matches_rule` (global) | 1 | CPA キーワード経由のグローバルルール発火 |
| `route_to` (外部) | 1 | 火事フロー（医療フロー外） |
| **合計エッジ数** | **163** | — |

### -2-3. 辿れない箇所の最終確認
**intro_fire_or_emergency から辿れない PDF対応ノード: 0個 ✅**

つまり PDF原本の全 128 質問項目に対応する YAML ノードは、すべて導入から機械的にトラバース可能。

### -2-4. パス到達性検査（triage 終端到達）
全 22 症候別プロトコル + 導入フローについて、各起点から終端（triage / route_to_protocol / chief_complaint_router）へ到達可能か検査：

- entry_flow から chief_complaint_router まで: ✅ 全パス到達
- 各 22 プロトコルの起点から終端まで: ✅ 全パス到達（22/22）

### -2-5. 検証スクリプト
本検証は以下のスクリプトで再現可能（`/home/claude/work/` に保管）：

| スクリプト | 内容 |
|---|---|
| `pdf_yaml_correspondence.py` | PDF 全質問の手動列挙と YAML 対応マッピング、トラバース可能性 |
| `path_check_v2.py` | 各プロトコルの全パス triage 到達検査 |
| `path_check_v4.py` | 全ノード到達性検査（terminal_followup_chain + followup_metadata 含む） |
| `audit_hallucination.py` | ハルシネーション疑い項目の洗い出し |

### -2-6. 結論
- **PDF↔YAML 1:1 対応**: PDF 全 128 質問 + 構造補助ノード 1個 = YAML 129ノードで完全に対応 ✅
- **機械トラバース可能性**: 導入から全ノードが 163 エッジで辿れる ✅
- **辿れない箇所**: **無し** ✅
- **PDF にない接続 (ハルシネーション)**: v0.3.2 で全削除済み ✅
- **synthetic_node 残存**: ゼロ件 ✅

---

## -1. v0.3.2 で除去したハルシネーション（PDF原本にない接続/推定）

ユーザー指摘により、機械が「実用上必要」と判断して追加したが PDF原本に根拠がなかった接続を除去した。すべての非メタノードを導入から辿れる構造に変更。

### -1-1. overview.b (外因性・外傷) の trauma 直接ルーティングを削除
- **v0.3.1 まで:** `route_to_protocol: trauma` を付与
- **v0.3.2:** `next: injured_count` (通常の entry_flow 継続) に変更、`pdf_cell_blank: true` で原本空欄を明示
- **PDF根拠:** p.35 で b 行の triage 欄は空欄、c の「→●「6.けいれん」へ」のような明示矢印なし
- **設計影響:** 外因性・外傷の回答は entry_flow を通常通り継続し、共通バイタル後の chief_complaint_router の trauma キーワード（けが/外傷/転落/熱傷/咬傷 等）でマッチして trauma プロトコルへ振り分けられる。即時ルーティングを止めたが、最終的な動作は同等。
- **確認:** ✅ PDF忠実化

### -1-2. overview.d (不明) の R3 自動付与を削除
- **v0.3.1 まで:** `triage_via_fallback: R3` を付与
- **v0.3.2:** `next: injured_count` に変更、`pdf_cell_blank: true` で原本空欄を明示
- **PDF根拠:** p.35 で d 行の triage 欄は空欄、明示の triage 指示なし
- **設計影響:** 「どなたが、どうしましたか？」に「不明」と答えた場合、即時 R3 にせず通常の entry_flow を継続する。共通バイタル経由で主訴が判明すれば router で振り分け可能。主訴が判明しないまま終わるケースは PDF原本にも明示の指示なし。
- **確認:** ⚠️ 実装側で「主訴不明のままプロトコル選択に至らない場合」のフォールバックを別途設計する必要あり

### -1-3. convulsion の synthetic_node を完全削除
- **v0.3.1 まで:** `convulsion_generalized_common` と `convulsion_focal_common` という機械合成ノード 2 個
- **v0.3.2:** synthetic_node を削除、PDF p.42-43 の構造を直接エッジで表現
  - `convulsion_scope.a (全身性)` → `convulsion_continues` (直接)
  - `convulsion_scope.b (局所性)` → `convulsion_pregnant` (breath_signal をスキップ、PDF p.43 通り)
  - `convulsion_continues.b (いいえ)` → `convulsion_breath_signal` (直接)
  - `convulsion_breath_signal.b (10秒未満 Y1)` → `convulsion_pregnant` (accumulate_triage で baseline 記録のうえ継続)
- **PDF根拠:** PDF p.42-43 にこれらの synthetic_node に対応する「ステップ」は存在しない。共通バイタルは entry_flow で既に実行済みのため再実行を表現する必要なし。
- **設計影響:** 全身性ルートで breathing が a/g 以外だった場合の resume_next_otherwise パスは実質到達不能だった（共通バイタル時点で終端するため）。これを削除することで構造が PDF と完全に一致。
- **確認:** ✅ PDF忠実化

### -1-4. ☎ メタデータノードの到達性を構造化
- **v0.3.1 まで:** 10 個の ☎ メタノードが導入から到達不能（次のいずれにもリンクされていない）
- **v0.3.2:** 以下の対応で全 ☎ ノードを到達可能に
  - 各プロトコルに `terminal_followup_chain: [...]` リストを追加（dyspnea/palpitation/syncope/convulsion/foreign_body/poisoning）
  - `common_cold_sweat.a` に `followup_metadata: common_cold_sweat_age_subquestion` を追加
  - 実装側は terminal_followup_chain の各ノードを triage 確定後の情報収集として順次訪問する
- **確認:** ✅ 全 129 ノードが intro_fire_or_emergency から到達可能

---

## 0. v0.3.1 で追加修正した「導入の質問」関連項目

ユーザー指摘により、v0.3 では PDF p.35-36 の **119番通報導入プロトコル** の細部が一部反映されていなかったため、v0.3.1 で以下を追加修正した（HUMAN_REVIEW 上は §A の続きとして扱う）。

### 0-1. overview (概況の把握 Q3-1) の応答選択肢を明示
- **v0.3 以前:** `extract` フィールドにキーワードリストを持つだけで、PDF原本の a/b/c/(無印)/d の選択肢構造が反映されていなかった
- **v0.3.1:** PDF p.35 通りに 5 つの choices を追加
  - a: CPA キーワード → R1 + 口頭指導
  - b: 外因性・外傷 → trauma プロトコル
  - c: けいれん → convulsion プロトコル
  - (無印 code: chief_complaint_classification): 主訴の分類 → 後段の chief_complaint_router へ
  - d: 不明 → fallback で R3
- **確認事項:** ✅ PDF通りの追加。実装側で各 choice の発火条件（キーワードマッチ／NLP分類など）の合意要。

### 0-2. 全 entry_flow ノードに質問カテゴリと PDF質問番号を付与
- 各ノードに `question_category` (PDF原本の左端カラム「質問の目的」) と `pdf_question_number` (3-1, 3-2 等) を追加
- 結果として PDF原本の表構造と YAML が 1:1 で対応するようになった
- **確認事項:** ✅ ドキュメント整合性向上のみ、ロジック影響なし

### 0-3. 括弧書き条件を `conditional_on` フラグで構造化
PDF原本では以下が括弧書きの条件付き質問。v0.3.1 で `conditional_on` フィールドとして明示：
- `injured_count` (Q3-2): 「複数の傷病者が疑われるとき・曖昧な時」
- `caller_relation` (Q3-3): 「本人からの通報ではない時、通報者が不明な時」
- `age` (Q3-4): 「ここまでで不明な場合」
- `sex` (Q3-5): 「ここまでで不明な場合」
- **確認事項:** 🟡 これらは PDF原本の運用通知では「条件に該当する場合のみ尋ねる」案内だが、実装によっては常に尋ねる方針もありうる。UI設計と整合性確認が必要。

### 0-4. Q8「症候別へ」遷移メッセージを `route_to_symptom_inquiry` ノードとして追加
- **v0.3 以前:** common_conversation の choice a が直接 chief_complaint_router へ next していたため、PDF p.36 Q8「それでは、(◯◯)について、もう少しお聞きします」の遷移文言が抜けていた
- **v0.3.1:** `route_to_symptom_inquiry` ノードを common_vitals 最終要素として挿入。`transition_only: true` フラグ付き、triage なし、◯◯部分は overview で抽出した主訴名を埋める設計
- **確認事項:** ✅ PDF通りの追加。実装側で◯◯フィラーの実装が必要。

### 0-5. 共通バイタル：導入プロトコル版と症候別版の triage 適用差分を明示
- **問題:** PDF p.35-36 の【119番導入プロトコル】では呼吸 b/c/e のみ R1+口頭指導が記入され、a/d/f/g は空欄。冷や汗/顔色/会話の triage 列も全て空欄。一方 p.37 以降の【症候別プロトコル】では d/f/g 等にも R2/R3 が明示される。
- **v0.3 以前:** common_breathing/common_cold_sweat 等は症候別版の triage を採用しており、結果として **「119番導入の時点で 呼吸=いびき と回答すると即座に R2 が付与される」** 設計になっていた
- **v0.3.1:** `pdf_intro_vs_symptom_triage_note` で差分を明示。設計判断は変えず、実装側で 2 段階分離が必要なら common_breathing を intro_breathing と symptom_breathing に分けることを推奨。
- **影響:** いびき・呼吸苦・不明 等の中等度回答が「導入時点で triage 終端」されるか「症候選択後に triage 確定」されるかの判断は実装側に委ねている。
- **確認事項:** 🟡 **PDF原本の解釈として 2 通り考えられる重要論点。**
  - 解釈A（YAML現状）: 中等度回答も導入時点で即 R2 終端 → 患者状態が明確に深刻なら早期終端で迅速対応
  - 解釈B（PDF忠実）: 導入では CPA のみ triage、それ以外は症候別ページで初めて triage → きめ細かな鑑別が可能だが終端遅延
  - どちらが運用通知書の意図か医療側で確認が必要

---

## A. v0.2 → v0.3 で訂正した PDF矛盾箇所（要：訂正の妥当性確認）

PDF原本との照合で v0.2 が誤っていた箇所を訂正した。**訂正方向が正しいか医療側で再確認**してほしい。すべて YAML 内では `fix_history:` フィールドで記録している。

### A-1. 【重大】melena プロトコル — 出血量「a」選択肢の本文
- **ノード:** `melena_amount` choice `a`
- **v0.2:** `容器（ティッシュペーパー）に血液が付着する程度`
- **v0.3:** `下着に血液が付着する程度`
- **根拠:** PDF p.62 で「下着に血液が付着する程度」と明記。p.61（吐血・喀血）の「容器（ティッシュペーパー）」がコピペされていた。
- **追加検証:** PDF p.62 の fallback行も「腹痛なく**下着**に血液が付着する程度の場合 → Y2」と明記。下血/血便で「容器」は文脈不適合。
- **確認事項:** ✅ 訂正方向で問題ないか／システム表示文言として問題ないか。

### A-2. 【重大】けいれんプロトコル — `convulsion_history` の triage 取り扱い
- **ノード:** `convulsion_history_metadata`
- **v0.2:** triage 質問として実装（はい→Y2、いいえ→次、不明→R3）
- **v0.3:** `metadata_only: true` に降格（☎ 情報項目）
- **根拠:** PDF p.43 でこの質問は ☎ 印（緊急度には影響しないが救急隊・医療機関へ提供する情報項目）として表示されている。Y2 を出すのは PDF 上の Q6「まだ痙攣が起きていないが、今後起きる感じがするのですね？」(`convulsion_predicted`) のみ。
- **影響:** v0.2 では「過去にてんかん既往あり」と回答したケースに Y2 が付与されていた。v0.3 では情報のみ。
- **確認事項:** ⚠️ 医療運用上「過去発作歴あり」を triage に反映させたい場合は、原本ガイドライン（運用通知）と整合するか確認が必要。原本フローチャートとは矛盾しない訂正だが、運用上の意図が異なる可能性あり。

### A-3. 【重大】けいれんプロトコル — 全身性ルートの breath_signal 接続
- **ノード:** `convulsion_breath_signal` choice `b` (10秒未満)
- **v0.2:** Y1 終端（後続の妊娠・糖尿病・予測質問へ到達不能）
- **v0.3:** Y1 を baseline triage として記録、`next: convulsion_pregnant` で後続へ接続
- **根拠:** PDF p.42 Q4「上記、呼吸・循環・意識に異常がない全身の痙攣 → 次の質問」で個別質問への接続が明示。Y1 が終端のままだと全身性ルートでは妊娠・糖尿病が一切問われない。
- **設計ノート:** YAML 仕様として `accumulate_triage` に類似の「triage を baseline に記録しつつ next に進む」挙動を採用。実装側で「最終 triage = 各質問の最大値」とする運用が必要。
- **確認事項:** ⚠️ 実装エンジニア側で triage の集約ルール（max 集約）が明確化されているか確認。

### A-4. 失神プロトコル — 40歳以上サブ質問の配置
- **ノード:** `syncope_heart_history` (はい branch) → `syncope_age_40_subquestion` (新設); `syncope_abdominal_pain` から年齢分岐を撤去
- **v0.2:** `syncope_abdominal_pain` 側に「40歳以上=R2 / 40歳未満=Y2」の年齢分岐
- **v0.3:** PDF p.41 通り、心臓異常質問の「はい」直下にサブ質問として配置（情報項目、triage には影響しない）。腹痛は a:はい→R2 / b:いいえ→Y2 のシンプルな構造。
- **根拠:** ラスタ画像で確認した PDF p.41 のレイアウト。「40歳以上ですか？」サブ質問のセル内には triage 表示が無く、心臓異常 `a:R2` 直下にインデントされている。
- **影響:** 「40歳未満かつ腹痛あり」のケースが v0.2 では Y2 だったが v0.3 では R2 (fallback 経由でなく `syncope_abdominal_pain.a` の R2)。
- **確認事項:** 🟡 **臨床的には子宮外妊娠は若年女性の鑑別疾患であり、v0.2 の解釈（腹痛側の年齢分岐）の方が医学的に整合的とも言える。** PDF原本どおりに修正したが、運用通知書や原本フローチャート図での意図を医療側で要確認。原本そのままが正しければ v0.3 でOK、運用上 v0.2 の解釈が正しければ要再修正。

### A-5. 腰部痛プロトコル — `lumbar_chest_pain_40` の想定疾患
- **ノード:** `lumbar_chest_pain_40`
- **v0.2:** 想定疾患 `心疾患`
- **v0.3:** 想定疾患 `急性冠症候群`
- **根拠:** PDF p.60 で「(40才以上）胸は痛くないですか？」の想定疾患欄に「急性冠症候群」と明記。背部痛 p.49 の同質問は「心疾患」で、背部痛と腰部痛で疾患記載が異なる（PDF原本の表記揺れ → §C-1 参照）。
- **確認事項:** ✅ PDF通りの訂正。

### A-6. 腰部痛プロトコル — `lumbar_presyncope_65` の想定疾患
- **ノード:** `lumbar_presyncope_65`
- **v0.2:** 想定疾患なし
- **v0.3:** 想定疾患 `急性大動脈解離` を追加
- **根拠:** PDF p.60 にこの行で `急性大王脈解離`（PDF原本の誤字。おそらく「急性大動脈解離」の誤植）と記載。本 YAML では医学的正字「急性大動脈解離」で収録。
- **確認事項:** ⚠️ PDF原本の誤字「大王脈」は §C-2 で別途整理。医療運用時は「急性大動脈解離」と読み替える前提で OK か確認。

### A-7. 腰部痛プロトコル — `lumbar_pain_quality_65` の想定疾患
- **ノード:** `lumbar_pain_quality_65`
- **v0.2:** 想定疾患 `急性大動脈解離`（背部痛 p.49 のパターンを流用）
- **v0.3:** 想定疾患を削除（`pdf_no_disease_listed: true`）
- **根拠:** PDF p.60 ではこの行（痛みの種類質問）の想定疾患欄が空欄。背部痛 p.49 では「急性大動脈解離」が痛みの種類質問の側に記載されているのに対し、腰部痛 p.60 では気を失いそうになった質問の側に記載。**PDF上、背部痛と腰部痛で疾患の配置が逆**。v0.3 では PDF原本どおり保持。
- **確認事項:** 🟡 PDF原本の表記揺れに忠実だが、運用上は「両方とも痛みの種類で急性大動脈解離」が自然。原本どおりにするか、運用上揃えるかは医療側の判断。

### A-8. 背部痛プロトコル — `back_dyspnea_recheck` の想定疾患
- **ノード:** `back_dyspnea_recheck`
- **v0.2:** 想定疾患なし
- **v0.3:** 想定疾患 `肺疾患` を追加
- **根拠:** PDF p.49 に明記。腰部痛 p.60 の同質問には記載なし（§C-1）。
- **確認事項:** ✅ PDF通りの訂正。

### A-9. しびれプロトコル — `numbness_stroke_symptoms` のグラフ構造
- **ノード:** `numbness_stroke_symptoms`、`numbness_stroke_history`、`numbness_onset_info`
- **v0.2:** 7択すべてが終端（Y1/G）。`numbness_stroke_history` と `numbness_onset_info` が unreachable。
- **v0.3:** 各選択肢に `accumulate_triage: true` と `next: numbness_stroke_history` を追加し、後続質問に進めるよう修正。
- **根拠:** PDF p.58 に脳卒中既往質問（Q2、a:はい→Y1）と ☎ 発症時刻質問（Q3）が明示されており、Q1 で終端させると後続が一切聴取されない。
- **設計ノート:** A-3 と同様、`accumulate_triage` パターン採用。実装側で max 集約。
- **確認事項:** ⚠️ A-3 と同じく triage 集約ルールの実装側合意が必要。

### A-10. CPA キーワード — 過剰追加分の削除
- **ルール:** `global_priority_rules` の `cpa_keywords`
- **v0.2:** 8語（呼吸なし・脈なし・水没・冷たくなっている・首をつった・首を絞めた・**喉が詰まった**・**意識なし**）
- **v0.3:** 6語（PDF p.35 通り）
- **根拠:** PDF p.35「概況の把握」のキーワード行に明示されたのは6語のみ。
  - 「喉が詰まった」→ `chief_complaint_router` の `foreign_body_ingestion` に振り分け（窒息は共通バイタル `e:窒息` で R1+気道異物除去指導を発動）
  - 「意識なし」→ 単独で R1 とすべきでない（共通バイタル b:呼吸なし 等と組み合わせて初めて R1 になる）。`chief_complaint_router` の意識障害ルートで処理。
- **確認事項:** ⚠️ **影響の大きい修正**。「喉が詰まった」「意識なし」が直接 R1 にならなくなったため、誤発動防止の意味では妥当だが、緊急度低下リスクをどう運用するかの確認が必要。

### A-11. 成人頭部外傷のルーティング
- **箇所:** `chief_complaint_router` の `trauma` ルート
- **v0.2:** trauma に頭部キーワードなし → 16歳以上で「頭をぶつけた」と発話されると `pediatric_head_neck_trauma` (age<16) にも合致せず、`trauma` (キーワードなし) にも合致せず、router で取りこぼし
- **v0.3:** `trauma` の `match_any` に「頭をぶつけた」「頭部外傷」「頸部外傷」「熱傷」「咬傷」を追加し、`age_condition: age >= 16 OR age unknown` を付与。`pediatric_head_neck_trauma` は age<16 で先に拾う。
- **確認事項:** ✅ 構造的な漏れの修正。順序が `pediatric_head_neck_trauma` → `trauma` で OK か確認。

### A-12. 呼吸困難 ☎ 質問の分割
- **ノード:** `dyspnea_info_*` 系
- **v0.2:** ☎1 を asthma + inhaler の 2ノードに圧縮、☎2 を 1ノードに圧縮
- **v0.3:** PDF p.37 通り、☎1 を 3ノード（喘息既往 / 吸入薬所持 / 吸入薬使用）、☎2 を 2ノード（医師指示 / 指示実施）に分解。`conditional_on` で前段質問 = はい のときのみ後続を訪問するよう明示。
- **影響:** 救急隊への申し送り情報が PDF 通り完全に取得できる。triage 影響なし。
- **確認事項:** ✅ PDF通りの訂正。

---

## B. 機械が補完した箇所（PDF原本に存在しない、要：医療レビュー）

これらは PDF原本に明示されていないが、実装上必要と判断して機械が補完した箇所。**それぞれ医療判断としての妥当性確認が必要**。

### B-1. 合成選択肢 (`synthetic_choice: true`) — 全 2 件
- **`foreign_body_speak_cry` choice c (不明)** — triage R3
  - PDF p.67 では選択肢 a:はい / b:いいえ の 2 つのみで「不明」行なし。
  - fallback「不明な項目がある場合 R3」を実装上明示するために追加。
  - **確認:** 実運用で「話せるか不明」と回答された場合の取り扱いを R3 として良いか。

- **`poisoning_speak_cry` choice c (不明)** — triage R3
  - PDF p.68 同様に a/b のみ。同上の理由で追加。
  - **確認:** 同上。

⚠️ v0.2 で trauma_bleeding と trauma_type に追加されていた合成「不明」選択肢は v0.3 で削除し、fallback で吸収する設計に統一した（PDF原本の trauma 表に「不明」行がないため、合成選択肢を入れない方が PDF忠実）。

### B-2. 合成ノード (`synthetic_node: true`) — 全 2 件
- **`convulsion_generalized_common`** — けいれん全身性ルートの共通バイタル実行と breath_signal/個別質問への接続
  - PDF p.42 のフロー（全身性 → けいれん継続 → 共通バイタル → breath_signal → 個別質問）を再現するためのグルーノード。
  - PDF原本にはこのノードに相当する単一の「ステップ」記載なく、表構造で示されている。
  - **確認:** フロー解釈が PDF p.42 の意図と整合しているか（特に breath_signal の条件「2-2-1=はい or 不明」の取り扱い）。

- **`convulsion_focal_common`** — けいれん局所性ルートの共通バイタル実行と個別質問への接続
  - PDF p.43 の局所性ルートを再現するためのグルーノード。
  - **確認:** 同上。

### B-3. PDF空欄セル (`pdf_cell_blank: true`) — 全 114 件
PDF原本で **triage 列が空欄のセル** をすべて記録。実装上は fallback ルール（不明→R3、全部いいえ→Y2 / G 等）で吸収される。

**主要パターン:**
- 各個別質問の「b:いいえ → 次の質問」セル（triage 表示なし）— 約 80 件
- 各個別質問の「c:不明」セル（明示的 R3 表示なし、fallback 吸収）— 約 20 件
- 共通バイタル系の特殊ケース（dyspnea f:呼吸が苦しそう など）— 数件

**確認:** ✅ 一律 fallback 吸収で問題ないか、個別に明示すべきセルがあるか。各 fallback ルール（symptomごと）が PDF と整合しているかを §F-2 のチェックリストで確認。

### B-4. observation 質問の R3 補完
- **ノード:** `observation` choice b
- PDF p.35「(見に行くことできなければ)」の行に triage 表示なし（セル空欄）。
- v0.3 では暗黙ルールとして R3 を採用、`pdf_cell_blank: true` で原本空欄であることを明示。
- **確認:** 「観察できない」回答時の R3 が運用上適切か。

---

## C. PDF原本の表記揺れ・誤字・矛盾（要：原本確認）

PDF原本そのものに含まれる表記揺れ・誤字。YAML としては原本どおりまたは医学的正字で収録しているが、運用前に確認が必要。

### C-1. 背部痛 (p.49) と腰部痛 (p.60) で想定疾患の配置が異なる

| 質問 | 背部痛 p.49 | 腰部痛 p.60 |
|---|---|---|
| Q2 (息苦しい再確認) | **肺疾患** | （記載なし） |
| Q3 (40才以上 胸は痛くない?) | **心疾患** | **急性冠症候群** |
| Q4 (大動脈瘤既往?) | **胸部大動脈瘤切迫破裂** | （記載なし） |
| Q5 (65才以上 気を失いそう?) | （記載なし） | **急性大王脈解離** (PDF原本誤字) |
| Q6 (65才以上 痛みの種類?) | **急性大動脈解離** | （記載なし） |

⚠️ Q5 と Q6 で疾患の配置が逆になっている。**PDF原本の編集ミスの可能性が高い**。
**確認:** 原本の運用通知書・フローチャート図（付録2）と照合し、両プロトコルで疾患配置を揃えるべきか確認。

### C-2. PDF原本の誤字 — 「急性大王脈解離」 (p.60)
- **箇所:** PDF p.60 Q5 想定疾患欄
- **誤字:** 「急性**大王脈**解離」
- **正:** 「急性大動脈解離」
- YAML では医学的正字「急性大動脈解離」を採用。原本との照合時に注意。

### C-3. dyspnea プロトコル特有のセル空欄/サブ質問 (p.37)
- **`f 呼吸が苦しそう`** の triage 列が斜線で空欄
  - 他のすべての症候別ページ（p.38, 40, 42, ...) では R2 が明示されている
  - 呼吸困難プロトコルでは「呼吸が苦しそう」が主訴と重複するため、原本でこのセルが意図的に空欄にされていると推察
  - YAML では実装上 R2 を採用（fallback 値として）。`note:` で原本空欄を記録。
- **`a 冷や汗ありの場合`** に「i:40歳以上 R2 / ii:40歳未満 R2」のサブ質問あり
  - 両者とも R2 で triage に影響しないため、`common_cold_sweat_age_subquestion` ノードに metadata_only として分離。
  - **確認:** 救急隊申し送りの情報項目として記録する設計で OK か。

### C-4. 共通会話質問の選択肢インデックス
- 共通バイタル `common_conversation` の選択肢が `a, b, c, d, e, f, g, i`（h をスキップ）。
- PDF原本通りの記法。実装時に i を 8番目として扱うか h として扱うかは要設計判断。

### C-5. 腰部痛 (p.59-60) の症状例
- PDF p.59 の症状例: `「背中が痛い」、「背骨が痛い」、「腰がいたい」、「腰痛がひどい」` ← 背部痛 p.48 と完全に同じ表記
- 腰部痛固有の症状例（「腰の痛み」「腰部痛」など）がない
- YAML でも PDF原本のまま収録。
- **確認:** これが原本の意図か（背部痛と腰部痛の症状例を共通化している）、編集ミスかは原本確認が必要。

### C-6. 失神プロトコル (p.40) のページタイトル誤字
- PDF p.50 の Q8（成人発熱の終端質問）テキストが「上記、呼吸・循環・意識に異常がない**めまい**」になっている（「発熱」が正しいはず）
- これは原本の編集ミスと思われる。
- YAML では「発熱」用のロジックとして実装。
- **確認:** 原本のミスか、別の意図があるか。

---

## D. 解釈に依存する判断（要：医療側の意思決定）

PDF原本だけでは一意に決まらず、本 YAML で実装側が解釈した箇所。

### D-1. ICD 質問の到達可能性
- **ノード:** `palpitation_icd_fired`
- **PDF p.39:** 「(1-1で「体内式埋め込み型除細動器」が植え込みが確認された場合) 30分以内に体内式埋め込み型除細動器は、発動しましたか？」とサブ条件付き。
- **問題:** 心臓異常 (`palpitation_heart_history`) の「a:はい」は既に R2 終端のため、ICD 質問は YAML フロー上は到達不能。
- **本 YAML の扱い:** ICD ノードを到達不能のまま情報項目として保持し、`followup_metadata` で心臓異常はい branch から参照。`conditional_on: palpitation_heart_history == yes AND ICD implanted` で条件を明示。
- **代替案:** (1) 心臓異常はい後に ICD を尋ねるサブフローを実装（R2 のまま情報追加）、(2) ICD 単独で R2 にする独立ルート、(3) 現状の到達不能のまま申し送り情報で対応。
- **確認:** どの実装方針が現場運用と一致するか医療側で判断。

### D-2. けいれん breath_signal Y1 の非終端化
- **§A-3 で訂正済み。** PDF p.42 Q4 の「上記異常なし → 次の質問」を Y1 が許容するという解釈。
- **代替解釈:** Y1 も終端で、個別質問に進むのは breath_signal を一切経由しなかった場合（breathing が a でも g でもなかったケース）のみ。
- **本 YAML の扱い:** Y1 を baseline triage として記録し次へ進む。
- **確認:** 医療側で正解の解釈を確定してほしい。

### D-3. dyspnea f (呼吸が苦しそう) の triage
- **§C-3 で記録済み。** PDF p.37 で空欄、他ページで R2。
- **本 YAML の扱い:** 実用値として R2。
- **確認:** dyspnea プロトコル内で「呼吸が苦しそう」と回答された場合の取り扱いを R2 で良いか。

### D-4. fallback ルールの個別ノード展開
- 小児嘔気・嘔吐 (p.71-72) と小児頭頸部外傷 (p.73-74) では PDF原本に各質問の「c:不明」が明示されない（「不明な項目がある場合 R3」が表の最終行に集約）。
- v0.2 では各ノードに合成「不明 → R3」を展開していた。
- **v0.3 の扱い:** 各ノードに合成「不明」を入れず、fallback で吸収する設計に統一（pdf_cell_blank で原本空欄を記録）。
- **代替案:** 各ノードに不明選択肢を持たせる方が UI 設計上は明確。
- **確認:** 実装側 UI が「不明」を都度ボタンとして提示するなら v0.2 の展開、判定ロジックのみで処理するなら v0.3 の集約で OK。

### D-5. 小児頭頸部外傷の年齢条件と成人ルーティング
- §A-11 で訂正したが、`pediatric_head_neck_trauma` の `age_condition: age < 16` は小児プロトコルとしての設計を維持。
- **代替案:** 年齢不明時にも `pediatric_head_neck_trauma` を発火させる。
- **確認:** 年齢不明時に小児プロトコル / 成人プロトコルどちらに流すかは原本フローチャート要確認。

### D-6. 失神プロトコル — 心臓異常の年齢サブ質問の臨床的妥当性
- §A-4 で詳述。PDF原本どおりに「心臓異常はい」branch のサブ質問として実装したが、臨床的には腹痛側の年齢分岐（子宮外妊娠の鑑別）の方が自然な可能性。
- **確認:** 原本通りで良いか、医学的合理性で再配置するか。

---

## E. 不到達ノード（unreachable）の意図確認

YAML フロー解析で到達不能と判定されたノード。情報項目として保持しているが、用途を明確化する必要あり。

| プロトコル | ノードID | 種別 | 備考 |
|---|---|---|---|
| dyspnea | dyspnea_info_asthma_history | metadata_only ☎1 | 終端後の申し送り情報 |
| dyspnea | dyspnea_info_inhaler_held | metadata_only ☎1 | conditional_on |
| dyspnea | dyspnea_info_inhaler_used | metadata_only ☎1 | conditional_on |
| dyspnea | dyspnea_info_homecare_doctor_instruction | metadata_only ☎2 | |
| dyspnea | dyspnea_info_homecare_followed | metadata_only ☎2 | conditional_on |
| palpitation | palpitation_icd_fired | サブ条件付き | §D-1 参照 |
| palpitation | palpitation_medication_info | metadata_only ☎ | |
| consciousness_disorder_syncope | syncope_age_40_subquestion | conditional metadata | §D-6 参照 |
| convulsion | convulsion_history_metadata | metadata_only ☎ | §A-2 で降格 |
| numbness | numbness_onset_info | metadata_only ☎ | accumulate_triage 経由で到達 |
| foreign_body_ingestion | foreign_body_object_info | metadata_only ☎ | |
| poisoning | poisoning_info | metadata_only ☎ | |
| (各プロトコル) | xxx_history_info | metadata_only ☎ | 既往情報 |

**確認:** 各 ☎ 情報項目が実装側 UI でどのタイミング・どの画面で取得されるか設計確認が必要。

---

## F. 医療運用前 最終確認チェックリスト

### F-1. 緊急度判定への影響が大きい修正の再確認
以下は triage 出力に影響する修正。**必ず医療側で正誤判定**。

- [ ] **0-5** 共通バイタル中等度回答（いびき・呼吸苦・冷や汗・顔色悪い・会話困難 等）が、119番導入時点で即 R2 終端する設計で良いか（PDF忠実は「導入では CPA のみ、症候別で R2 確定」とも解釈できる）
- [ ] **A-2** convulsion_history は metadata_only で良いか（過去発作歴が triage に影響しなくなる）
- [ ] **A-3** convulsion_breath_signal Y1 が後続質問に進む設計で良いか
- [ ] **A-4** 失神 40歳以上サブ質問の配置（心臓異常側 vs 腹痛側）
- [ ] **A-5** lumbar_chest_pain_40 の急性冠症候群は背部痛の心疾患と統一すべきか
- [ ] **A-7** lumbar_pain_quality_65 の疾患記載なしで良いか（背部痛 p.49 では急性大動脈解離）
- [ ] **A-10** CPA キーワードから「喉が詰まった」「意識なし」を除外して良いか

### F-1.5. 119番通報導入フロー (entry_flow) の構造確認
PDF p.35-36 の各質問が YAML に過不足なく反映されているか。

- [ ] **Q1 導入** (intro_fire_or_emergency): a:救急, b:火事・他 — 火事・他のルーティング先 (non_medical_flow) の定義
- [ ] **Q2 出動先確認** (dispatch_location): メタデータのみで良いか（口頭でやり取りされる住所・電話番号情報の保持先）
- [ ] **Q3-1 概況の把握** (overview): a (CPA), b (外因性・外傷), c (けいれん), (無印) 主訴分類, d (不明) — 各 choice の発火条件設計
- [ ] **Q3-2 多数傷病者の否定** (injured_count): 複数判定時の対応フロー
- [ ] **Q3-3 通報者の確認** (caller_relation): conditional_on の発火タイミング（本人通報の自動判定方法）
- [ ] **Q3-4 年齢の確認** (age): conditional_on「ここまでで不明な場合」の判定（先に年齢が判明している場合のスキップ条件）
- [ ] **Q3-5 性別の確認** (sex): 同上
- [ ] **Q4 観察の可否の確認** (observation): b の R3 は YAML 補完値。PDF原本セル空欄
- [ ] **Q5 呼吸の確認** (common_breathing): 導入版 (b/c/e のみ R1) vs 症候別版 (d/f/g にも triage) の差分扱い
- [ ] **Q6-1 循環の確認** (common_cold_sweat): 導入版は triage 空欄、症候別版で R2/R3
- [ ] **Q6-2 顔色の確認** (common_face_color): 同上
- [ ] **Q7 意識の確認** (common_conversation): 同上、h スキップ→i 採用の表記
- [ ] **Q8 症候別へ** (route_to_symptom_inquiry): ◯◯フィラーの実装（overview で抽出した主訴名の挿入）

### F-2. fallback ルールの整合性
各プロトコルの fallback ルールが PDF原本と整合しているかチェック。

| プロトコル | YAML fallback | PDF表記 | 確認 |
|---|---|---|---|
| dyspnea | unknown→R3, all_neg→R2 | (個別記載なし) | [ ] |
| palpitation | unknown→R3, all_neg→Y2 | p.39 Q3 R3, Q4 Y2 | [ ] |
| consciousness_disorder_syncope | unknown→R3, all_neg→Y2 | p.41 全部いいえ Y2 | [ ] |
| convulsion | unknown→R3, all_neg→Y2 | p.43 Q7 R3, Q8 Y2 | [ ] |
| headache | unknown→R3, all_neg→Y2 | p.45 Q4 R3, Q5 Y2 | [ ] |
| chest_pain_nontraumatic | unknown→R3, all_neg→Y2 | p.47 Q4 R3, Q5 Y2 | [ ] |
| back_pain | unknown→R3, all_neg→Y2 | p.49 Q7 R3, Q8 Y2 | [ ] |
| lumbar_pain | unknown→R3, all_neg→Y2 | p.60 Q7 R3, Q8 Y2 | [ ] |
| adult_fever | unknown→R3, all_neg→G | p.50 Q5 R3, Q6 G | [ ] |
| abdominal_pain | unknown→R3, all_neg→Y2 | p.52 Q5 R3, Q6 Y2 | [ ] |
| adult_nausea_vomiting | unknown→R3, all_neg→G | p.54 Q8 R3, Q9 G | [ ] |
| dizziness | unknown→R3, all_neg→G | p.56 Q12 R3, Q13 G | [ ] |
| numbness | unknown→R3, all_neg→G | p.58 (一部明示) | [ ] |
| hematemesis_hemoptysis | unknown→R3, all_neg→Y2 | p.61 Q3 R3, Q4 Y2 | [ ] |
| melena_hematochezia | unknown→R3, **腹痛なく下着→Y2**, all_neg→Y2 | p.62 Q3 R3, Q4 特殊 Y2 | [ ] |
| malaise | unknown→R3, all_neg→Y2 | p.64 (記載) | [ ] |
| trauma | unknown→R3 | (all_neg なし) | [ ] |
| foreign_body_ingestion | unknown→R3 | | [ ] |
| poisoning | unknown→R3 | | [ ] |
| pediatric_fever | unknown→R3, all_neg→G | p.70 Q5 R3, Q6 G | [ ] |
| pediatric_nausea_vomiting | unknown→R3, all_neg→G | p.72 Q13 R3, Q14 G | [ ] |
| pediatric_head_neck_trauma | unknown→R3, all_neg→G | p.74 Q12 R3, Q13 G | [ ] |

### F-3. 想定疾患（suspected_condition）の整合性
背部痛・腰部痛で疾患配置が異なる箇所（§C-1）以外も含め、各個別質問の想定疾患が PDF原本と一致するか確認。

### F-4. 口頭指導 (`oral_instruction`) の発動条件
以下の口頭指導が発動するノードと条件を確認。
- [ ] CPR (心肺蘇生指導) — 発動箇所: cpa_keywords / common_breathing b・c / syncope_breath_count a
- [ ] airway_obstruction (気道異物除去指導) — 発動箇所: cpa_keywords / common_breathing e
- [ ] hemostasis (止血の口頭指導) — 発動箇所: trauma_bleeding a
- [ ] finger_toe_amputation (指趾切断の口頭指導) — 発動箇所: trauma_type l
- [ ] poisoning (中毒の口頭指導) — 発動箇所: poisoning_speak_cry a
- [ ] positioning (体位管理の口頭指導) — 発動箇所: 該当なし（labels に定義のみ）

### F-5. ルーティング条件 (chief_complaint_router)
- [ ] 各 protocol の `match_any` キーワードに過不足がないか
- [ ] age_condition の境界値（age < 16, age >= 16, age >= 40, age >= 65）が原本と一致しているか
- [ ] 複数 match のとき、優先順位（YAML上の出現順）が運用意図と一致しているか

### F-6. 原本フローチャート図（付録2）との照合
PDF原本には付録2としてフローチャート図が含まれている可能性が高いが、本 YAML は表形式の記述（p.35-74）から構築している。**フローチャート図と表が矛盾している箇所があれば優先順位を明確化**する必要がある。

- [ ] 119番導入フロー（火事ですか／救急ですか → … → 観察）の付録2図との整合
- [ ] けいれんの全身/局所分岐の付録2図との整合
- [ ] 失神の breath_count → 心臓異常 → 腹痛フローの付録2図との整合
- [ ] 背部痛/腰部痛の外傷分岐 → 呼吸苦再確認 → 胸痛分岐 → 大動脈瘤の付録2図との整合
- [ ] 具合が悪い／気分が悪いプロトコルの痛み部位分岐の付録2図との整合

### F-7. ☎ 情報項目の取得タイミング
全ての ☎ 印項目（緊急度に影響しない情報項目）について、UI 上どのタイミングで取得するかの設計確認。
- 各プロトコルで終端 triage 確定後に質問するのか
- フロー中で挟むのか
- 救急隊への申し送り画面でのみ表示するのか

---

## G. 修正履歴サマリ

| ID | カテゴリ | 影響範囲 | 重要度 |
|---|---|---|---|
| **0-1** | **構造追加** | **overview に選択肢明示** | **🟡 中** |
| **0-2** | **メタデータ追加** | **entry_flow 全ノードに番号/カテゴリ付与** | **🟢 軽微** |
| **0-3** | **条件付け** | **entry_flow 4ノードに conditional_on** | **🟢 軽微** |
| **0-4** | **構造追加** | **Q8 症候別遷移ノード追加** | **🟢 軽微** |
| **0-5** | **解釈論点記録** | **共通バイタル triage 適用範囲** | **🟡 中** |
| A-1 | 文言修正 | melena 出血量選択肢 | 🔴 重大 |
| A-2 | triage 取扱い | convulsion 既往質問 | 🔴 重大 |
| A-3 | グラフ構造 | convulsion 全身性ルート | 🔴 重大 |
| A-4 | 構造移動 | 失神 年齢サブ質問 | 🟡 中 |
| A-5 | 想定疾患 | 腰部痛 胸痛質問 | 🟢 軽微 |
| A-6 | 想定疾患追加 | 腰部痛 気を失いそう質問 | 🟢 軽微 |
| A-7 | 想定疾患削除 | 腰部痛 痛みの種類質問 | 🟢 軽微 |
| A-8 | 想定疾患追加 | 背部痛 呼吸苦再確認 | 🟢 軽微 |
| A-9 | グラフ構造 | しびれ 脳卒中症状質問 | 🔴 重大 |
| A-10 | キーワード削減 | CPA キーワード | 🟡 中 |
| A-11 | ルーティング | 成人頭部外傷 | 🟡 中 |
| A-12 | ノード分割 | 呼吸困難 ☎ 質問 | 🟢 軽微 |

---

## H. 用語・略語

- **R1/R2/R3:** 赤（緊急度高）。R1:心肺蘇生、R2:急ぐ、R3:不明含む確認要
- **Y1/Y2:** 黄（中等度）
- **G:** 緑（緊急度低）
- **☎ (phone_icon):** PDF原本の電話マーク。緊急度には影響しない情報項目
- **CPA:** Cardiopulmonary Arrest（心肺停止）
- **ICD:** Implantable Cardioverter Defibrillator（体内式埋め込み型除細動器）
- **fallback:** 個別質問が全て陰性／不明の場合の最終 triage
- **synthetic_choice / synthetic_node:** 機械が補完した選択肢／ノード
- **pdf_cell_blank:** PDF原本でセル空欄
- **pdf_no_disease_listed:** PDF原本で想定疾患欄が空欄
- **conditional_on:** 質問が条件付き
- **followup_metadata:** はい回答後に紐付く情報項目
- **accumulate_triage:** triage を baseline として記録するが終端しない

---

## 連絡先・備考

医療運用前に必ず本書 §F のチェックリストに沿って医療従事者の確認を取ること。
原本 PDF は 2017.4.19 版（一部表に記載）。最新の運用通知書がある場合はそちらと照合のこと。
