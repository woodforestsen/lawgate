# 模型卡（Model Card）—— 面向 CA-LegalGate 系统

> **本卡片描述的是一个"系统"，不是一个"训练出来的模型"。**
> CA-LegalGate 是**免训练（training-free）**系统：它不微调任何权重、不做任何参数更新，
> 其行为完全由（a）**第三方预训练模型的权重**、（b）**结构化事实库**、
> （c）**确定性规则与校准阈值**三者共同决定。
> 因此本节的目的不是记录"训练数据与超参"，而是记录**能力边界、数据来源与失效模式**。

| 项 | 内容 |
|---|---|
| 系统名称 | CA-LegalGate（律核）· 三通道异构法律问答路由系统 |
| 版本 | `0.1.0`（`lawgate/__init__.py: __version__`） |
| 卡片版本 | v0.1 |
| 系统类型 | 检索增强生成（RAG）的**门控式变体**：通道 B 确定性结构化（0 次检索）/ 通道 A 直接生成（0 次检索）/ 通道 C 语义检索增强（1 次检索） |
| 是否训练 | **否**。无任何微调；无训练数据；无训练超参 |
| 运行环境 | Windows 11 / Python 3.13.9 / CPU-only（18 逻辑核、无 CUDA）/ torch 2.11.0+cpu |
| 产出方 | 本项目组 |
| 适用语言 | 中文（简体），法律领域（民事为主） |

---

## 1. 预期用途（Intended Use）

| 用途 | 说明 | 依据的能力 |
|---|---|---|
| ① 法条查询与条文引用 | 给定「《X法》第 Y 条」，返回条文正文并附官方来源 URL | 通道 B P2（`TemporalChecker.check_provision` + `fetch_current`） |
| ② **时效性核验**（本系统的核心差异化能力） | 判断某法/某条截至指定时点是否**现行有效 / 已修订 / 已废止 / 尚未生效**，并给出替代条文与法律沿革链 | 通道 B P2/P3、`law_lifecycle` + `SUPERSEDE_MAP` + `validity_status` |
| ③ 案号格式与一致性核验 | 判断案号是否**格式非法 / 不存在 / 存在但类型不符 / 存在但案由不符 / 核验通过** | 通道 B P1（`CaseNoVerifier`） |
| ④ 主题式条文检索（结构化） | 给定法律主题，返回现行有效条文（FTS5 主题词检索） | 通道 B P4（`provision_fts`） |
| ⑤ 语义检索增强问答 | 门控判定"需要检索"时，走向量库 + 重排后生成 | 通道 C（`Retriever` + `Reranker` + `HFLLM`） |
| ⑥ **可解释路由的教学/演示** | 展示每一步决策：通道、门控分、阈值、桶、槽位继承、来源、检索明细 | `trace`（`lawgate/router.py`） |
| ⑦ 方法论研究基准 | 作为"桶级阈值校准 + 三通道异构"这一方法学主张的研究对象与可复现实验载体 | `scripts/` 全部实验脚本、`data/benchmark/` |

**预期使用者**：法律信息检索方向的研究者、法律科技产品的原型开发者、
法律专业学生（用于理解条文时效性与案号规范）。
**预期使用场景**：研究与教学环境中的**参考性**查询；本地/离线部署下的演示与消融实验。

---

## 2. 超出范围的使用（Out-of-Scope Use）

> ### ⛔ 明确禁止与不适用
>
> 1. **不得用于真实的、个性化的法律意见。** 本系统不进行事实认定，不了解用户具体案情，
>    其输出不构成法律意见，不得作为任何法律决策的依据。
> 2. **不得用于诉讼文书、法律意见书、合同条款的引用依据。** 本系统的法条语料是
>    **人工录入的关键条文种子语料**（非官方原文），条文内容未与官方文本逐字核对；
>    案号库是**合成数据**（`data_source='SYNTHETIC'`），其中的案号**不对应真实案件**。
> 3. **不得把合成案号当作真实判例引用**，也不得引用系统给出的任何"文书信息"
>    （法院、案由、裁判日期）作为真实案例证据。
> 4. **不得用于已废止/已修订法律文本的传播**：系统的时效判定依赖种子数据与规则，
>    未覆盖全部法律体系，可能漏判。
> 5. **不得作为任何自动化决策系统（信贷、雇佣、行政处罚、司法辅助裁判）的组成部分。**
> 6. **不适用于刑事、行政、涉外、知识产权等本系统语料未覆盖的领域**——
>    库内 13 部法律全部属于民事/公司/劳动/诉讼程序范畴，其余领域无任何数据。
> 7. **不适用于对答案表达质量要求高的场景**（如生成正式法律文书段落）：
>    底座模型为 1.5B，绝对生成质量受严重限制。
> 8. **不得在未披露数据来源状态的情况下二次分发本系统的输出。**
>    任何引用必须同时给出：法条语料状态（`PENDING_FLK_VERIFICATION`）、
>    案号数据状态（`SYNTHETIC`）、标签状态（规则化生成），见 §5 与 §6。

