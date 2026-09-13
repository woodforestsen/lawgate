# 风险登记与处置台账（Risk Register）

> 本文件把项目手册的风险表**展开为可核查的处置台账**。每一条风险给出
> **触发条件 / 影响 / 处置动作 / 当前状态**，并在"具体处置落点"列给出**代码或产物中的
> 实际位置**——凡标注"已触发并已处置"的风险，都能在仓库里找到对应的实现。
>
> 状态取值：
> - **已触发并已处置**——风险已实际发生，且已在代码/产物中落实处置动作；
> - **已触发，部分处置**——已发生，处置动作已落实但仍有残余敞口；
> - **已触发，未处置**——已发生，且尚无处置手段（高优先级）；
> - **未触发（已预案）**——尚未发生，处置路径已写入代码或文档。
>
> 相关文档：逐条偏差见 `docs/deviations.md`（**D0–D35**，本台账的截点）；缺口与补做步骤见 `docs/DATA_GAP.md`；
> 验收判定见 `docs/ACCEPTANCE.md`；系统能力边界见 `docs/model_card.md`。
>
> **2026-09-13 状态刷新（D33/D34/D35 之后）**：R8、R9 两条**已闭环**（E1 已跑满 test 全量 944、
> τ_b 已按新草稿源重校准）；新增 **R24**（判分器测量效度）。下文各条目的"当前状态/残余敞口"
> 已按此刷新，文末状态汇总同步更新。

---

## 0. 风险总览

| 编号 | 风险 | 触发条件 | 状态 | 是否影响核心结论 |
|---|---|---|---|---|
| R1 | 网络中间层劫持，法律原文不可信 | 已实测触发 | **已触发并已处置** | **是** |
| R2 | 案号库与文书为合成数据 | 已触发（600+600 条） | **已触发并已处置**（口径已披露） | **是** |
| R3 | 人工录入法条语料覆盖不足（民法典仅 62/1260） | 已触发 | **已触发，部分处置** | **是** |
| R4 | E0 门控信号红灯（神经信号汇总 AUC < 0.60） | 已触发 | **已触发并已处置**（hybrid 路由） | **是**（路由方案） |
| R5 | 复杂度评分高 AUC 的构造重叠 | 已触发 | **已触发并已处置**（披露 + 由端到端决定） | **是** |
| R6 | 无 GPU，模型降到 0.5B | 已触发 | **已触发并已处置** | **是** |
| R7 | 无人工标注员，标签为规则生成 | 已触发 | **已触发，未处置** | **是** |
| R8 | 计算预算超限（全量 E1 需 12–13 机时） | 已触发 | **已触发并已闭环**（子集+缓存先解燃眉；**2026-09-12 E1 已跑满 test 全量 944**，D33） | **是**（旧口径 E1 覆盖率；现已解除） |
| R9 | 桶级阈值未正式校准 | 已触发 | **已触发并已闭环**（2026-09-11 首校 + **2026-09-12 按新草稿源重校准**，D33-3） | **是**（已解除） |
| R10 | 校准目标函数方向矛盾（否则检索率恒 100%） | 已触发（手册内部矛盾） | **已触发并已处置** | **是** |
| R11 | 时效状态机误判现行法为已修订 | 已触发（开发期实测） | **已触发并已处置** | 否 |
| R12 | 文本归一化破坏条文标点 | 已触发（开发期实测） | **已触发并已处置** | 否 |
| R13 | 项序按字符串排序导致引用顺序错乱 | 已触发（开发期实测） | **已触发并已处置** | 否 |
| R14 | E6 满分是构造性结果，可能被误读为真实能力 | 已触发 | **已触发并已处置**（口径限制） | **是**（表述风险） |
| R15 | 法律专用模型基线缺失，改用代理基线 | 已触发 | **已触发并已处置**（命名隔离） | **是**（该对照臂） |
| R16 | 环境/控制台故障掩盖真实运行结果 | 已触发（GBK 乱码、OpenMP 冲突、重定向编码） | **已触发并已处置** | 否 |
| R17 | 依赖与后端的单点失败（chromadb/编码器/vllm） | 已预案 | **未触发（已预案）** | 否 |
| R18 | 串法：`LIKE '%法%'` 命中旧版本沿革记录 | 已触发（开发期实测） | **已触发并已处置** | 否 |
| R19 | 概念题误入通道 B（手册判据与验收用例自相矛盾） | 已触发 | **已触发并已处置** | 否 |
| R20 | 合成库无法测量案号"假阴性" | 已触发 | **已触发，未处置**（需真实文书库） | 部分 |
| R21 | 结果被静默覆盖 / 部分失败被当成全部成功 | 已预案 | **未触发（已预案）** | 否 |
| R22 | 演示数据混入正式结果 | 已触发（`results/_demo`、`figures/_demo` 存在） | **已触发并已处置**（打戳隔离） | **是**（表述风险） |
| R23 | 最终回答改云端 API：网络依赖 + 凭据/计费 | 已触发（D30 起） | **已触发并已处置**（可一键切回本地 + 缓存 + 台账） | 部分（断网即不可用） |
| R24 | 判分器测量效度缺陷（指标测错对象）+ 多轮离线协议不可测 | 已触发（E1 QA 发现） | **已触发并已处置**（全方法统一离线重判；多轮判据选择不修并披露） | **是**（D34 改变 E1/E5 数字；D35 为负结果） |

---

## R1 网络中间层劫持：法律文本与裁判文书一律不可从网络获取

| 项 | 内容 |
|---|---|
| **触发条件** | 从公网获取法律原文或裁判文书 |
| **实测证据** | `raw.githubusercontent.com` 解析到**非公网 IP**（`web_fetch` 直接拒绝：`resolves to a non-public IP address`）；`flk.npc.gov.cn/api/list`（GET）返回 **552 字节的 SPA HTML** 而非 JSON；`flk.npc.gov.cn/api/*`（POST）返回 **405 Not Allowed**；flk 首页 HTML 含**注入的隐藏外链** `<div style="width:-100px;height:-100px;display:none"><a href="/ics/cpf/hlink123939">`；`huggingface_hub`(httpx) 报 `CERTIFICATE_VERIFY_FAILED`（改用 `urllib` 直连才成功）；而 `pypi.org` / `huggingface.co` 直连可达 |
| **影响** | ① 无法获得权威法条原文；② **更要紧的是**：在存在中间层注入的通道上获取的文本无法证明未被篡改，入库会污染整个法条库的可信度；③ 裁判文书网同样不可达，E6 失去真实真值 |
| **处置动作** | ① **不从网络获取任何法律文本或裁判文书**（理由是可证明性，不只是"抓不到"）；② 改为人工录入**关键条文种子语料**（`lawgate/knowledge/seed_corpus.py`，13 部法律），全部标记 `source_kind='MANUAL_TRANSCRIPT'`、`verification='PENDING_FLK_VERIFICATION'`（`lawgate/knowledge/build_sqlite.py: ingest_laws()`），`source_url` 指向该法在 FLK 的检索页（可追溯，但非本次抓取的具体文件）；③ 模型权重改走 `urllib` 直连清单式下载（`scripts/fetch_hf_files.py`），绕开 huggingface_hub 的 CA 问题；④ 运行时全局禁用联网（`configs/base.yaml: allow_network: false`） |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `lawgate/knowledge/seed_corpus.py`（模块 docstring 即完整披露）、`data/raw/sources.json`（逐法 `note`）、`data/kb/qa_report.md` §3"溯源与残余风险"、`docs/deviations.md` D0 |
| **残余敞口** | 全部法条内容的**权威性未获验证**：`data/kb/qa_report.md` 的"抽验 ≥95%"目前只有**自动等价性检查**（往返一致率 100%，证明"没丢字、没串条"，**不能**证明"源文本就是官方原文"）。必须执行 `scripts/import_law_text.py` 导入官方原文并升级为 `VERIFIED`（见 `docs/DATA_GAP.md` G1） |

