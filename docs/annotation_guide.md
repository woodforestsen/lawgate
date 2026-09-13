# 标注规范（Annotation Guide）

> ## ⚠️ 首要声明（必须最先读）
>
> **本仓库当前 `data/benchmark/` 中的 `need_retrieval` / `golden_answer` /
> `golden_provisions` / `gold_pass` 全部由规则化程序生成，不是人工标注。**
>
> - 本环境**没有任何人工标注员**（见 `data/benchmark/construction_log.md` §6
>   与 `data/benchmark/counts.json` 的 `need_retrieval_source: "rule-based (NOT human-annotated)"`）；
> - 条目的 `annotators: ["A","B"]` 与 `arbitrated: false` 是**占位字段**，
>   不是"已经标过了"的记录；
> - `data/benchmark/kappa_report.md` 中的 κ 来自**模拟**标注员（噪声率 0.12 的程序化假投票），
>   **不是**标注者间一致性证据。
>
> 因此：**本文档是"待执行的标注协议"，不是"已执行标注的记录"。**
> 它的用途是给两名真人标注员一套可复现、可仲裁、可验收的操作规程；
> 只有在真人按本文档标注并算出真实 κ 之后，本文档才成为"标注过程记录"。
> 缺口清单与执行步骤见 `docs/DATA_GAP.md` G4。

---

## 1. 标注目标与产物

| 项 | 约定 |
|---|---|
| 标注对象 | `data/benchmark/all.jsonl` 的 **1180 条**条目（qid 唯一，构建时已校验） |
| 标注员 | **2 名**（记为 A / B），法学背景，**独立标注、不得互相讨论条目内容** |
| 覆盖率 | A 标 100%；B 盲标 ≥ 50%（建议全标，κ 才有统计意义） |
| 每条的标注产物 | JSONL 一行：`{"qid":…, "need_retrieval":bool, "golden_provisions":[…], "golden_answer":"…", "gold_pass":bool|null}` |
| 存放 | `data/benchmark/votes_A.jsonl`、`data/benchmark/votes_B.jsonl` |
| 仲裁产物 | `data/benchmark/votes_final.jsonl` + `data/benchmark/arbitration_log.md`（逐条分歧与结论） |
| 一致性验收 | `python scripts/kappa.py --a votes_A.jsonl --b votes_B.jsonl`，**两项 κ 均 ≥ 0.7 为 PASS** |

> **不要**运行 `python scripts/kappa.py --simulate` 并把结果写进任何交付物——
> 该开关存在只为验证 κ 流水线，其输出在 `kappa_report.md` 顶部自带
> "⚠️ 本报告的 kappa 来自【模拟标注员】"的警告。

---

## 2. 字段定义

### 2.1 `need_retrieval`：该问题是否必须调用外部检索才能可靠回答

**定义**：假设你是一个**参数里没有中国法律条文、也没有案号库**的模型。
仅凭常识与一般语言能力，能否给出**可核查的准确答案**（需要点出具体法律名称与条号、
或具体案号核验结论）？

- `true` = **不能**。必须查库（法条库 / 文书库 / 案号真值库）才能给出可核查答案。
- `false` = **能**。答案不需要外部条文或案号即可给出，或该问题**不需要**引用具体条文
  （例如"该法是否还有效"这类**状态询问**，其结论来自时效状态机本身，而不是条文正文）。

**关键判据（按顺序判断，命中即停）：**

| 顺序 | 判据 | 结论 |
|---|---|---|
| R1 | 问题**要求给出法定规则的具体内容**（"第 X 条怎么规定""适用哪条""法律上怎么规定"） | `true` |
| R2 | 问题提供了**具体事实场景**，要求判断"该适用什么/能否主张" | `true` |
| R3 | 问题要求**案例/判例/裁判规则**（"类似案件法院怎么判""判例"） | `true` |
| R4 | 问题**未给出条号**，但要求精确的规范性内容 | `true` |
| R5 | 问题**直接给出条号并只要原文**（"《X法》第 Y 条的内容是什么"） | `false`（结构化通道按条号直取，无需语义检索） |
| R6 | 问题只要**效力状态**（"还有效吗""是否废止""什么时候生效"） | `false`（时效状态机应答） |
| R7 | 问题只要**案号核验结论**（"这个案号是真的吗"） | `false`（核验器应答） |
| R8 | 纯概念问、不涉及具体条号与具体案件细节 | 见 §3.1 的 40% 规则 |