---

## 3. 实际使用的模型与版本

| 角色 | 实际使用 | 版本/规格 | 说明 |
|---|---|---|---|
| 因果语言模型 | **`Qwen/Qwen2.5-1.5B-Instruct`** | 本地目录 `models/qwen2.5-1.5b-instruct`（`model.safetensors` 3 087 467 144 字节）；CPU fp32；14 线程 | 手册原指定 `Qwen2.5-7B-Instruct`（S3.8）；因**无 CUDA**而降级。**实际生效的为什么是 1.5B**：`lawgate/config.py` 的 `CAUSAL_MODEL_CANDIDATES` 把 1.5B 目录排在第一位，`_first_existing_dir()` 命中即用——早期文档写的"0.5B（1.5B 已下载但未采用）"**与实际不符**，已由 `docs/deviations.md` D21 更正。0.5B 现仅作为 1.5B 缺失时的兜底，其产物见 `results/pilot`、`results/timing`、`figures/e0/e0_signals_*`（口径不同，不得混用） |
| 向量编码模型 | **`BAAI/bge-small-zh-v1.5`** | `models/bge-small-zh-v1.5`，**512 维** | **手册指定模型，且已实际使用**（非降级）。查询侧加指令前缀「为这个句子生成表示以用于检索相关文章：」，文档侧不加（`lawgate/knowledge/embed.py`） |
| 检索后端 | chromadb | `1.5.9`，集合 `legal`，`hnsw:space=cosine` | 失败自动降级为 `NumpyStore`（`lawgate/knowledge/store.py`） |
| 重排器 | BM25 + 字符覆盖 + 结构命中 | `0.60·BM25归一 + 0.25·字符覆盖率 + 0.15·结构命中`（jieba 分词） | `lawgate/knowledge/rerank.py: Reranker` |
| 部署后端（未启用） | vLLM | **未安装**（`docs/env_report.json: vllm.available=false`） | `VLLMLLM` 代码路径完整，`llm_backend: vllm` 时才启用 |
| 兜底后端（未启用） | `ExtractiveLLM` | 规则抽取式，**不是语言模型** | 仅在无任何权重时启用；产出会标注 `llm_backend=rule_based` |
| 代理基线（对照方法之一） | `LegalLLMPersona` | 同一底座模型 + 法律专家系统提示 + 不检索 | **不是**法律微调模型；对应 `docs/deviations.md` D16 |

**实测性能指纹**（原载 `docs/smoke_s0.json`，该文件与生成脚本 `scripts/smoke_s0.py` 已于 2026-09-16 删除，见 `docs/deviations.md` D37；以下数字保留为历史记录）：模型加载 2.1 s；20 token 生成 1.62 s；
吞吐 **12.36 tok/s**；`scores_available=true`（真实 logprobs，门控可用）；`smoke_pass=true`。

**关键依赖版本**（`docs/env_report.json`）：torch 2.11.0+cpu、transformers 5.8.0、
sentence-transformers 5.5.1、python-docx 1.2.0、jieba 0.42.1、matplotlib 3.10.3、
scikit-learn 1.7.2、scipy 1.16.3、statsmodels 0.14.5、gradio 5.50.0、fastapi 0.136.1。

---

## 4. 决策规则与可配置项（系统"行为"的来源）

由于不训练，系统的行为由以下**显式规则与参数**决定：