---

## R2 案号库与"裁判文书"为程序化合成数据

| 项 | 内容 |
|---|---|
| **触发条件** | 需要案号真值以评测核验能力，但裁判文书网不可达（R1） |
| **影响** | E6 的 P/R/F1 不能代表对真实文书库的判别/覆盖能力；`case` 类金标没有真实来源 URL；最重要的是**最难的"库里没有但确实存在"的假阴性无法测量** |
| **处置动作** | ① 合成案号**遵循真实案号格式规则**（年份 + 法院代字 + 类型代字 + 序号），法院代字取自真实存在的 15 个中级/基层法院（`lawgate/knowledge/seed_cases.py: COURTS`）；② 全量打标 `data_source='SYNTHETIC'`（`build_sqlite.build_registry()` 写入 600 条案号 + 600 篇合成文书）；③ **绝不伪造 URL**：`case` 类条目的 `golden_source` 一律 `null`（`data/benchmark/counts.json: data_caveats` 第 1 条）；④ 答案里**主动**显示溯源警告——通道 B 命中合成案号时自动追加「⚠️【数据溯源】本案号来自**合成案号库**…不得作为真实引用依据」（`lawgate/channel/b_structured.py` P1 分支）；⑤ E6 结果文件自带口径说明（`results/e6/prf.json` 的 `threshold_note`、`results/e6/confusion.json` 的 `data_caveat`） |
| **当前状态** | **已触发并已处置**（口径已在 5 处披露） |
| **具体落点** | `lawgate/knowledge/seed_cases.py`、`results/e6/prf.json`、`results/e6/confusion.json`、`data/benchmark/counts.json`、`data/kb/qa_report.md` §3 |
| **残余敞口** | 真实文书导入（`scripts/import_judgments.py --replace-synthetic`）后 E6 结论口径升级、并新增假阴性专项测试（见 `docs/DATA_GAP.md` G2、本表 R20） |

---

## R3 法条语料覆盖不足：民法典仅 62 条（应为 1260 条）

| 项 | 内容 |
|---|---|
| **触发条件** | 人工录入种子语料只能覆盖"打通链路 + 产出实验"所需的**关键条文** |
| **实测规模** | `legal_provisions` **158 行**；去重 (law_short, article_no) **95** 对；现行有效去重 **80** 对（民法典 62 / 公司法 7 / 劳动合同法 7 / 民事诉讼法 1 / 民法典时间效力规定 3）；13 部法律；状态分布：现行有效 131 行 / 已废止 26 行 / 已修订 1 行（对 `data/kb/legal_facts.db` 的只读 SQL 探测，与 `data/benchmark/counts.json: kb_stats` 及 `data/kb/qa_report.md` 一致）。手册硬验收要求民法典 **1–1260 连续无缺口**，当前缺 **1198** 个条号 |
| **影响** | ① `provision` 类需要 260 条，而现行有效条文只有 80 条 → 超额 180 条是同条文**不同问法的变体**（复用系数 **3.25**），即 `provision` 类的有效样本多样性不足；② 少数已废止法律（如担保法）的**目标替代条文不在语料内** → 11 条 `temporal_trap` 条目的 `golden_provisions` 为空；③ 库中 `validity_status='已修订'` 的条文只有 1 条（公司法(2018修正)第 26 条）→ T2 陷阱混合使用"被后续立法改写的已废止条文" |
| **处置动作** | ① **不凑数、不隐藏**：`data/benchmark/counts.json` 的 `provision_construction` 如实报告"真实去重条数 80 / 复用系数 3.25"；② `temporal_trap` 逐条标注 `slots.coverage_note` / `slots.coverage_gap`，**不给语义不相关的兜底条号**（"宁缺毋滥"，`golden_provisions_empty.count = 111`）；③ `data/kb/qa_report.md` 在验收判定区打印 ⚠️「**未达手册验收**：种子语料仅收录关键条文。须执行 `python scripts/import_law_text.py` 导入 FLK 官方《民法典》全文」；④ `data/kb/manual_audit.csv` 生成 45 条抽验清单供法学生核对 |
| **当前状态** | **已触发，部分处置**（缺口已如实披露，但语料未补） |
| **具体落点** | `data/kb/qa_report.md`、`data/benchmark/counts.json`、`data/benchmark/construction_log.md` §2、`data/benchmark/verify_report.md` |
| **残余敞口** | 官方原文未导入前，S2.4 的"抽验 ≥95%"只能给自动等价性检查结果，**不能**作为条文正确性的验收证据 |

---

## R4 E0 红灯：神经不确定性信号的**全桶统一**门控不可用

| 项 | 内容 |
|---|---|
| **触发条件** | E0 门控信号诊断在 dev 集上算四个神经信号对 `need_retrieval` 的 AUC |
| **实测结果** | `figures/e0/e0_auc.json`（dev n=236，正 158 / 负 78）：汇总 AUC margin **0.5553** / entropy **0.5131** / variance **0.5565** / neglogp **0.4920**，**四个全部低于手册 0.60 下限 → 触发红灯**（手册规定"关闭信号门控，改意图分类 + 复杂度路由"） |
| **影响** | 若照红灯字面执行，就要放弃"门控"这一核心机制；但分桶数据显示汇总结论是**统计假象**：b2 法条查询 variance **0.8199**（信号有效）、b1 概念咨询 **0.1900**、b4 多轮 **0.2775**（信号**反向**）、b3 案例检索 `null`（标签恒定，AUC 无定义）；分类别 provision 0.9647、temporal_trap 0.7983、multi-turn 0.2775 |
| **处置动作** | ① 诊断为**辛普森悖论**：`need_retrieval` 标签由**类别规则**生成（case 恒为 1、provision 按问法分、concept 40% 随机切分），桶间标签分布差异主导了汇总 AUC，把桶内真实判别力抵消了；② **在手册风险表框架内**改默认路由模式为 **`hybrid`**：b2 用神经不确定性信号、b1/b3/b4 用确定性复杂度评分（`lawgate/router.py: RouterOptions.router_mode="hybrid"`、`signal_buckets=("b2",)`；实现见 `lawgate/gate/complexity.py` 模块注释）；③ **不绕过手册贡献**：τ_b 仍逐桶在 dev 上校准，三通道异构不变，只是"闸门信号按桶取用"；④ 三种模式（`hybrid`/`signal`/`complexity`）全部进入 E2/A3 消融，让结论由端到端 acc–RR 决定而非 AUC |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `lawgate/router.py`、`lawgate/gate/complexity.py`、`figures/e0/e0_auc.json` 的 `verdict.simpson_note`、`docs/deviations.md` D18 |
| **残余敞口** | 结论建立在**规则化标签 + 0.5B 模型**上；人工标注 + 大模型下必须重跑 E0（`docs/DATA_GAP.md` G3/G4） |

