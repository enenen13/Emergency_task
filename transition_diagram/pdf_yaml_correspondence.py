"""
最終検証:
1. PDF p.35-74 の全ての質問が YAML に存在するか（PDF→YAML 抜け検査）
2. YAML の全ノードが PDF に対応するか（YAML→PDF 余剰検査）
3. 機械トラバース可能エッジで全ノードが intro から到達可能か
4. 辿れない箇所のリスト
"""
import yaml

with open("/home/claude/work/protocol.yaml", encoding="utf-8") as f:
    d = yaml.safe_load(f)

# === PDF 全質問インベントリ（手動列挙） ===
# 各 PDF ページに記載された質問項目を、YAML の id と対応付けて列挙
pdf_questions = {
    "p.35 Intro Q1 火事/救急": "intro_fire_or_emergency",
    "p.35 Intro Q2 出動先確認": "dispatch_location",
    "p.35 Intro Q3-1 概況の把握": "overview",
    "p.35 Intro Q3-2 何人": "injured_count",
    "p.35 Intro Q3-3 通報者": "caller_relation",
    "p.35 Intro Q3-4 年齢": "age",
    "p.35 Intro Q3-5 性別": "sex",
    "p.35 Intro Q4 観察可否": "observation",
    "p.35-36 Intro Q5 呼吸": "common_breathing",
    "p.36 Intro Q6-1 冷や汗": "common_cold_sweat",
    "p.37 dyspnea Q6 冷や汗40歳サブ": "common_cold_sweat_age_subquestion",
    "p.36 Intro Q6-2 顔色": "common_face_color",
    "p.36 Intro Q7 普通に話し": "common_conversation",
    "p.36 Intro Q8 症候別へ": "route_to_symptom_inquiry",
    "p.37 dyspnea Q8 上記異常なし": "dyspnea_terminal",
    "p.37 dyspnea ☎1-1 喘息既往": "dyspnea_info_asthma_history",
    "p.37 dyspnea ☎1-2 吸入薬所持": "dyspnea_info_inhaler_held",
    "p.37 dyspnea ☎1-3 吸入薬使用": "dyspnea_info_inhaler_used",
    "p.37 dyspnea ☎2-1 在宅指示": "dyspnea_info_homecare_doctor_instruction",
    "p.37 dyspnea ☎2-2 在宅実施": "dyspnea_info_homecare_followed",
    "p.39 palpitation Q1 心臓異常": "palpitation_heart_history",
    "p.39 palpitation Q2 ICD": "palpitation_icd_fired",
    "p.39 palpitation Q3 胸痛": "palpitation_chest_pain",
    "p.39 palpitation ☎ 薬剤": "palpitation_medication_info",
    "p.41 syncope Q1 呼吸合図": "syncope_breath_count",
    "p.41 syncope Q2 心臓異常": "syncope_heart_history",
    "p.41 syncope Q2-sub 40歳以上": "syncope_age_40_subquestion",
    "p.41 syncope Q3 腹痛": "syncope_abdominal_pain",
    "p.42 convulsion Q1 範囲": "convulsion_scope",
    "p.42 convulsion Q2 続いている": "convulsion_continues",
    "p.42 convulsion Q3 呼吸合図": "convulsion_breath_signal",
    "p.43 convulsion Q4 妊娠": "convulsion_pregnant",
    "p.43 convulsion Q5 糖尿病": "convulsion_diabetes",
    "p.43 convulsion Q6 予測": "convulsion_predicted",
    "p.43 convulsion ☎ 既往": "convulsion_history_metadata",
    "p.45 headache Q1 突然激痛": "headache_sudden_severe",
    "p.45 headache Q2 しびれ麻痺": "headache_numbness_paralysis",
    "p.45 headache Q3 振る舞い": "headache_abnormal_behavior",
    "p.45 headache ☎ 発症時刻": "headache_onset_info",
    "p.47 chest_pain Q1 吐き気": "chest_nausea_vomiting",
    "p.47 chest_pain Q2 心筋梗塞既往": "chest_mi_angina_history",
    "p.47 chest_pain Q3 同様の痛み": "chest_recurrent_pain",
    "p.47 chest_pain Q4 40歳以上": "chest_age_40",
    "p.49 back_pain Q1 外傷": "back_trauma",
    "p.49 back_pain Q2 呼吸苦再確認": "back_dyspnea_recheck",
    "p.49 back_pain Q3 胸痛40歳以上": "back_chest_pain_40",
    "p.49 back_pain Q4 大動脈瘤": "back_aortic_aneurysm_history",
    "p.49 back_pain Q5 65歳以上失神": "back_presyncope_65",
    "p.49 back_pain Q6 65歳以上痛みの種類": "back_pain_quality_65",
    "p.50 adult_fever Q1 起き上がれない": "adult_fever_cannot_situp",
    "p.50 adult_fever Q2 頭痛+嘔吐": "adult_fever_headache_vomit",
    "p.50 adult_fever Q3 暑い所運動": "adult_fever_heat_exposure",
    "p.50 adult_fever Q4 頭痛+意識": "adult_fever_headache_consciousness",
    "p.52 abdominal Q1 大動脈瘤": "abd_aortic_aneurysm_history",
    "p.52 abdominal Q2 気を失いそう": "abd_presyncope_risk_age",
    "p.52 abdominal Q3 臍より上": "abd_above_navel",
    "p.52 abdominal Q4 痛みの種類": "abd_pain_quality_65",
    "p.54 adult_vomit Q1 血": "adult_vomit_blood",
    "p.54 adult_vomit Q2 2日以上": "adult_vomit_2days",
    "p.54 adult_vomit Q3 強い腹痛": "adult_vomit_strong_abdominal_pain",
    "p.54 adult_vomit Q4 腹部膨満": "adult_vomit_abdominal_distension",
    "p.54 adult_vomit Q5 胸背中": "adult_vomit_chest_back_pain",
    "p.54 adult_vomit Q6 最近外傷": "adult_vomit_recent_trauma",
    "p.54 adult_vomit Q7 強い頭痛": "adult_vomit_strong_headache",
    "p.56 dizziness Q1 動けない": "dizzy_cannot_move",
    "p.56 dizziness Q2 脱力": "dizzy_weakness",
    "p.56 dizziness Q3 下痢嘔吐": "dizzy_diarrhea_vomiting",
    "p.56 dizziness Q4 吐き気": "dizzy_nausea",
    "p.56 dizziness Q5 しゃべりにくい": "dizzy_speech_difficulty",
    "p.56 dizziness Q6 高血圧": "dizzy_hypertension",
    "p.56 dizziness Q7 目見えにくい": "dizzy_visual_disturbance",
    "p.56 dizziness Q8 脈乱れ": "dizzy_arrhythmia",
    "p.56 dizziness Q9 胸痛": "dizzy_chest_pain",
    "p.56 dizziness Q10 ひどいめまい": "dizzy_severe",
    "p.56 dizziness Q11 頭痛": "dizzy_headache",
    "p.58 numbness Q1 脳卒中症状": "numbness_stroke_symptoms",
    "p.58 numbness Q2 脳卒中既往": "numbness_stroke_history",
    "p.58 numbness ☎ 発症時刻": "numbness_onset_info",
    "p.60 lumbar Q1 外傷": "lumbar_trauma",
    "p.60 lumbar Q2 呼吸苦再確認": "lumbar_dyspnea_recheck",
    "p.60 lumbar Q3 胸痛40歳以上": "lumbar_chest_pain_40",
    "p.60 lumbar Q4 大動脈瘤": "lumbar_aortic_aneurysm_history",
    "p.60 lumbar Q5 65歳以上失神": "lumbar_presyncope_65",
    "p.60 lumbar Q6 65歳以上痛みの種類": "lumbar_pain_quality_65",
    "p.61 hematemesis Q1 出血量": "hematemesis_amount",
    "p.61 hematemesis Q2 胸腹痛": "hematemesis_chest_or_abdominal_pain",
    "p.61 hematemesis ☎ 既往": "hematemesis_history_info",
    "p.62 melena Q1 出血量": "melena_amount",
    "p.62 melena Q2 腹痛": "melena_abdominal_pain",
    "p.62 melena ☎ 既往": "melena_history_info",
    "p.63-64 malaise Q1 どこか痛い": "malaise_pain_anywhere",
    "p.63-64 malaise Q2 それはどこ": "malaise_pain_location",
    "p.64 malaise Q3 手足脱力": "malaise_weakness",
    "p.64 malaise Q4 しゃべりにくい": "malaise_speech",
    "p.64 malaise Q5 下痢嘔吐": "malaise_diarrhea_vomiting",
    "p.65-66 trauma Q1 出血": "trauma_bleeding",
    "p.66 trauma Q2 種別": "trauma_type",
    "p.67 foreign_body Q1 話せる/泣ける": "foreign_body_speak_cry",
    "p.67 foreign_body ☎ 何を": "foreign_body_object_info",
    "p.68 poisoning Q1 話せる/泣ける": "poisoning_speak_cry",
    "p.68 poisoning ☎ 何を": "poisoning_info",
    "p.70 ped_fever Q1 意識もうろう": "ped_fever_consciousness",
    "p.70 ped_fever Q2 ウトウト": "ped_fever_drowsy",
    "p.70 ped_fever Q3 興奮": "ped_fever_agitated",
    "p.70 ped_fever Q4 頭痛": "ped_fever_severe_headache",
    "p.72 ped_vomit Q1 繰り返し": "ped_vomit_repeated",
    "p.72 ped_vomit Q2 血/胆汁": "ped_vomit_blood_bile",
    "p.72 ped_vomit Q3 発熱": "ped_vomit_fever",
    "p.72 ped_vomit Q4 尿なし": "ped_vomit_no_urine",
    "p.72 ped_vomit Q5 ぐったり": "ped_vomit_lethargic",
    "p.72 ped_vomit Q6 噴出嘔吐": "ped_vomit_infant_projectile",
    "p.72 ped_vomit Q7 腹痛": "ped_vomit_strong_abdominal_pain",
    "p.72 ped_vomit Q8 頭痛": "ped_vomit_severe_headache",
    "p.72 ped_vomit Q9 イチゴゼリー便": "ped_vomit_bloody_stool",
    "p.72 ped_vomit Q10 腹部膨満": "ped_vomit_distension",
    "p.72 ped_vomit Q11 涙なし": "ped_vomit_no_tears",
    "p.72 ped_vomit Q12 腹部打撲": "ped_vomit_abdominal_trauma",
    "p.74 ped_head Q1 嘔吐": "ped_head_vomiting_repeated",
    "p.74 ped_head Q2 手足しびれ": "ped_head_limb_weakness_numbness",
    "p.74 ped_head Q3 意識消失": "ped_head_loss_of_consciousness",
    "p.74 ped_head Q4 頭痛": "ped_head_headache",
    "p.74 ped_head Q5 鼻血": "ped_head_nosebleed",
    "p.74 ped_head Q6 耳出血": "ped_head_ear_bleeding_without_direct_hit",
    "p.74 ped_head Q7 頭出血多い": "ped_head_bleeding_much",
    "p.74 ped_head Q8 首かしげ": "ped_head_neck_posture",
    "p.74 ped_head Q9 髄液様": "ped_head_csf_leak",
    "p.74 ped_head Q10 めまい": "ped_head_dizziness",
    "p.74 ped_head Q11 視覚障害": "ped_head_visual_disturbance",
}