| 类别 | 项 | 当前值 | 位置 |
|---|---|---|---|
| 路由 | `router_mode` | **`hybrid`**（b2 用神经信号，b1/b3/b4 用复杂度评分） | `lawgate/router.py: RouterOptions` |
| 路由 | `signal_buckets` | `("b2",)` | 同上 |
| 门控 | `signal` | `margin`（也可 `entropy`/`variance`/`neglogp`） | `configs/base.yaml` / `RouterOptions` |
| 门控 | `k_draft` | `20` | 同上 |
| 阈值 | `tau_b`（b1–b4） | **b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0**（2026-09-12 按新草稿源重校准，`dev_calib` 144 条；b1 为 `feasible:false` 兜底） | `configs/thresholds.json` |
| 校准 | `rule` | `largest_feasible`（手册原式为 `smallest_feasible`，保留为对照） | `lawgate/gate/calibrate.py` |
| 校准 | 网格 / 容差 | 内置 `GRID = 0.01…0.50`（50 点）/ `δ=0.005`；正式校准用 `--grid-max 1.0` 放宽上界（D33-3） | 同上 |
| 生成 | `max_new_tokens` | **192**（演示/接口/全部实验统一口径，D28/D33） | `configs/base.yaml` / `RouterOptions` / `scripts/run_pipeline.py --max-tokens 192` |
| 检索 | `top_k` / `rerank` | `8` / `True` | `RouterOptions` |
| 复杂度评分 | 七维权重 | 人工设定、**不调参**（long 0.30 / case_word 0.30 / topic 0.20 / multi_turn 0.10 / compare 0.10 / boilerplate −0.30（有条号再 −0.20）/ category_prior 0.30\|0.20） | `lawgate/gate/complexity.py` |
| 案号 | 年份合理区间 | `(1990, 当前年+1)` | `lawgate/channel/b_case_verify.py` |
| 门控草稿 | 草稿来源 | `auto` → 本机 `Qwen/Qwen2.5-0.5B-Instruct`（**不是**回答模型，D30） | `configs/base.yaml: draft_source` |
| 生成 | 回答模型 | **DeepSeek API `deepseek-v4-flash`（关思考）**；离线兜底 `models/fuzi-mingcha-v1_0` | `configs/base.yaml: llm_backend` |
| 缓存 | key | `sha256(model\|kind\|max_tokens\|prompt)` | `lawgate/cache.py` |

---

## 5. 数据来源与溯源状态

> **这一节是全卡片最重要的一节。** 每项数据都必须连同其**溯源等级**一起被引用。

| 数据 | 当前来源 | 规模（实测） | 溯源状态 | 补齐路径 |
|---|---|---|---|---|
| 法条正文 | **项目组人工录入的关键条文种子语料**（`lawgate/knowledge/seed_corpus.py`，`SEED_VERSION="seed-2026.09"`） | `legal_provisions` **158 行**；去重 (法, 条) **95** 对；现行有效去重 **80** 对；**13 部法律**；状态分布 现行有效 131 行 / 已废止 26 行 / 已修订 1 行 | ❌ **非官方原文**。全部 `source_kind='MANUAL_TRANSCRIPT'`、`verification='PENDING_FLK_VERIFICATION'`（13/13 条溯源记录均如此）；`source_url` 指向该法在 FLK 的检索页（可追溯，但**非**本次抓取的具体文件） | `scripts/import_law_text.py`（见 `docs/DATA_GAP.md` G1） |
| 民法典条文 | 同上，**仅 62 条**（1、3、7、10、19、20、40、143…1258） | 62 条 vs 应有 **1260** 条；缺 1198 个条号 | ❌ **远未达手册验收**（`data/kb/qa_report.md` §2 自动打印"未达手册验收"） | 同上（`--expect-max 1260`） |
| 案号真值 | **程序化合成**（`lawgate/knowledge/seed_cases.py`） | `case_registry` **600** 条，`data_source='SYNTHETIC'` **600/600** | ❌ **不对应真实案件**；来自**非**中国裁判文书网 | `scripts/import_judgments.py --replace-synthetic`（见 `docs/DATA_GAP.md` G2） |
| "裁判文书" | **程序化生成的案情摘要**（`render_full_text()`，非真实裁判说理） | `judgments` **600** 篇，`data_source='SYNTHETIC'` **600/600** | ❌ 同上；`case` 类条目的 `golden_source` 一律 `null`（**不伪造 URL**） | 同上 |
| 向量库 | 由上述 95 条法条 + 600 篇合成文书分块（300/50）编码 | **1295** 文档 × **512** 维，chromadb，构建 37.8 s | ⚠️ 继承了数据的合成属性 | 重建：`python -m lawgate.knowledge.build_vector` |
| 评测集 | 程序化构建（`lawgate/eval/benchmark.py`，seed=42） | **1180** 条（concept 200 / provision 260 / case 200 / multi-turn 300 / temporal_trap 120 / case_verify 100）；dev 236 / test 944 | ⚠️ 自校验 PASS（`verify_report.md`），但标签为**规则化生成** | 见 `docs/annotation_guide.md` |
| 标注标签 | **规则化生成，非人工标注** | `need_retrieval`：`counts.json` 明标 `"rule-based (NOT human-annotated)"`；`annotators=['A','B']` 与 `arbitrated=false` 为**占位字段** | ❌ **无人工标注员** | 双盲标注（`docs/DATA_GAP.md` G4） |
| κ 一致性报告 | **程序化模拟标注员**（噪声率 0.12 的两份假投票） | `need_retrieval κ=0.7263`、`golden_provisions κ=0.8920`，共同 qid **300** | ❌ **不是**标注者间一致性证据；文件位于 `data/benchmark/simulated_annotators/` | 真实投票后重跑 `scripts/kappa.py` |
| 判分词表 | 人工整理（`lawgate/knowledge/risk_terms.py`） | `ABOLISH_MARKERS` / `REFUSAL_MARKERS` / `UNCERTAIN_MARKERS` / `STOPWORDS` | ⚠️ **直接影响 E5 的 TVC 与"非法引用"判定**，属于"影响结论的定义" | 由法学生独立复核 |
| 演示夹具 | 全合成小样本（`plot_all.py --demo`） | `results/_demo/`、`figures/_demo/`（8 张图 + 9 个结果文件） | ❌ **DEMO DATA / NOT REAL RESULTS**（每张图带戳） | 无需补齐，引用时勿混用 |