---

## R5 复杂度评分的高 AUC 含**构造重叠**（会高估该替代方案）

| 项 | 内容 |
|---|---|
| **触发条件** | 看到 `complexity` 的汇总 AUC **0.861**（绿灯）就切到"全部桶用复杂度评分" |
| **影响** | 该 0.861 中有相当部分是**"用类别规则预测类别规则"**：`need_retrieval` 标签由类别规则生成，而 `complexity_score()` 显式含 `category_prior`（case +0.30 / temporal_trap +0.20）与 `topic` 特征，两者共享同一套规则来源 |
| **处置动作** | ① 在 E0 产物里**明文披露**（`figures/e0/e0_auc.json` 的 `construct_overlap_caveat`），不隐藏；② 给出**判别证据**：复杂度评分在 **`provision` 类别内** AUC = **1.0**，正是 `boilerplate_penalty` 恰好识别出生成标签时使用的"直接问 vs 场景问"划分——这是构造重叠的铁证；③ 明确"**汇总 AUC 不是有效的仲裁指标**"，路由方案由 E1 端到端 acc–RR 决定；④ 在 `docs/deviations.md` D18 的"必须同时披露的两条反向证据"中重复一次 |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `figures/e0/e0_auc.json`（两个 caveat 字段）、`docs/deviations.md` D18、`lawgate/eval/benchmark.py` 模块 docstring |
| **残余敞口** | 复杂度评分的权重是**人工设定、不调参**（`complexity.py` 注释说明：调参会把该模块变成第二个不可解释的黑箱），因此它作为闸门的泛化能力未经验证；人工标签下需重测 |

---

## R6 无 GPU：语言模型降到 0.5B（绝对分数不可比）

| 项 | 内容 |
|---|---|
| **触发条件** | 需要 CUDA 才能跑手册指定的 Qwen2.5-7B / 1.5B |
| **实测证据** | `docs/env_report.json`：`torch 2.11.0+cpu`、`cuda_available=false`、`cuda_device_count=0`；`vllm.available=false`；本机确有 `Qwen/Qwen2.5-0.5B-Instruct` 完整缓存；1.5B 权重已下载完成（`models/qwen2.5-1.5b-instruct/model.safetensors` 3087467144 字节，`ok: true`，约 1.81 MB/s）但实验采用 0.5B |
| **影响** | 0.5B 的答案质量与指令遵循能力显著弱于 7B，**绝对准确率不可与手册预期对比**；门控信号质量同样受模型规模影响（R4 的红灯结论需在大模型上复测） |
| **处置动作** | ① 按手册风险表执行降级（"GPU 不可用 → 改为小模型 + 云额度；通道 B 与 E5/E6 纯 CPU 照常跑"）；② 保证**方法间对比公平**：6 个方法共用同一模型、同一 `max_new_tokens`、同一 `top_k`/`rerank`/`k_draft`（`lawgate/eval/run_exp.py` 的 `common` 参数与 `RouterOptions` 同步传递，`baselines.build_methods()` 统一注入）；③ 所有结果的图注与 provenance 自动写入实际模型名与设备（`Settings.provenance()`、`io.stamp_footer()`）；④ `VLLMLLM` 代码路径完整保留，`llm_backend: vllm` 时即可切换 |
| **当前状态** | **已触发并已处置**（降级 + 公平性保障 + 溯源） |
| **具体落点** | `lawgate/config.py: CAUSAL_MODEL_CANDIDATES`、`lawgate/channel/llm_base.py: get_llm()`、`docs/deviations.md` D9、`docs/env_report.json` |
| **残余敞口** | 拿到 GPU 后恢复 `device: cuda:0` / `load_in_4bit: true` / `llm_backend: vllm` 与更大模型，重跑 E0/E1（`docs/DATA_GAP.md` G3） |

---

## R7 无人工标注员：全部标签为规则化生成

| 项 | 内容 |
|---|---|
| **触发条件** | 需要一个可计算的"金标"，但环境内没有标注员，也没有可用的外部 LLM 裁判 |
| **影响** | 这是**效力最关键的敞口**：① `need_retrieval` 是规则切分（concept 40% 随机、case 恒 1、provision 按问法），它直接决定了 R4/R5 的诊断结论；② `correct` 是**机械代理判据**（词面/结构），对"答案表述质量"只能给近似分；③ 难以声称"达到人工标注的一致性水平" |
| **处置动作** | ① 全链路**显式标注来源**：条目的 `slots.need_retrieval_source='rule-based'`、`annotators=['A','B']` 与 `arbitrated=false` 明确定义为**占位字段**（`lawgate/eval/benchmark.py` 模块 docstring + `data/benchmark/counts.json` 的 `need_retrieval_source: "rule-based (NOT human-annotated)"`）；② κ 报告**标题即警告**，并把"结论口径（必须照抄进论文，禁止改写）"写进 `kappa_report.md`；③ `metrics.py` 同时输出 `refusal`/`answer_len`/`cited_golden`/`key_recall`/`score_detail` 等原始字段，使后续换人工判分**无需重跑模型**；④ 提供 `docs/annotation_guide.md`（1180 条双盲标注协议 + 仲裁流程 + κ≥0.7 验收与再对齐流程）与 `scripts/kappa.py`（`--simulate` 仅用于验证流水线） |
| **当前状态** | **已触发，未处置**（披露完备，但人工标注未做） |
| **具体落点** | `lawgate/eval/metrics.py`、`lawgate/eval/benchmark.py`、`data/benchmark/counts.json`、`data/benchmark/construction_log.md` §6、`data/benchmark/kappa_report.md`、`docs/annotation_guide.md` |
| **残余敞口** | 人工标注 + 真实 κ 完成后，E0 与 hybrid 路由的论证必须重做（`docs/DATA_GAP.md` G4） |