> **注意**：`need_retrieval` 描述的是"**该方法学定义下的信息需求**"，
> **不是**"我们系统的路由决策"。系统是否真的检索由门控 `u > τ_b` 决定
> （`lawgate/router.py`），两者可以不一致——不一致正是我们要测量的对象。

### 2.2 `golden_provisions`：回答该问题所依据的**法条金标集合**

- 元素形如 `{"law_short": "民法典", "article_no": 667}`；
- **只填真正能支撑答案的条文**，一般 **1–3 条**，宁少勿滥；
- **「条」级即可，不要求款/项级**（款项信息写进 `golden_answer` 或备注）；
- 使用**稳定法名**（与 `law_alias` 表一致）：`民法典` / `公司法` / `劳动合同法` /
  `民事诉讼法` / `民法典时间效力规定` / `合同法` / `物权法` / `侵权责任法` / `婚姻法` /
  `继承法` / `收养法` / `担保法` / `公司法(2018修正)`；
- **不要**为了凑集合填语义无关的兜底条号——**宁可留空**。
  当前评测集中有 **111 条** `golden_provisions` 为空，其中 `case_verify` 100 条
  （按设计不带法条金标）、`temporal_trap` 11 条（目标替代条文不在语料内），
  这是**正确的留空**，不是遗漏（见 `counts.json` 的 `golden_provisions_empty`）；
- 若某法的目标替代条文**不在本仓库语料内**，在备注里写明"缺口"，并按 F2 规则处理。

### 2.3 `golden_answer`：**参考答案**（不是"唯一答案"）

- 写**能覆盖关键词与条号引注**的 1–3 句；
- 对 `provision` 类：以条文正文的关键句为准（"《X法》第 Y 条规定：……"）；
- 对 `temporal_trap` 类：必须同时含**时效结论 + 现行替代条文 + 沿革/溯及力提示**三段；
- 对 `case_verify` 类：含**核验级别 + 理由**（格式非法 / 不存在 / 存在但案由不符 / 核验通过）；
- 对 `case` 类：含**案由 + 裁判要点 + 相关条文**；案号为合成时**必须**写明"不对应真实案件"。

> 评测判分是**机械代理判据**（`lawgate/eval/metrics.py: score_item`），比对的是
> 关键词召回率与"法律名 + 条号"是否同时命中，因此 `golden_answer` 的**信息密度**
> 比文采重要：请把"应当/无效/视为"这类操作性表述和条号写全。

### 2.4 `gold_pass`：仅 `case_verify` 类使用（布尔）

- `true` = 该案号**应当被放行**（核验通过）；
- `false` = 该案号**应当被拦截**（格式非法 / 不存在 / 存在但案由不符 / 存在但类型不符）；
- 其它类别一律填 `null`（不要填 `false`）。

> 二分类口径见 `lawgate/eval/metrics.py: case_verify_prf()`：
> "放行（核验通过）vs 拦截（任一非通过级别）"。

### 2.5 `temporal.expect_status`：仅时效类使用

取值必须落在时效状态机的状态集合内（`lawgate/channel/b_temporal.py`）：
`现行有效` / `已修订` / `已废止` / `尚未生效` / `部分失效` / `查无此条`，
以及 T4 跨时点条目使用的 `溯及力判断`。

### 2.6 `temporal.trap_type`：仅时效类使用（T1–T4）

| 取值 | 含义 | 判定要点 |
|---|---|---|
| `T1` | 已废止法律的条文被当作现行依据 | 需检查 `law_lifecycle` 的废止关系与生效日 |
| `T2` | 已修订条文（文本被改写） | 需检查 `validity_status='已修订'` 与修订说明 |
| `T3` | 显式时效询问（"现在还有效吗"） | 结论来自时效状态机；**注意有 1/3 是"现行有效"**，不要一律答废止 |
| `T4` | 跨时点行为（事实发生日早于 2021-01-01，现在起诉） | 依据《民法典时间效力规定》第 1–3 条判断溯及力 |