**法条权威性的实测边界**（`data/kb/qa_report.md`）：
全部 13 部法律的解析**往返一致率均为 100%**（最低值 100.00%）。
这证明"**没丢字、没串条**"（覆盖 100% 条文的自动等价性检查），
**不能**证明"源文本就是官方原文"。后者只能由 FLK 官方文件导入 + 人工抽验解决。
人工抽验清单（45 条）已生成于 `data/kb/manual_audit.csv`，**尚未有人签名**。

---

## 6. 评估结果（含全部保留意见）

> **引用规则**：下表每一行都必须连同括号内的限定语一起引用。
> 未落盘的实验一律写 `待生成`，不得用估计值或近似值填充。

### 6.1 E0 门控信号诊断（`figures/e0/e0_auc.json`，dev n=236，正 158 / 负 78）

| 信号 | 汇总 AUC | 分桶 AUC（b1 / b2 / b3 / b4） |
|---|---|---|
| margin | 0.5553 | 0.4662 / **0.7341** / `null` / 0.3588 |
| entropy | 0.5131 | 0.1938 / **0.7403** / `null` / 0.3425 |
| variance | 0.5565 | 0.1900 / **0.8199** / `null` / 0.2775 |
| neglogp | 0.4920 | 0.1562 / **0.7445** / `null` / 0.3025 |
| 确定性复杂度评分 | 0.861 | 0.6056 / **0.9578** / `null` / 0.8219 |

- **四个神经信号的汇总 AUC 全部 < 手册 0.60 下限 → 触发红灯**；
- **但这是辛普森悖论**：`need_retrieval` 标签由类别规则决定（case 恒 1、provision 按问法、
  concept 40% 随机），桶间标签分布差异主导了汇总 AUC。b3 的标签在该桶内**恒定**，
  故 AUC **无定义**（`null`）；b1/b4 信号**反向**（AUC < 0.5）；
  分类别看，provision **0.9647**（neglogp 0.9831）、temporal_trap 0.7983、multi-turn 0.2775；
- 因此默认路由采用 **`hybrid`**：**b2 用神经信号、b1/b3/b4 用复杂度评分**，
  τ_b 仍逐桶校准（`docs/deviations.md` D18）。
- ⚠️ **复杂度评分的 0.861 含构造重叠**：其 `category_prior` 与 `topic` 特征与生成标签的
  类别规则同源，属"用类别规则预测类别规则"；判别证据是它在 **provision 类别内 AUC = 1.0**
  （`boilerplate_penalty` 恰好识别出生成标签时用的"直接问 vs 场景问"划分）。
  正式结论应由 E1 端到端 acc–RR 决定，**不是** AUC。