> **κ 的特别声明**：`data/benchmark/kappa_report.md` 中的 `need_retrieval κ=0.7263` 与
> `golden_provisions κ=0.8920` 来自**模拟标注员**（由 `scripts/kappa.py --simulate --noise 0.12`
> 从 `all.jsonl` 程序化生成两份假投票，共同 qid 300 个，文件在
> `data/benchmark/simulated_annotators/`），**只证明 κ 流水线可运行、可复现**。
> **禁止**表述为"标注者间一致性已达 κ=X"，**禁止**作为"人工标注已完成"的证据。

---

## R8 计算预算超限：全量 E1 需 12–13 机时

| 项 | 内容 |
|---|---|
| **触发条件** | E1 要在 test 全量（944 条）× 6 方法上生成；CPU-only 单条约 10–19 s |
| **实测证据** | 128-token：1 进程 × 14 线程 p50 **18.9 s**；**4 进程 × 3 线程 p50 163.8 s（mean 155.5 s）**——线程级并发是**负收益**（`results/timing/`）。全量 E1 需 **12–13 机时**（口径：本地 1.5B、128 token；现已不适用——回答改为 API 后单条 1–3 s），超出当时的交付窗口；且沙箱**禁止外部进程编排**（`Wait-Process` 被拒为 `Access is denied`）与命名管道 |
| **影响** | ① E1 只能在**子集**上跑：`test_e1` = **300 条 = test 的 31.8%**，E1 的 acc–RR 点估计精度受限；② `max_new_tokens` 由 192 压到 **80**，答案完整性受限、绝对准确率被压低（**①②均已于 2026-09-12 解除，见"当前状态"**） |
| **处置动作** | ① **降价 token**：`max_new_tokens=80`（判分基于条文引用与关键词召回，80 token 足以给出"法律名 + 条号 + 规则"）；② **构造分层子集**（`scripts/make_subsets.py`，seed=42 全过程可复现，manifest 落 `data/benchmark/subsets_manifest.json`）：`dev_calib` 144 条、`test_e1` 300 条（**保留 test 的全部 temporal_trap 96 与全部 case_verify 80**，多轮按整组抽 20 组 = 60 条）、`test_e4` 60 条；③ **抽样过程公开**：manifest 的 `coverage_note` 明确写出"E1 结论基于 test 的 31.8% 子集（n=300/944），必须随结果披露"；④ **缓存折叠重复**：`lawgate/cache.py` 内容寻址缓存让 4 个方法共享同一 prompt（实测把唯一生成数从约 1.5 万降到千级，当前缓存 275 条）；⑤ 提供 `--shard/--nshards` 多进程分片路径（**不用线程并发**，见 `run_exp.RunContext` 的线程安全说明） |
| **当前状态** | **已触发并已闭环（2026-09-12，D33）**：当初的处置（子集 + 缓存 + 降价 token）让实验先跑起来；后因回答模型换成 API（单条 1–3 s，D30），算力约束消失，E1 已**跑满 test 全量 944 条**（`results/e1/summary.csv`），`max_tokens` 也回到 **192** 的演示口径 |
| **具体落点** | `data/benchmark/subsets_manifest.json`、`scripts/make_subsets.py`、`lawgate/cache.py`、`docs/deviations.md` D19（历史）与 **D33**（补完） |
| **残余敞口** | 已无（E1 全量已落盘）；`test_e1`(300)/`test_e4`(60) 仍是 **E2/E3** 的口径，其绝对 acc 与 E1 全量**不可横比**（构成效应，见 README §6 第 8 条）；旧口径（1.5B/tok80）产物已归档 `results/_archive/2026-09-11_qwen1.5b_tok80/`，**禁止与新数字并排引用** |

---

## R9 桶级阈值尚未正式校准（核心指标暂时不成立）

| 项 | 内容 |
|---|---|
| **触发条件** | 路由判据 `u > τ_b` 依赖 `configs/thresholds.json` 中的四个 τ_b |
| **实测证据** | 该文件当前是**占位值**：`{"b1":0.1,"b2":0.1,"b3":0.1,"b4":0.1, "_note": "占位默认值。全部实验前必须由 scripts/calibrate.py 在 dev 集上重算并覆盖本文件…"}`；`load_taus()` 在文件缺失时亦返回全 `0.10` |
| **影响** | "**检索调用下降 ≥40%**"这一核心指标**尚未在正式校准的工作点上报告**；若用占位 τ，RR 只是巧合数值而非"满足精度约束的最小检索代价"工作点 |
| **处置动作** | ① 文件内自带 `_note` 明示占位，禁止把当前值当作已校准结果；② `lawgate/gate/calibrate.py` 已完整实现校准（网格 0.01–0.50 共 50 点、`DELTA=0.005`、默认 `rule='largest_feasible'`），只需在 dev 基线与 E0 信号就绪后运行 `python scripts/calibrate.py`；③ `run_pipeline.py` 把校准作为**第 2 步**（dev 基线之后、E1 之前），顺序上不会漏；④ `scripts/calibrate.py` 会把 **signal / complexity / hybrid 三种模式**的校准结果全部写入 `results/calibrate_report.json`，不做隐藏 |
| **当前状态** | **已触发并已闭环（2026-09-12，D33-3）**：`configs/thresholds.json` 现为 **b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0**（`dev_calib` 144 条、`grid_max=1.0`、`largest_feasible`）；`results/calibrate_report.json` 已落盘（三种模式全在） |
| **具体落点** | `configs/thresholds.json`、`results/calibrate_report.json`、`lawgate/gate/calibrate.py: load_taus()` / `calibrate()`、`scripts/run_pipeline.py` 第 2 步、`docs/deviations.md` **D33-3** |
| **残余敞口** | ① **b1 仍 `feasible:false`**（该桶 `acc_if_always_retrieve` 0.25 < 目标 0.4742，没有任何 τ 能达标），0.29 是 `largest_feasible` 兜底，须随结果披露（D13 口径）；② 换回答/草稿后端后必须重走"看 u 分布 → 重校准"（D30 教训）；③ `load_taus()` 缺文件时静默退回全 0.10 |

---

## R10 校准目标函数方向矛盾（若不修正，检索率恒为 100%）