print(f"PDF 質問項目数: {len(pdf_questions)}")
print()

# === YAML 全ノード列挙 ===
yaml_nodes = set()
for n in d["entry_flow"]: yaml_nodes.add(n["id"])
for n in d["common_vitals"]: yaml_nodes.add(n["id"])
yaml_nodes.add("chief_complaint_router")  # routerもノードとして
for p in d["protocols"]:
    for n in p["nodes"]: yaml_nodes.add(n["id"])

print(f"YAML ノード数: {len(yaml_nodes)}")
print()

# === PDF→YAML 抜け検査 ===
pdf_yaml_ids = set(pdf_questions.values())
missing_in_yaml = pdf_yaml_ids - yaml_nodes
print(f"=== PDF にあるが YAML にないノード: {len(missing_in_yaml)}件 ===")
for x in sorted(missing_in_yaml):
    matching = [k for k, v in pdf_questions.items() if v == x]
    print(f"  {x} (PDF: {matching})")
print()

# === YAML→PDF 余剰検査 ===
yaml_extra = yaml_nodes - pdf_yaml_ids
print(f"=== YAML にあるが PDF 直接対応のないノード: {len(yaml_extra)}件 ===")
for x in sorted(yaml_extra):
    print(f"  {x}")
print("  ※chief_complaint_router は PDF Q8 の <主訴の選択> 機構に対応する router 構造体（直接1ノードではない）")
print()