---

## 3. 决策规则与边界情形

### 3.1 概念题（`concept`，b1）的 `need_retrieval` 判定

概念题**没有条号**，因此不能用 R5/R6 直接判 `false`。协议规定：

- 若该概念**能在现行法律中定位到明确的条文规则**（例如"违约金过高能否调整"对应《民法典》
  第 585 条），标 `true`；
- 若该概念属于**法律常识性说明**、答案不依赖具体条号也成立（例如"什么是离婚冷静期"的一般解释），
  标 `false`；
- **对齐目标**：概念题中 `need_retrieval=true` 的比例**控制在 40% 左右**（±10 个百分点）。

> 现状提醒：当前评测集的 40% 是**规则随机切分**的结果（`CONCEPT_NEED_RETRIEVAL_RATIO = 0.40`，
> 实测 80/200），并在每条 `slots.need_retrieval_source = "rule-based"` 中标注。
> 换成人工标注后，这个比例**允许偏离**——请按真实判断标注，不要为了凑 40% 而改标。

### 3.2 场景 vs 原文：`provision` 类内部的两分

| 风格（`slots.style`） | 例子 | `need_retrieval` |
|---|---|---|
| `direct`（直接问条） | 「《公司法》第一条的内容是什么？」 | `false` |
| `scenario`（场景问条） | 「（当事人遇到的情况是：有限责任公司的注册资本为……）当事人应当如何主张？适用哪条法律？」 | `true` |

一致性要求：**所有 `direct` 条目必须是 `false`，所有 `scenario` 条目必须是 `true`。**
这是 `provision` 类 κ 的主要来源；若你发现自己标得不一致，请先复核 R5/R2。

### 3.3 `case_verify` 类的四级判定（`expect_status` / `gold_pass`）

| 子类 | 输入特征 | 期望级别 | `gold_pass` |
|---|---|---|---|
| V1 | 格式合法 + 在案号库中可命中 + 类型正确 + 案由一致 | 核验通过 | `true` |
| V2 | 格式合法 + 在案号库中**不存在** | 不存在 | `false` |
| V3 | 格式合法 + 在案号库中可命中 + **案由不符** | 存在但案由不符 | `false` |
| V4 | 格式非法（缺要素 / 年份不合理 / 法院代字非法 / 案件类型代字非法，如"测"） | 格式非法 | `false` |

**案由一致性判定（双向包含）**：提问写"借贷"、真实案由"民间借贷纠纷" → **视为一致**
（`b_case_verify.py: _cause_match()`：去掉"纠纷"后双向 `in`）。请按同一规则标注。

**⚠️ 真值口径**：本仓库 `case_registry` 是 **SYNTHETIC 合成库**，
"存在/不存在"**仅指在本仓库案号库中能否命中**，**不代表**中国裁判文书网上是否存在该案件。
标注时必须沿用这个口径（`counts.json` 的 `case_verify_verification.note` 已如此声明）；
真实文书导入后（`docs/DATA_GAP.md` G2），此口径须整体升级并重标。

### 3.4 多轮（`multi-turn`，b4）

- 每条按**其底层类别**判 `need_retrieval` 与 `golden_provisions`（判分口径见
  `metrics.py` 的 `category == "multi-turn"` 分支：有金标条文按 provision、否则按 concept）；
- T2 轮开始出现**省略主语的追问**，参数 `slots.inherited` 应设为 `["topic","cause_action"]`；
  标注时**只判当前轮的内容需求**，不要因为上一轮已给过上下文就改判 `false`；
- T3 轮是时效询问或案号询问，按 R6/R7 判 `false`。

### 3.5 时效类（`temporal_trap`，b2）的特殊约定

- T1/T2/T3 判 `false`（结构化通道应答），T4 判 `true`（需检索时效规定原文）；
- `golden_provisions` 应填**现行有效法**的目标条文（如合同法第 52 条 → 民法典第 153 条），
  而不是被废止的旧条文；
- T3 里有 1/3 条目的 `expect_status='现行有效'`（避免"一律答已废止"的偏置），
  标注时不要因为"法名看起来老"就判废止。

---

## 4. 已标注样例（**当前为规则生成，供对照口径，不是人工标注范例**）