| 项 | 内容 |
|---|---|
| **触发条件** | 按手册原文"取满足 `acc(τ_b) ≥ acc_AlwaysRAG − δ` 的**最小** τ_b"执行校准 |
| **影响** | 路由判据是 `u > τ_b → 检索`，因此 τ 越小 → 超过阈值者越多 → **检索越多**；`acc(τ)` 关于 τ 单调不增 → 可行集为 `τ ≤ τ_max`；该集合里的**最小** τ 就是网格下界 → **检索率 100%**，与项目核心指标"检索调用下降 ≥40%"**直接矛盾**。即：手工册会**自己做不出自己的核心指标** |
| **处置动作** | ① 默认规则改为 `rule='largest_feasible'`——在满足精度约束前提下取**最大** τ，即"满足精度约束的最小检索代价"工作点（Pareto 框架下的正确选择）；② 手册原式保留为 `rule='smallest_feasible'`，由 `scripts/calibrate.py` 把**两种规则的结果都写进报告**，作为可核查的消融对照；③ 报告字段保留 `tau_min_feasible` / `tau_max_feasible` / `acc_at_tau` / `rr_at_tau`，使方向修正**可复核** |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `lawgate/gate/calibrate.py` 模块 docstring 与 `calibrate()`、`docs/deviations.md` D13 |
| **残余敞口** | 无（属于方法学修正，不引入新数据依赖） |

---

## R11 时效状态机误判现行法为"已修订"（串法）

| 项 | 内容 |
|---|---|
| **触发条件** | `check_law()` 用 `from_law LIKE '%法%'` 查询沿革表 |
| **影响** | 查 `公司法` 会命中 `公司法(2018修正)` 这条**旧版本**沿革记录，于是 **2024-07-01 起已生效的现行《公司法》被误判为"已修订"**——即整条"时效安全"结论反向 |
| **处置动作** | ① 沿革查询改为**仅精确匹配** `from_law = law_short`（`_lifecycle_rows()`）；② 沿革链改为收集**全部后继**、按生效日排序、去重、限深 6（`_trace_chain()`），修复分支处断链；③ 条号预览限定 `paragraph_no=1 AND item_no=''`，避免"一条多项"时预览取到最后一个项 |
| **当前状态** | **已触发并已处置** |
| **验证** | `docs/quickcheck.txt` §2：`公司法47 @2024-06-30 = 尚未生效`、`@2024-07-02 = 现行有效`、`公司法(2018修正)26 = 已修订` |
| **具体落点** | `lawgate/channel/b_temporal.py`、`docs/deviations.md` D14.1–D14.3 |
| **残余敞口** | 无 |

---

## R12 文本归一化破坏条文标点（NFKC）

| 项 | 内容 |
|---|---|
| **触发条件** | 为处理全角空格而在解析时调用 `unicodedata.normalize("NFKC", line)` |
| **影响** | NFKC 会把**全角标点与数字转成半角**：原文"借款合同是借款人向贷款人借款，到期返还…"里的中文逗号被改写成半角 `,`。对法条库这是**文本保真破坏**——引用条文时标点与官方文本不符，且可能改变语义 |
| **处置动作** | ① `normalize_lines()` **只处理不可见空白**（`\u3000`、`\xa0`、`\u200b`、`\r`、`\t`、连续空格），**不做任何 Unicode 归一化**；② 项标记识别改用同时接受 `（）` 与 `()` 的正则 |
| **当前状态** | **已触发并已处置** |
| **验证** | `docs/quickcheck.txt` 中第 667 条正文为全角逗号原文（`docs/deviations.md` D5 的验证条目） |
| **具体落点** | `lawgate/knowledge/flk_parser.py`、`docs/deviations.md` D5 |
| **残余敞口** | 无 |

---

## R13 项序按字符串排序导致条文引用顺序错乱

| 项 | 内容 |
|---|---|
| **触发条件** | 按手册写法 `ORDER BY paragraph_no, item_no` 排序款项 |
| **影响** | `(一)(二)(三)(四)` 的 Unicode 码位**并非数字序**（一 = U+4E00、三 = U+4E09、二 = U+4E8C、四 = U+56DB），字符串排序得到 **一、三、二、四**——项序被打乱，条文引用顺序错误 |
| **实测证据** | 修复前往返一致率：民法典 88.71%、劳动合同法 57.14%、民事诉讼法 0%、收养法 0%；**全部 12 处不一致恰好都是"≥3 项"的条文**，与解释完全吻合 |
| **处置动作** | ① 新增列 `legal_provisions.item_idx INTEGER`；② 解析时按出现顺序赋 1,2,3…；③ `render_article()`、通道 B 渲染、审计比对一律按 `(paragraph_no, item_idx)` 排序 |
| **当前状态** | **已触发并已处置** |
| **验证** | 修复后 13 部法律的往返一致率均 **100%**（`data/kb/qa_report.md`） |
| **具体落点** | `lawgate/knowledge/flk_parser.py`、`lawgate/knowledge/schema.sql`、`lawgate/knowledge/build_sqlite.py`、`docs/deviations.md` D7 |
| **残余敞口** | 无 |

---

## R14 E6 满分是**构造性**结果，可能被误读为真实能力

| 项 | 内容 |
|---|---|
| **触发条件** | 评测集 `case_verify` 的 V1–V4 子类**就是按核验器的四级判定构造的**，因此 100% 是构造预期 |
| **实测结果** | `results/e6/prf.json`：precision = recall = f1 = **1.0**（tp 30 / fp 0 / fn 0 / tn 70）；`results/e6/confusion.json` 混淆矩阵完全对角（V1 30/30→核验通过、V2 30/30→不存在、V3 25/25→存在但案由不符、V4 15/15→格式非法） |
| **影响** | 若不加限定地引用这三个 1.0，会让人误以为系统在真实裁判文书上达到完美核验——**这不成立** |
| **处置动作** | ① 两个结果文件各自携带 `threshold_note` / `data_caveat`，明写"本机案号库为合成数据，指标仅反映核验器判别能力，不代表对真实裁判文书库的覆盖"；② `counts.json` 的 `case_verify_verification.note` 明写"V1/V3 的'真实'仅指在本仓库合成 case_registry 中可命中，不代表中国裁判文书网上存在该案件"；③ `scripts/e6_case_verify.py` 模块 docstring 把"结论口径限制（必须随结果披露）"写成硬要求；④ `docs/model_card.md` 与 README 的结果表均重复该限定 |
| **当前状态** | **已触发并已处置**（表述风险已封堵） |
| **具体落点** | `results/e6/prf.json`、`results/e6/confusion.json`、`data/benchmark/counts.json`、`scripts/e6_case_verify.py` |
| **残余敞口** | 需真实文书库替换后，P/R/F1 **必然下降**，那时的数字才是可申报的真实指标（R20） |

---

## R15 法律专用模型基线缺失，改用"人设代理"

