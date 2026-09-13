# 评测集构建日志（自动生成，请勿手改）

生成时间（run_date）：2026-09-10
随机种子：build seed=42，split seed=42
评测冻结日期（temporal.as_of 默认值）：2026-11-01

## 1. 条数口径（不做数字粉饰）

| 分项 | 条数 |
|---|---|
| concept (b1) | 200 |
| provision (b2) | 260 |
| case (b3) | 200 |
| multi-turn (b4) | 300（100 组 × 3 轮） |
| temporal_trap (b2) | 120（T1=30、T2=30、T3=30、T4=30） |
| case_verify (—) | 100（V1=30、V2=30、V3=25、V4=15） |
| **单轮合计** | **880** |
| **含多轮全部轮次** | **1180** |

手册 S4 写的"1080 条（保底 500+220）"是**目标总量**，与手册自己的分项条数不自洽：
单轮 = 200+260+200+120+100 = 880；把 multi-turn 三轮全算 = 1180。
本次构建**按分项全量产出**，没有为了凑 1080 而裁剪条目。

## 2. 逐类生成方式（如实披露规则化/合成成分）

### concept（200）
由 `legal_provisions` 中 `validity_status='现行有效'` 的条文派生：取主题词
（`topic_keyword` ∪ `provision_keywords`）生成"什么算 X / 法律上怎么规定 X"式问法，
题干**不含条号**（避免退化为逐字检索）；`golden_answer` 取条文首句或含
"应当/无效/视为"标记的操作性句，并附条号引注。
`need_retrieval` 按 40% 规则化切分为 true
（实测 80 条），**这是规则切分，不是人工标注**，
已在每条 `slots.need_retrieval_source='rule-based'` 中标记。

### provision（260）
直接问条（`《X法》第Y条的内容是什么？`）与场景问条（`（场景）适用哪条法律？`）各 50%。
**真实可得条文只有 80 条**（现行有效去重后），
不足 260 条，因此超额 180 条为同一批条文
的不同问法变体（复现系数 3.25）。qid 与 query 均不重复，但底层法条复用。

### case（200）
每案由 50 条，取自 `judgments.full_text` 的【原告诉称】事实段（截断约 200 字）。
**合成数据告警**：`case_registry`/`judgments` 的 `data_source='SYNTHETIC'`，
案号不对应真实案件，因此 `golden_source=null`（**不伪造 URL**）；
`case_no.exists=true` 仅表示在本仓库合成库中命中。

### multi-turn（300）
固定三段结构：T1 完整陈述（含案由主题词）→ T2 省略主语的追问
（`slots.inherited=['topic','cause_action']`，`history` 携带 T1 的 query/answer）→
T3 时效询问（`temporal.expect_status` 按所问法律是否废止设定）或案号询问
（`case_no.exists=false`，构造的案号不在库中）。100 组按 4 类案由均分。

### temporal_trap（120）
四类各 30，全部由 `law_lifecycle` + `SUPERSEDE_MAP` + `validity_status` 规则化生成：

* T1 已废止法律（expect_status='已废止'，superseded_by='民法典#667' 形式）；
* T2 已修订条文（`公司法(2018修正)#26` 及 `validity_status='已修订'` 条文）；
* T3 显式时效询问（2/3 废止 + 1/3 现行有效，避免"一律答已废止"的偏置）；
* T4 跨时点行为（事实发生日 < 2021-01-01，现在起诉，
  金标条文为《民法典时间效力规定》第 1–3 条）。

T1/T2/T3 的 `need_retrieval=false`（结构化通道应答），T4 `need_retrieval=true`。
**覆盖缺口（如实记录）**：库中 `validity_status='已修订'` 的条文只有
1 条法律（公司法(2018修正) 第 26 条），
T2 因此混合使用"被后续立法改写的已废止条文"，并在每条 `slots.coverage_note` 中标注；
另有 11 条 temporal_trap 条目因目标替代条文未收录于种子语料而
`golden_provisions=[]`（`slots.coverage_gap` 逐条标注，
**不使用语义不相关的兜底条号**）。

### case_verify（100）
V1 真实一致 30（gold_pass=true）/
V2 格式合法不存在 30 /
V3 真实但案由不符 25 /
V4 格式非法 15。
V2 由 V1 案号平移 `seq_no`（偏移池 500–4999）构造并**断言不在** `case_registry`：
30 条全部通过断言；
V1/V3 全部可在库中命中。**"真实"仅指本仓库合成库，不代表裁判文书网真值。**

## 3. 划分

分层随机 20/80（seed=42）：|dev|=236，
|test|=944。multi-turn 以 `group_id` 为整体单位，绝不跨 dev/test 拆分。
另出 `test_multiturn.jsonl`（仅 test 侧多轮）。

## 4. 数据可得性（实测）

* `legal_provisions` 行数 158，
  去重 (law_short, article_no) 95 对；
* 现行有效去重条文 80 对，分布：公司法:7、劳动合同法:7、民事诉讼法:1、民法典:62、民法典时间效力规定:3；
* 已废止去重条文：侵权责任法:1、合同法:6、婚姻法:1、担保法:2、收养法:1、物权法:2、继承法:1；已修订：公司法(2018修正):1；
* **民法典种子语料实际仅 62 条**（非全量 1260 条）：
  [1, 3, 7, 10, 19, 20, 40, 143, 144, 146, 153, 154, 157, 188, 464, 465, 469, 490, 496, 497, 509, 510, 563, 577, 584, 585, 586, 587, 667, 668, 674, 675, 676, 679, 680, 703, 704, 705, 707, 714, 716, 721, 722, 728, 733, 1042, 1062, 1063, 1064, 1076, 1077, 1079, 1084, 1087, 1088, 1091, 1165, 1166, 1179, 1183, 1191, 1258]；
* `case_registry` 600 行，`data_source='SYNTHETIC'`。

## 5. 必须随结果一起披露的偏差

- case_registry/judgments 全部为 SYNTHETIC 合成数据，case 类 golden_source 一律为 null（不伪造 URL）。
- case_verify 的'真实'仅指在本仓库合成案号库中可命中，不代表中国裁判文书网上存在该案件。
- 本环境无人工标注员：need_retrieval/golden_answer/gold_pass 均为规则化生成；annotators=['A','B'] 与 arbitrated=False 为占位字段。
- 民法典种子语料仅收录 62 条，非全量 1260 条；provision 类超额部分为同条文变体。
- temporal_trap 的四类陷阱模板由 law_lifecycle + SUPERSEDE_MAP + validity_status 规则化生成，非人工构造。
- 库中 validity_status='已修订' 的条文只有 1 条（公司法(2018修正)第26条），T2 因此混合使用被后续立法改写的已废止条文，并在 slots.coverage_note 标注。
- 部分已废止法律（如担保法）的目标替代条文未收录于种子语料，对应条目的 golden_provisions 为空，缺口已在 temporal_trap.jsonl 的 slots.coverage_gap 与本文件中逐条标注。
- 全部 1180 条 query 逐条唯一（构建时校验），query 末尾附有可审计的编号。

## 6. 人工标注缺口

本项目当前**没有任何人工标注员**。以下字段为规则化占位，申报前必须由真人双标 + 仲裁替换：

* `need_retrieval`（全部条目）；
* `golden_answer` / `golden_provisions`（全部条目）；
* `gold_pass`（case_verify）；
* `annotators=['A','B']` / `arbitrated=False`。