- ⚠️ 该结论建立在**规则化标签 + 0.5B 信号采集**上（`figures/e0/e0_auc.json` 的四个 AUC
  实测于 0.5B，见 `docs/deviations.md` D21）；人工标注 + 1.5B 复测下必须重跑。

### 6.2 E6 案号核验（`results/e6/prf.json`、`results/e6/confusion.json`，n=100）

| 指标 | 值 |
|---|---|
| precision / recall / f1 | **1.0 / 1.0 / 1.0** |
| tp / fp / fn / tn | 30 / 0 / 0 / 70 |
| 混淆矩阵 | **完全对角**：V1 30/30 → 核验通过；V2 30/30 → 不存在；V3 25/25 → 存在但案由不符；V4 15/15 → 格式非法 |

- ⚠️ **这是构造性满分，不是真实能力证据**：评测集的 V1–V4 子类**就是按核验器的四级判定
  生成的**，100% 是构造预期；
- ⚠️ 真值口径："真实一致 / 存在但案由不符"中的"真实"**仅指在本仓库合成 `case_registry`
  中可命中**，**不代表**中国裁判文书网上存在该案件；
- ⚠️ 因此该指标只能表述为"核验器在**格式合法 / 不存在 / 案由不符 / 格式非法**
  四类输入上的判别能力"，**不能**表述为对真实文书库的覆盖率。

### 6.3 单轮吞吐量试跑（**不是方法对比结果**）

| 记录 | 配置 | 实测 |
|---|---|---|
| `results/pilot/summary_legalgate_seed0_test.json` | legalgate，n=8，`max_tokens=128` | p50 15599.1 ms；mean 15814.9 ms；mean_tokens 127.5 |
| `results/timing/summary_alwaysrag_seed0_test.json` | alwaysrag，n=8，单进程 14 线程 | p50 18877.6 ms；mean 19806.4 ms |
| `results/timing/summary_alwaysrag_seed0_test_part1.json` | alwaysrag，n=8，**4 进程 × 3 线程** | p50 163849.1 ms；mean 155503.1 ms → **线程级并发为负收益** |

### 6.4 E1–E5 主实验（**2026-09-12 全部落盘，D33；经 D34 统一离线重判**）

> **口径三件套（引用任何一个数字都必须带上）**：回答模型 = DeepSeek API `deepseek-v4-flash`（关思考）；
> 门控草稿 = 本机 `Qwen2.5-0.5B-Instruct`（贪心）；生成长度 = **192 token**。
> 2026-09-11 的旧口径（本地 1.5B / 80 token）产物已归档 `results/_archive/2026-09-11_qwen1.5b_tok80/`，
> **禁止与新数字并排引用**。

| 实验 | 结论一句话 | 关键数字 | 产物 |
|---|---|---|---|
| **E1 主对比**（6 方法 × **test 全量 944**） | **核心断言 PASS**：检索降 **91%** 且 acc **显著更优** | legalgate acc **0.6144** / RR **0.09** / TVC 0.9375 / 引用失效法率 0.0042；Always-RAG 0.5551 / 1.0 / 0.3333 / 0.0985；配对 Wilcoxon **p=0.00205**、Holm(4) 拒绝 H0、bootstrap 95% CI [+0.0212, +0.0985]；通道分布 B 568 / A 291 / C 85；分桶 acc b1 0.5838 / b2 0.8211 / b3 0.6468 / **b4 0.3375**（最弱） | `results/e1/{summary.csv,summary_full.json,core_assertion.json,stats.json}`、`figures/e1/pareto_rr_acc.png` |
| **E2 消融**（A1–A4 × `test_e1` 300） | **通道 B 是准确率的主要贡献源**；单 τ 与神经信号选择在当前校准下**无实质影响**（如实报告） | A1 关通道 B：full 0.7433 → **0.3233**（−42pp）；A2 单 τ(0.99) 0.7467/0.0467 vs τ_b 0.7433/0.0533（仅 b1 的 2 条分叉）；A3 五臂**逐条相同**；A4 k=8..64 **逐条一致** | `results/e2/ablation_rows.json`、`figures/e3/ablation.png`（目录错位见 D23） |
| **E3 多轮**（`test_e4` 60 = 20 组×3 轮） | **负结果（D35）**：多轮是最弱一环 | legalgate 总体 **0.4167** < neverrag 0.5667 / alwaysrag 0.5333；turn2 全 0 是**代理判分假象**；槽位继承 error_rate 1.0（**离线协议下不可测**） | `results/e3/e3_report.json`、`figures/e4/multiturn_trend.png` |
| **E5 时效陷阱**（T1–T4 × 120） | **主打卖点成立**：确定性时效处理 vs 通用 RAG 的"效力盲" | TVC **0.9333** vs alwaysrag 0.35 / legal_llm 0.3417 / neverrag 0.2667；引用失效法率 0.0333 vs 0.6417；分陷阱 T1 **1.0** / T2 0.8667 / T3 0.8667 / T4 **1.0** | `results/e5/{e5_report.json,tvc_by_trap.json,failure_cases.jsonl}`、`figures/e5/tvc_by_trap.png` |
| 桶级阈值校准 | 已完成并**两次校准** | 现行 τ_b = b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0（`dev_calib` 144 条，b1 仍 `feasible:false` 兜底）；hybrid 工作点 acc 0.4722 / RR 0.2431 | `configs/thresholds.json`、`results/calibrate_report.json` |
| E0 门控信号诊断 | **红灯 + 辛普森悖论**（真实负结果，见 §6.1） | 汇总 AUC 0.492–0.557（<0.60 下限）；b2 信号有效 | `figures/e0/e0_auc.json` |
| 合格/合格性判定 | 逐项见 `docs/ACCEPTANCE.md` | — | — |