| 项 | 内容 |
|---|---|
| **触发条件** | 手册要求加载 LawGPT_zh / ChatLaw 开源权重作为对照基线 |
| **影响** | 本机无这两个模型，且**无法从被劫持的网络获取第三方权重**（R1）→ 若不做处理，"通用模型 vs 法律专用模型"的对比会缺失或失真 |
| **处置动作** | ① 改用"同一底座模型 + 法律专家系统提示 + 不检索"作为**代理基线**，并**显式命名 `LegalLLMPersona`**（方法名 `legal_llm`，`LEGAL_PERSONA` 提示词常量）；② 在结果表里以星号标注 `Legal-LLM*`，避免与真实法律模型混淆；③ 在代码 docstring 与偏差登记中写明：该基线**不是**法律微调模型，**不能**用于支持"通用模型 vs 法律专用模型"的结论，只能说明"仅换系统提示、不加检索与结构化通道"的效果上界 |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `lawgate/eval/baselines.py: LegalLLMPersona`、`docs/deviations.md` D16 |
| **残余敞口** | 该对照臂的结论受限；真实权重到位后须重跑（`docs/DATA_GAP.md` G6） |

---

## R16 环境与控制台故障掩盖真实运行结果

| 项 | 内容 |
|---|---|
| **触发条件** | 在 Windows + Anaconda + GBK 控制台环境下运行 |
| **实测证据** | ① **OpenMP 双运行时冲突**：Anaconda MKL 与 torch 同时链接 `libiomp5md.dll`，不设 `KMP_DUPLICATE_LIB_OK` 直接崩；② **控制台 codepage 为 GBK**：脚本的中文 stdout 全部乱码，**看起来像崩溃其实已跑通**（`quickcheck.py` 的 72 行结果已正确写入 `docs/quickcheck.txt`）；③ **重定向编码陷阱**：Windows PowerShell 5.1 的 `python x.py > out.txt` 写出 **UTF-16LE**（见 `docs/_e6.txt`、`docs/_t4.txt` 均为 `ff fe` 开头），第三方按 UTF-8 读会当成二进制 |
| **影响** | 可能把"成功的运行"误判为失败（或把失败当成成功），从而**做出错误的工程决策**；也可能把 UTF-16 日志误当损坏文件 |
| **处置动作** | ① `lawgate/__init__.py` 在任何 torch 导入之前 `setdefault("KMP_DUPLICATE_LIB_OK","TRUE")` 与 `TOKENIZERS_PARALLELISM=false`；`lawgate/api/app.py`、`lawgate/api/ui.py`、`scripts/run_pipeline.py`、`scripts/quickcheck.py`、`scripts/e0_diagnostic.py` 等入口重复设置；② **一切中文结果写文件而非打印控制台**：脚本用 `Path.write_text(..., encoding="utf-8")` 或 `Out-File -Encoding utf8`（`docs/quickcheck.txt`、`data/kb/qa_report.md`、`figures/e0/e0_auc.json` 等）；③ 脚本的控制台输出**一律保持 ASCII**（如 `report_path`、`n_items=… backend=…`），绘图模块亦遵守（`plotting.py` 设计约定第 5 条）；④ 本项目的文档与验收以**文件内容**为准，不以控制台输出为准 |
| **当前状态** | **已触发并已处置** |
| **具体落点** | `lawgate/__init__.py`、`lawgate/api/app.py`、`scripts/quickcheck.py`（`OUT = Path("docs/quickcheck.txt")`）、`lawgate/eval/plotting.py` 模块设计约定 |
| **残余敞口** | 需在 README/系统说明书中向使用者重复提示（已在 `README.md` §1 的"三条踩过的坑"与 `docs/system_manual.md` §2.3 写明） |

---

## R17 依赖与后端的单点失败

| 项 | 内容 |
|---|---|
| **触发条件** | chromadb 持久化/遥测失败、编码模型缺失、BM25 依赖缺失、vllm 不可用、sentence-transformers 版本 API 改名 |
| **影响** | 若任一环节直接抛异常，全链路不可运行 |
| **处置动作（已预案，部分已实测生效）** | ① `get_store(prefer)` 先试 `ChromaStore`，失败退 `NumpyStore`（精确余弦，`embeddings.npy` + `records.jsonl`），后端名写进 `RetrievalResult.backend`；② `get_embedder()` 先试本地 bge 目录与 HF 缓存，失败退 `HashingCharEmbedder`（**不是语义模型**，名称会写进 `embed_backend`）；③ `BGEEmbedder.__init__` 兼容 `get_embedding_dimension` / `get_sentence_embedding_dimension` 两个方法名；④ `Reranker._bm25()` 在 `rank_bm25` 不可用时返回全 0，降级为"字符覆盖 + 结构命中"；⑤ `get_llm()` 三级降级（HFLLM → VLLMLLM → ExtractiveLLM）并把 `llm_backend` 写进 trace；⑥ `plotting._no_data()` 在数据缺失时画 `NO DATA` 占位图而不抛异常 |
| **当前状态** | **未触发（已预案）**——本机 chromadb 1.5.9 与 sentence-transformers 5.5.1 均正常工作，向量库确实由 chromadb 构建（`vector_build.json: backend="chromadb"`） |
| **具体落点** | `lawgate/knowledge/store.py`、`lawgate/knowledge/embed.py`、`lawgate/knowledge/rerank.py`、`lawgate/channel/llm_base.py`、`lawgate/eval/plotting.py` |
| **残余敞口** | 降级后端的产出**必须**与正式结果分开统计（各降级点在 trace/产物中均有标记位） |

---

## R18 串法：`LIKE '%法%'` 命中旧版本沿革记录

见 R11（`docs/deviations.md` D14.1）。此处单列仅为与手册风险表条目对齐：
处置为**仅精确匹配** `from_law`，验证见 `docs/quickcheck.txt` §2。**已触发并已处置**。

---

## R19 概念题误入通道 B（手册判据与验收用例自相矛盾）

| 项 | 内容 |
|---|---|
| **触发条件** | 采用手册的完整性判据 `or (slots.topic and not slots.article_no)` |
| **影响** | 手册自己的冒烟用例 6「什么是离婚冷静期」中，"离婚冷静期"命中 `TOPIC_KEYWORDS['离婚纠纷']`，于是 `hit_provision=True` 且 `slots_complete=True`，**直接走通道 B 主题 FTS**——与手册期望的"通道 A 或 C"冲突；即手册判据**与自己的验收用例不自洽**。同时，手册缺少"法律名 + 时效问句"路径，导致 S3.2 的验收用例「担保法现在还有用吗」（无条号）三条路径都接不住 |
| **处置动作** | ① 主题路线额外要求出现**法条索取标记**（`PROVISION_SEEKING`：哪条/依据/法条/条文/条款/第/怎么规定/原文/内容是什么…），未出现标记的纯概念题不进通道 B（`route_b_reason=""`）；② 新增 **P3「法律效力询问」**路径（`law_short ∧ law_validity_query`），返回时效结论 + 逐条替代条文原文与官方来源 + 沿革链 |
| **当前状态** | **已触发并已处置** |
| **验证** | `docs/quickcheck.txt` 意图判定 **6/6**，其中「什么是离婚冷静期」`slots_complete=False`；「担保法现在还有用吗」返回"已废止"+民法典替代条文；界面第 ⑤ 个预设按钮即为此用例 |
| **具体落点** | `lawgate/gate/intent.py`、`lawgate/channel/b_structured.py`（P3）、`docs/deviations.md` D10/D12 |
| **残余敞口** | `PROVISION_SEEKING` 中单字标记「第」可能对含"第"的无关问句误触发（如"第一步"）；因同时要求主题词命中，实际影响面小，但人工标注阶段应作为分歧点复核 |