# === 到達性検査（path_check_v4 と同じ） ===
nodes = {}
for n in d["entry_flow"]: nodes[n["id"]] = n
for n in d["common_vitals"]: nodes[n["id"]] = n
nodes["chief_complaint_router"] = d["chief_complaint_router"]
protocol_starts = {}
protocol_followups = {}
for p in d["protocols"]:
    for n in p["nodes"]: nodes[n["id"]] = n
    if p.get("start_node"):
        protocol_starts[p["id"]] = p["start_node"]
    else:
        for n in p["nodes"]:
            if not n.get("metadata_only") and not n.get("transition_only"):
                protocol_starts[p["id"]] = n["id"]; break
    if p.get("terminal_followup_chain"):
        protocol_followups[p["id"]] = p["terminal_followup_chain"]

def walk():
    seen = set()
    stack = ["intro_fire_or_emergency"]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in nodes: continue
        seen.add(nid)
        node = nodes[nid]
        if nid == "chief_complaint_router":
            for r in node.get("routes", []):
                t = r.get("protocol")
                if t in protocol_starts: stack.append(protocol_starts[t])
                if t in protocol_followups:
                    for fu in protocol_followups[t]: stack.append(fu)
            continue
        if "next" in node and isinstance(node["next"], str): stack.append(node["next"])
        for ch in node.get("choices") or []:
            if "next" in ch: stack.append(ch["next"])
            if "route_to_protocol" in ch and ch["route_to_protocol"] in protocol_starts:
                stack.append(protocol_starts[ch["route_to_protocol"]])
                if ch["route_to_protocol"] in protocol_followups:
                    for fu in protocol_followups[ch["route_to_protocol"]]: stack.append(fu)
            if "followup_metadata" in ch:
                stack.append(ch["followup_metadata"])
    return seen