> 以下 8 条直接从 `data/benchmark/` 摘出（节选字段），用于说明每种类型的字段填法。
> 它们**现在**是规则生成的；人工重标时请按 §3 的判据**独立判断**，
> 若结论与下表不同，**以你的判断为准**并记录差异原因。

### 4.1 `concept`（direct 概念问，`false`）

```json
{"qid": "con_00002", "query": "合同成立在法律上到底怎么界定？（咨询编号 con_00002）",
 "category": "concept", "bucket": "b1", "need_retrieval": false,
 "golden_answer": "根据《民法典》第四百九十条，当事人采用合同书形式订立合同的，自当事人均签名、盖章或者按指印时合同成立。",
 "golden_provisions": [{"law_short": "民法典", "article_no": 490}],
 "slots": {"topic": "合同效力", "keyword": "合同成立", "need_retrieval_source": "rule-based"},
 "temporal": {"as_of": "2026-11-01", "expect_status": "现行有效"}}
```

对照 → **同类别但判 `true`** 的一条：

```json
{"qid": "con_00003", "query": "遇到违约金的问题，法律上的处理规则是什么？（咨询编号 con_00003）",
 "category": "concept", "bucket": "b1", "need_retrieval": true,
 "golden_provisions": [{"law_short": "民法典", "article_no": 585}]}
```

**差异原因**：两者都是概念问，但 con_00002 的问题**本身就点名了要给的规则对象**
（"合同成立"），属常识性说明；con_00003 问的是"法律上的**处理规则**"，
必须落到第 585 条的调整规则上。此类边界题是 κ 的主要分歧源，标注时请写明理由。

### 4.2 `provision` · `direct`（`false`，R5）

```json
{"qid": "prov_00001", "query": "《公司法》第一条的内容是什么？",
 "category": "provision", "bucket": "b2", "need_retrieval": false,
 "slots": {"style": "direct", "law_short": "公司法", "article_no": 1, "article_label": "第一条"},
 "golden_answer": "《公司法》第一条规定：为了规范公司的组织和行为，……根据宪法，制定本法。",
 "golden_provisions": [{"law_short": "公司法", "article_no": 1}]}
```

### 4.3 `provision` · `scenario`（`true`，R2）

```json
{"qid": "prov_00002", "query": "（当事人遇到的情况是：有限责任公司的注册资本为在公司登记机关登记的全体股东认缴的出资额。）当事人应当如何主张？适用哪条法律？",
 "category": "provision", "bucket": "b2", "need_retrieval": true,
 "slots": {"style": "scenario", "law_short": "公司法", "article_no": 47},
 "golden_provisions": [{"law_short": "公司法", "article_no": 47}]}
```

> ⚠️ 注意本条是"**把条文原文塞进题干再问适用哪条**"的构造问法，
> 因此**存在构造重叠**：它不是评测检索能力的干净样本。人工重标时应标注
> `slots.design_note: "query 泄漏条文原文，检索基线天然占优"`，
> 或在评测中单独成层报告。

### 4.4 `case`（`true`，R3；金标来源为 null）

```json
{"qid": "case_00001", "query": "（双方系朋友关系，原告通过银行转账向被告支付450000元，……）类似案件法院通常怎么判？",
 "category": "case", "bucket": "b3", "need_retrieval": true,
 "golden_answer": "与（2019）京01民终2940号（民间借贷纠纷，北京市第一中级人民法院，2020-09-02）类似的情形，法院通常围绕《民法典》第六百六十七条……【数据提示】本案号为程序化合成案号，案情为程序化生成，不对应真实案件。",
 "golden_provisions": [{"law_short": "民法典", "article_no": 667},
                       {"law_short": "民法典", "article_no": 676},
                       {"law_short": "民法典", "article_no": 680}],
 "golden_source": null,
 "slots": {"cause_action": "民间借贷纠纷", "data_source": "SYNTHETIC", "synthetic": true}}
```

`golden_source` 为 `null` 是**刻意的**：合成数据**不伪造 URL**（`counts.json` 的
`data_caveats` 第 1 条）。导入真实文书后此项必须填真实 URL。

### 4.5 `case_verify` · V1（`false` + `gold_pass=true`，R7）