---

## R20 合成库无法测量案号"假阴性"

| 项 | 内容 |
|---|---|
| **触发条件** | 案号核验的"不存在"判定只能相对于**手头这个库**成立 |
| **影响** | 真实世界里最难、也最要紧的误差是"**库里没有但确实存在**"（例如案号库未覆盖该法院/年份）——本合成库上**无法测量**该假阴性率；因此当前 E6 的召回率 1.0 **不能**解释为"不会误杀真实案号" |
| **处置动作** | ① 在 `docs/deviations.md` D15 明确写出"残余风险（重要）"；② `results/e6/prf.json` 的 `threshold_note` 与 `confusion.json` 的 `data_caveat` 均限定了指标含义；③ 提供 `scripts/import_judgments.py`（导入真实文书后 `data_source` 升级为 `CJWS`，可支撑"库外真实案号"专项测试） |
| **当前状态** | **已触发，未处置**（需真实文书库） |
| **具体落点** | `docs/deviations.md` D15、`scripts/import_judgments.py`、`docs/DATA_GAP.md` G2 |
| **残余敞口** | 真实文书导入后须新增"库外真实案号假阴性率"这一指标并重跑 E6 |

---

## R21 结果被静默覆盖 / 部分失败被当成全部成功

| 项 | 内容 |
|---|---|
| **触发条件** | 重复跑实验、多步流水线部分失败 |
| **影响** | 旧结果被新结果覆盖或丢失；或"9 步里第 3 步失败"被当成整体成功，得出错误结论 |
| **处置动作** | ① `lawgate/eval/io.py: write_jsonl()` 在目标文件存在时抛 `FileExistsError`（**手册 S6.1 硬规则：拒绝静默覆盖**），只有显式 `--overwrite` 才重写；② `run_pipeline.py` 每一步 `try/except`，失败**不阻断**后续步骤，并把 `{step, cmd, ok, error, seconds}` 写入 `docs/pipeline_steps.json`（**2026-09-12 两轮流水线共 35 步，全部 `ok: true`**；`docs/pipeline.log` 保留完整台账）；③ 分片运行时不写主文件，只写 `results/<exp>/shards/…_part<i>.jsonl`，需显式 `--merge` 才合成正式结果 |
| **当前状态** | **未触发（已预案）** |
| **具体落点** | `lawgate/eval/io.py`、`scripts/run_pipeline.py`、`docs/pipeline_steps.json` |
| **残余敞口** | 无 |

---

## R22 演示数据混入正式结果

| 项 | 内容 |
|---|---|
| **触发条件** | 绘图模块自检时用合成小样本走通全部出图流程，产物与真实结果同目录 |
| **影响** | 若不加隔离，`results/_demo/`、`figures/_demo/` 里的合成数字（如 `figures/_demo/DEMO_README.json` 中 margin AUC 0.8725）可能被误当真实结果引用 |
| **处置动作** | ① 演示产物**固定写入 `_demo/` 子目录**（`scripts/plot_all.py --demo`）；② 每张演示图的图注自动加戳 `DEMO DATA / NOT REAL RESULTS`（`lawgate/eval/plotting.py: DEMO_MARK`）；③ `figures/_demo/DEMO_README.json` 顶部即 `"warning": "DEMO DATA / NOT REAL RESULTS"`，并含 `"note": "所有数值均为合成数据，仅用于验证绘图管线；切勿写入论文或与真实结果混用。"`；④ README 与系统说明书均把 `_demo` 定义为"冒烟夹具，不是结果" |
| **当前状态** | **已触发并已处置**（已有 8 张演示图 + 9 个演示结果文件被隔离打戳） |
| **具体落点** | `figures/_demo/`、`results/_demo/`、`figures/_demo/DEMO_README.json`、`lawgate/eval/plotting.py`、`scripts/plot_all.py` |
| **残余敞口** | 无（只要引用时带上 `_demo` 路径） |

---

## R23 最终回答模型改为云端 API：新增**运行时网络依赖**与**凭据/计费**面（D30）

| 项 | 内容 |
|---|---|
| **触发条件** | 把最终回答的大模型从本地权重（fuzi-mingcha 6.7B）换成 DeepSeek 官方 API（`deepseek-v4-flash`）后，每次生成都要发 HTTPS 请求 |
| **影响** | ① **断网/无密钥即答不出**（此前本地权重离线可用）；② 每次生成**产生费用**，且费用随 token 变化；③ 密钥一旦泄露（截图/聊天记录/误提交）等于把账号交出去；④ 云端模型是**会变的**（服务端把旧模型名路由到新版本，实测 `deepseek-v4-flash` 回报 `model=deepseek-flash`），同一份题面在不同日期不保证逐字可复现 |
| **处置动作（已实测生效）** | ① **一键切回本地**：`启动服务.ps1 -LlmBackend hf` 或 `$env:LAWGATE_LLM_PROVIDER="hf"`，本地 fuzi-mingcha 权重仍在 `models/`，离线演示不受影响；② **密钥只走 `.env`/环境变量**（`.gitignore` 已排除），代码与 `configs/base.yaml` **从不写明文**，`/health` 与 `check_backend.py` 只报"有没有"；③ **费用透明**：`DeepSeekLLM.describe()` 与逐次调用台账 `call_log` 记录每次的 prompt/completion token 与耗时，`/health` 的 `cache` 块给出缓存节省量；④ **内容缓存**（`data/kb/gen_cache.db`）让重复提问**不重复计费**，也保证同一题面在本机逐字可复现；⑤ 真机自检 `scripts/check_deepseek.py`（离线段 29/29 + `--live`）作为日常回归（原冒烟 `scripts/smoke.py`（历史 20/20）已于 2026-09-16 删除，D37，结论留档 deviations.md） |
| **当前状态** | **已触发并已处置**（残余敞口见下） |
| **具体落点** | `lawgate/channel/deepseek_llm.py`、`lawgate/channel/draft_source.py`、`lawgate/env_setup.py`（读 `.env`）、`configs/base.yaml: llm_backend`、`.env`（不提交）、`scripts/check_deepseek.py`、`docs/deviations.md` D30 |
| **残余敞口** | ① **断网即不可用**（除非切回本地），答辩/演示前应确认网络与密钥（`scripts/check_backend.py` 秒级可查）；② ~~门控阈值 τ_b 尚未按新草稿模型重新校准~~ → **已于 2026-09-12 解除**（D33-3：b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0）；换后端仍须重走"看 u 分布（`scripts/check_deepseek.py --live --with-local-draft`）→ 重校准"；③ 历史 E0–E6 结果的**旧口径**（1.5B/tok80）已归档 `results/_archive/2026-09-11_qwen1.5b_tok80/`，新口径 E1–E5 已全部落盘（D33），**新旧禁止并排引用**；④ 云端模型版本漂移不可控（只能靠缓存与 `provenance` 留痕）；⑤ **实际花费已有台账**：`data/kb/gen_cache.db` 记录 deepseek generate **3188 条 / 1,009,487 tokens**（含全部实验与 τ 缩放臂），重复提问不二次计费 |
| **与偏差的对应** | D30（另见 D29：上一版接入的是本地 fuzi-mingcha） |