reachable = walk()
unreachable_pdf = pdf_yaml_ids - reachable

print(f"=== intro_fire_or_emergency から辿れない PDF対応ノード ===")
if not unreachable_pdf:
    print("  ✅ なし — PDF に対応する全 YAML ノードが導入から機械的に辿れる")
else:
    for x in sorted(unreachable_pdf):
        matching = [k for k, v in pdf_questions.items() if v == x]
        print(f"  ❌ {x} (PDF: {matching})")
print()

# === エッジ種別の集計 ===
edge_types = {"next": 0, "route_to_protocol": 0, "followup_metadata": 0,
              "terminal_followup_chain": 0, "matches_rule (global)": 0,
              "route_to (external)": 0, "transition next (node-level)": 0}
for nid, node in nodes.items():
    if "next" in node: edge_types["transition next (node-level)"] += 1
    if nid == "chief_complaint_router":
        edge_types["route_to_protocol"] += len(node.get("routes", []))
    for ch in node.get("choices") or []:
        if "next" in ch: edge_types["next"] += 1
        if "route_to_protocol" in ch: edge_types["route_to_protocol"] += 1
        if "route_to" in ch: edge_types["route_to (external)"] += 1
        if "matches_rule" in ch: edge_types["matches_rule (global)"] += 1
        if "followup_metadata" in ch: edge_types["followup_metadata"] += 1
for p in d["protocols"]:
    if p.get("terminal_followup_chain"):
        edge_types["terminal_followup_chain"] += len(p["terminal_followup_chain"])

print(f"=== 機械トラバース可能エッジの種別と件数 ===")
for k, v in edge_types.items():
    print(f"  {k}: {v}")

# === 最終結論 ===
print()
print("=" * 60)
print("最終結論")
print("=" * 60)
print(f"PDF 質問項目: {len(pdf_questions)}個")
print(f"YAML ノード: {len(yaml_nodes)}個")
print(f"PDF→YAML 抜け: {len(missing_in_yaml)}個 {'✅' if not missing_in_yaml else '❌'}")
print(f"YAML→PDF 余剰: {len(yaml_extra)}個 (内訳: 構造的補助ノード)")
print(f"  - 余剰の内訳: {sorted(yaml_extra)}")
print(f"intro から辿れない PDF対応ノード: {len(unreachable_pdf)}個 {'✅' if not unreachable_pdf else '❌'}")