### 6.5 评测方法论上的保留意见（必须随任何指标一起引用）

1. **判分是机械代理判据**（`lawgate/eval/metrics.py`）：`provision` 判"是否给出正确法名+条号
   且与条文正文词面重叠 ≥0.30"；`concept` 判关键词召回 ≥0.40；`case` 判是否提到案由/案号；
   `temporal_trap` 判 TVC；`case_verify` 判核验结论是否与 `gold_pass` 一致；
   多轮沿用底层类别判据。它能把"是否引用了正确法条/是否识别了失效"测准，
   对"答案表述质量"只能给近似分。
2. ~~**E1 只覆盖 test 的 31.8%**~~ → **已解除（D33，2026-09-12）**：E1 主对比已跑满
   **test 全量 944**（`scripts/run_pipeline.py --e1-split test`）。`test_e1`（300 条）现为
   **E2 消融**的口径（D24）、`test_e4`（60 条）为 **E3** 的口径（D19）——
   两者绝对 acc 与 E1 全量**不可直接横比**（分层子集富集了通道 B 可解的时效/案号题，
   故 E2 full 臂 0.7433 高于 E1 的 0.6144 是**构成效应**）。
3. ~~**`max_new_tokens` 在实验中为 80**~~ → **已统一为 192**（演示口径 = 接口口径 = 实验口径，D28/D33）。
4. **判分是机械代理判据，且 2026-09-13 修过三处效度缺陷**（D34：继承陈述句/法名子串/否定警示句），
   修复后对**所有方法统一离线重判**（`scripts/rescore_results.py`，原件备份 `results/_prescore_backup/`）。
   披露两点：① 修复方向**利好本项目**（只有通道 B 会产出"【法律沿革】X→Y"式文本，E1 legalgate
   acc 0.553→0.6144、TVC 0.3333→0.9375），但核心断言在修复**前**即为 PASS，不是为过断言而改；
   ② `_law_hit()` 仍偏松（句中裸提"依照合同法第52条"会漏检），本语料失效法引用几乎都以《》或句首形式出现。
5. **未做多 seed 重复**：贪心解码（`do_sample=False`）下同一 (模型, prompt, max_tokens)
   输出完全确定，跑 3 个 seed 只是同一结果的 3 次复制；随机性改由统计层的
   bootstrap（n=10000）承担（`docs/deviations.md` D8）。
5. **κ 来自模拟标注员**（见 §5 末三行），**不得**作为一致性证据。

---

## 7. 已知失效模式（Known Failure Modes）

### F1 把已废止法律当作现行依据引用（**最高优先级**）

- **表现**：模型直接照引《合同法》《物权法》《侵权责任法》《婚姻法》《继承法》《收养法》
  《担保法》等 2021-01-01 起已废止的法律作为现行依据。