---

## R24 判分器测量效度缺陷：指标测错了对象（D34）+ 多轮离线协议不可测（D35）

| 项 | 内容 |
|---|---|
| **触发条件** | 自动判分器（`lawgate/eval/metrics.py`）用**字符串规则**判"引用失效法 / 时效正确"，规则与被判文本的真实语义不一致时即触发 |
| **实测证据** | E1 跑完后 QA 发现 legalgate 的 TVC 与 Always-RAG **完全相同（0.3333）**，与"时效题走通道 B"的设计预期矛盾。逐条透视定位出三层缺陷：① 通道 B 的**继承陈述句**"【法律沿革】合同法 → 民法典"被整条记为非法引用（E1 上误杀 **61 条**满分警示答案）；② 法名**子串误命中**——现行有效的《劳动合同法》含子串"合同法"→ 被记为引用已废止《合同法》；③ **否定警示句**"不能继续引用《担保法》"被记为非法引用 |
| **影响** | "通道 B 解决法条时效问题"这一**主打卖点在判分器上被埋没**：修复前 E1 legalgate TVC 0.3333、引用失效法率 0.2362；若不复核就要据错数字下结论。此外 E3 多轮 turn2 全 0 是**代理判据奖励冗长含糊、惩罚简洁确定**造成的假象（golden 自己 16/20 也不满足该判据） |
| **处置动作** | ① **跑完才改、全方法统一离线重判**（`scripts/rescore_results.py`，零 API 花费、确定性），原件备份 `results/_prescore_backup/`，两轮报告 `docs/rescore_report*.md`；② 判据修复：`RE_SUCCESSION` 同句豁免、`_law_hit()` 要求《法名》完整形式、`NEGATION_WARN_MARKERS` 豁免；③ 下游产物（summary/断言/统计/Pareto/E2/E5/E3 报告与图）全部重判后再生；④ **多轮 turn2 判据明知有假象也不改**——判分器本轮已因 D34 动过两次，再恰好修在"本方法得 0 分"的位置就是事后移动球门，改为如实披露 + 机制解释 |
| **当前状态** | **已触发并已处置**（①②③），**其中第④项选择"不处置"并已披露**（D35-1） |
| **验证** | `docs/rescore_report.md` 逐文件翻转数（基线 correct/tvc 翻转各 0–2 条、`dev_baseline` 零翻转 → τ_b 不受影响）；修复后 E1 legalgate acc 0.553→**0.6144**、TVC 0.3333→**0.9375**、invalid_cite 0.2362→**0.0042**；E5 TVC 0.3417→**0.9333** |
| **具体落点** | `lawgate/eval/metrics.py`、`scripts/rescore_results.py`、`results/_prescore_backup/`、`docs/rescore_report.md`、`docs/deviations.md` **D34/D35** |
| **残余敞口** | ① 修复方向**利好本项目**（只有通道 B 会产出"【法律沿革】X→Y"式文本）——已如实披露，且核心断言在修复**前**（v0 判分）即为 PASS，不是为过断言而改；② `_law_hit()` 已知偏松：句中裸提"依照合同法第52条"（前面是汉字）会漏检，本语料失效法引用几乎都以《》或句首形式出现；③ **S6.7 槽位继承指标在离线评测协议下对任何方法都不可达成**（基准 history 纯文本、无通道 B trace → 继承恒不触发，E3 实测 error_rate 1.0），列为未来工作；④ 多轮是已证实的**最弱一环**（E3 legalgate 0.4167 < 两基线；E1 分桶 b4 acc 0.3375），申报/论文必须同时呈现，不得只报 E1/E5 强结果 |

---

## 附：风险与偏差的对应关系

| 风险 | 对应偏差编号（`docs/deviations.md`） |
|---|---|
| R1 网络劫持 | D0 |
| R2 合成案号库 | D0、D15 |
| R3 语料覆盖不足 | D0（数据权威性）、`data/kb/qa_report.md` §2 |
| R4 E0 红灯 | D18 |
| R5 构造重叠 | D18 |
| R6 无 GPU / 0.5B | D9 |
| R7 无人工标注 | `construction_log.md` §6、`kappa_report.md` |
| R8 算力预算 | D19 |
| R9 阈值未校准 | D13（机制）、`configs/thresholds.json`（状态） |
| R10 校准方向矛盾 | D13 |
| R11 串法误判 | D14 |
| R12 NFKC 破坏文本 | D5 |
| R13 项序错乱 | D7 |
| R14 E6 构造性满分 | D15、`counts.json` |
| R15 代理基线 | D16 |
| R16 环境/控制台 | D0（部分）、`lawgate/__init__.py` |
| R17 后端单点失败 | 无（属工程健壮性，未列入偏差） |
| R18 串法（同 R11） | D14.1 |
| R19 概念题误入 B | D10、D12 |
| R20 假阴性不可测 | D15 |
| R21 静默覆盖 | D8（seed 与落盘规则相关） |
| R22 演示数据 | 无（属产物治理约定） |
| R23 云端 API 依赖 | D30（上一版为 D29 本地模型） |
| R24 判分器测量效度 / 多轮离线协议 | **D34、D35**（另见 D33 实验补完） |

> **状态汇总（2026-09-13 刷新，共 24 条）**：
> **20 条已触发并已处置**（R1、R2、R4–R6、R8–R16、R18、R19、R22、R23、**R24**）——
> 其中 **R8（E1 覆盖率）与 R9（阈值校准）已由 D33 闭环**；
> **1 条已触发部分处置**（R3 语料覆盖不足：缺口已披露但语料未补）、
> **2 条已触发未处置**（R7 人工标注、R20 假阴性——**都是外部资源类，本机闭不了环**）、
> **1 条未触发已预案**（R17 后端单点失败）。
> 现在**最优先**的是 R3 / R7 / R20 这三条数据真实性问题（决定标签与语料的效度），
> 补做步骤见 `docs/DATA_GAP.md` 的 G1/G4 与 G2；工程侧的"实验没跑完"与"阈值没校准"
> 已不再是缺口（D33）。