```json
{"qid": "cv_00001", "query": "（（2019）京01民终2940号）我查到的这个案号是真的吗？它属于民间借贷纠纷，法院是北京市第一中级人民法院，对吗？",
 "category": "case_verify", "bucket": null, "need_retrieval": false,
 "golden_provisions": [], "golden_source": null, "gold_pass": true,
 "slots": {"verify_type": "V1", "expected_exists": true, "claimed_cause": "民间借贷纠纷"},
 "case_no": {"raw": "（2019）京01民终2940号", "exists": true, "true_cause": "民间借贷纠纷"}}
```

### 4.6 `temporal_trap` · T1（`false`）

```json
{"qid": "tt_t1_00002", "query": "我2017年的那件事，现在（2026年）起诉的话，能依照《合同法》第五十二条处理吗？……",
 "category": "temporal_trap", "bucket": "b2", "need_retrieval": false,
 "temporal": {"as_of": "2026-11-01", "expect_status": "已废止", "trap_type": "T1",
              "superseded_by": "民法典#153"},
 "golden_provisions": [{"law_short": "民法典", "article_no": 153}],
 "golden_answer": "不能直接依照《合同法》第五十二条处理。《合同法》已于2021-01-01被废止……现行规则为：《民法典》第一百五十三条规定：……"}
```

### 4.7 `temporal_trap` · T2（`false`；含数据不足披露）

```json
{"qid": "tt_t2_00001", "temporal": {"expect_status": "已修订", "trap_type": "T2",
              "superseded_by": "公司法#47"},
 "slots": {"trap": "T2", "amended_law": "公司法(2018修正)", "amended_article": 26,
           "coverage_note": "数据不足披露：库中 validity_status='已修订' 的条文仅 1 条，T2 混合使用被后续立法改写的已废止条文；真实已修订条目数=15"},
 "golden_provisions": [{"law_short": "公司法", "article_no": 47}]}
```

### 4.8 `temporal_trap` · T4（`true`；`as_of` 是**事实发生日**）

```json
{"qid": "tt_t4_00001", "temporal": {"as_of": "2015-03-12", "expect_status": "溯及力判断", "trap_type": "T4"},
 "golden_provisions": [{"law_short": "民法典时间效力规定", "article_no": 1}],
 "golden_answer": "依据《民法典时间效力规定》第一条：民法典施行（2021-01-01）前的法律事实引起的民事纠纷案件，适用当时的法律、司法解释的规定；……"}
```

---

## 5. 双盲标注工作流

```
① 准备   python scripts/build_benchmark.py --seed 42        # 确保评测集与 counts.json 一致
         copy data/benchmark/all.jsonl → <工作副本>          # 两人各自一份
         ★ 工作副本必须**删除** golden_answer / golden_provisions /
           need_retrieval / gold_pass / annotators 等字段，只保留
           qid / query / history / category / bucket / temporal / case_no / slots
           ——否则等于把规则标签泄漏给标注员，κ 会被系统性高估。

② 双标   A、B 各自独立完成，互不查看对方结果，不得讨论具体条目。
         每标完一批（建议 100 条）落盘一次 votes_X.jsonl，防止丢失。

③ 核对   python scripts/kappa.py --a votes_A.jsonl --b votes_B.jsonl
         - 退出码 0 且两项 κ ≥ 0.7 → 进入 ⑤
         - 任一 κ < 0.7 → 进入 ④

④ 复盘   κ < 0.7 时执行"重新对齐标注规范"（§7），不得直接开始仲裁凑数。

⑤ 仲裁   对**所有分歧条目**逐条仲裁（由第三人或两名标注员在组长主持下逐条讨论），
         写 votes_final.jsonl 与 arbitration_log.md（含 qid / A 值 / B 值 / 结论 / 依据条款）。

⑥ 归档   把 votes_A / votes_B / votes_final 与 kappa_report.md（HUMAN 版）一并归档，
         并在本文件末尾追加"本次标注记录"（日期、人数、n、κ）——**此时本文件才成为过程记录**。
```

**反作弊约定**：不允许用程序按 `query` 文本规则批量生成投票文件（那就是规则标签的换皮）；
不允许两名标注员先统一再落盘（那就不是独立双标）；不允许只标分歧大的子集。