- **根因**：底座模型参数知识停留在旧法体系；0.5B 模型几乎不具备自主的时效判别能力。
- **缓解**：通道 B 的时效状态机（P2/P3）命中即返回废止警示 + 替代条文 + 沿革链，
  `must_show_warning=true`；判分层用 `invalid_law_citations()` 逐句检测
  "句中提到失效法名且该句无任何失效提示语"，并计入 `invalid_law_citations` 指标。
- **残余风险**：① 只在**库内已收录**的法条/法律上有效——未收录的法律不会被拦截；
  ② 通道 A/C 的生成（不走通道 B）仍可能产生非法引用（这正是 E1/E5 要测的东西）；
  ③ 判据依赖 `risk_terms.ABOLISH_MARKERS` 词表，措辞未被词表覆盖时会误报/漏报。
- **当前可测证据**：`docs/quickcheck.txt` §2 已覆盖废止/修订/未生效/查无此条四类判定；
  `results/pilot/summary_legalgate_seed0_test.json`（n=8）中
  `invalid_law_citation_rate = 0.5`——**仅 8 条的试跑，不构成结论**。

### F2 案号假阴性：库中没有但**确实存在**的案号被误判为"不存在"

- **表现**：对真实存在但不在 `case_registry` 中的案号，系统返回
  "❌ 案号 … 在裁判文书库中不存在，疑似编造。请勿在正式文书中引用。"
- **根因**：案号核验是**库内存在性查询**（`SELECT * FROM case_registry WHERE case_no=?`
  且 `exists_in_db=1`），而当前案号库是 **600 条合成数据**，任何真实案号都不在其中。
- **影响**：这是真实部署中最严重的一类错误（把真案号诬为编造），且在**当前合成库上完全无法测量**。
- **缓解**：① 结果文件的 `threshold_note` / `data_caveat` 已限定"指标仅反映核验器判别能力"；
  ② `docs/deviations.md` D15 明确列为"残余风险（重要）：无法在本合成库上测量"；
  ③ 提供 `scripts/import_judgments.py` 以真实文书库替换（`data_source='CJWS'`）；
  ④ 提示语本身写明"请勿在正式文书中引用"而非断言违法。
- **残余风险**：**当前未处置**。真实文书导入后须新增"库外真实案号假阴性率"指标并重跑 E6
  （`docs/DATA_GAP.md` G2、`docs/risk_register.md` R20）。

### F3 b1/b4 桶的门控信号反转

- **表现**：在 b1（概念咨询）与 b4（多轮追问）上，神经不确定性信号与 `need_retrieval`
  标签**反相关**：margin AUC 0.4662 / 0.3588，entropy 0.1938 / 0.3425，
  variance 0.1900 / 0.2775，neglogp 0.1562 / 0.3025（全部 < 0.5）。
- **根因**：这些桶的标签由**类别规则**决定而非实例难度；且存在反向机制——
  "问法越像法条原文 → 模型越自信 → u 越低"，而该问法恰好被标为**需检索**。
- **缓解**：`router_mode="hybrid"` 使 b1/b4 改用确定性复杂度评分
  （b1 复杂度评分 AUC 0.6056、b4 0.8219），并在 E2/A3 消融中同时报告
  `signal` / `complexity` / `hybrid` 三种模式的端到端表现。
- **残余风险**：复杂度评分的收益部分来自**构造重叠**（F 见 §6.1 第三条）；
  人工标注下 b1/b4 的真实信号关系未知，**不能**据此声称"概念题不需要神经门控"。

### F4 0.5B 模型的质量天花板

- **表现**：生成答案偏短、指令遵循弱、拒答率与不确定表述率偏高；早期在
  `max_new_tokens=80` 下常出现半句截断，不易稳定给出"法律名 + 条号 + 规则"三段式答案。
- **根因**：手册指定的 7B 不可用（无 CUDA），当时实际使用 0.5B（`docs/deviations.md` D9）。
  **2026-09-12 起该风险已大幅缓解**：最终回答改由 DeepSeek API `deepseek-v4-flash` 生成（D30），
  生成长度回到 **192**（D33），且实测 acc 0.6144 / 显著优于 Always-RAG（§6.4）；
  本机 0.5B 现在只负责**门控草稿**（20 token 的前缀分布），不再决定答案质量。
- **缓解**：门控与判分尽量依赖**结构性判据**（是否引用正确条号、是否漏判失效）
  而非表述质量；通道 B 的答案完全由数据库渲染，与模型规模无关；
  `ExtractiveLLM` 兜底路径保证无权重也可运行。
- **残余风险**：**旧口径（本地 1.5B/80 token）的绝对准确率不可与新口径对比**；
  新口径数字必须带"API 回答 / 0.5B 草稿 / 192 token"三件套引用。

### F5 法条覆盖缺口导致的"查无此条"降级

- **表现**：询问未收录条文（如民法典第 9999 条、绝大多数 1260 条中的绝大多数）时返回
  "数据库未收录《X法》第Y条，降级至语义检索"（`quickcheck.txt` §2 已实测）。
- **根因**：种子语料仅 62/1260 条民法典条文（`docs/risk_register.md` R3）。
- **缓解**：降级路径明确（回落到门控），并在 trace 的 `validity_status='查无此条'` 留痕；
  评测集**不给语义不相关的兜底条号**（111 条条目 `golden_provisions` 为空）。
- **残余风险**：`provision` 类评测集的 260 条中 180 条是同 80 条条文的不同问法变体
  （复用系数 **3.25**），有效样本多样性不足。

### F6 "查无此条"被误解为"该条不存在"

- **表现**：用户可能把"数据库未收录"读作"法律上没有这一条"。
- **根因**：`TemporalChecker.check_provision()` 对库内缺失返回
  `status="查无此条", is_safe=False`。
- **缓解**：警示语已写作"**数据库**未收录《X法》第Y条"，明确限定为**库内**缺失；
  界面 Trace 面板显示 `validity_status`。
- **残余风险**：低，但需在使用说明中重复（已在 `docs/system_manual.md` §4 场景 1 提及）。

### F7 案由模糊匹配的双向包含可能放宽判定

- **表现**：`_cause_match()` 去掉"纠纷"后双向 `in` 匹配，故自称"借贷"、真实"民间借贷纠纷"
  判定为一致；但也可能把"房屋租赁"与"租赁"以外的短串误判为一致（如自称"合同"）。
- **缓解**：匹配前会剥离"纠纷"后缀，空串一律视为 True（不拦）；
  E6 的 V3 子类专门检验"案由不符"能否被拦下。
- **残余风险**：过宽匹配会漏拦 V3 类错误；真实文书导入后应抽样复核该函数（`docs/DATA_GAP.md` G2）。

---

## 8. 伦理、合规与使用建议

1. **数据合规**：本项目**未**从网络抓取任何法律文本或裁判文书（`docs/deviations.md` D0）。
   法条来自项目组人工录入的关键条文；"文书"为程序化生成，不含任何真实自然人或法人的信息。
   因此本仓库不含个人隐私数据、不含未公开的裁判文书。
2. **引用合规**：任何对外材料引用本系统输出时，必须同时标注
   ①法条语料状态（`PENDING_FLK_VERIFICATION`，非官方原文）；
   ②案号数据状态（`SYNTHETIC`，不对应真实案件）；
   ③标签状态（规则化生成，非人工标注）；
   ④E6 的 1.0 是构造性结果。
3. **可解释性义务**：本系统的价值在于**决策可复核**。使用时应保留并展示 `trace`，
   不要剥离 `gate_source` / `source_url` / `validity_status` 等字段后单独传播答案。
4. **人工复核建议**：在生产化改造前，任何涉及时效与案号的结论都应经人工复核；
   本系统的定位是**辅助检索与提示风险**，不是替代判断。
5. **改进优先级**（与 `docs/risk_register.md` 的状态汇总一致）：
   先补**人工标注与真实 κ**（决定标签效度），再补**桶级阈值校准**（决定核心指标是否成立），
   然后才是官方法条原文与真实文书库（决定数据权威性与 E6 的真实口径）。

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | — | 首次发布：系统级模型卡；记录免训练特性、实际模型（Qwen2.5-0.5B + bge-small-zh-v1.5）、数据溯源状态、E0/E6 结果与全部保留意见、七类已知失效模式 |

> 后续在以下任一条件成立时**必须**更新本卡片并提升版本：
> ① 更换因果模型或向量模型；② 导入 FLK 官方法条原文（溯源状态升级为 `VERIFIED`）；
> ③ 导入真实裁判文书（`data_source` 升级为 `CJWS`）；④ 完成人工标注并取得真实 κ；
> ⑤ 完成桶级阈值校准；⑥ 启用 `router_mode="signal"` 或以 7B 复测 E0。