---

## 6. 字段口径与评测判分的衔接

人工金标写进 `all.jsonl`（或评测时以 `votes_final.jsonl` 覆盖）后，判分链路**无需改代码**：
`lawgate/eval/metrics.py: score_item(answer, meta, ctx)` 只读取 `meta` 的以下字段：

| 字段 | 判分用途 |
|---|---|
| `category` | 选择判据分支（provision / concept / case / multi-turn / temporal_trap / case_verify） |
| `golden_provisions` | `cited_golden()`：法律名 + 条号是否同时命中 |
| `golden_answer` | `key_term_recall()` 的关键词召回率分母 |
| `temporal.expect_status` | TVC（是否不把失效法条当现行依据）判据分支 |
| `gold_pass` | case_verify 的 `correct = (got_pass == gold_pass)` |
| `slots.inherited` + trace 的 `slots_inherited` | `slots_inherited_ok`（多轮槽位继承正确率） |
| `need_retrieval` | **仅用于 E0**（信号 AUC），不参与 `correct` 判分 |

> 因此：`need_retrieval` 影响的是**门控质量的评价**（`figures/e0/e0_auc.json`），
> 其余字段影响**答案质量评价**（`results/e1|e5`）。两类标签都要标。

---

## 7. κ < 0.7 时的重新对齐流程（最多 3 轮）

1. **定位分歧类型**：`scripts/kappa.py` 会输出全部分歧 qid。按类别统计
   （concept / provision / multi-turn / temporal_trap / case_verify）与按 `bucket` 统计，
   找出**分歧集中区**——通常是 concept 的概念/条文边界，或 multi-turn 的"当前轮"口径。
2. **逐条归因**：对分歧最集中的 20 条，两名标注员各自写下"我为什么这么标"，
   归因到本文档的**具体条款**（R1–R8 / §3.x / T1–T4）。
3. **修订规范**：
   - 若归因是"规范没写清" → **修订本文档**并提升版本号，把新增判例写进 §4 的样例；
   - 若归因是"条目本身有缺陷"（如 query 泄漏答案、题干歧义、真值不可判定）→
     在 `arbitration_log.md` 记录并把该条调出评测（或加 `slots.design_note` 标注），
     **不要**靠改标迁就。
4. **重标**：只重标"分歧条目 + 受规范修订影响的同类条目"（不是全量重标），
   重算 κ。
5. **收敛判据**：两轮后 κ 仍 < 0.7 → 说明该字段的**可标注性**有问题（而非标注员能力），
   必须在报告中如实披露该字段的 κ 与不稳定原因，**不得**通过调整样本（删掉分歧样本）
   把 κ 抬过 0.7。三轮仍不达标 → 上报项目负责人，并在 `docs/ACCEPTANCE.md` 记为不达标项。

**验收硬门槛（照抄进论文）**：

| 字段 | κ 门槛 | 判定 |
|---|---|---|
| `need_retrieval` | **≥ 0.7** | PASS / FAIL |
| `golden_provisions`（集合规范化后） | **≥ 0.7** | PASS / FAIL |

其中 `golden_provisions` 的一致性计算**先按 `law_short#article_no` 规范化、去重、排序**，
再拼成字符串比较——顺序与重复**不计入分歧**（`scripts/kappa.py` 实现）。

> ⚠️ 解读提醒（照抄进论文）：`golden_provisions` 的期望一致率 Pe 通常很低
> （法条组合高度分散），因此其 κ 容易偏高，属于**指标性质**，不等于标注质量一定更好。
> 这一点在 `kappa_report.md` 的"如何解读这些数字"一节中已有同样说明。

---

## 8. 版本与变更记录

| 版本 | 日期 | 变更 | 状态 |
|---|---|---|---|
| v0.1（本版） | — | 首次制定；**尚未由真人执行**；当前评测集标签为规则生成 | 待执行 |

> 执行完成后，请在此表追加一行：执行日期 / 标注员人数 / 标注条数 / 两项 κ / 是否 PASS，
> 并把 `data/benchmark/kappa_report.md` 的 HUMAN 版本作为附件归档。
> 在此之前，任何材料引用 κ 时都必须附带"来自模拟标注员"的限定语。
