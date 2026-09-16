# 与实现手册的偏差登记（Deviation Register）

> 本文件是 CA-LegalGate 项目的**诚实性核心文档**。手册（《CA-LegalGate 实现方案与
> 分步操作手册》）是设计意图；本文件逐条记录**实际实现与手册不一致之处、不一致的
> 原因、以及该不一致对结论的影响**。论文、申报书与答辩材料中凡涉及这些点，均应
> 引用本文件，不得只报"符合手册"。

编号规则：`D0` 为环境级前提，`D1` 起为实现级/方法学偏差（**编号已至 `D37`**，
其中 D19/D24/D29/D30/D33 为口径级变更，D36 为收尾的文档/产物治理，D37 为按用户要求删除 smoke
脚本与产物（纯工具/文档治理），新旧口径数字禁止并排）。每条给出：
**手册要求 → 实际情况 → 处置 → 影响与残余风险**。

---

## D0 执行环境：网络存在中间层劫持，法律原文一律未从网络获取

**手册要求**：S1.1 从国家法律法规数据库（flk.npc.gov.cn）下载《民法典》等官方 Word；
S1.3 从中国裁判文书网抓取 2000 篇文书。

**实际情况**（实测证据）：

| 探测项 | 结果 |
|---|---|
| `raw.githubusercontent.com` | 解析到**非公网 IP**（web_fetch 直接拒绝：`resolves to a non-public IP address`） |
| `flk.npc.gov.cn/api/list`（GET） | 200，但返回 SPA 的 HTML（552 字节），非 JSON |
| `flk.npc.gov.cn/api/*`（POST） | 405 Not Allowed |
| flk 首页 HTML | 含**注入的隐藏外链** `<div style="width:-100px;height:-100px;display:none"><a href="/ics/cpf/hlink123939">` |
| `huggingface_hub`(httpx) | `CERTIFICATE_VERIFY_FAILED`（改用 `urllib` 直连才成功） |
| `pypi.org` / `huggingface.co` 直连 | 可达（说明并非完全断网，而是**部分域被劫持/证书链被替换**） |

**处置**：
1. **不从网络获取任何法律文本或裁判文书**。理由不只是"抓不到"，而是**完整性与
   可信性**：在存在中间层注入的通道上获取的法律文本，无法证明其未被篡改，
   直接入库会污染整个法条库的可信度。
2. 改为**人工录入关键条文的种子语料**（`lawgate/knowledge/seed_corpus.py`），
   全部标记 `source_kind=MANUAL_TRANSCRIPT`、`verification=PENDING_FLK_VERIFICATION`，
   `source_url` 指向该法在 FLK 的检索页（可追溯，但非本次抓取的具体文件）。
3. 模型权重改为"本地 HF 缓存优先 + `urllib` 直连清单式下载"（`scripts/fetch_hf_files.py`），
   绕开 huggingface_hub 的 CA 问题。

**影响与残余风险**：
- 本项目**全部法条内容的权威性未获验证**。`S2.4` 的"抽验 ≥95%"目前只有
  **自动等价性检查**（往返一致率 100%，证明"没丢字、没串条"，**不能**证明"源文本
  就是官方原文"）。
- 正式申报前必须在干净网络环境执行 `scripts/import_law_text.py` 导入官方原文，
  并把 `ingest_provenance.verification` 升级为 `VERIFIED`。
- 见 `docs/DATA_GAP.md`。

---

## D1 `provision_fts` 增加 `provision_id` 列

**手册要求**：S2.1 中 FTS 表只有 `(text, law_short, article_no)`，主题检索用
`JOIN legal_provisions p ON p.law_short=f.law_short AND p.article_no=f.article_no` 回连。

**问题**：一条法文常含多款多项（多行同 `article_no`）。按 `(law_short, article_no)`
回连会**乘出重复行**，一条 5 项的法文在结果里出现 5 次。

**处置**：FTS 表增加 `provision_id UNINDEXED`，用 `legal_provisions.provision_id`
精确回连（`lawgate/knowledge/schema.sql`、`b_structured.py`）。

**影响**：修正缺陷，无结论影响。

---

## D2 `item_no` 默认 `''` 而非 `NULL`

**手册要求**：S2.1 的 `item_no TEXT`，默认 NULL，并有
`UNIQUE(law_short, version, article_no, paragraph_no, item_no)`。

**问题**：SQLite 的 UNIQUE 约束中 **NULL 互不相等**，故手册写法无法阻止
`(条, 款, NULL)` 重复插入——该唯一约束对"款"行形同虚设。

**处置**：`item_no TEXT DEFAULT ''`，款行用空串而非 NULL，使唯一约束真正生效。

---

## D3 `flush()` 的款项重复入库

**手册要求**：S2.2 的 `flush()`。

**问题**：当一条同时有款与项时，手册代码先把项文本作为 item 行输出，又用
`if p_text not in cur_items.get(p_i, [])` 判断是否输出 paragraph 行——该判断比较的是
"款的文本"与"项的文本列表"，逻辑上恒真，导致**同一段文字重复入库**。

**处置**：重写为"每款 1 条 paragraph 行（`item_no=''`）+ 每项 1 条 item 行"，
互不重复。

---

## D4 项文本的正则捕获

**手册要求**：`cur_items.setdefault(para_idx, []).append(line)`——整行入库。

**问题**：`line` 含 `（一）` 前缀，而 `item_no` 也被单独保存，造成前缀重复；
且全角/半角括号处理不一致。

**处置**：用捕获组取正文，`item_no` 统一存 `'(一)'` 形式（半角括号 + 原序号字符）。

---

## D5 文本清洗不得做 NFKC 归一化（**重要**）

**手册要求**：S2.2 原代码未做归一化；本实现初版为处理全角空格引入了
`unicodedata.normalize("NFKC", line)`。

**问题（实测发现）**：NFKC 会把**全角标点与数字转成半角**：

```
原文：借款合同是借款人向贷款人借款，到期返还借款并支付利息的合同。
NFKC：借款合同是借款人向贷款人借款,到期返还借款并支付利息的合同。
                                     ↑ 中文顿号/逗号被改写
```

对法条库而言这是**破坏文本保真**的严重缺陷——引用条文时标点与官方文本不符，
且可能改变语义（如全角/半角括号、数字）。

**处置**：`normalize_lines()` **只处理不可见空白**（`\u3000`、`\xa0`、`\u200b`、
`\r`、`\t`、连续空格），**不做任何 Unicode 归一化**；项标记的识别由正则同时接受
`（）` 与 `()` 解决。

**验证**：`docs/quickcheck.txt` 中第 667 条正文为全角逗号原文。

---

## D6 增加"第X条之一"支持与页眉/page 噪声剔除

**手册要求**：S2.2 的 `RE_ARTICLE` 不支持 `之N`。

**处置**：`Provision.suffix` 字段保留"之N"，`article_no` 取基础条号，
`validate_continuity()` 单独报告 suffix。为后续导入《公司法》等含"之一"的法律预留。

---

## D7 项序必须用整数索引排序（**重要**）

**手册要求**：`ORDER BY paragraph_no, item_no`。

**问题**：`(一)(二)(三)(四)` 的**Unicode 码位并非数字序**：
`一`=U+4E00、`三`=U+4E09、`二`=U+4E8C、`四`=U+56DB。
按 `item_no` 字符串排序得到的顺序是 **一、三、二、四**——项序被打乱，
条文引用顺序错误。

**实测**：修复前往返一致率：民法典 88.71%、劳动合同法 57.14%、民事诉讼法 0%、
收养法 0%；**全部 12 处不一致恰好都是"≥3 项"的条文**，完全吻合该解释。

**处置**：新增 `legal_provisions.item_idx INTEGER`；解析时按出现顺序赋 1,2,3…；
`render_article()`、通道 B 渲染、审计比对一律按 `(paragraph_no, item_idx)` 排序。

**验证**：修复后全部 13 部法律往返一致率均 **100%**。

---

## D8 `--seed` 的语义与执行范围

**手册要求**：S6.1 "所有脚本强制 `--seed`，默认遍历 `{0,1,2}`"。

**实际情况**：本系统**免训练**，且解码为**贪心**（`do_sample=False`）。给定
(模型, prompt, max_tokens)，输出**完全确定**，与 seed 无关；dev/test 划分也已固定
（seed=42）。因此对生成结果跑 3 个 seed 是**同一份结果的 3 次复制**，得到的
mean±std 恒为 0，不具备统计含义。

**处置**：
- `--seed` 全链路完整保留（`scripts/run_exp.py --seed`），可随时跑多 seed；
- 主对比固定 **seed=0 生成**；
- 统计不确定性改由**统计层的 bootstrap 重采样 seed**（`n=10000`）承担，
  输出 CI95 与 p 值——这才是本设定下唯一有意义的"随机性来源"。

**影响**：不满足手册字面的"3 seed 表"，但避免了"用 3 次相同结果伪造稳健性"的
更严重问题。若后续接入采样解码（`temperature>0`）或微调模型，应恢复 3 seed。

---

## D9 语言模型降到 Qwen2.5-0.5B-Instruct（GPU 不可用）

**手册要求**：S0.1 要求 ≥16GB 显存 GPU（RTX 3090/4090 级）；S0.4 与 S6.3 用
`Qwen/Qwen2.5-1.5B-Instruct`，S3.8 与 S7 用 `Qwen/Qwen2.5-7B-Instruct`。

**实际情况**：本机 **无 CUDA**（`torch 2.11.0+cpu`），`nvidia-smi` 不存在。
手册风险表已给出对应处置："GPU 不可用 → 门控实验降到 Qwen2.5-1.5B + 云学生额度；
通道 B 与 E5/E6 纯 CPU 照常跑"。

进一步地，本机确有 `Qwen/Qwen2.5-0.5B-Instruct` 的完整本地缓存，而 1.5B 需下载
约 3.1GB（实测带宽约 1.3MB/s，下载中途停滞）。故：

- **语言模型**：`Qwen/Qwen2.5-0.5B-Instruct`（本地缓存快照），CPU fp32，14 线程；
- **向量模型**：`BAAI/bge-small-zh-v1.5`（**手册指定模型，已成功获取并实际使用**，512 维）；
- `vllm` 未安装，部署路径 `VLLMLLM` 代码完整但未启用（`llm_backend=vllm` 时才会走）。

**影响与残余风险**：
- 0.5B 模型的答案质量与指令遵循能力显著弱于 7B，**绝对准确率不可与手册预期对比**；
- 但**方法间对比是公平的**（6 个方法共用同一模型、同一 `max_new_tokens`、同一
  `top_k`/`rerank` 设置），故"路由带来的相对收益"这一核心结论仍然成立；
- 门控信号的质量受模型规模影响，E0 的红灯结论需要在大模型上复测（见 D18 与
  `docs/DATA_GAP.md`）。

---

## D10 `slots_complete` 判据修正：概念题不得误入通道 B

**手册要求**：S3.1 的完整性判据
`or (slots.topic and not slots.article_no)`。

**问题**：手册自己的冒烟用例 6「什么是离婚冷静期」中，"离婚冷静期"命中
`TOPIC_KEYWORDS['离婚纠纷']`，于是 `hit_provision=True` 且 `slots_complete=True`，
**直接走通道 B 的主题 FTS**——与手册期望的"通道 A 或 C"冲突。手册的判据与
自己的验收用例不自洽。

**处置**：主题路线额外要求出现**法条索取标记**（`PROVISION_SEEKING`：
哪条/依据/法条/条文/条款/第/怎么规定/原文/内容是什么…）。未出现标记的纯概念题
不进通道 B。

**验证**：`docs/quickcheck.txt` 意图判定 6/6，其中「什么是离婚冷静期」
`slots_complete=False`。

---

## D11 门控信号方向统一为"不确定性"

**手册要求**：S3.6 `margin_from_logprobs` 取"前两个 token 的 top-1 logprob 之差"；
S3.7 路由判据 `u > τ_b → 检索`；S6.3 用 u 预测 `need_retrieval` 并期望 AUC 高。

**问题**：三处互相矛盾。
- 若 margin 是"置信度"（越大越有把握），则 `u > τ` 应走向**不检索**；
- 而 AUC 要 > 0.5，必须"u 越大越需要检索"。

**处置**：统一把 u 定义为**不确定性**（越大越该检索），与路由判据和 AUC 方向一致。
同时保留 `SIGNALS_CONFIDENCE` 与原始量，E0 把两个方向的 AUC 一并报出，不做隐藏。

---

## D12 补"法律名 + 时效问句"路径

**手册要求**：S3.4 通道 B 只有三条路径（案号 / 法条 / 主题）。

**问题**：手册 S3.2 的验收用例「担保法现在还有用吗」**没有条号**，
三条路径都接不住，但期望结果是 `check_law` 返回"已废止 + 沿革链"。

**处置**：通道 B 增加 **P3「法律效力询问」**路径（`law_short ∧ law_validity_query`），
返回时效结论 + **逐条列出替代条文原文与官方来源** + 沿革链。

**验证**：`docs/quickcheck.txt` →「担保法现在还有用吗」返回"已废止"+《民法典》第686条。

---

## D13 校准目标函数方向修正：取**最大**可行 τ（**重要**）

**手册要求**：S3.6 "取满足 `acc(τ_b) ≥ acc_AlwaysRAG − δ` 的**最小** τ_b"。

**问题**：路由判据是 `u > τ_b → 检索`，故
- τ 越小 → 超过阈值者越多 → **检索越多**；
- `acc(τ)` 关于 τ 单调不增 → 可行集为 `τ ≤ τ_max`；
- 该集合里的**最小** τ 就是网格下界 → **检索率 100%**，与项目核心指标
  "检索调用下降 ≥40%"**直接矛盾**。

**处置**：默认 `rule='largest_feasible'`——在满足精度约束的前提下取**最大** τ，
即"满足精度约束的最小检索代价"工作点，这才是 Pareto 框架下的正确选择。
手册原式保留为 `rule='smallest_feasible'` 并由 `scripts/calibrate.py` 两种规则
都写入 `results/calibrate_report.json`，可作为消融对照。

---

## D14 时效状态机三处补强

**D14.1 沿革查询改为精确匹配（重要）**
手册 `check_law` 用 `from_law LIKE '%法%'` 会串法。本实现初版曾加
"LIKE 变体兜底"，结果把 `公司法` 命中了 `公司法(2018修正)` 这条**旧版本**沿革记录，
于是 2024-07-01 起已生效的**现行**《公司法》被误判为"已修订"。
**处置**：仅精确匹配 `from_law = law_short`。查询 `公司法(2018修正)` 时精确匹配即可命中。

**D14.2 沿革链不再中途断链**
手册 `_trace_chain` 每步只取一行 `to_law`，在分支处断链。改为收集全部后继、
按生效日期排序、去重、限深 6。

**D14.3 条号预览取款行而非项行**
手册 `check_provision` 的 `WHERE paragraph_no=1 ORDER BY version DESC LIMIT 1`
在"一条多项"时会命中**项**行，导致"原条文预览"显示最后一个项。
**处置**：限定 `paragraph_no=1 AND item_no=''`，并用 `article_text()` 拼接完整条文。

**验证**：`docs/quickcheck.txt` →
`公司法47 @2024-06-30 = 尚未生效`、`@2024-07-02 = 现行有效`（与 S3.2 验收一致）；
`公司法(2018修正)26 = 已修订`。

---

## D15 案号核验补强

**D15.1 区分"格式非法"与"格式合法但不存在"**
手册靠 `parsed` 为空判定"格式非法"，但意图层对"看起来像案号但代字非法"的输入
（如 `（2022）沪01测12345号`）也会给出空 `parsed`，两者需区分。本实现显式区分，
并把解析细节（年份/法院代字/类型代字/序号）写入 `detail` 便于审计。

**D15.2 年份合理性检查**：拦截 `（2099）沪01民终12345号` 这类格式合法但不可能的案号。

**D15.3 案由模糊匹配**：真实案由"民间借贷纠纷"与提问"借贷"用双向包含匹配，
而非严格相等。

**验证**：`docs/quickcheck.txt` 案号核验 6/6。

**残余风险（重要）**：本仓库案号库为**合成**数据，"不存在"的判定只对本库成立。
真实场景中最难的"库里没有但确实存在"的**假阴性**无法在本合成库上测量。

---

## D16 `LegalLLMPersona` 替代法律专用模型基线

**手册要求**：S6.2 `LegalLLM` 加载 LawGPT_zh / ChatLaw 开源权重。

**实际情况**：本机无这两个模型，且**无法从被劫持的网络获取第三方权重**（D0）。

**处置**：改用"同一底座模型 + 法律专家系统提示 + 不检索"作为**代理基线**，
并**显式命名为 `LegalLLMPersona`**，在结果表里标星（`Legal-LLM*`）。

**影响**：该方法**不是**法律微调模型，**不能**用于支持"通用模型 vs 法律专用模型"
的结论。它只能说明"仅换系统提示、不加检索与结构化通道"的效果上界。

---

## D17 `ComplexityRouter` 阈值未调参

手册 S6.2 的启发式（长度 > 30 字 或 含"案例/判决/判例/类似"）**原样沿用、未调参**，
以保证它是"未调优的朴素基线"。

---

## D18 E0 红灯：神经信号的**全桶统一**门控不可用，改按桶取信号（**核心结论**）

**手册要求**：S6.3 三档判定，AUC ≥0.75 绿灯 / 0.60–0.75 黄灯 / <0.60 **红灯**
→ "关闭信号门控，路由改为『意图分类 + 复杂度路由』"。

**实测结果**（`figures/e0/e0_auc.json`，dev n=236）：

| 信号 | 汇总 AUC |
|---|---|
| margin | 0.5553 |
| entropy | 0.5131 |
| variance | 0.5565 |
| neglogp | 0.4920 |
| 确定性复杂度评分 | **0.861** |

四个神经信号**全部低于 0.60 → 触发红灯**。但分桶与分类别结果揭示了更准确的图景：

| 桶 / 类别 | AUC（variance） | 标签平衡 |
|---|---|---|
| b2 法条查询 | **0.8199** | 76 条，标签混合 |
| b1 概念咨询 | 0.1900 | 标签由规则 40% 随机决定 |
| b4 多轮追问 | 0.2775 | 标签由轮次决定 |
| b3 案例检索 | 无定义 | 标签恒定（全为"需检索"） |
| provision 类别 | 0.9647（neglogp 0.9831） | 52 条，31 正 |
| temporal_trap 类别 | 0.7983 | 24 条，7 正 |
| multi-turn 类别 | 0.2775 | 60 条，40 正 |

**诊断：这是辛普森悖论（Simpson's paradox）**。`need_retrieval` 标签由**类别规则**
生成（case 恒为 1、provision 按问法分、concept 40% 随机），桶间标签分布差异主导了
汇总 AUC，把桶内的真实判别力抵消掉了。在标签真正"实例级"的法条查询上，神经信号
AUC 达 **0.82–0.98**，是**有效**的。

**处置（在手册风险表框架内，而不是绕过它）**：默认路由模式改为 **`hybrid`**
（`lawgate/router.py` 的 `RouterOptions.router_mode`）：

| 桶 | 闸门 | 依据 |
|---|---|---|
| b2 法条查询 | 神经不确定性信号 | 桶内 AUC 0.82，实例级有效 |
| b1/b3/b4 | 确定性复杂度评分（`lawgate/gate/complexity.py`） | 神经信号反向/无定义；手册风险表的降级方案 |

τ_b **仍然逐桶在 dev 上校准**，故手册的核心贡献（**桶级阈值校准 + 三通道异构**）
完整保留，只是"闸门信号按桶取用"。`router_mode` 的三种取值
（`hybrid` / `signal` / `complexity`）均在消融中报告端到端 acc–RR，让结论由
端到端实验而非 AUC 决定。

**必须同时披露的两条反向证据（否则会高估复杂度评分）**：
1. **0.861 含构造重叠成分**：复杂度评分显式含 `category_prior` 与 `topic` 特征，
   而标签本身由类别规则生成——这是"用类别规则预测类别规则"。它在 provision 上
   类别内 AUC = **1.0** 正是构造重叠的铁证（`boilerplate_penalty` 恰好识别出
   生成标签时使用的"直接问 vs 场景问"划分）。
2. **汇总 AUC 不是有效的仲裁指标**。因此本项目不用 AUC 选路由方案，
   而用 E1 的端到端 acc–RR 表现（见 `docs/ACCEPTANCE.md` 与 E2/A3 消融）。

**残余风险**：以上结论建立在**规则化构造的 `need_retrieval` 标签**与 **0.5B 模型**
上。人工标注 + 7B 模型下必须重跑 E0（见 `docs/DATA_GAP.md`）。

---

## D19 算力预算导致的抽样与超参调整

**手册要求**：E1 在 test 全量（944 条）× 6 方法 × 3 seed 上跑。

**实测算力**：

| 配置 | 单条延迟 |
|---|---|
| 1 worker × 14 线程，max_tokens=128 | 18.9 s（p50） |
| 4 worker × 3 线程，max_tokens=128 | **155.5 s**（p50） |

**线程级并发是负收益**：torch 的 intra-op 并行已吃满内存带宽，再叠加 inter-op
并发只会互相抢核。全量 E1 需 **12–13 机时**，超出本次交付窗口。另外，本机沙箱
**禁止外部进程编排**（`Wait-Process` 被拒为 `Access is denied`）与命名管道，
故传统的"多进程分片 + 汇总"路径也不可用。

**处置**：
1. `max_new_tokens` 由 192 降到 **80**（生成成本与答案完整性的折中；判分基于
   条文引用与关键词召回，80 token 足以给出"法律名 + 条号 + 规则"）；
2. 构造**分层子集**（`scripts/make_subsets.py`，seed=42，全过程可复现，
   manifest 见 `data/benchmark/subsets_manifest.json`）：
   - `dev_calib` **144** 条（分桶校准；保留全部时效陷阱 24 与案号核验 20）
   - `test_e1` **300** 条（**保留 test 的全部**时效陷阱 96 与**全部**案号核验 80；
     多轮按**整组**抽 20 组 = 60 条；概念/法条/案例分层 24/24/16）
   - `test_e4` **60** 条（多轮 20 组，供 E3 轮次趋势）
3. 生成结果走内容寻址缓存（`lawgate/cache.py`），跨方法复用：6 个方法中
   `neverrag`/`complexity`(直答)/`targ`(直答)/`legalgate`(通道A) 共享同一 prompt，
   `alwaysrag`/`complexity`(检索)/`targ`(检索)/`legalgate`(通道C) 共享另一 prompt。
   实测把唯一生成数从约 1.5 万降到千级。

**影响与残余风险**：
- **E1 的结论建立在 test 的 31.8% 子集上（n=300/944）**，必须随结果披露；
  子集对时效陷阱与案号核验是**全覆盖**（100%），故 E5/E6 结论不受抽样影响；
  受影响的是概念/法条/案例/多轮的 acc-RR 点估计精度。
- 绝对准确率受 `max_tokens=80` 与 0.5B 模型双重压制，**不可与手册预期值比较**；
  方法间相对比较仍然公平（同模型、同超参）。
- 拿到 GPU 后应恢复 test 全量与 `max_tokens≥256` 并复跑（`docs/DATA_GAP.md`）。

---

## D20 校准链路三处修复：网格上限、报告崩溃、流水线路径不一致（**本次会话新增**）

在准备执行 G8（桶级阈值校准）时，对校准链路做了端到端核查，发现并修复三处缺陷。
三者都会让"校准"这一步**跑不出可信结果或直接崩溃**，此前从未被触发，因为
`scripts/run_pipeline.py` 在 dev 基线阶段就因 GBK 编码崩溃（见 `docs/pipeline_steps.json`），
校准步骤从未真正执行过。

**D20.1 τ 网格上限 0.50 与复杂度评分值域不匹配（重要）**

手册 S3.6 的 `GRID = [0.01 … 0.50]` 是为 **margin 信号**设计的（实测 dev_calib 上
margin 值域 0.009–0.456，完全落在网格内）。但 E0 红灯后默认路由改为 `hybrid`
（D18），b1/b3/b4 三个桶改用**确定性复杂度评分**，其值域是 **[0,1]**。实测
`figures/e0/e0_signals_dev_calib.jsonl`（n=144）：

| 桶 | 复杂度评分 min / 中位 / max | frac > 0.50 |
|---|---|---|
| b1 概念（n=36） | 0.000 / 0.400 / 0.500 | 0.00 |
| b2 法条（n=44） | 0.000 / 0.200 / 0.500 | 0.00 |
| b3 案例（n=16） | **1.000 / 1.000 / 1.000** | **1.00** |
| b4 多轮（n=48） | 0.400 / **0.500** / 0.600 | 0.46 |

后果：网格上界 0.50 之下，
- **b3**：τ 无论取何值都 < 1.0，`u > τ` 恒成立 → b3 检索率**恒为 100%**，τ 对 b3
  完全失去调节作用（对 b3 而言这恰好是正确行为——案例题确实都该检索，但这是
  "碰巧对"，不是校准的结果）；
- **b4**：中位数正好等于网格上界 0.500，τ 顶到上界后仍有 **46%** 的条目必然被检索，
  校准**无法**把 b4 的检索率压到 46% 以下——而"检索调用下降 ≥40%"正是核心指标。

**处置**：`scripts/calibrate.py` 新增 `--grid-max`（默认仍为 0.50，即手册原值），
对 hybrid / complexity 模式建议取 `--grid-max 1.0`；`lawgate/gate/calibrate.py` 的
`calibrate()` 与 `calibrate_single_tau()` 本就接受 `grid` 参数，无需改动。
网格上限写入 `results/calibrate_report.json` 的 `grid_max` 字段以便复核。

**影响**：不改此处的话，b4 的 τ 是"被网格上界夹住"的值而非"精度约束下最优"的值，
E1 的 RR 会被系统性高估、核心指标"检索降幅 ≥40%"的达成性无法归因于校准。

---

**D20.2 `calibrate()` 报告字段访问不存在的网格点（崩溃缺陷）**

`lawgate/gate/calibrate.py` 的 `report[b]` 里写：

```python
"acc_if_always_retrieve": round(curve[0.0]["acc"], 4),
```

而 `_acc_curve()` 只在 `grid` 上建键，`GRID` 从 **0.01** 起、**不含 0.0** →
`KeyError: 0.0`，校准在写报告时必然崩溃。

**发现方式**：`scripts/_selftest_calibrate.py`（本次会话新增的合成数据自测，
不依赖 LLM），在真实校准运行前就复现了该崩溃。

**处置**：改为直接对桶内样本求均值——
`acc_if_always_retrieve = mean(correct_if_retrieve)`、
`acc_if_never_retrieve = mean(correct_if_direct)`，语义与"τ=0 全检索 / τ=∞ 全直答"
一致且不再依赖网格端点。

**影响**：修正崩溃，无结论影响。

---

**D20.3 `run_pipeline.py` 的 dev 基线输出目录与校准读取目录不一致**

流水线第 1 步把 dev 基线写到 `results/dev_calib/`（`--exp dev_calib`），
第 2 步的 `scripts/calibrate.py` 却从 `results/dev_baseline/` 读取
（`DEV_BASELINE_DIR`），且默认 `split="dev"` 而基线跑的是 `dev_calib`。
两处**目录名与划分名双重不匹配** → 校准永远找不到基线，只会重新跑一遍
（`max_tokens=128`，与流水线用的 80 还不一致）。

**处置**：
- `run_pipeline.py` 第 1 步改为 `--exp dev_baseline`（与 `DEV_BASELINE_DIR` 一致）；
- 第 2 步改为 `scripts/calibrate.py --split dev_calib`（与基线划分一致）；
- `scripts/calibrate.py` 新增 `--split`，`load_dev_pairs()` 与基线文件名按划分取；
- 校准基线的 `max_tokens` 与流水线保持同一口径（本次用 80，见 D19）。

**影响**：修正链路断裂，无结论影响；但不修则 G8 无法通过流水线自动完成。

---

**本次校准的实际执行口径**（供复核）：

| 项 | 值 |
|---|---|
| 校准划分 | `dev_calib`（144 条，dev 的分层子集，见 D19） |
| neverrag 基线 | 复用 `results/dev_calib/neverrag_seed0_dev_calib.jsonl`（`max_tokens=80`）→ 复制到 `results/dev_baseline/` |
| alwaysrag 基线 | 本次新跑，`--exp dev_baseline --split dev_calib --max-tokens 80 --threads 14` |
| 门控信号 | 复用 `figures/e0/e0_signals_dev.jsonl` 按 `dev_calib` qid 过滤出 `e0_signals_dev_calib.jsonl`（144/144 全覆盖，已校验） |
| 校准规则 | `largest_feasible`（D13），δ=0.005 |

> **残余限制**：校准基于 **144 条子集**而非手册要求的 dev 全量 236 条，且
> `need_retrieval` 与判分标签均为**规则化生成**（R7）。因此 τ_b 是"与构造标签一致"
> 的工作点，不是"与人工标注一致"的工作点。补做见 `docs/DATA_GAP.md` G4/G8。

---

## D21 实际生效的因果模型是 1.5B，而早期的 pilot / timing / E0 信号是 0.5B 产物（**本次会话新增**）

**手册要求**：S0.3 指定 Qwen2.5-7B-Instruct；风险表允许在无 GPU 时降级到
更小的 Qwen2.5 指令模型，并要求**全文统一披露实际使用的模型**。

**实际情况**：仓库里同时存在两个可用模型，配置解析顺序（`lawgate/config.py`）是

```python
CAUSAL_MODEL_CANDIDATES = [
    "models/qwen2.5-1.5b-instruct",   # ← 本次实际生效（权重 2.9 GB，已完整）
    "models/qwen2.5-0.5b-instruct",   # 不存在该目录
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen2.5-0.5B-Instruct",
]
```

`_first_existing_dir()` 命中第一个"有 config.json + 有真实权重"的目录，因此
**1.5B 一直是实际生效的模型**，而 `docs/deviations.md` D9、`docs/ACCEPTANCE.md`、
`README.md` 早期版本都写的是"0.5B（1.5B 已下载但未采用）"。实测证据：

| 证据源 | 实际模型 | 判定依据 |
|---|---|---|
| `results/dev_baseline/*.jsonl`（校准基线） | 1.5B | `provenance.causal_model = qwen2.5-1.5b-instruct`（`results/calibrate_report.json`） |
| 生成缓存 `data/kb/gen_cache.db` | 1.5B | `model='qwen2.5-1.5b-instruct'`：`generate/80` 294 条、`generate/128` 8 条 |
| 同库的 `draft20` 条目（244 条） | **0.5B** | `model='7ae557604adf67be50417f59c2c2f167def9a775'`，即 HF 缓存快照目录名（0.5B） |
| `docs/smoke_s0.json`、`results/pilot/`、`results/timing/` | **0.5B** | 同上快照路径 / 当时的吞吐记录 |
| `figures/e0/e0_signals_dev*.jsonl`（门控信号） | **0.5B** | 草稿缓存 key 由 `HFLLM.name`（= 权重目录名）决定，即上表的快照哈希 |

**处置**：
1. 文档口径统一改为 **Qwen2.5-1.5B-Instruct**（本文件 D9 行、`ACCEPTANCE.md` 口径基准、
   `README.md` 状态表与限制章），并在两处显式标注"早期 pilot/timing/E0 为 0.5B 产物"。
2. **不**改 `config.py` 的候选顺序、**不**改系统环境变量：1.5B 是"按目录解析顺序自动生效"，
   若要临时回到 0.5B 只需 `$env:LAWGATE_MODEL`（并在文档中同步声明）。
3. E1 及之后的全部实验在 1.5B 上重跑；缓存以 `model` 字段参与 key，换模型**自动失效**，
   不会出现"0.5B 的答案被当成 1.5B 结果"的混用（`lawgate/cache.py: make_key`）。

**影响与残余风险**：
- **E0 的 AUC 数字与 E1 的模型口径不一致**：`figures/e0/e0_auc.json` 的四个信号 AUC
  （margin 0.5553 / entropy 0.5131 / variance 0.5565 / neglogp 0.4920）是在 **0.5B** 上测得。
  1.5B 的对应数字必须重跑 `scripts/e0_diagnostic.py` 才能陈述；在那之前，
  论文/申报书中引用 E0 时必须写明"该 AUC 来自 0.5B 模型的信号采集"。
- 复杂度评分（确定性）与模型无关，`complexity` 侧的结论不受此影响。
- 1.5B 在 CPU fp32 下单条 80-token 生成实测 **12–15 s/条**（`--workers 2`，见
  `docs/pipeline.log` 的 `[方法] 20/50 用时 …s`），算力预算仍按 D19 的分层子集执行。

**D21.1 本机只能"一个模型进程"（内存与算力双约束，实测）**

| 观测 | 数值 | 出处 |
|---|---|---|
| 单个实验进程常驻内存 | ≈6 GB（1.5B fp32 权重文件 3.09 GB） | `Get-Process` 的 `WS`，2026-09-11 实测 |
| 本机物理内存 | ≈16 GB（并行两进程时可用内存掉到 **3.9 GB**） | `\Memory\Available MBytes`。⚠️ 这是 **2026-09-11 当时的读数**；同日晚复测 `Win32_OperatingSystem.TotalVisibleMemorySize` = **32373 MB（≈32 GB）**、空闲 12.4 GB。两次数值不一致（可能中途扩容，或前一次读到的是受限视图），**引用内存结论时必须写明日期** |
| E1 独占时吞吐 | **9.3 s/条**（`[neverrag] 20/50 用时 186s`） | `docs/pipeline.log` |
| E1+E0 并行时吞吐 | **15.1 s/条**（`[neverrag] 40/50 用时 544s`），E1 的 CPU 份额只占 4–7 核 | 同上 |

**处置**：E1 与 E0 **串行**执行（E1 跑完再跑 E0），不靠多进程换并行度；
需要并发时只用 `--workers`（进程内并发条目），并把 `--threads` 控制在 14 以内。
`scripts/mem_watchdog.py` 提供了"可用内存跌破阈值就杀掉白名单之外的胖进程"的兜底，
但**分片会不断更换 pid**，白名单难以维持，实际使用中仍需人工盯 `free` 内存
（本次就因此停掉了看门狗，见 `docs/pipeline.log` 同期记录）。

**影响**：算力预算（D19）按"单进程 12–15 s/条 × 子集条数"估算，E1 全量约 5–7 机时；
论文/答辩中给出的时间是这一口径下的实测值，**不是** GPU 或 7B 模型下的时间。

---

## D22 E0 复测的 README 命令无效（`--split` 只收单值），两个 split 同目录会互覆 `e0_auc.json`（**本次会话新增**）

- `scripts/e0_diagnostic.py` 的 `--split` 是**单个**字符串参数（默认 `dev`，见脚本
  `main()`：`ap.add_argument("--split", default="dev")`）。原 README E0 行的
  `--split dev dev_calib` 会被 argparse 当作"未知参数"直接拒绝，命令不可用。
- `analyse()` 把结果统一写到 `OUT/e0_auc.json` 与 `OUT/e0_distributions.png`
  （**不带 split 名**）。若在同一个输出目录（同一 `--tag`）先后跑两个 split，
  后跑的那个会覆盖先跑 verdict。
- **正确的 1.5B 复测顺序**（须在 E1 流水线结束后执行，串行约束见 D21.1）：
  1. `dev`：`--tag 15b --shard i --nshards 6`（i=0..5 顺序跑）→ `--tag 15b --split dev --merge`
     → `--tag 15b --split dev --analyse-only`，产物在 `figures/e0_15b/`
     （`e0_signals_dev.jsonl` / `e0_auc.json` / `e0_distributions.png`，0.5B 旧产物 `figures/e0/` 不受影响）；
  2. `dev_calib`：`--tag 15b_calib --split dev_calib` 单次跑（144 条，draft20 较便宜；
     不切分片），产物进独立目录 `figures/e0_15b_calib/`，避免互覆；
  3. 引用时分别写明模型（1.5B）与 split（dev / dev_calib），与 0.5B 的 `figures/e0/` 区分。
- README 两处失效命令（§0 E0 行与 §4 E0 表行）已同步修正；
  属**文档修正**，不影响任何核心结论。

---

## D23 README §4 的 E2/E3 图产物路径各偏了一格（**本次会话新增，文档修正**）

- 代码实际写出的图路径（以脚本为准）：
  | 图 | 实际路径 | 出处 |
  |---|---|---|
  | E1 Pareto | `figures/e1/pareto_rr_acc.png` | `scripts/e1_plot.py` |
  | E2 消融 | `figures/e3/ablation.png` | `scripts/e2_ablation.py`（`plot_ablation(out_path="figures/e3/ablation.png")`） |
  | E3 多轮 | `figures/e4/multiturn_trend.png` | `scripts/e3_multiturn.py`（`out_path="figures/e4/multiturn_trend.png"`） |
  | E5 时效 | `figures/e5/tvc_by_trap.png` | `scripts/e5_temporal.py` |
  | E6 混淆 | `figures/e6/confusion.png` | `scripts/e6_case_verify.py` / `plot_all.py` |
- 目录命名与实验编号错位一格（e2 的图在 `e3/`、e3 的图在 `e4/`），
  与 `scripts/plot_all.py` 的 `FIG_SPECS` 一致（pareto→e2、ablation→e3、multiturn→e4）。
  原 README §4 写的 `figures/e2/ablation.png`、`figures/e3/multiturn_trend.png` 已修正。
- 注意：`plot_all.py --exp all` 的输入是按**图目录**读 `results/<图目录>/` 的，
  与上面结果目录（`results/e2`、`results/e3`）并不逐一对应，
  所以验收时**以各实验脚本实际写出的文件为准**（逐张图核对存在性与图注），不要假设 `plots` 步骤会重画全部图。
- 属**文档修正**，不影响任何实验结果。

---

## D24 E2 改用 test_e1 子集；本机实测吞吐远低于 README 记录且随他用应用剧烈波动（**本次会话新增**）

**手册/README 原设定**：E2 消融（S6.6）在 `test` 全量（944 条）上跑 A1/A2/A3
共 10 个变体 ≈ 9440 条生成；E1 6 方法 × `test_e1`(300) + 3 个 τ 缩放工作点；
E5 4 方法 × `temporal_trap`(120)；E3 3 方法 × `test_e4`(60)。

**实际情况一：单流吞吐约为 README 记录的 1/3**

`scripts/_bench_threads.py`（一次性诊断脚本）在本机实测，80-token 生成：

| 配置 | tok/s | s/条 |
|---|---|---|
| threads=4, workers=1（单流） | 2.6 | ~31 |
| threads=14, workers=1（README §1 默认） | 3.47 | 23.07 |
| threads=7, workers=2 | 6.08 | 13.15 |
| threads=4, workers=3 | 6.50 | 12.31 |
| **threads=3, workers=4** | **9.10** | **8.79**（空闲时最优） |
| threads=2, workers=6 | 8.16 | 9.81 |

E1 `neverrag` 已落盘分片的实测 p50 为 **31.1–53.6 s/条**（`threads=14, workers=2`），
而 README §1 记的是 12–15 s/条。原因：本机是 **14 物理核 / 18 逻辑核 @1.2 GHz**
的低功耗 CPU（`Win32_Processor.MaxClockSpeed=1200`），且为**他人正在使用的
个人机**，非 README 写作时的独占机器。

**实际情况二：同配置重复测量相差 4.3 倍**

`scripts/_bench_threads2.py` 用**唯一 prompt**（规避 `GenCache` 命中）复测同一组配置：

| 配置 | 空闲时 s/条（第 1 次） | 他用应用活跃时 s/条（第 2 次） | 倍差 |
|---|---|---|---|
| threads=3, workers=4 | 8.79 | 37.38 | **4.3×** |
| threads=4, workers=3 | 12.31 | — | — |
| threads=3, workers=5 | — | 21.09 | — |
| threads=5, workers=3 | — | 45.28 | — |

同机同配置的差异完全由**他人应用占用 CPU**主导（实测活跃进程含
`WorkBuddy`、`electron`、`Tabbit Browser`、`wallpaper64`、`llama-server`、`douyin`；
空闲时仅占 0.4/14 核，活跃时可达 10+ 核）。**因此本仓库任何"预计总时长"
都只能是量级估计，不能作为交付承诺**；流水线因此全部按 `--shards 6` 切片，
配合 `GenCache` 内容寻址缓存，做到"中断只丢当前片、重跑秒过已算部分"。

**处置**：

1. E2 **改在 `test_e1`（300 条，与 E1 主对比同一子集）上跑** A1/A2/A3 三组
   （`scripts/run_pipeline.py` 显式传 `--split test_e1`）。据此 E2 从 ≈9440 条
   降到 ≈3000 条生成。
2. `scripts/e2_ablation.py` 增加 `--split`（默认仍为手册原设定 `test`）与
   `--max-tokens`（默认 80）。**原脚本的 `run_experiment` 走的是 `max_tokens=128`
   默认值**，与 D19 定下的 80 不一致，会让消融与主对比口径不可比——这是本次
   一并修掉的缺陷。
3. E1 / E5 / E3 维持 README 已定子集（`test_e1` / `temporal_trap` / `test_e4`）不变。
4. E5 / E3 / E2 的调用也补上分片 + merge（原 `run_pipeline.py` 只有 E1 分片）。

**影响与残余风险**：

- **是（E2）**：E2 的样本从 `test` 全量缩到其 31.8% 子集。三组消融之间的
  **相对**差异仍可比（同子集、同模型、同超参），但绝对值不可与手册目标值对比，
  且 E2 与 E1 之外的方法在主对比中的点估计精度受同一抽样限制。
- E1 本就基于 `test` 的 31.8% 子集（D19），本次未再缩减，口径不变。
- 若后续在**独占**机器上复跑，只需去掉 `run_pipeline.py` 里的 `--split test_e1`
  覆盖即可回到手册原设定。

---

## D25 `plot_all.py --exp all` 会用 E3 的多轮结果覆盖 E2 的消融图（**本次会话新增，已实测确认并修复**）

**缺陷**：`scripts/plot_all.py` 的 `FIG_SPECS` 是按**图目录**去 `results/<图目录>/`
找输入的（D23 已记为"目录与实验编号错位一格"），但当时只把它当作文档口径问题。
实际它还是一条**破坏性路径**：

- 规格 `{"name": "ablation", "exp": "e3", "inputs": ["ablation.jsonl|*.jsonl"]}`；
- `results/e3/` 恰好是 **E3 多轮**的逐条结果目录（`e3_multiturn.py` 的 `OUT = Path("results/e3")`）；
- `inputs` 里的 `*.jsonl` 是兜底通配，**命中 E3 的多轮记录**；
- `_draw_real(kind="ablation")` 调 `_all_rows(exp_info)`，**不做任何 schema 校验**，
  于是把多轮记录当成消融行喂给 `_plot_ablation()`；
- 输出路径 `figures/e3/ablation.png` 与 `e2_ablation.py` 写出的**是同一个文件**。

**实测确认**（本次会话）：造一个哨兵 `figures/e3/ablation.png`（内容
`SENTINEL-REAL-ABLATION-FIGURE`）+ 一个 `results/e3/*.jsonl`（E3 形状的多轮记录），
跑 `python scripts/plot_all.py --exp all --results-dir ... --figures-dir ...`：

```
ablation    _clobber_test/figures/e3/ablation.png    written (36083 bytes)
```

哨兵被重写成 36 KB 的 PNG；对照组的 `figures/e4/multiturn_trend.png` 因
`results/e4/` 不存在而未被触碰（`_draw_real` 返回 `None` → 记 skipped）。
即：**该步会把数小时算力换来的 E2 消融图，换成一张用错数据画的图**。

**处置**：`scripts/run_pipeline.py` 的步骤顺序改为
**e1 → e5 → e6 → __plots__ → e3 → e2**（出图提到 E3/E2 之前）。
此时 `results/e3`、`results/e4` 尚不存在，`plot_all` 对这两张图只会记 skipped、
不落盘，随后由 `e2_ablation.py` / `e3_multiturn.py` 写出正确版本。
`plot_all.py` 本身未改（避免动 36 KB 的绘图管线，且其 `--demo` 自检依赖现行为）。

**残余风险**：若单独重跑 `--only plots`（例如在 e2/e3 已完成之后再跑一次），
该覆盖仍会发生。**验收一律以各实验脚本实际写出的图为准**（与 D23 同），
并在跑 `plot_all` 前确认 `results/e3`、`results/e4` 不存在，或跑完后用
`python scripts/e2_ablation.py --analyse-only --split test_e1` /
`python scripts/e3_multiturn.py --analyse-only --split-e4` 重画。

**影响**：修正缺陷。若不修，E2 的图表产物会被静默替换成错误内容
（且脚本仍返回 exit 0，属"先污染后掩盖"）—— 这正是本仓库要避免的失败模式。

---

## D26 三通道端到端冒烟（`scripts/smoke.py`，20 组）查出四个缺陷（**本次会话新增；四个均已修复并复测 20/20**）

**缘起**：验收表 §S3 一直有一行空白——「三通道冒烟 20 组 | （空） |
`scripts/smoke.py` 已存在 | 未运行完整 20 组」。**但 `scripts/smoke.py` 从来不存在**
（仓库里只有 `scripts/smoke_s0.py`，那是 S0.4 的单模型冒烟）。也就是说这一格既没有脚本
也没有结果，却在"备注"里写着"已存在"。本次补上脚本并真跑了一遍，**首轮 14/20 通过、
6 组失败**，失败集中指向下面四个缺陷；**修复后复测 20/20**。

冒烟走的是**真实 HTTP 路径**（`POST /chat` → 路由 → trace），不是直接调库；
这一点是关键——下面四条**在组件级自检（`quickcheck.py`）里全是绿的**，
只有走端到端才暴露。产物：`docs/smoke_report.md`、`docs/smoke.json`、
`docs/smoke_raw.jsonl`（含逐条 trace 全文）。

### D26-1 条号形式不成时静默降级，模型会**原文引用已废止法律且不给警示** ✅ 已修

复现：`POST /chat {"query": "婚姻法32"}`

```
channel=A  bucket=b1  u=0.0  tau=0.29  gate=complexity  calls=0
答案：《中华人民共和国婚姻法》第三十二条：男女一方要求离婚的，可由有关部门进行调解…
```

`RE_ARTICLE_REF` 只认「第X条」形式，所以「婚姻法32」抽不到 `article_no` →
`slots_complete=False` → 不进通道 B → 落到门控 → 通道 A 由 1.5B **凭记忆背诵条文原文**，
**没有任何废止警示**。《婚姻法》已于 2021-01-01 废止，这正是本项目存在的理由
（E1 的 `invalid_law_citation_rate` 量化的就是这个）。`公司法47` + `as_of=2024-06-30` 同理。

注意 `quickcheck.py` §2 测「婚姻法32」是**直接调 `TemporalChecker`**，
所以该组件级验收永远是绿的；端到端才看得出它根本没进那条路径。
**影响**：**是**（演示与申报的核心卖点"失效法条警示"在这类输入下不触发）。

**修复**：`lawgate/gate/intent.py` 新增 `RE_ARTICLE_AFTER_LAW`（`^\s*第?\s*(\d{1,4})(?!\d)\s*条?(?!\s*[年月日])`）
与 `_plausible_article_no()`，在 `RE_ARTICLE_REF` 未命中时，从**法名结束位置**起匹配"紧跟数字"的写法。
两道守卫都必要：`(?!\d)` 防回溯（实测漏掉它时「民法典2021年施行」被啃成第 **202** 条），
`_plausible_article_no` 排除 1900–2100 的年份。复测：`婚姻法32` → 通道 B「已废止 + 民法典第一千零七十九条」
替代条与来源；`公司法47 @2024-06-30` → 「尚未生效」。`intent_accept.py` 复跑无回归
（recall 0.9756 / 误触 0.05 / 槽位 0.9344，与修复前逐位相同）。

### D26-2 UI 预设③「真实案号 + 错误案由」不会触发案由比对 ✅ 已修

复现（`lawgate/api/ui.py` 里那一行**原样字符串**）：

```
POST /chat {"query": "（2019）鲁01民终1242号 这个案子是劳动争议，对吗？"}
→ case_verify.level = "核验通过"        （承诺的是「存在但案由不符」）
```

链路：`ui.py` 预设③ → `detect_intent` 靠 `topic_keywords` 抽 `topic`；
「劳动争议」这一**条目名**本身不在自己的关键词表里（该表是 加班费/辞退/裁员/劳动合同…），
所以 `slots.topic=None` → `ChannelB` 调 `verifier.verify(parsed, slots.topic)`
拿到的是 `claimed_cause=None` → 核验器**无从比对** → 报「核验通过」。

`README.md` §1 演示话术第 3 步、`lawgate/api/ui.py` 模块 docstring 都写着这一步应出
「存在但案由不符」。**影响**：**是**（这是答辩演示的第 3 步，现场会翻车）。

**修复**：`lawgate/channel/b_structured.py` 新增 `_load_causes()` / `_claimed_cause()`——
案由词表直接取自 `case_registry.cause_action` 的去重值（长词优先），从提问原文里扫出用户**声称**的案由；
`slots.topic` 降为兜底（它只是主题标签，粒度不等于案由）。没有声称就不做比对（返回 None）。
复测：UI 预设③ 那句原文 → 「⚠️ …真实案由为「民间借贷纠纷」，与所述「劳动争议」不符」。

### D26-3 通道 B 的 P4（主题条文 FTS）**恒不命中**，实际形同虚设 ✅ 已修

复现：`POST /chat {"query": "民间借贷不还钱适用哪条法律？"}`

```
slots = {topic: "民间借贷", provision_seeking: true}   → intent 判 slots_complete=True，确实进了通道 B
但 channel=A（降级）  steps=[]（无 topic_fts）
```

根因在**建库时的分词器**：`lawgate/knowledge/schema.sql` 里
`CREATE VIRTUAL TABLE provision_fts USING fts5(..., tokenize='unicode61')`。
`unicode61` **不切分中文**——它把一整串连续汉字当成**一个** token。实测：

```
SELECT count(*) FROM provision_fts WHERE provision_fts MATCH '利息';        -- 0
SELECT count(*) FROM provision_fts WHERE provision_fts MATCH '"支付利息"';  -- 0
SELECT count(*) FROM provision_fts WHERE provision_fts MATCH '民法典';      -- 96（law_short 列里是独立 token）
```

而 `ChannelB._fts_query('民间借贷')` 生成的是一长串中文 OR 条件，于是
**永远 0 行** → `trace["steps"].append("topic_fts_miss->C")` → `return None` →
降级到门控。也就是说 P4 这条路线自建成起就没有命中过一次，而 README §2 的架构图
与 ACCEPTANCE 都把 P1–P4 并列陈述为"四条路径"。

顺带：`docs/quickcheck.txt` §4「通道 B 端到端」只覆盖了 P1/P2/P3（民法典667 / 合同法52 /
担保法 / 案号），**没有一条 P4 用例**，所以这个缺陷在自检里看不见。

**连带后果**（本次实测的真实答案）：B-P4-1 降级到通道 A 后，模型回答
「…适用《中华人民共和国合同法》的相关条款，如第一百零七条…」——**引用了已废止的《合同法》，
且没有警示**。与 D26-1 是同一类伤害：该用知识库的时候没用上，模型就自由发挥。

**影响**：**是**（通道 B 四条路线实际只有三条可用；主题类法条索取问题会被推给不可靠的直答）。

**修复**：`lawgate/channel/b_structured.py` 的 P4 改为走 `_topic_provisions()`，
对 `provision_keywords`（建库时按条文标注的主题词表，没有分词问题）做**等值**匹配，
再回连 `legal_provisions` 取"现行有效"行；同一 (law, article) 的多"款/项"行只取首行。
**没有重建数据库**——`provision_keywords` 本来就是建库产物，只是先前没人用它。
`_fts_query()` / `_FTS_SPECIAL`（含那条**写反了的注释**"unicode61 对中文按字切分"）已删除；
`provision_fts` 表仍由 `build_sqlite.py` / `import_law_text.py` 写入，但**已不参与路由**。
trace 步骤名相应由 `topic_fts` 改为 `topic_kw`。

顺带补上 `PROVISION_SEEKING` 的 `"有哪些"`：`「借款合同的法律规定有哪些？」`是典型查条问法，
而原表只有"怎么规定/如何规定/规定是"，一个标记都不命中 → `slots_complete=False` → **P4 根本进不去**
（只修检索也白搭）。刻意**不**收"有什么"——G5-09 记的正是「…对消费者有什么影响？」这类误触。
`intent_accept.py` 复跑无回归（数值与修复前逐位相同）。

复测：`民间借贷不还钱适用哪条法律？` → 通道 B，取自知识库的《民法典》667/668/674/675/676/679/680
及官方来源 URL（此前会降级到通道 A 并引用已废止的《合同法》第一百零七条）。

### D26-4 多轮槽位继承在 **FastAPI `/chat` 路径上永远不可能生效**（UI 不受影响） ✅ 已修

复现（history 按 `ui.py` 的格式给全，含 `trace`）：

```
POST /chat {"query":"那我能主张多少利息？",
            "history":[{"query":"民法典第六百六十七条","answer":"x",
                        "trace":{"channel":"B","slots":{"law_short":"民法典",...}}}]}
→ slots_inherited = []
```

对照实验（同一 history，绕开 HTTP 直接调 `detect_intent`）：

```
带 trace        → inherited=['law_short']  law_short=民法典
不带 trace      → inherited=[]             law_short=None
```

根因：`lawgate/api/app.py` 的 `Turn` 模型只声明了 `query` / `answer` 两个字段：

```python
class Turn(BaseModel):
    query: str
    answer: str | None = None
```

pydantic 默认丢弃未声明字段，于是 `ui.py` 明明把 `trace` 塞进了 history
（`history + [{"query":…, "answer":…, "trace": trace}]`），**在 API 边界上被丢掉**；
而 `detect_intent` 的继承逻辑只认 `turn["trace"]["channel"] == "B"`（D10/D12 的设计：
只继承"经通道 B 事实确认"的槽位，不从未确认轮次抓词）。两者对不上，
继承分支在 API 上**恒不触发**。

**范围限定（重要）**：`lawgate/api/ui.py` 的 `respond()` 是**进程内直调**
`router.answer(query, history, meta=meta)`，history 里的 `trace` 一路带到 `detect_intent`，
所以 **Gradio 界面的多轮继承是好的**；只有走 FastAPI `/chat` 的调用方
（`app.py` 的 `Turn` 模型）会被静默剥掉 trace。即缺陷在 **API 契约**，
不在路由逻辑——验证时别把两者混为一谈。

**影响**：**是**（对外提供的 HTTP 接口上，"多轮槽位继承"这一条契约不成立；
用 `/chat` 做集成或答辩演示 API 时会得到与 UI 不一致的行为）。

**修复**：`lawgate/api/app.py` 的 `Turn` 模型补上 `trace: dict | None = None`。
复测：history 带上 trace 后 `slots_inherited=['law_short']`（修复前恒为 `[]`）。

### 处置（**四处已全部修复，复测 20/20**）

| 缺陷 | 改动文件 | 是否动路由/检索行为 |
|---|---|---|
| D26-1 | `lawgate/gate/intent.py`（新增 `RE_ARTICLE_AFTER_LAW`、`_plausible_article_no`） | 是（意图抽取） |
| D26-2 | `lawgate/channel/b_structured.py`（`_load_causes` / `_claimed_cause`） | 是（所称案由的来源） |
| D26-3 | `lawgate/channel/b_structured.py`（P4 改走 `provision_keywords`）+ `intent.py`（`PROVISION_SEEKING` 补"有哪些"） | 是（检索路径首次可用） |
| D26-4 | `lawgate/api/app.py`（`Turn.trace`） | 否（纯漏字段） |

**为什么动完之后 τ_b 不用改**：改动后重跑了
`python scripts/calibrate.py --split dev_calib --grid-max 1.0 --overwrite`，
产出与改动前**逐位相同**（`b1 0.29 / b2 1.0 / b3 1.0 / b4 0.49`，
hybrid `dev_acc=0.3403 dev_rr=0.4931`），即本次修复没有移动已校准的工作点。
**没有重建知识库**（D26-3 用的是既有的 `provision_keywords` 表，不是新建表）。

**回归检查**（全部通过）：

| 检查 | 结果 |
|---|---|
| `scripts/intent_accept.py`（66 条意图集） | recall 0.9756（40/41）/ 误触 0.05 / 槽位 0.9344 —— 与修复前**逐位相同** |
| `scripts/quickcheck.py` | 意图 6/6、案号 6/6，exit 0 |
| `scripts/calibrate.py --split dev_calib --grid-max 1.0` | τ_b 不变（见上） |
| `scripts/smoke.py` | **20/20**，exit 0 |

**口径提醒**：`scripts/smoke.py` 的 20 组断言是**按设计契约写死**的（写在 `CASES` 里，
不是照抄实际输出）。首轮 6 组 FAIL 是真实缺陷；修复后 20/20 代表"契约被满足了"，
**不代表**系统在法律问答上准确——那是 E1/E6 的事，且仍受本文件其它口径限制约束。
`results/e1/` 里已落盘的 5 个分片是 `neverrag` **基线**（不经过路由），
不受本次修复影响；但**尚未落盘的 LegalGate 臂**若将来重跑，其行为与修复前不同，
引用时必须写明是哪一版代码产出的。

---

## D27 `启动服务.bat` 双击必然失败：LF 行尾 + UTF-8 中文路径（**本次会话新增，已实测复现并修复**）

**现象**（用户双击 `启动服务.bat` 报的原文）：

```
'demo' 不是内部或外部命令，也不是可运行的程序或批处理文件。
文件名、目录名或卷标语法不正确。
'his' 不是内部或外部命令…
'is' … '/d' … 'el' … 'go' … 'rshell' …
Services stopped. Press any key to close.
```

**两个独立缺陷叠加**，只修一个都不够：

**D27.1 文件是 LF 行尾**。`启动服务.bat` 实测 `CRLF=0 / 裸LF=20`。
cmd.exe 读批处理是按固定缓冲块找行边界的，LF-only 会让它**停在行中间**，
于是每行的**尾巴**被当成命令执行 —— 报错里的每个碎片都对得上：
`demo` = "…LawGate **demo** services…"、`his` = "this"、`is` = "is"、
`/d` = "cd **/d**"、`rshell` = "pow**ershell**"。

**D27.2 .bat 里写了中文文件名**。CMD 是按**控制台代码页**读 .bat 字节的，
本机 `chcp` = **936**，而文件是 UTF-8，于是字面量「启动服务.ps1」在 cmd 眼里是乱码。
实测（把测试 .bat 放进仓库根目录，确保 `%~dp0` 指向真实位置）：

```
if exist "%~dp0启动服务.ps1" (echo LAUNCHER_EXISTS) else (echo LAUNCHER_MISSING)
→ LAUNCHER_MISSING          # 文件明明就在那儿
```

即**即使行尾修好了，启动器也永远找不到**。讽刺的是该文件自己的第 7 行注释写着
`Keep this file ASCII-only so any Windows code page can run it` —— 它违反了自己的规矩。
（注意与 README 既有约定的**冲突**：两个 `.ps1` 要求 UTF-8 with BOM，而 `.bat` 要求纯 ASCII。）

**为什么以前没暴露**：README 里给的命令是 `powershell -ExecutionPolicy Bypass -File .\启动服务.ps1`，
**直接调 .ps1 是好的**（中文名由用户在控制台输入，走 936 无损）；这个缺陷**只在"双击 .bat"这条路径上**出现。

**处置**：重写 `启动服务.bat` 为**纯 ASCII + CRLF**（39 个 CRLF、0 个裸 LF、0 个非 ASCII 字节），
并不再用字面量定位启动器 —— 改在 PowerShell 里按**文件名首字符的码位**筛选：

```powershell
Get-ChildItem -Filter *.ps1 |
  Where-Object { [int][char]$_.Name[0] -eq 0x542F } | Select-Object -First 1
```

`%~dp0` 可以放心用：它由 cmd **自己生成**（真实路径 → 当前代码页），对本机路径里的
「桌面」是**无损**的；出问题的只是**文件字节**里的中文。这样该 .bat 在任何代码页下都能用。

**实测验证**（修好后）：

| 检查 | 结果 |
|---|---|
| `cmd /c 启动服务.bat` | 无碎片报错；Gradio `7860` 与 FastAPI `8010` 均就绪并监听 |
| HTTP 探活 | `UI 200`、`/health` `status=ok` |
| 参数透传 | `启动服务.bat -BogusParam` → PowerShell 报 `NamedParameterNotFound`（说明 `@args` 转发正常，`-NoApi` / `-ApiPort` 可用） |

**影响**：修正缺陷。不修则**演示/答辩时双击入口 100% 起不来**，
且报错信息（"不是内部或外部命令"）指向完全无关的方向，极易误判成环境问题。

---

## D28 大模型输出改为**流式**（新增 `POST /chat/stream` + 双栏流式界面）；统一两栏生成长度上限为 **192**；顺带查出通道 B 的 sqlite 连接**跨线程共享**（**本次会话新增**）

**缘起**：演示时两栏都要等十几秒才一次性出字（本机 CPU 单条 80 token 约 10–15 s），
现场观感就是"点完没反应、页面像卡住了"。本次把"大模型输出"整体改为流式：
后端逐 token 产出 → `POST /chat/stream`（SSE）逐帧推 → Gradio 双栏边生成边显示；
并把**两栏的生成长度上限统一**（否则"双栏对照"不公平，见 D28-3）。
实现过程中撞出一个**潜伏缺陷**（下面的 D28-1），一并修掉。

### D28-1 通道 B / 时效核查 / 案号核验持有**单条 sqlite 连接**，跨线程使用必然抛异常 ✅ 已修

复现（离线自检 `scripts/check_stream.py` 的第一条 SSE 用例就撞上）：

```
event: error
data: {"message": "ProgrammingError: SQLite objects created in a thread can only be
       used in that same thread. The object was created in thread id 22612 and this
       is thread id 22660."}
```

根因：`ChannelB.__init__` / `TemporalChecker.__init__` / `CaseNoVerifier.__init__`
都在构造时 `sqlite3.connect()` 一条连接存进 `self.conn`，之后所有查询复用它。
sqlite3 的连接默认**禁止跨线程**，而 FastAPI 的同步端点（`/chat`，以及新增的
`/chat/stream`）跑在 Starlette 的线程池里，评测的 `workers>1` 也是多线程——
构造线程与请求线程不是同一条，于是直接抛异常。

**为什么之前一直没被发现（重要）**：
* 评测路径 `run_exp.RunContext` 有一条 thread-local 垫片（`_tls`），
  `workers>1` 时每个工作线程会另建一份 ChannelB，所以评测多线程是好的；
* `uvicorn` 下同一客户端的连续请求**大多**落在同一条线程池线程上（AnyIO 线程池复用），
  20 组冒烟因此全绿；`quickcheck.py` 是单线程直调，更是永远绿。
  换句话说，这是**潜伏缺陷**：只要有并发（两人同时点、或浏览器重发请求）就会冒出来。
  （该"HTTP 下偶发"未单独复现，此处结论来自线程模型的推理 + TestClient 下的稳定复现。）

**修复**：三个类统一改为**按线程惰性建连接**——`threading.local()` + `conn` 属性
（与 `lawgate/cache.py` 里既有的 `GenCache._local` 做法一致，不引入新范式），
`ChannelB.close()` 只关当前线程那一条。所有 `self.conn.execute(...)` 调用点**无需改动**。

**影响**：**是**（不修则 `/chat/stream` 的真实往返 100% 失败；`/chat` 在并发下会随机 500）。

### D28-2 流式输出：`POST /chat/stream`（SSE）+ 双栏同步流式 ✅ 已实现

**帧协议**（每帧 `event: <名>` + `data: <JSON>`，UTF-8）：

```
event: stage    {"stage":"channel_b|retrieval|generate", "channel":…, "u":…, "tau_b":…, …}
event: delta    {"text": "增量文本"}
event: done     {"answer": 权威全文, "trace": {…}, "provision": …}
event: error    {"message": "异常类型: 异常信息"}
```

**分工**：

| 文件 | 改动 |
|---|---|
| `lawgate/channel/llm_base.py` | `BaseLLM.generate_stream`（默认整段产出一次）；`HFLLM` 用 `TextIteratorStreamer` + 后台线程**真·逐 token**；`VLLMLLM` 用离线引擎 `add_request` + `step()` 循环（本机无 vllm，**未实测**） |
| `lawgate/router.py` | 路由决策抽成唯一的 `_answer_events(stream)`；`answer()`（评测用）与 `answer_stream()`（界面/接口用）都消费它 |
| `lawgate/api/app.py` | `POST /chat/stream`：**同步** `def` + `StreamingResponse`，靠 Starlette 的 `iterate_in_threadpool` 逐帧取数 |
| `lawgate/api/ui.py` | `respond_stream` 生成器：左栏（对照）在后台线程写 `queue`、右栏在本线程按事件推进 |

三处口径必须写清楚，否则容易被误读成"逐字打字"：

1. **流式与整段共用同一把缓存键**（`model|generate|max_tokens|prompt`）且解码参数相同
   （贪心、无采样），两者互认缓存、文本一致——`scripts/smoke_stream.py` 双向断言；
2. **通道 B 是查库拼装，没有 token 流**：整段一次性 delta，不假装打字；
3. **`delta` 只是增量**：`done.answer` 才是权威全文（前端以它收口，
   避免逐片解码在"多字节字符被切成两个 token"时留下的细微差异）。

**为什么端点是同步 `def` 而不是 `async def`**：路由 + 生成是 CPU 密集阻塞调用，
放进事件循环会卡死整个服务（本项目既有教训：端口 LISTENING 但全部超时）。
Starlette 对同步生成器自动用线程池逐帧取数，事件循环始终空闲。

**验证**：

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `scripts/check_stream.py`（离线，`ExtractiveLLM`，**秒级**，不占权重） | 事件序列、`answer()`/`answer_stream()` 同源一致性、SSE 帧协议、中文编码、异常成帧 | 全部通过 → `docs/check_stream.txt` |
| `scripts/smoke_stream.py`（进程内，**真机 1.5B**） | 片数 ≥ 2、首字远早于结束、流式≡整段（缓存双向互通）、UI `respond_stream` 渐进刷新、SSE 帧序列 | 全部通过 → `docs/smoke_stream.md`（`--unique` 真实生成口径） |
| `scripts/smoke_stream.py --base-url … [--unique]`（打真实服务） | 线上 SSE 帧协议、`charset=utf-8`、真实逐 token 时延 | 全部通过 → `docs/smoke_stream_http.md` |
| `scripts/smoke.py`（三通道 20 组，走真实 `POST /chat`） | **回归**：sqlite 连接改按线程建后，通道 B 端到端不受影响 | **20/20**（`docs/smoke_report.md`） |
| `scripts/quickcheck.py` | **回归**：意图/时效/核验/通道 B 组件级 | 通过（`docs/quickcheck.txt`） |

真机口径（本机 CPU-only，AMD64 18 核、他人共用；1.5B fp32；`--unique` 强制不吃缓存）：

| 场景 | 首字/首片 | 整段 | 增量片数 |
|---|---|---|---|
| 纯生成（`max_new_tokens=48`） | **1.0 s** | 13.9 s | 39（无 U+FFFD） |
| 走完整门控（草稿 20 token + 通道 C 检索 + **192 token** 生成） | **19 s**（多次运行 9–22 s） | 64 s（多次运行 56–70 s） | 173 |
| 同一 prompt 第二次（缓存命中） | ≈ 0 | **0.001 s** | 11–47 |
| 通道 B（查库拼装） | ≈ 0 | 毫秒级 | 1 |

即"干等 60 秒"变成"十几秒开始出字、边出边看"，且缓存命中时仍走同一条渲染路径。
首字延迟在 9–22 s 之间波动，来源是本机为他人共用的 18 核 CPU（同一配置重复测量本就有 2× 以上差异，
参见 D24）；**这不是流式实现的开销**——它由"门控草稿 20 token → 检索 → 192 token 上下文预填充"三段组成，
UI 上正由 `stage` 事件（`channel_b`/`retrieval`/`generate`）覆盖这段等待。
线上（`--base-url`）实测同样通过：缓存命中时整条 SSE 62 ms / 45 个 delta 帧；
强制真实生成时首 delta 22.4 s、总 92.6 s / 168 帧。

**影响**：**否**（新增能力）。`/chat` 的返回值与 trace 字段未变，
E1/E2/E3 等实验结果不受影响（`answer()` 仍走非流式生成）。

### D28-3 演示界面两栏的**生成长度上限不一致**（左栏 80 / 右栏 192）✅ 已统一为 **192**

**现象**：同一场演示里，左栏（无门控直答）写不满就停，右栏能继续往下写。
两个上限来自**两个不同的源**：

| 栏 | 上限来源 | 改前实际取值 |
|---|---|---|
| 左栏 直答 | `HFLLM.max_new_tokens` ← `Settings.max_new_tokens` | `启动服务.ps1 -UiMaxTokens`（当时默认 **80**） |
| 右栏 律核 | `RouterOptions.max_new_tokens`（通道 A/C 生成用） | `ui.ensure_loaded()` 调 `build_context()` 时**没传 options** → dataclass 默认 **192** |

后果有两层：① "双栏对照"本身**不公平**——右边能写到 192 token，长答案看起来"更完整"，
而这正是本项目最该避免的演示瑕疵；② 两栏的等待时间也对不上。

**统一取值：192**（用户的明确口径）。为什么是 192 而不是 80：

* 192 是 `RouterOptions` 的默认值，也是**接口侧**（`/chat`、`/chat/stream`）实际在用的值
  —— 统一到 192 之后，界面、接口、评测三处不再有两套口径；
* 演示要展示"通道路由带来的差异"，答案被截断在半句上会掩盖差异，
  上限放宽到 192 让答案自然收尾；
* 代价是每问等待变长（左栏从 ≤80 变成 ≤192 token），已写进 `启动服务.ps1` 的提示行，
  并保留 `-UiMaxTokens 80` 这条"临时缩短"的口子（两栏仍会一起变，口径不破）。

**修复**（`lawgate/api/ui.py`）：

1. 新增 `gen_max_tokens()`：**唯一来源** = `Settings.max_new_tokens`
   （`LAWGATE_UI_MAXTOK` / `base.yaml: max_new_tokens`，现默认 **192**）；
2. `ensure_loaded()` 用 `RouterOptions(max_new_tokens=mt)` 构造 router —— 与评测侧
   `eval/run_exp.py` 的既有约定一致（"LegalGate 的生成参数走 router options，
   必须与其它基线一致，否则对比不公平"）；
3. `respond_stream()` 经 `column_max_tokens(router)` 取**右栏真实生效的那个数**，
   显式传给左栏 `generate_stream(max_tokens=…)`——两栏同长由**构造**保证，
   而不是靠两处配置碰巧相等；
4. 状态行写明"两栏生成长度上限均 N token"，现场可直接指给评审看。

配套默认值改动（三处，现在都是 192）：`configs/base.yaml: max_new_tokens`、
`scripts/serve_one.py` 的 `LAWGATE_UI_MAXTOK` 默认值、`启动服务.ps1 -UiMaxTokens` 默认值。

**验证**（三层，都是可复现命令）：

| 检查 | 位置 | 做法 | 实测 |
|---|---|---|---|
| 装配链路（Settings → RouterOptions → 两栏） | `scripts/check_stream.py` §5（离线，秒级，假后端） | 临时把 `Settings.max_new_tokens` 设成 **77**（与 192/128/80 都不撞），记录两栏**实际请求**的 `max_tokens`（按线程名区分左/右栏） | `ensure_loaded` 传入 77 → 左栏 `{77}`、右栏 `{77}` |
| 真机请求值 + 实际长度 | `scripts/smoke_stream.py` §F（真权重） | 包一层 `generate_stream` 记录两栏请求值与线程名，并用 `tok.encode` 量实际输出 | 上限 192 → 两栏请求同为 `[192]`；实际长度 **167 / 192 token**（左栏自然收尾、右栏顶到上限，两者都不超） |
| **演示口径**（一键脚本） | `scripts/_ui_cap_probe.py`（真权重，只加载不生成） | 复现 `serve_one.py` 的动作（`Settings.max_new_tokens = 192`），真实 `ensure_loaded()` 后打印两栏上限 | 左栏 **192** / 右栏 **192** / `STATE` **192** ✅（`LAWGATE_UI_MAXTOK=80` 时→ 两栏 80，验证"临时缩短"仍一致） |

**影响**：**否**（演示口径修正）。`Settings.max_new_tokens` 只被 `HFLLM` 的**默认**上限与 UI 读取；
评测各脚本都显式传 `--max-tokens`（`eval/run_exp.py` → `RouterOptions`），故 E1–E6 结果不受本项影响。

**代价（须向使用者讲清楚）**：上限 192 比 80 长一倍多，两栏各要生成更久——
本机实测一"问"两栏合计约 **60–90 s**（左栏 192 token 约 46 s，右栏门控路径约 64 s，
两者并行；缓存命中后 ≈ 0.1 s）。演示时建议用预设按钮里的**缓存过的问题**
（如《合同法》第52条、编造案号、担保法是否有效）以便秒回；
需要更快时用 `启动服务.ps1 -UiMaxTokens 80`（两栏一起变短，口径仍一致）。

---

## D29 最终回答的大模型换成**本地 fuzi-mingcha-v1_0**（夫子·明察，6.7B / ChatGLM 底座）：接入过程撞出 14 处"新旧框架接口缝"（**本次会话新增**）

**用户要求**：把 `models/fuzi-mingcha-v1_0`（本机已下载的司法大模型）接入为**最终回答**所用的大模型。

**结论（一句话）**：已接入并真机验证通过——最终回答由本地 **fuzi-mingcha-v1_0**（fp16、CPU、15 个权重分片 / 368 个张量）生成；流式与整段一致、缓存生效、后端溯源写入 trace。为此新增一层兼容装载模块 `lawgate/compat_chatglm.py`，并**没有改模型目录里的任何文件、也没有升降依赖版本**。

**为什么"接入"不是改一行路径**：`lawgate/config.py` 早已把 fuzi 列为候选第一名，`llm_base.py` 里也已写好 ChatGLM 的提示词模板分支，但**真正加载是跑不起来的**——它会依次撞上下面这一串问题（每一处都是独立故障点，修对前一处才会露出下一处）。把这条链完整记录下来的价值在于：它同时解释了"为什么之前实际生效的是 HF 缓存里的 0.5B 小模型"。

| 序号 | 症状（实测报错/现象） | 根因 | 处置 |
|---|---|---|---|
| D29-1 | `OSError: Not found: "D:\????\lawgate\...\ice_text.model"` | sentencepiece 在 Windows 用 ANSI(936) 解码路径，**本仓库路径含中文**（`D:\桌面\`） | 补丁：`Load→LoadFromSerializedProto(bytes)` |
| D29-2 | `TypeError: ChatGLMTokenizer._pad() got an unexpected keyword argument 'padding_side'` | 旧 tokenizer 的 `_pad` 签名比新版 `PreTrainedTokenizerBase.pad()` 少一个参数，连 `tok.encode()` 都过不去 | 补丁：补一个吃掉多余关键字的 `_pad`（只补签名） |
| D29-3 | 加载中 `PermissionError: [WinError 5] … E:\ModelCache\…\transformers_modules\…` | `trust_remote_code` 的动态模块缓存写在全局 `%HF_HOME%/modules`（仓库外，不可写且不可复现） | 加 `lawgate/env_setup.py`，把 `HF_MODULES_CACHE` 指到仓库内 `models/.hf_modules_cache` |
| D29-4 | `ValueError: Unrecognized configuration class ChatGLMConfig for AutoModelForCausalLM` | 新版 `AutoModelForCausalLM` 有模型类型白名单，不收未注册的远程 config | 按官方 README 用 `AutoModel`（`auto_map` 里正指向 `ChatGLMForConditionalGeneration`） |
| D29-5 | 权重全部加载完却 `AttributeError: … no attribute 'all_tied_weights_keys'`，随后 `'frozenset' object has no attribute 'keys'` | transformers 5.x 的加载收尾要读该属性，旧基类没有 | 补丁：给模型类挂 `all_tied_weights_keys = {}`（空字典是**准确**的：368 个权重全部来自 checkpoint，且 lm_head 与词嵌入本就是两张独立权重） |
| D29-6 | `AttributeError: 'ChatGLMForConditionalGeneration' object has no attribute 'generate'` | transformers ≥4.50 起 `PreTrainedModel` 不再继承 `GenerationMixin`，而老模型代码只写了 `prepare_inputs_for_generation` | 动态派生 `(原类, GenerationMixin)` 子类补回生成能力（顺序关键：GenerationMixin 在后，不覆盖 ChatGLM 自己的特殊方法） |
| D29-7 | `AttributeError: 'ChatGLMConfig' object has no attribute 'num_hidden_layers'` | 新版预分配 KV cache 读 `num_hidden_layers`，ChatGLM 的 config 里叫 `num_layers` | 补属性别名，不改模型仓库的 `configuration_chatglm.py` |
| D29-8 | `TypeError: 'DynamicCache' object is not subscriptable` | 新版 `generate` 塞进来的是 `DynamicCache` 对象，旧模型代码按元组 `past_key_values[i]` 取 | 加**自管缓存桥接**（`ChatGLMCacheBridge`），框架侧缓存不参与 |
| D29-9 | 上述桥接"明明装了却不起作用" | `nn.Module.__getattr__` 把未定义的 `generate` 透传给内层模型，生成循环里的 `self(...)` 指向内层，绕过了桥接 | 桥接自己实现 `generate`（在内层实现上以本壳为 `self` 调用）+ 内层入口改道回本壳 |
| D29-10 | `AttributeError: type object 'ChatGLMCacheBridge' has no attribute '_sample'` | 新版 `generate` 用 `getattr(type(self), ...)` 取解码函数，实例级透传不参与 | 让桥接壳也继承 `GenerationMixin`（动态派生） |
| D29-11 | `RuntimeError: masked_fill_ only supports boolean masks, but got mask with dtype __int64` | 新版框架递 2D int64 掩码，旧模型要自己的 4D **bool** 因果掩码 | 桥接摘掉 ≤2D 的掩码，交给模型自己 `get_masks()` 造 |
| D29-12 | `IndexError: index 1 is out of bounds for dimension 1 with size 1` | 位置编码形状写成 `[batch, seq, 2]`，模型内部按 `position_ids[:, 1, :]` 取块位置，要 `[batch, 2, seq]` | 改成 `[batch, 2, seq]` |
| D29-13 | 解码步 `AttributeError: 'NoneType' object has no attribute 'max'` | 自管缓存时框架把 `position_ids` 置空，而 ChatGLM 的位置编码不能缺 | 桥接按模型自身规则补 `[[mask位置], [块序号]]`，块序号 = `past长度 − prompt长度 + 1` |
| D29-14 | **输出退化**：回答变成"拖欠工资怎么办? 拖欠工资,你有权利,你有权利向劳动仲裁机构机构机构机构…" | 预填/解码判据写错——新版 `generate` **第一步就递一个空 DynamicCache**，用"有没有 past_key_values"判断会把预填步当成解码步（位置编码用了块序号、prompt 长度也没记下） | 判据改为"我们自己的预填是否已完成"（`_prefilled`） |
| D29-15 | `AttributeError: 'dict' object has no attribute 'to'` | 适配后的 tokenizer 返回普通 dict，上层要 `.to(device)` | 改返回 `BatchEncoding` |

**另外两处口径修正（不是报错，是"接进去也不对"）**：

* **D29-16 精度**：`HFLLM` 原先在 CPU 上固定 `float32`。fuzi 是 6.7B 参数、权重本身存的就是 fp16（`config.json: torch_dtype=float16`），按 fp32 加载会**膨胀到约 27 GB**（本机总内存 31.6 GB）——既慢又极易把机器拖死。故新增 `Settings.dtype`（`configs/base.yaml: dtype`，可被 `LAWGATE_DTYPE` 覆盖），本机取 `float16`（实占 **12.5 GiB**）。**实测 fp16 在本机 CPU 上输出正常**（官方 README 的示例问句"你好"→"你好,我是一个AI助手,有什么可以帮助你?"）。
* **D29-17 模型选择显式化**：`configs/base.yaml` 新增 `causal_model: models/fuzi-mingcha-v1_0`。此前是"候选目录碰巧存在谁就用谁"（`models/qwen2.5-1.5b-instruct` 实际不存在，于是静默退到 HF 缓存里的 0.5B）——与本项目 D21 记录的问题同源。现在生效模型**由配置显式钉死**，并在 `Settings.model_source` 里记录"这个路径是怎么定下来的"。

**新增/改动的文件**：

| 文件 | 改动 |
|---|---|
| `lawgate/compat_chatglm.py` | **新增**。兼容装载层：sentencepiece 字节加载补丁、旧 tokenizer `_pad` 补丁、config 别名、`GenerationMixin` 注入、`all_tied_weights_keys` 补丁、tokenizer 适配器（补 `[gMASK] <sop>`、返回 `BatchEncoding`）、**KV cache 桥接**、`load_chatglm()` / `is_chatglm_dir()` |
| `lawgate/env_setup.py` | **新增**。环境变量统一引导（含 `HF_MODULES_CACHE` 指到仓库内），在任何 torch/transformers 导入前生效 |
| `lawgate/__init__.py` | 改为调用 `env_setup.apply()`（原先只设了两个变量） |
| `lawgate/config.py` | 新增 `dtype`、`causal_model`（可由 yaml 显式指定）、`model_source` 溯源字段 |
| `lawgate/channel/llm_base.py` | `HFLLM` 支持 `dtype`；ChatGLM 系走兼容装载；`describe()` 增加精度与实现路径 |
| `configs/base.yaml` | 新增 `causal_model` / `dtype: float16` 及理由注释 |
| `lawgate/api/ui.py` | 页脚写明生效模型 + 精度 + 生成长度上限 |
| `scripts/check_backend.py` | **新增**。秒级自检：打印生效模型、精度、来源、候选可用性 |
| `scripts/check_fuzi_e2e.py` | **新增**。真机自检：13 项断言（后端指纹 / 非流式 / 缓存 / 流式一致性） |

**验证（真机，可复现命令）**：

| 检查 | 命令 | 实测结果 |
|---|---|---|
| 生效模型是谁 | `E:\Anaconda\python.exe scripts\check_backend.py` | `causal_model = models/fuzi-mingcha-v1_0`，来源=显式配置，权重完整（15 分片），精度 float16 |
| 端到端（13 项断言） | `scripts\check_fuzi_e2e.py --max-tokens 48` | **13/13 通过**；加载 14.5 s；后端自述 `{'llm_backend': 'hf', 'llm_name': 'fuzi-mingcha-v1_0', 'llm_dtype': 'float16', 'llm_backend_impl': 'chatglm-compat'}` |
| 流式与整段一致 | 同上第 4 节 | `delta` 拼接 == `done.answer`；7 片增量；首字 30.0 s |
| 缓存 | 同上第 3 节 | 同问二轮 **0.0 s**，文本与首轮逐字一致 |
| 离线链路无回归 | `set DOTENV_OVERRIDE=0` + `scripts\check_stream.py` | **全部通过**（3.5 s，规则后端，不加载 13 GB 权重） |
| 官方路径交叉验证 | `scripts\_probe_cross_check.py` | 模型自带 `.chat()` 与我们的 `generate` 都可用且文本同一口径（`.chat()` 也走桥接） |

**代价（必须向使用者讲清楚）**：

1. **速度**：6.7B 模型纯 CPU 实测约 **0.8–1.35 token/s**（随机器负载波动）。换算：上限 192 token 时**单栏约 2–4 分钟**，演示界面两栏并行，一"问"约 **3–5 分钟**（旧 0.5B 模型是 60–90 s）。缓存命中的问题仍秒回。
   建议：演示用 `启动服务.ps1 -UiMaxTokens 80`（两栏一起变短，口径仍一致），或优先点预设里缓存过的问题。
2. **内存**：常驻 **12.5 GiB**（fp16）。本机总 31.6 GB，实测加载前后系统可用内存约 12.7 GB → 12.8 GB（权重走 mmap，增量体现在系统缓存与工作集上）。**同时运行其它大模型服务（如另一个 llama-server 占 4 GB+）时可能吃紧**，必要时先关掉。
3. **输出质量**：该模型偶发**重复退化**（贪心解码下长句会打转，如"你有权利,你有权利…"），这与本项目的"贪心 + 缓存键固定"约定冲突——**不改采样参数**（改了会让"流式/整段一致"和缓存可复现性失效）。复核时建议看前 1–2 句结论，长文不宜直接当结论用。
4. **兼容层是"内存补丁"**：不改模型目录文件、不降 transformers（本机 5.8.0 为其它模型共用）。若日后升级 transformers 或换更规范的司法模型（如提供 `generation_config` / 标准 `_pad` 的版本），优先走"直接支持"，本层可作为历史包袱逐步退役。

**影响**：**是**（最终回答所用模型变了：0.5B 缓存模型 → 6.7B 本地司法模型）。历史评测结果（E0–E6）均基于旧模型，**不可与新配置直接比较**；各项结论口径以 `results/` 里的 `provenance` 为准（已新增 `causal_model_dtype` / `causal_model_source` 两个字段）。

---

## D30 最终回答的大模型换成 **DeepSeek 官方 API（`deepseek-v4-flash`）**：门控草稿因此必须换源，实测发现 API 的 logprobs"能取但不可用"（**本次会话新增**）

**用户要求**：把后端大模型换成 DeepSeek API 服务的大模型 `deepseek-v4-flash`。

**结论（一句话）**：已接入并真机验证通过——最终回答由 **DeepSeek 官方 API** 的 `deepseek-v4-flash` 生成（服务端回报 `model=deepseek-flash`），**关闭思考模式**，单条 192 token 约 **1–3 秒**（此前本地 6.7B 是 2–4 分钟）；门控信号 u 改用**本机 Qwen2.5-0.5B 的 logprobs**（理由见下，实测证据）；离线自检 29/29、真机自检全通过、三通道冒烟 20/20、流式冒烟两模式全通过。

### D30-1 真机探测：这个模型是"思考型"的，且默认就会把 token 预算吃光

接之前先拿真 key 探了一遍（探针脚本用完即删，结论全部可复现）：

| 现象（实测） | 说明 |
|---|---|
| `model="deepseek-v4-flash"` 被接受 | 响应体回报 `model="deepseek-flash"`——旧模型名被服务端路由到现行 Flash。`GET /v1/models` 只列 `deepseek-flash` / `deepseek-v4-pro`，即**列表里没有不代表不能用** |
| 默认返回 `reasoning_content` + `content` | 该模型默认**开思考**：先写一大段推理，再写正式答案 |
| `max_tokens` 小时 `content` 为**空字符串** | 实测 `max_tokens=32` → `reasoning_tokens=32`、content 空；`max_tokens=600` 的三次尝试里**全部 600 token 都被 reasoning 吃掉**，content 仍是空。若不处理，演示会表现为"答非所问地返回空" |
| 关思考：`{"thinking": {"type": "disabled"}}` | 关掉后 1.1 s 出正文（140 token）；`{"reasoning_effort": "none"}` 也可；而 `{"thinking": "off"}`（字符串）被 **HTTP 400** 拒绝 |
| **logprobs 的真实性取决于思考模式** | 思考**关闭**时：`choices[0].logprobs.content` 里被选中 token 的 logprob **逐位恒为 0.0**、候选**恒为 -9999.0**（占位符，算出来的 margin 会是 9999）；思考**开启**时：`choices[0].logprobs.reasoning_content` 是**真实 top-k 分布**（实测 `[(-0.0018,'我们需要'), (-6.58,'We'), (-9.07,'需要')]`） |

**处置**：`DeepSeekLLM` 默认 `thinking=False`（答案要快、要非空），但 `draft_logprobs()` **强制走思考模式 + logprobs**（不这样就取不到真实分布），并带**退化分布检测**：一旦发现"选中 token 全 0.0"或"候选里超过一半是 -9999"，抛 `DegenerateLogprobs`，由来源链换源——**绝不用假分布算出一个看起来很正常的 u**。

### D30-2 探测发现的第二个坑（更隐蔽）：API 的 logprobs **格式合法但信号塌缩**

上表那条"思考开启时是真实分布"只保证了**格式**。真拿它当门控信号算 u 之后，实测（`scripts/check_deepseek.py --live --with-local-draft`，原始输出 `docs/check_deepseek_gate.txt`）：

| 查询 | u（API 草稿） | u（本机 0.5B 草稿） |
|---|---|---|
| 什么是离婚冷静期？ | 0.0000 | 0.0161 |
| 《合同法》第52条规定哪些情形合同无效？ | 0.0001 | 0.0263 |
| （2022）沪01民终12345号 这个案子是劳动争议，对吗？ | 0.0012 | **0.3632** |
| 担保法现在还有用吗？ | 0.0000 | 0.0790 |

原因：思考模式下能拿到的 logprob 是**推理前缀**的分布，而那段前缀几乎逐字固定（"我们需要回答用户…"），模型在上面极度自信——平均裕度 top1−top2 ≈ **9–11 nats**，而 u = exp(−裕度) 只有 **1e-4** 量级。于是 u 在所有问题上都贴着 0，**门控信号没有区分度**。这不是"取不到信号"，而是"取到了但塌缩了"，比报错更隐蔽，因此**必须写清楚**而不是照用。

**处置（默认顺序 local → api）**：`DraftSourceChain` 默认先用本机小模型（Qwen2.5-0.5B，HF 缓存里已有，实测 u 有量级差异、案号题最高），本机模型不可用时才退到 API 草稿，再退到确定性伪分布。每条 `trace` 都记 `draft_source`（`local`/`api`/`rule`/`channel_b`）与 `draft_attempts`（哪条失败、失败原因、耗时）。想改成 API 优先：`LAWGATE_DRAFT_SOURCE=api`。

### D30-3 附带修好的接线问题（不修就会静默错）

| 序号 | 问题 | 处置 |
|---|---|---|
| D30-3a | `.env` **没有任何一行代码读它**（历史遗留），密钥只能靠启动脚本手工设环境变量 → 换个终端就"莫名 401" | `lawgate/env_setup.py` 新增 `load_env_file()`（不引入 python-dotenv 依赖）：`apply()` 时读仓库根 `.env`，**真实环境变量优先**，`LAWGATE_DOTENV=0` 可整段关闭 |
| D30-3b | TARG 基线直接 `self.llm.draft_logprobs()` | 改为与 LegalGate **同一条草稿来源链**（来源不同会让"单阈值 vs 桶级阈值"的对比掺进信号口径差异） |
| D30-3c | `LegalLLMPersona` 基线依赖 `llm.tok.apply_chat_template`，API 后端没有 tokenizer → **人设被静默丢弃** | `DeepSeekLLM` 提供 `generate_messages()`，基线优先走它 |
| D30-3d | 流式冒烟里"首片 ≤ 总时长 60%/40%"的断言在 API 后端下**必然误报**（生成只要 1 s，首字延迟其实是取草稿的 2 s） | 改成"≥5 片且首末片跨度 ≥50 ms"（判据：一次性倒出只需微秒级，实测 48 token 也有 ~170 ms 跨度）；本地后端仍走老规则，缓存命中则 SKIP 并说明 |
| D30-3e | 流式冒烟统计两栏 token 用 `llm.tok.encode` → API 后端拿不到，退化成字符启发式（300 字估成 200 token，**超过 192 上限而误报**） | `DeepSeekLLM` 记**逐次调用台账** `call_log`（含线程名、是否命中缓存、服务端 usage），冒烟改用**服务端自报的 `completion_tokens`**：实测两栏均为 **192/192**，口径最准 |
| D30-3f | 服务形态（本地/API）此前只能靠翻配置猜 | `/health` 新增 `llm` 块（provider/model/api_base/thinking/key_present/draft_source）；`/trace/schema` 增加 `draft_source` / `draft_attempts` / `draft_seconds` 说明；UI Trace 面板新增"草稿来源""草稿耗时"两行；页眉页脚按**实际生效**的后端动态措辞 |

**新增/改动的文件**：

| 文件 | 改动 |
|---|---|
| `lawgate/channel/deepseek_llm.py` | **新增**。DeepSeek API 后端：整段 / 流式（SSE，显式 `encoding="utf-8"`）/ 草稿 logprobs（思考模式 + 退化检测）、401/402/429/5xx 的人话报错与指数退避重试、线程局部 Session、逐次调用台账、`describe()` 溯源 |
| `lawgate/channel/draft_source.py` | **新增**。草稿来源链：`local`（本机小模型）/ `api` / `rule` 兜底，惰性加载 + 逐条尝试 + `DraftStats.source/attempts` 留痕 |
| `lawgate/env_setup.py` | 新增 `.env` 读取（`load_env_file` / `parse_dotenv`），优先级与开关写进模块文档 |
| `lawgate/config.py` | 新增 `llm_provider` / `deepseek_*`（模型、地址、密钥、思考、超时、重试）/ `draft_source` / `draft_model`；`model_label` 支持 API 与草稿模型；`provenance()` 增加 provider/模型/思考/草稿等字段 |
| `lawgate/channel/llm_base.py` | `DraftStats` 增加 `source` / `attempts`；`get_llm()` 支持 `deepseek` 与"按 `llm_backend` 决定"；显式要求 API 时**不静默换模型** |
| `lawgate/router.py` | 新增 `draft_llm` 参数（默认仍与回答同源）；trace 增加 `draft_source` / `draft_attempts` / `draft_seconds`；通道 B 的 trace 也写明"未取草稿" |
| `lawgate/eval/run_exp.py` | `build_context()` 构造草稿来源链并透传给 router / `RunContext`（含线程局部 router） |
| `lawgate/eval/baselines.py` | TARG 共用草稿链；`LegalLLMPersona` 支持 `generate_messages`；`build_methods` 透传 `draft` |
| `lawgate/api/app.py` | `/health` 增加 `llm` 块；`/trace/schema` 补充新字段说明 |
| `lawgate/api/ui.py` | 页眉/页脚按后端动态措辞（API 模式不再写"CPU 上 60–90 s"）；Trace 面板显示草稿来源与耗时 |
| `configs/base.yaml` | `llm_backend: deepseek`、`deepseek_model: deepseek-v4-flash`、`draft_source: auto` 及理由注释；`causal_model` 降级为"本地兜底" |
| `.env` | 填入 `DEEPSEEK_API_KEY`（**不提交**）、`DEEPSEEK_MODEL=deepseek-v4-flash`，新增思考/超时/重试/草稿来源开关，并订正"API 不返回 logprobs"的旧注释 |
| `scripts/serve_one.py` | 启动横幅按后端分行打印（API 模式写明地址、思考模式、密钥状态、门控来源）；入口处显式读 `.env` |
| `scripts/check_deepseek.py` | **新增**。两段自检：离线段自起**假 OpenAI 兼容服务**（验证请求体口径、真逐片、中文编码、缓存、退化换源、401/429 重试），真机段加 `--live` |
| `scripts/check_backend.py` | 按服务形态打印（API 模式打印模型/地址/密钥状态/思考模式/草稿来源） |
| `scripts/smoke_stream.py` | D30-3d / D30-3e 两处断言与统计口径修正（详见上表） |
| `启动服务.ps1` | 新增 `-LlmBackend hf` 可切回本机权重；启动前秒级解析并打印"回答模型/门控草稿/密钥状态"；提示文案按后端分叉（**仍保持 UTF-8 BOM**） |

**验证（可真机复现的命令）**：

| 检查 | 命令 | 实测结果 |
|---|---|---|
| 离线段（不花钱、不联网） | `E:\Anaconda\python.exe scripts\check_deepseek.py` | **29/29 通过**（假服务覆盖：thinking 参数口径、真逐片+中文不乱码、缓存不重复请求、退化分布换源、401 人话报错、429 重试）；产物 `docs/check_deepseek.txt` |
| 真机段 | `... check_deepseek.py --live` | 全部通过：整段 1.07 s / 57 字、流式 38 片 1.08 s 且与整段一致、真机 logprobs 非退化；产物 `docs/check_deepseek_live.txt` |
| 门控新旧对照 | `... check_deepseek.py --skip-offline --live --with-local-draft` | 见 D30-2 的 u 对照表；产物 `docs/check_deepseek_gate.txt` |
| 三通道端到端 | `... scripts\serve_one.py --service api --port 8010` 后 `... scripts\smoke.py` | **20/20 通过，耗时 40.1 s**（此前同套用例要几分钟） |
| 流式（HTTP） | `... scripts\smoke_stream.py --base-url http://127.0.0.1:8010 --unique` | 全部通过：169 片，跨度 922 ms，`delta` 拼接 == `done.answer`，charset 正确；产物 `docs/smoke_stream_http.md` |
| 流式（进程内 + UI 双栏） | `... scripts\smoke_stream.py --unique` | 全部通过：两栏上限 192、**服务端 usage 口径实测 192/192**、UI 渐进刷新 19 帧；产物 `docs/smoke_stream.md` |
| 离线链路无回归 | `... scripts\check_stream.py` | 全部通过（3.5 s，规则后端，不加载任何权重） |
| 后端指纹 | `... scripts\check_backend.py` | 服务形态 deepseek、模型 deepseek-v4-flash、Key 已读到、草稿 Qwen/Qwen2.5-0.5B-Instruct |
| **离线兜底路径复测** | `... scripts\check_fuzi_e2e.py --max-tokens 16` | **12/13**：唯一失败项是"第二轮明显更快"——首轮本身已命中内容缓存（0.0 s vs 0.0 s），属断言在缓存态下的**假失败**，已修成"首轮命中缓存即 SKIP"；其余全通过（本地 fuzi 加载 15.2 s、回答非空含中文、trace 记 hf、流式 7 片且 `delta` 拼接 == `done.answer`）。**同次实测吞吐仅约 0.11 token/s**（D29 记录 0.8–1.35），原因是当时本机可用内存只剩约 11 GB 而模型需 12.5 GB——**离线兜底能否跑起来取决于内存，不是配置问题**；`docs/fuzi_e2e.txt` 里现在写的就是这次 12/13 的记录 |

**代价与风险（必须讲清楚）**：

1. **联网依赖**：每问必发 HTTPS 请求（命中内容缓存则 0 请求）。断网/无密钥时 `DeepSeekError` 会逐层冒泡到界面与接口，报错写明"检查 `.env` 的 `DEEPSEEK_API_KEY`"。**离线演示**请用 `启动服务.ps1 -LlmBackend hf`（或 `$env:LAWGATE_LLM_PROVIDER="hf"`）切回本机 fuzi-mingcha。
2. **花钱**：按 token 计费。单条约 60–350 prompt token + 最多 192 completion token；`DeepSeekLLM.describe()` / `call_log` 给出逐次用量（冒烟实测一次 64 prompt + 38 completion）。缓存是内容寻址的（`data/kb/gen_cache.db`），**重复提问不重复计费**。
3. **首字延迟的构成变了**：API 生成只要 ~1 s，但门控草稿（本机 0.5B）要 ~1.5–3 s，因此"首字"≈ 取草稿耗时（实测 3.3 s 里 2.4 s 是草稿）。想更快：`k_draft: 8`（信号聚合只用前 8 位，**u 不变**）可把草稿耗时砍半。
4. **阈值 τ_b 没有重新校准（重要）**：`configs/thresholds.json` 的 τ（b1=0.29 / b2=1.0 / b3=1.0 / b4=0.49）是在**更早的草稿模型**上校准的，而草稿模型这次换了（fuzi/1.5B → 0.5B）。目前对 LegalGate 的**路由结果没有影响**：默认 hybrid 模式下 b1/b3/b4 用的是确定性复杂度评分（与草稿无关），而 b2 的 τ=1.0 本来就不可能被超过。但两件事必须注意：**①** TARG 基线用单阈值 τ=0.10 直接吃神经信号，换草稿源会改变它的检索率，**重跑 E1/E2 前应先跑一遍 `scripts/check_deepseek.py --live --with-local-draft` 看 u 分布**（必要时按既有链路重新校准：`scripts/e0_diagnostic.py` → `scripts/calibrate.py`）；**②** 历史 E0–E6 结果是在旧回答模型 + 旧草稿上产生的，**不可与新配置直接比较**。
5. **密钥纪律**：Key 只存在于 `.env`（已 `gitignore`）与环境变量里，代码与配置**从不写明文**；`/health`、`check_backend.py` 只报"有没有"。若 Key 曾出现在聊天记录/截图里，应去控制台作废换新。

**影响**：**是**（最终回答模型由本地 fuzi-mingcha-v1_0 换成 DeepSeek API 的 `deepseek-v4-flash`；门控信号来源由"回答模型自身"改成"本机 0.5B 草稿链"，并新增 `trace.draft_source` 留痕；`.env` 从此真正被代码读取）。

---

## D31 代码瘦身：**案号知识**收敛为单一出处 + DeepSeek 传输层换成官方 `openai` SDK（**本次会话新增**）

**用户要求**："这个项目的代码可以瘦身吗，比如多调用成熟的库。"——在不动"不可再约的领域逻辑"（门控数学 / 中文法条解析 / 评测）的前提下，把两处"在重写一个成熟库"的重复与手写实现收敛掉。

**结论（一句话）**：做成了**两件**：① 全仓库散布在 5 个文件里的"案号四要素正则 + 法院/案件类型映射 + 异常代字判定"**统一收敛到 `knowledge/judgment_parser.py` 这一个出处**（原先 `gate/intent`、`knowledge/judgment_parser`、`scripts/import_judgments`、`eval/metrics`、`knowledge/rerank` 各存一份，改一处漏四处）；② `channel/deepseek_llm.py` 的 HTTP 传输层由**手写 `requests` + 手撕 SSE + 手动重试退避 + 手动修中文编码**换成**官方 `openai` SDK**（本机已装 `openai==2.44.0`，零新增依赖）。两者都不改变任何**对外行为**——离线自检（`intent_accept` / `quickcheck` / `check_stream` / `e6_case_verify` / `check_deepseek`）改前改后**全绿逐位一致**。

### D31-1 案号知识：从"五份拷贝"到"一个出处"

**改前的问题**：同一条案号正则（`（年份）法院代字 类型代字（+审级）序号 号`，代字限定 民/刑/行/知/执）在 5 个文件里**各写了一份**，且彼此**口径并不完全一致**：

| 文件 | 改前 | 改后 |
|---|---|---|
| `knowledge/judgment_parser.py` | 自己定义 `RE_CASE_NO`/`CODE_REGION`/`CASE_TYPE_NAME`（**主参考**，建库/导入用它） | **唯一出处**（正则 + 两张映射 + 异常代字 + 解析函数都在这里维护） |
| `gate/intent.py` | 自带一份 `RE_CASE_NO` + `COURT_CODE_MAP` + `CASE_TYPE_MAP` + `INVALID_CASE_CHARS` + `RE_CASE_NO_LIKE` | 全部 `import` 自 `judgment_parser`；`extract_case_no` 改为**在 `judgment_parser.parse_case_no`（库口径）之上做一层"槽位口径"字段映射**（`court_region`/`case_type_name`/`abnormal_marker`），下游字段名**保持不变** |
| `scripts/import_judgments.py` | 自带一份 `extract_case_no`/`parse_case_no`（字段名与库口径**不一致**，最危险） | 直接 `import` 库口径的两个函数，删掉本地副本 |
| `eval/metrics.py` | `RE_CITE_CASENO` 是一份独立的案号正则 | 改为 `RE_CITE_CASENO = RE_CASE_NO`（历史别名，导入路径不变） |
| `knowledge/rerank.py` | 一份**无捕获组**的案号正则（与判断库口径分叉） | 改用 `judgment_parser.RE_CASE_NO` + `finditer` 重组规范化串 |

**为什么不直接删 `judgment_parser` 里的字段名**：库口径（`region`/`case_type`/`case_type_short`）与门控槽位口径（`court_region`/`case_type_name`/`abnormal_marker`）**服务两个不同的下游**（建库入库 vs. `b_case_verify.verify()` / `api/app.py` 的 `/verify_case`）。所以**保留两套字段名**，但让它们在"解析"这一点上走**同一个函数**——数据只有一份，字段映射只做一次。`gate/intent` 里另留 `COURT_CODE_MAP = CODE_REGION` / `CASE_TYPE_MAP = CASE_TYPE_NAME` 两个**别名**，是为了 `scripts/import_judgments` 等历史导入路径不碎。

**行为不变性**：`intent_accept` 的 `recall=0.9512 (39/41)`、`over_trigger=0.0500`、`slots=0.9344` 改前改后**逐位相同**；`quickcheck` 案号三级核验 6/6（真实/存在但案由不符/格式非法/不存在/非法代字/裸年份）改前改后**逐条相同**；`e6_case_verify` 四子类（V1 核验通过 / V2 不存在 / V3 案由不符 / V4 格式非法）**acc=1.0** 全对。

### D31-2 DeepSeek 传输层：手写 `requests` → 官方 `openai` SDK

**改前**（`channel/deepseek_llm.py`，~475 行手写）：
- `requests.Session` 手拼 `POST /chat/completions`；
- 手撕 SSE（`iter_lines` + 自写 `_iter_sse`），还要手动修"SSE 头不带 charset → `requests` 默认按 ISO-8859-1 解码 → 中文乱码"的坑（`resp.encoding="utf-8"`）；
- 手写指数退避重试（`0.8·2^n` 封顶 8 s），逐类判 401/402/400/429/5xx；
- 非标准参数（`thinking` / `reasoning_effort` / `stream_options` / DeepSeek 扩展的 `logprobs`）塞进 body 手拼。

**改后**（官方 `openai` SDK，本机已装 `openai==2.44.0`，**零新增依赖**）：
- `openai.OpenAI(base_url, api_key, timeout, max_retries=0)` + `client.chat.completions.create`；整段 `stream=False`、流式 `stream=True` 逐块迭代；
- **全部** SSE 解析、UTF-8 编码、连接池、重试退避交给 SDK（SDK 内部按 UTF-8 解码，"中文乱码"那个坑**直接消失**）；
- 非标准参数用 SDK 的 `extra_body` 透传（语义不变，假服务端照样看得到 `thinking`/`stream_options`/`logprobs`）；
- 异常归一化：SDK 的 `AuthenticationError`(401/403) / `RateLimitError`(429) / `InternalServerError`(5xx) / `APIConnectionError` / `APITimeoutError` / `APIStatusError`(402 等) → 统一归到 `DeepSeekError` 家族（**人话报错文案逐字保留**，`check_deepseek` 断言的是文案，不是异常类型）；
- **重试口径刻意与旧行为对齐**：SDK 自身 `max_retries=0`，改由本模块 `_request` 层做**可控**重试（只对 429/5xx/连接/超时，且把次数写进 `usage.retries`，供自检断言 `retries>=2`）；**参数降级**（`thinking`↔`reasoning_effort`、去 `stream_options`）是**立即重试、不占网络重试槽位**——"换个参数写法"不是"网络抖动"。

**保留不变的独门逻辑（SDK 不替我们判断，留在应用层）**：思考模式参数自动降级、退化 logprobs 检测（`degenerate_reason`：选中 token 全 0.0 / 候选 >1/2 是 -9999 → 抛 `DegenerateLogprobs` 由 `DraftSourceChain` 换源）、内容寻址缓存（`GenCache`）、逐次调用台账（`call_log`，含线程名，双栏并发额度分摊）、`describe()` 溯源。这些**一字未删**。

**行为不变性**：`check_deepseek.py` 离线段 **29/29 逐位全绿**（thinking 参数口径、真逐片 + 中文不乱码、缓存不重复请求、退化分布自动换源、401 人话报错、429 重试 2 次后成功）。`describe()` 新增一个**信息性**字段 `llm_http_client: "openai-sdk"`（仅溯源，不改任何既有字段名/值）。

**代价与风险**：
1. **新增 `openai` 运行时依赖**——但本机已装（2.44.0），未新增安装；`pyproject`/`requirements` 应补列（已列则无新增）。若未来换环境，需确保 `openai` 可 import（缺失时 `DeepSeekLLM.__init__` 给出人话报错，不静默回退）。
2. **402 判定的版本差异**：`openai` 各版本对 402 的异常类命名不一（2.44.0 没有专属 QuotaError），现统一靠 `APIStatusError.status_code==402` 判"余额不足"；若未来 openai 版本变更该映射，改 `_classify` 一处即可（单一出处，不扩散）。
3. **行为不变、但代码"看起来"不一样**：手写的 `resp.status_code`/`resp.json()` 不见了，换成 SDK 对象。读代码的人若按"requests 习惯"找，会被 `extra_body` 与 SDK 异常名绊一下——已在模块 docstring 的"D31"一节写明对照。

**新增/改动的文件**：

| 文件 | 改动 |
|---|---|
| `lawgate/knowledge/judgment_parser.py` | **唯一出处**：补 `INVALID_CASE_CHARS`/`RE_CASE_NO_LIKE`（从 intent 收敛进来）；模块 docstring 明确"全仓库案号知识只此一份"；`parse_case_no` docstring 说明"库口径 vs 槽位口径"两层 |
| `lawgate/gate/intent.py` | 删本地 `RE_CASE_NO`/`COURT_CODE_MAP`/`CASE_TYPE_MAP`/`INVALID_CASE_CHARS`/`RE_CASE_NO_LIKE` 副本；改为 `import`；`extract_case_no` 改为在 `parse_case_no` 上做字段映射（下游字段名不变）；留 `COURT_CODE_MAP`/`CASE_TYPE_MAP` 别名 |
| `scripts/import_judgments.py` | 删自带 `extract_case_no`/`parse_case_no`，直接 `import` 库口径（**消除字段名分叉**） |
| `lawgate/eval/metrics.py` | `RE_CITE_CASENO` 改为 `= RE_CASE_NO`（别名，导入路径不变） |
| `lawgate/eval/benchmark.py` | `_year_of` 改走 `judgment_parser.parse_case_no`（原自带一份**口径不同的**锚定正则） |
| `lawgate/knowledge/rerank.py` | 案号结构命中改走 `judgment_parser.RE_CASE_NO` + `finditer` 重组（原无捕获组本地正则） |
| `lawgate/channel/deepseek_llm.py` | 传输层换 `openai` SDK（手写 `requests`/SSE/重试/编码全部交给 SDK）；异常归一化、参数降级、退化检测、缓存、台账**全部保留**；`describe()` 加 `llm_http_client` |

**验证（可复现命令，改前改后逐位一致）**：

| 检查 | 命令 | 改前 = 改后 |
|---|---|---|
| 意图集 | `E:\Anaconda\python.exe scripts\intent_accept.py` | `recall=0.9512 (39/41) over_trigger=0.0500 slots=0.9344 -> PASS`（逐位相同） |
| 离线链路 | `... scripts\quickcheck.py` | 意图 6/6 + 案号核验 6/6 全 PASS（逐条相同） |
| 流式 | `... scripts\check_stream.py` | 全 PASS（离线，规则后端，不加载权重） |
| 案号四子类 | `... scripts\e6_case_verify.py` | V1/V2/V3/V4 acc=1.0，precision/recall=1.0 |
| DeepSeek 离线 29 项 | `... scripts\check_deepseek.py` | **29/29 全 PASS**（thinking 口径 / 真逐片 / 中文不乱码 / 缓存 / 退化换源 / 401 报错 / 429 重试） |

**影响**：**否**（纯瘦身，对外行为与指标**零变化**；唯一新增是 `openai` 运行时依赖（本机已装）与 `describe()` 的一个信息性字段。核心结论、门控路由、案号核验、答案文本、trace 字段**全部不变**）。

---

## D32 申报/论文口径修正：README 第一句话被 dev_calib 实测数字否证 + 意图召回引用过时值 + 新增两份评估文档（**本次会话新增，纯文档口径**）

**起因**：应"这个项目能不能参加大创拿到国家立项和发论文"的问题，对照仓库真实产物
（`results/calibrate_report.json`、`results/dev_baseline/*`、`results/e6/prf.json`、
`data/benchmark/counts.json`、`docs/intent_acceptance.md`）做了一轮口径核查，
产出两份新文档：`docs/大创与论文可行性评估.md`（判断与路线）与 `docs/大创申报书骨架.md`（填空模板）。
核查发现三条此前**未被偏差登记覆盖**的口径问题：

### D32-1 README 第 3–5 行"在**不牺牲准确率**的前提下把检索调用压下来"与实测冲突

`results/calibrate_report.json`（dev_calib 144 条、1.5B、`max_new_tokens=80`）+
`results/dev_baseline/` 并排读法：

| 方法 | acc | RR | 引用失效法率 | TVC |
|---|---|---|---|---|
| Always-RAG | 0.3889 | 1.000 | 0.2847 | 0.4583（n=24） |
| Never-RAG | 0.2917 | 0.000 | 0.4306 | 0.3750（n=24） |
| hybrid 校准 τ 工作点 | **0.3403** | **0.4931** | 未跑 | 未跑 |

- 检索降幅 50.69% ≥40% 目标 **成立**；但准确率 −4.9 个百分点，**不满足"≥ Always-RAG − 1%"**；
- b1/b4 桶 `feasible: false`——没有任何 τ 同时达标，现行 τ（b1 0.29 / b4 0.49）是
  `largest_feasible` 兜底规则硬取，不是"满足目标"的解；
- **处置**：申报材料与论文的主打口径改为"在可接受的准确率损失内降低检索开销，并以确定性
  通道解决 RAG 无法解决的法条失效与案号核验问题"（评估 §5.2 给出可直接抄的新卖点段落）；
  **"不牺牲准确率"表述禁用**；"检索降幅 ≥40% 且准确率不降"的手册核心指标在当前证据下不成立，
  待 E1 全量（test 944 条、API 后端、RC1/RC3 补数据后）重述。

### D32-2 意图检测召回引用过时值（0.9756 → 0.9512）

`docs/intent_acceptance.md` 与 `results/intent_acceptance.json`（66 条用例、5 条已知边界不计分母）
实测 **recall = 0.9512（39/41）**；但 `README.md` §0/§1 与 `docs/ACCEPTANCE.md` S3 引用的是旧值
**0.9756（40/41）**（D31 瘦身时复测过 intent_accept，改前改后逐位相同=0.9512，两处旧值未同步）。
README §1 的"意图检测 50 条测试集"同样过时（用例集已扩为 66 条）。
**处置**：`README.md` 与 `ACCEPTANCE.md` 中 0.9756 → 0.9512、"50 条" → "66 条"（本次已同步修订）。

### D32-3 dev_calib 工作点数字首次成文（此前只散落在 JSON 里）

§5.1 并排表（含 Always-RAG 引用失效法率 0.2847 / TVC 0.4583）为首次成文，
可作为论文"问题陈述"段落的数据来源；所有引用必须带**口径三件套**
（模型 1.5B、max_new_tokens=80、split=dev_calib，机械代理判分），与 D30 的 API 后端数字区分。

**影响**：**否**（纯文档口径修正 + 两份新增评估文档，不改任何代码、配置、结果文件与既有实验数字；
唯一同步改动是 README/ACCEPTANCE 两处过时引用值。若按 D32-1 改申报材料口径，属申报动作，不在本仓代码范围）。

---

## D33 实验补完：新口径下 E1–E5 首次全部跑完 + 五处脚本修复 + τ_b 重校准 + 旧口径归档（**本次会话新增**）

**起因**：2026-09-11 的流水线在 E1 neverrag 第 6/6 分片处被外部终止（exit 137），E2/E3/E5
从未完成，`results/e1/` 里只有 5 个不完整分片；且 D30 换源后 τ_b 一直没有按新草稿重新校准
（D30 的遗留警告）。本次按科研标准把实验补完，全部数字落盘、可复现、可审计。

### D33-1 口径三件套（经用户批准）

| 维度 | 取值 | 说明 |
|---|---|---|
| 回答模型 | DeepSeek API `deepseek-v4-flash`（关思考） | D30 既定；单条 1–3 s |
| 门控草稿 | 本机 `Qwen2.5-0.5B-Instruct`（HF 快照 `7ae55760…`，贪心） | D30 既定；`trace.draft_source` 全程 `local`（E1 legalgate 376 条取草稿 + 568 条通道 B 未取草稿，台账见 QA） |
| 生成长度 | `max_tokens=192` **全部实验统一** | 替代 D19 时代的 80 |
| E1 划分 | **test 全量 944 条** | D19"D1 基于 31.8% 子集"的限制就此**解除**；E2 仍用 test_e1（D24）、E3 用 test_e4（D19）、E5 用 temporal_trap 全量 120 |

### D33-2 五处脚本修复（不修则实验跑不通/跑错）

| # | 文件 | 缺陷 | 修复 |
|---|---|---|---|
| ① | `scripts/e0_diagnostic.py` | deepseek 后端下会**静默收集塌缩的 API logprobs** 当门控信号 | 草稿改走 `build_draft_source` 链；验证：dev+dev_calib 380/380 条信号与 `figures/e0/` **逐位一致**（max abs diff 0.0，同一 0.5B 快照+贪心+缓存命中），`figures/e0_draftchain/` 保留为验证证据，`figures/e0/` 仍是唯一正本 |
| ② | `scripts/e2_ablation.py`（A2） | 单全局 τ 写死 0.30（D30 换源后无任何依据） | 运行时读 `calibrate_report.single_tau.hybrid.tau`（=0.99），跑批与分析同源，note 留痕 |
| ③ | `scripts/e2_ablation.py`（run_group） | `disable_channel_b`/`single_tau`/`router_mode` 被当 `run_experiment` 形参透传 → **TypeError**，A1/A2/A3 消融臂从未跑通过 | 变体参数装配进 `RouterOptions` 显式传入；`signal`/`k_draft`/`tau_single` 照常透传保证缓存键同源 |
| ④ | `scripts/e3_multiturn.py` L88 | 多行字符串未闭合 → **SyntaxError**，E3 从未跑通过 | 字符串拼接修复 |
| ⑤ | `scripts/run_pipeline.py` | E1 无法指定 split；E2 循环缺 A4 | 新增 `--e1-split`；A4（草稿长度 k=8..64）恢复进流水线（手册 S6.6 本就要求 4 组；k≥8 时 u 不变→路由不变→答案全命中缓存，增量成本只有本机草稿） |

### D33-3 τ_b 重校准（D30 遗留警告解除）

dev 基线按新口径重跑（dev_calib 144 条）：Always-RAG acc **0.4792** / Never-RAG **0.4583**
（1.5B 时代为 0.3889/0.2917——旧口径已归档，**禁止并排混用**）。校准目标 = always−0.005 = 0.4742：

- τ_b = **{b1 0.29, b2 1.0, b3 1.0, b4 1.0}**（`configs/thresholds.json`，2026-09-12）；
  b4 由 0.49→**1.0**（新口径下首次 `feasible:true`）；**b1 仍 `feasible:false`**——没有任何 τ
  能让 b1 达标（acc_if_always_retrieve 0.25 < 目标），0.29 是 `largest_feasible` 兜底（D13 口径，继续披露）；
- dev_calib 工作点：hybrid acc 0.4722 / RR 0.2431（1.5B 时代 0.3403/0.4931）；
- single_tau：**hybrid 0.99**（E2 A2 用）、complexity 0.99、**signal 0.01**（TARG 调参臂用）。

### D33-4 TARG 双臂（D17 先例：主表用手册值，调参臂做敏感性披露）

| 臂 | τ | acc | RR | 说明 |
|---|---|---|---|---|
| 主表 `targ` | 0.10（手册） | 0.5201 | 0.3051 | 与既有文档口径一致 |
| 调参臂 `targ_taucal` | 0.01（同一 dev_calib、signal 门控校准出的 τ*） | 0.5614 | **0.9629** | 调优后的 TARG **退化为近似恒检索**（RR≈1），acc 仍低于 legalgate 5.3pp——"单全局阈值在法律域调不出既省又准的工作点"是数据驱动的结论，不是 strawman |

### D33-5 API 非逐位可复现 → 内容缓存冻结 + 花费台账

DeepSeek API 不保证逐位复现。所有生成经 `data/kb/gen_cache.db`（键=sha256(model|kind|max_tokens|prompt)）
**冻结**：同 prompt 不二次计费，重判分/重分析全程离线、确定性、逐位可复现。台账（QA 探针实测）：
**deepseek generate 缓存 3188 条 / 1,009,487 tokens**（含 dev 基线、E1 六方法、E5/E3/E2 增量与
τ 缩放臂）；本机草稿不计费。旧口径产物移入 `results/_archive/2026-09-11_qwen1.5b_tok80/`
（含 README 引用规则）；`results/e6`、`pilot`、`timing`、`_demo` 有意不归档（E6 确定性回归通过：
复跑与 2026-09-11 快照**逐位一致**）。

### D33-6 主要结果（test 944，新口径，经 D34 统一重判；全表见 README §4 / `results/e1/summary.csv`）

- **核心断言 PASS**：legalgate acc **0.6144** ≥ alwaysrag 0.5551−0.01，RR **0.09** ≤ 0.6×1.0
  （检索调用降 **91%**，远超 ≥40% 目标）；配对 Wilcoxon p=0.00205，Holm(4 比较) 阈值 0.0125 → 拒绝 H0；
- 通道分布 **B 568 / A 291 / C 85**；分桶 acc：b1 0.5838（RR 0.4913）/ b2 0.8211 / b3 0.6468 / b4 **0.3375**（最弱桶，见 D35）；
- E5：legalgate TVC **0.9333** vs alwaysrag 0.35 / neverrag 0.2667 / legal_llm 0.3417；
  引用失效法率 **0.0333** vs 0.6417；分陷阱 T1 **1.0** / T2 0.8667 / T3 0.8667 / T4 **1.0**
  （残余 8 条：4× 公司法(2018修正) 第26条**种子语料未收录**→通道 B 降级提示无时效警示（G1 缺口）；4× 通道 A 生成未警示）；
- τ 敏感性（tau_scale 臂，`figures/e1/pareto_rr_acc.png` 含四工作点）：×0.5 → acc 0.6324/RR 0.339；
  ×0.75 → 0.6335/0.2479；×1.0 → 0.6144/0.09；×1.25 → 0.6112/0.0339——acc 对 τ 极不敏感（±2pp），RR 十倍可调；
- E2（test_e1 300）：A1 关通道 B → acc **0.7433→0.3233**（−42pp，通道 B 是主要贡献源）；
  A2 单 τ(0.99) vs τ_b → 0.7467/RR 0.0467 vs 0.7433/0.0533，**无实质差异**（如实报告：当前校准下
  b2 τ=1.0 已等效"神经门短路"，单 τ 与逐桶 τ 只在 b1 的 2 条上分叉）；A3 四个神经信号变体+complexity_only
  **逐条相同**（同上原因；signal_only 臂 RR=0、acc 0.7367）；A4 k=8..64 **逐条一致**（预期验证：聚合只用前 8 位）；
- E3（test_e4 60）：**负结果**，见 D35。

**运行台账**：主流水线（e1/e5/e6/plots）约 66 min + 修复后重跑（e3/e2）约 20 min + TARG 调参臂约 4 min
（18 核 CPU、workers 4、threads 4）；两轮步骤台账都在 `docs/pipeline.log`（`docs/pipeline_steps.json` 只保留最后一轮）。

**影响**：**是**（E1–E5 首次在新口径下跑完：D19"E1 基于 31.8% 子集"的限制解除、D30 的 τ_b 警告解除、
手册核心指标"检索降幅 ≥40% 且准确率不低于 Always-RAG−1pp"从 D32-1 时代的"当前证据不成立"**逆转为成立**
——但该逆转必须与 D34 的判分口径修复一并引用，且 D32-1 时代数字为 1.5B/tok80 旧口径，不可与新数字并排）。

---

## D34 判分器三层缺陷（失效法引用判据）+ 全量统一离线重判（**本次会话新增**）

**发现路径**：E1 跑完后 QA 发现 legalgate 的 TVC（0.3333）与 Always-RAG **完全相同**——而设计预期
是时效题走通道 B 确定性路径、TVC 应接近 1.0。逐条透视（`trace` + 完整答案 + 逐句复算）定位到
`lawgate/eval/metrics.py` 的 `invalid_law_citations`/`score_item` 存在**三层测量效度缺陷**：

| # | 缺陷 | 实例（E1 实测记录） | 修复 |
|---|---|---|---|
| ① | **继承陈述句被记非法引用**：通道 B 答案的"【法律沿革】合同法 → 民法典"同句无 ABOLISH_MARKERS → 整条 TVC=0 | `tt_t1_00002`：答案含"⚠️《合同法》第52条已随该法已废止…现行规定见《民法典》…【法律沿革】合同法 → 民法典"——教科书式正确，却被判 0。E1 test 上通道 B **61 条误杀** | `RE_SUCCESSION = 【法律沿革】|→|->` 同句豁免 |
| ② | **法名子串误命中**：现行有效的《劳动合同法》包含子串"合同法"→ 被记为引用已废止《合同法》——判分器自己掉进 T3"易混法名"陷阱 | `tt_t3_00003`：答案"《劳动合同法》…【现行有效】，可以继续作为现行依据引用" → invalid_cited=['合同法'] | `_law_hit()`：《法名》完整形式，或前面不是汉字的裸法名。**已知局限（披露）**：句中裸提"依照合同法第52条"（前有汉字）会漏检——代理判据偏松，本语料失效法引用几乎都以《》或句首形式出现 |
| ③ | **否定警示句被记非法引用**："不能继续引用《担保法》作为现行有效法律依据"是**警示**，不是现行引用 | `tt_t3_00004`（通道 A 答案，语义完全正确） | `NEGATION_WARN_MARKERS`（8 词）同句豁免 + temporal 分支计入 `warned`。**不动** `risk_terms.ABOLISH_MARKERS`（生成侧护栏共用，动它会改变路由/生成行为而不只是判分） |

**修复纪律（防"移动球门"）**：① 流水线**跑完后**才改判分代码；② 对**所有方法统一**重判
（新增 `scripts/rescore_results.py`：纯离线、零 API 花费、确定性，qid→meta + ctx 按
`RunContext.score_ctx` 口径复原）；③ 原始文件先备份 `results/_prescore_backup/`（审计线索，勿删）；
④ 两轮重判报告落盘：`docs/rescore_report_round1.md`（仅①）、`docs/rescore_report.md`（①②③），
逐文件翻转数 + 新旧聚合并排；⑤ 下游产物（summary.csv / core_assertion / stats / Pareto /
E5・E2・E3 报告与图）全部在重判后重新生成；⑥ 核心断言在修复**前**（v0 判分）已是 PASS
（acc 0.553 ≥ 0.5451）——修复不是为了让断言通过，而是 TVC/invalid_cite 两个指标本身测错了对象。

**影响面（诚实披露：修复方向性利好本项目）**：只有通道 B 会产出"【法律沿革】X→Y"式文本，
因此 legalgate 受益最大；四个基线的 correct/tvc 翻转各 **0–2 条**（944 条中），acc 变化 ≤±0.0011，
但 invalid_cite 率全线下降（子串误命中对基线答案同样存在，如 alwaysrag 0.2203→0.0985）。
**dev_baseline 的 correct/tvc 翻转为 0** → 校准目标与 τ_b **不受影响**，无需重校准、无路由重跑。
修复前后关键数字：E1 legalgate acc 0.553→**0.6144**、TVC 0.3333→**0.9375**、invalid_cite 0.2362→**0.0042**；
E5 legalgate TVC 0.3417→**0.9333**。E2/E3 记录生成于①修复之后、并经②③重判，口径统一。

**影响**：**是**（判分口径修复改变 E1/E5 的指标数字；对所有方法对称应用、基线几乎不动；
"通道 B 解决时效问题"的主张从**被判分器埋没**恢复为可测量——E5 的 T1/T4 = 1.0 vs 基线 ≤0.87/0.2）。

---

## D35 E3 多轮实验两项发现：turn2 的代理判分假象 + 槽位继承在离线协议下不可测（**本次会话新增，诚实负结果**）

### D35-1 legalgate turn2 acc=0.0 是代理判分假象（如实报告，**不修判据**）

turn2 的 20 条全部是案号核验追问（"那（2024）京99民初900X号这个案子呢？是真的吗？"）。
legalgate 正确路由通道 B（`route=B reason=case_no`，19/20）并给出确定性核验判定
（"❌ 案号…在裁判文书库中不存在，疑似编造"），与 golden 结论（"目前无法核验：该类法院代字与
当前合成案号库不匹配"）**语义一致**；但 mt 判分分支因条目带 golden_provisions 而按 provision 规则
（cited_golden + key_recall≥0.25）打分 → 简洁判定词面重合不足，20/20 全 0。
**golden 自己也不满足该判据**（16/20 cited_golden=False）；基线 turn2 的 0.40/0.45 来自泛化含糊
表述与 golden 的词面重合——代理判据奖励冗长含糊、惩罚简洁确定。
**处置**：判分器本轮已因 D34 修过两次，再恰好修在"本方法得 0 分"的位置就是事后移动球门——
**不改判据**，数字如实报告 + 机制解释 + 样例留档（`results/e3/` 逐条 trace 可复核）。

### D35-2 槽位继承错误率 1.0：机制存在但在离线评测协议下**永不触发**

`detect_intent` 的继承逻辑（`lawgate/gate/intent.py` 第 281–293 行）只继承**前一轮经通道 B
事实确认**（history 条目的 `trace.channel=="B"` + `trace.slots`）的槽位——这是 D26-④ 时代的保守设计。
而基准数据的 `history` 是纯文本（query/answer，无 trace）→ `confirmed` 恒空 → 继承恒不触发：
E3 实测 n_should_inherit=40、**0 条继承**（三个方法 error_rate 都是 1.0；基线本无此机制）。
手册 S6.7 的槽位继承指标在当前**离线评测协议下对任何方法都不可达成**；实时 UI 会话（history 带
trace）不受影响。**列为未来工作**：要么基准数据生成时带上真实 trace，要么继承逻辑增加
"文本槽位回填"降级路径。

### D35-3 一致性证据

E1 分桶 acc 的 b4（多轮追问）= **0.3375**，是四桶最弱——与 E3 相互印证：**多轮是本系统当前
最弱的一环**（τ_b4=1.0 → 恒走通道 A 直答 + 继承不触发 + turn2 判分假象三因叠加），
论文与申报材料必须如实呈现，不得只报 E1/E5 的强结果。

**影响**：**是**（E3 为负结果；S6.7 槽位继承验收项在离线协议下不达成；多轮能力列为已知局限与未来工作）。

---

## D36 收尾阶段：数字核验 → 文档同步 → 探针归档（**本次会话新增，纯文档/产物治理**）

> **说明**：本轮**不改任何代码、结果文件与阈值**，只做"实验跑完之后的收尾"。按 D32 先例
> （口径修正才登记）登记在此，是为了让"文档与产物何时被对齐到哪一版事实"有据可查。

### D36-1 权威数字核验（先于所有文档修改）

`scripts/_final_numbers.py`（后归档）在会话中被重跑一次，逐项核对：

| 核对项 | 结果 |
|---|---|
| E1 六方法 acc/RR/TVC/invalid_cite（test 944） | legalgate **0.6144 / 0.09 / 0.9375 / 0.0042**；alwaysrag 0.5551 / 1.0 / 0.3333 / 0.0985 —— 与 README §4 逐格一致 |
| 统计检验 | 配对 Wilcoxon **p=0.00205**、Holm(4) 阈值 0.0125 拒绝 H0、bootstrap 95% CI [+0.0212, +0.0985] |
| 分桶 / 通道分布 | b1 0.5838（RR 0.4913）/ b2 0.8211 / b3 0.6468 / **b4 0.3375**；通道 **B 568 / A 291 / C 85** |
| τ 敏感性 | ×0.5 0.6324/0.339、×0.75 0.6335/0.2479、×1.0 0.6144/0.09、×1.25 0.6112/0.0339 |
| TARG 双臂 / 单 τ | 0.5201/0.3051 与 0.5614/0.9629；single_tau hybrid 0.99 |
| E3 / E5 / E6 | E3 legalgate 0.4167（负结果，槽位 error_rate 1.0）；E5 TVC 总分与分陷阱一致；E6 P=R=F1=1.0 |
| 校准 | τ_b = b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0；hybrid 工作点 dev_acc 0.4722 / dev_rr 0.2431 |
| 流水线 | `docs/pipeline_steps.json` 末轮 35 步 **全部 `ok:true`**；结果文件时间戳均 ≥ 2026-09-13 01:10（重判后） |

**结论**：**"完成未完成的实验"这一诉求的答案是——实验在 2026-09-12 就已被 D33 补完**，
本轮未发现任何缺失或过时产物；剩余缺口全部是**外部资源类**（FLK 官方原文、真实文书、
人工标注 κ、软著受理）。

### D36-2 文档同步中查出并修正的三处"过时状态回归"（本轮的实际价值）

同一事实在文档里有多个副本时，D33–D35 的第一轮同步**漏改了三处**，本轮补齐：

| # | 位置 | 过时内容 | 修正 |
|---|---|---|---|
| ① | `docs/小白复现指南.md` §10.2 | 仍写"E1 主对比/E2/E3/E5 ❌ 原机器中断未跑完，这些数字不存在，不得编造" | 改为"✅ 已完成（2026-09-12，新口径）"并补上 E3 负结果行；同步修 §4 目录说明、第 9 步、Q8、参数总表（b4 0.49→1.0、`--max-tokens 80`→192） |
| ② | `docs/model_card.md` §6.4 | 标题为"待生成的实验"，五行全写"待生成"，阈值仍写"占位值 0.1（待校准）"、生成长度写"实验实际 80" | 重写为"E1–E5 主实验（全部落盘）"含口径三件套与关键数字；阈值/长度/校准网格同步；§6.5 第 2、3 条保留意见改为"已解除（D33）"，并新增第 4 条（判分器效度修复的披露） |
| ③ | `docs/risk_register.md` R8/R9/R21/R23 | R9 状态仍是"已触发，未处置（等待 dev 基线与校准脚本运行）"、残余敞口写"`calibrate_report.json` 尚未落盘（待生成）"；R21 写"`pipeline_steps.json` 当前只有一条 `subsets` 记录"；R23 残余敞口仍写"τ_b 尚未按新草稿模型重新校准" | R8/R9 改判为**已闭环**（附 b1 仍 `feasible:false` 的披露）；R21 改为"两轮 35 步全部 `ok:true`"；R23 残余敞口②改判解除并补花费台账（3188 条 / 1,009,487 tokens）；**新增 R24**（判分器测量效度 D34 + 多轮离线协议 D35），状态汇总由 23 条改为 24 条 |

另修正：`docs/答辩预问与标准答法.md` Q2 把当前 `dev_calib` 基线数字写成了
"0.2708 / 0.4167"（其中 0.4167 是 **TVC** 而非 acc），改为实测的 **acc 0.4792 / TVC 0.4167**；
`docs/大创与论文可行性评估.md` §5.1 旧口径并排表上方补一段"新口径同 split 读数"
（Always-RAG 0.4792 / Never-RAG 0.4583 / hybrid 工作点 0.4722 与 RR 0.2431），避免读者把旧表当现状。

### D36-3 产物治理：探针与一次性日志归档

- `scripts/_archive/2026-09-13_probes/`：移入 6 个一次性探针
  （`_e3_probe` / `_tvc_probe` / `_tvc_probe2` / `_stage4_check` / `_stage6_qa` / `_final_numbers`）
  与 2 份旧输出 txt；文档零引用（已 grep 确认）。
- `docs/_archive/2026-09-13_logs/`：移入 `_calib.log` / `_verify.log` / `pipeline_calib.log` /
  `pipeline.err` / `_pipeline_launch.out` / `_check_deepseek_cache.db`（后者运行时会自动重建）。
- **有意不动**：`docs/pipeline.log`、`docs/pipeline_steps.json`（活跃台账）、
  `docs/_serve_ui.log`、`docs/_serve_api.log`（`serve_one.py --log-file` 的运行期落点，
  移动会让下次启动写到一个不存在的位置）、`results/_prescore_backup/`（D34 明令保留的审计线索）。
- 更早的 `_probe_env.py` / `_timing_breakdown.py` / `_ui_cap_probe.py` 三个探针**在本轮之前就已被删除**，
  其产物 `docs/env_report.json` 仍在；README §3 的脚本清单已按实际状态改正
  （30 个可复现脚本，无 `_` 前缀探针），旧行为"29 + 25"的数字漂移一并修掉。

### D36-4 新增收尾文档

新建 **`docs/最终总结_小白版.md`**（面向零基础读者）：一句话结论、三通道比喻、
头条数字（含"每 100 条少查 91 次、多对约 6 条"的读法）、验收及格/不及格对照表、
**必须一起讲的三条坏消息**（多轮负结果 / 槽位继承离线不可测 / 判分器修复方向性利好）、
产物地图、5 分钟自查命令、术语人话词典、外部缺口清单与下一步建议。README §0 已挂链接。

**影响**：**否**（不改任何代码、配置、结果文件与既成数字；E1–E5 的数值与判定与 D33–D35
完全一致。唯一实质修正是**文档陈述与产物现状不一致**的地方——即把"实验未完成/阈值未校准"
一类过时现状句对齐到 D33 之后的事实；历史性陈述一律保留原文不改写）。

---

## D37 按用户要求删除全部 smoke 脚本与产物（**本次会话新增，纯工具/文档治理**）

**用户要求**："smoke 有什么用，工程上也用不上吧" → 确认"全删了吧"。

**删除对象**（2026-09-16）：

| 类别 | 文件 | 原用途 |
|---|---|---|
| 三通道端到端冒烟（S3） | `scripts/smoke.py` + `docs/smoke_report.md`、`docs/smoke.json`、`docs/smoke_raw.jsonl` | 20 组走真实 `POST /chat` 断言路由与 trace（D26 的载体） |
| 流式真机冒烟 | `scripts/smoke_stream.py` + `docs/smoke_stream.md`、`docs/smoke_stream.json`、`docs/smoke_stream_http.md`、`docs/smoke_stream_http.json` | 真·逐片 / 流式≡整段 / UI 渐进刷新 / SSE 帧（D28/D30 的验证载体） |
| S0.4 单模型冒烟 | `scripts/smoke_s0.py`（产物 `docs/smoke_s0.json` 此前已不在仓库） | 吞吐指纹 12.36 tok/s 与 logprobs 可用性（D9/D21 的证据链一环） |

**处置与文档同步**（本轮一并完成）：

1. 代码注释里的 smoke 引用改为指向替代自检：`scripts/check_stream.py`（离线流式）、
   `scripts/check_deepseek.py --live`（真机流式/整段）、`scripts/check_fuzi_e2e.py`（离线兜底后端）。
2. `docs/ACCEPTANCE.md`：S0.4 行改判 ⚰️（证据文件已删，历史值保留）；S3 行改 ⚰️
   （20/20 为历史结论，现无可复跑脚本，回归改走 quickcheck + check_stream + check_deepseek）；
   S7 流式行移除 smoke_stream 引用、保留 check_stream 与 check_fuzi_e2e。
3. `README.md`：§0 状态表两行（可运行系统 / S3 冒烟）改 ⚰️ 历史口径；§1 快速开始第 9/11 步
   改为替代命令；§3 脚本清单与仓库结构行标注已删（30 → 27 个可复现脚本）；§4 吞吐指纹段标注
   源文件已删；§5 偏差摘要表追加 D37。
4. 各偏差历史条目（D26/D28/D30/D9/D33 等）**原文保留不改写**——它们记录的是当时的缺陷发现与
   验证过程，删脚本不改变那些既成事实。
5. 其余文档（system_manual / 数据汇总 / 大创评估 / 答辩预问 等）的 smoke 引用改为历史口径或
   指向替代自检。

**影响**：**否**（不改任何代码逻辑、配置、结果文件与既成数字；删除的是验收/回归工具，
S3「20/20」、流式「169 片/922 ms」、S0.4「12.36 tok/s」等结论仍由本文件与 README §4 的文字
作为历史记录留底，但**不再可一键复跑**）。

---

## D38 回答+草稿模型全换成魔搭 Qwen3-4B（含 Qwen3 思考陷阱修复 + 门控信号塌缩发现）

**用户要求**（2026-09-13，原话"回答和draft模型全换成魔搭社区的Qwen/Qwen3-4B"）：把最终回答模型（causal）与门控草稿（draft）都从旧口径（fuzi-mingcha 6.7B 回答 / 0.5B 草稿）换成魔搭 ModelScope 的 `Qwen/Qwen3-4B`，本地权重落到 `models/Qwen3-4B`，`llm_backend: hf`（本机权重）。fuzi 权重保留在 `models/` 可改回。

**代码/配置改动**（全部落盘且编译/解析自洽）：

| 文件 | 改动 |
|---|---|
| `configs/base.yaml` | `causal_model`/`draft_model` = `models/Qwen3-4B`；`dtype: float16`；新增 `local_thinking: false`（**关键修复**） |
| `lawgate/config.py` | 新增 `local_thinking: bool = False` 字段 + `LAWGATE_LOCAL_THINKING` 解析；`CAUSAL_MODEL_CANDIDATES`/`DRAFT_MODEL_CANDIDATES` 首位改为 `models/Qwen3-4B`；`provenance()` 按生效后端报告 thinking |
| `lawgate/channel/llm_base.py` | `HFLLM` 新增 `thinking` 参数 + 模板探测（`enable_thinking in tpl` → `_tpl_kwargs`）；`build_prompt` 传 `enable_thinking=False` 并 `TypeError` 回退；`describe()` 报 `llm_thinking`/`llm_thinking_supported`；`get_llm` 的 hf 分支传 `thinking=local_thinking` |
| `lawgate/channel/draft_source.py` | `_local_factory` 的 `HFLLM` 也传 `thinking=False`（草稿只取前 k token，落在 `<think>` 前缀上信号会失真）；现行本地配置下走 `answer_model` 单源 |
| `scripts/check_qwen3_e2e.py` | **新建**：现行默认路径真机自检（17 项断言，含思考模式断言） |
| `scripts/check_draft_u.py` | **新建**：换草稿后 u 分布体检（与 D30 对照表逐字一致） |

另：README / ACCEPTANCE / model_card / system_manual / 功能流程图 / 最小可移植包 / risk_register / DATA_GAP / 小白复现指南 九处文档，把"默认 DeepSeek API / 0.5B 草稿"旧表述全部同步为现行 Qwen3-4B 口径。

**真机验证结果**（2026-09-14 00:13 跑通，权重下载完整落地：三片 safetensors 齐全，分片2 最终 3.71 GB）：

1. `scripts/check_qwen3_e2e.py` → **17/17 PASS**（`docs/check_qwen3_4b.txt`）：
   - 确实加载 Qwen3-4B（非 fuzi / 0.5B 快照）、走 HF 后端、精度 float16、**思考模式已显式关闭**（`llm_thinking=False`，模板支持=True）；
   - 加载占用 7.1 GB（< 13 GB，未落成 fp32——dtype 显式 float16 生效）；
   - 草稿源 = `answer_model`（复用回答模型，未额外加载第二份权重）；
   - 非流式回答 86 字含中文、后端记录 hf；缓存轮 0.0s vs 首轮 68.5s（缓存生效）；流式 39 片 delta 拼接 == done.answer。
   - 性能：加载 35.3s（7.1 GB）/ 首轮 68.5s（48 token）/ 流式 95.3s（首字 44.2s）。**CPU 上 192 token 单栏约数分钟**（与旧 fuzi 6.7B 同量级，关思考后正文完整）；快演示仍走 DeepSeek API（1–3 s）。

2. `scripts/check_draft_u.py` → **门控信号 u 塌缩（核心发现）**（`docs/check_draft_u.txt`）：
   - u(margin) 六题全在 **0.0000–0.0005**（极差 0.0005）；平均裕度（margin）7.5–19.0 nats。
   - 对照 D30 的 **0.5B 草稿**实测 u = 0.0161 / 0.0263 / 0.3632 / 0.0790——Qwen3-4B 的 u 比它小 **2–3 个数量级**。
   - 超过 TARG 单阈值 τ=0.10 的样本 **0/6**；现行桶级阈值 τ_b = `{b1:0.29, b2:1.0, b3:1.0, b4:1.0}` 更是全部远在 u 分布之上。

**影响（核心）：是（重大）** —— 门控信号 u 的绝对尺度由草稿模型决定（D11 / D30）。Qwen3-4B（4B）比 0.5B **自信得多**（裕度 7–19 nats vs 0.5B 的约 1–4 nats），于是 `u = exp(-裕度)` 塌缩到 ≈0。**后果：现行 τ_b 与 TARG τ=0.10 全部失效——门控对所有查询都判"足够自信、不检索"（通道 A），律核检索通道（通道 B）实际永不触发**。这正好复刻了 D30 在 DeepSeek API 思考模式下踩到的"u 塌缩"失效，只是这次发生在本机大模型上。

- **结论**：换 Qwen3-4B 后 τ_b **不能默认沿用，而是必须重校准到一个极小的尺度（≈0.0003 量级）** 门控才重新有区分度；否则整套"三通道+一道门"的核心价值（检索降 91%、双栏对照）在现行配置下形同虚设。
- **推荐决策（待用户定，未擅自改架构）**：
  - (a) 在 Qwen3-4B 上重跑 E1/E2 重新校准 τ_b 到新尺度——但 u 的有效区分带极窄（0–0.0005），校准脆弱；
  - (b) **回答模型保留 Qwen3-4B、门控草稿恢复成小模型（0.5B）** 以保留 u 的量级区分度（需改 `draft_source` 让"本地回答模型"也能挂独立小草稿，与 S3.5 "草稿=回答模型" 默认不同）。
  - **重校准/决策之前，E1/E2 的核心指标（检索降 91%）不可直接引用 Qwen3-4B 口径**，须与历史口径显式区分（同 D33 的"新旧口径禁止并排"纪律）。

**附带修复（与换模型无关，但同款陷阱，必须保留）**：Qwen3 的 chat template 写法 `{%- if enable_thinking is defined and enable_thinking is false %}<think></think>` 等价于"**不传 enable_thinking = 开思考**"，开启后先写一大段 `<think>` reasoning 把 `max_new_tokens=192` 吃光、正文为空。这与 D30 在 DeepSeek API 上踩的是同一款陷阱（那边靠 `thinking=False` 解决），故本机权重也默认 `local_thinking: false`。该修复独立于"草稿换不换"，即使将来把草稿改回 0.5B 也必须保留。

---

## D39

**标题**：Qwen3-4B 口径门控阈值重校准（route A 落地，2026-09-14）

**背景**：D38 暴露 Qwen3-4B 草稿下 u(margin) 塌缩到 0.0000–0.0005，旧 τ_b={0.29/1.0/1.0/1.0}（0.5B 尺度）全部失效。用户在 D38 后选择 **route A（保留 Qwen3-4B 回答+草稿、只重校准阈值，不恢复 0.5B 小草稿）**，并等待 e0_qwen3 全量信号（T6O4J5）产出后给结论。

**重校准结果**（基于 `figures/e0_qwen3/e0_signals_dev.jsonl` 236 条 dev + `need_retrieval` 金标，逐桶 Youden 点网格搜索 τ∈[0,0.1] 步长 0.0001，`scripts/_calibrate_qwen3_from_e0.py`，已写回 `configs/thresholds.json` 与 `results/calibrate_report_qwen3.json`）：
1. **margin 信号判别力（AUC）**：全量 **0.8964（绿灯）**；分桶 b1=0.9363 / b2=0.9183 / b4=0.8737 均强，b3 标签恒定无定义。这**推翻了 D38 基于 6 题体检外推的"门控失活"判断**——信号本身有强判别力，只是 u 绝对尺度小、旧 τ_b 定高未触发；问题在阈值尺度，不在信号。
2. **hybrid 默认路由（b2→margin，b1/b3/b4→complexity；与 `router.py` 默认 `signal_buckets=('b2',)` 一致）**：
   - τ_b = `{b1:0.0, b2:0.0002, b3:0.0, b4:0.0}`
   - b2(margin) TPR=0.816 / FPR=0.079 ✅；b1(complexity) AUC=0.108 **反判别**→τ=0 退化为恒检索；b4(complexity) AUC=0.82 但取向在 `u>τ→检索` 规则下**反转**→τ=0 退化为恒检索；b3 标签恒定→恒检索
   - **dev RR=0.8178 → 检索仅降 18.2%**（门控实际只在 b2 起作用）
3. **signal 模式（全桶 margin，E0 推荐的强判别配置，作为对照）**：
   - τ_b = `{b1:0.0001, b2:0.0002, b3:0.0, b4:0.0002}`
   - **dev RR=0.5424 → 检索降 45.8%**

**影响（核心）：是（重大）** —— 旧核心断言"检索降 91%（RR 0.09）"是 **D33 在 0.5B 草稿口径**下的数字，对当前 Qwen3-4B 默认配置**不成立**。README.md / ACCEPTANCE.md / DATA_GAP.md / model_card.md / risk_register.md / 小白复现指南.md 中凡以"91% ✅ PASS / 核心指标成立"呈现该数字的，必须显式标注为 **0.5B 历史口径**，并改为 Qwen3-4B 口径的真实值：**hybrid 18.2% / signal 45.8%**（且须注明这是"信号判别力校准"口径，见下）。未改前，这些材料对"实验结果也是真的吗"的回答是**不准确**的。

**口径红线（延续 D38，强制标注，不可省略）**：
- 本 τ_b 为**信号判别力校准**（用 `need_retrieval` 构造标签），**非**端到端 correct 校准（后者需 Qwen3-4B 的 neverrag/alwaysrag baseline，CPU 数十小时本机不可行）。
- `need_retrieval` 标签由**规则化构造**（非人工标注），且 dev 的 `case` 类为**合成文书**（见 `e0_auc.json: dataset_caveat`）；AUC 只反映"信号与构造标签的一致性"，**不等同人工标注下的真实判别力**。
- 阈值量级 ~1e-4，区分带极窄，对 margin 信号漂移敏感 → **脆弱，非稳健门控**，绝不当成"有效门控"对外报。

**推荐决策（延续 D38 待办，本次 route A 已落地）**：保留 Qwen3-4B 回答+草稿、重校准阈值（已完成）。进一步把 `router.py` 的 `signal_buckets=('b2',)` 扩到 `('b1','b2','b4')`（b3 恒检索），即可让门控在三个桶都有强判别力、检索降 ~46%（等价于 signal 模式 τ_b），属**代码改动**（超出阈值重校准范围），待用户决定。

---

## 偏差影响汇总

| 编号 | 一句话 | 是否影响核心结论 |
|---|---|---|
| D0 | 网络被劫持，法条/文书一律未从网络获取，改用人工录入种子语料 | **是**（数据权威性待补） |
| D1–D7 | 解析与存储缺陷修正（含 NFKC 破坏文本、项序错乱两处严重缺陷） | 否（修正缺陷） |
| D8 | seed 对贪心解码无意义，随机性改由统计层 bootstrap 承担 | 否（方法学澄清） |
| D9 | 模型降到 0.5B（GPU 不可用，手册风险表已覆盖） | **是**（绝对分数不可比）↳ 后续实际生效模型为 1.5B，见 D21 |
| D10 | 概念题不再误入通道 B | 否（修正缺陷） |
| D11 | 信号方向统一为不确定性 | 否（消除手册内部矛盾） |
| D12 | 补"法律名+时效问句"路径 | 否（补齐手册验收用例） |
| D13 | 校准改取最大可行 τ（否则检索率 100%，与核心指标矛盾） | **是**（否则指标不成立） |
| D14 | 时效状态机三处补强（含误判现行为已修订的严重缺陷） | 否（修正缺陷） |
| D15 | 案号核验补强；合成库的假阴性不可测 | 部分 |
| D16 | 法律专用模型基线改为"人设代理" | **是**（该基线结论受限） |
| D17 | ComplexityRouter 未调参 | 否 |
| D18 | E0 红灯 → 按桶取信号（hybrid），并披露复杂度评分的构造重叠 | **是**（路由方案） |
| D19 | 算力预算 → 分层子集 + max_tokens=80 | **是**（E1 基于 31.8% 子集） |
| D20 | 校准链路三处修复（τ 网格上限 / 报告崩溃 / 流水线路径不一致） | 否（修正缺陷，使 G8 可自动完成） |
| D21 | 实际生效的因果模型是 **Qwen2.5-1.5B-Instruct**（`models/` 目录解析优先），此前的 pilot/timing/E0 信号是 **0.5B** 产物 | **是**（E0 信号与 E1 主实验的模型口径不一致） |
| D22 | E0 复测命令修正：`--split` 只收单值（原 `dev dev_calib` 写法无效），两 split 分目录（`--tag 15b` / `--tag 15b_calib`）避免互覆 `e0_auc.json` | 否（文档修正） |
| D23 | README §4 的 E2/E3 图产物路径修正：消融图在 `figures/e3/`、多轮图在 `figures/e4/`（与脚本/`FIG_SPECS` 一致，目录与实验编号错位一格） | 否（文档修正） |
| D24 | E2 改用 `test_e1` 子集（原为 `test` 全量 944 条）；本机 14 核 @1.2GHz 且为他人共用机，实测吞吐约为 README 记录的 1/3，同配置重复测量相差 4.3×；顺带修掉 E2 未传 `--max-tokens` 导致口径与 E1 不一致的缺陷 | **是**（E2 样本缩至 31.8% 子集） |
| D25 | `plot_all.py --exp all` 会用 E3 的多轮结果覆盖 E2 的消融图（同一输出路径 + 无 schema 校验的 `*.jsonl` 兜底）。已实测确认并把流水线步骤改为"出图先于 E3/E2" | 否（修正缺陷，但未修则会静默产出错误图） |
| D26 | 补齐 `scripts/smoke.py`（三通道端到端 20 组）后跑出 **14/20**，暴露四个缺陷：**①** 条号形式不成（"婚姻法32"）时静默降级，通道 A **原文引用已废止法律且无警示**；**②** UI 预设③承诺的「存在但案由不符」实测为「核验通过」；**③** 通道 B 的 P4 主题 FTS 因 `tokenize='unicode61'` **不切中文**而恒不命中，实际只有 P1–P3 在用；**④** `app.py` 的 `Turn` 模型丢掉 `trace`，多轮槽位继承在 **HTTP `/chat`** 上永不触发（UI 直调不受影响）。**四处已全部修复，复测 20/20**；重跑校准后 τ_b 逐位不变，意图集无回归 | **是**（修复前 ①②③ 落在演示主路径、④ 落在对外 API 契约；已修） |
| D27 | `启动服务.bat` **双击必然起不来**：① LF 行尾让 cmd 从行中间继续、执行每行尾巴（`'demo'`/`'his'`/`'/d'`/`'rshell'`）；② .bat 里的 UTF-8 中文文件名在 CP936 控制台下解析成乱码 → `if exist "%~dp0启动服务.ps1"` 报 **MISSING**。**两个叠加，只修一个都不够**。已重写为**纯 ASCII + CRLF**，并改用首字符码位（`0x542F`）定位启动器；实测双击可用、参数可透传 | **是**（不修则演示入口 100% 打不开；且 `powershell -File .\启动服务.ps1` 直接调用是好的，缺陷只在双击 .bat 这条路径上） |
| D28 | 输出改为**流式**：新增 `POST /chat/stream`（SSE：stage→delta*→done[→error]）与双栏流式界面，`HFLLM` 用 `TextIteratorStreamer` 真·逐 token；**并把演示界面两栏的生成长度上限统一为 192**（原为左栏 80 / 右栏 192，来自 Settings 与 RouterOptions 两个源）；实现中撞出**通道 B 的 sqlite 连接跨线程共享**的潜伏缺陷（`/chat` 并发下随机 500，`/chat/stream` 必现），已改为按线程惰性建连接 | **是**（D28-1 不修则新接口 100% 失败、`/chat` 并发随机 500；D28-3 关系到"双栏对照"是否公平；D28-2 为新增能力） |
| D29 | 最终回答问题的大模型接入**本地 fuzi-mingcha-v1_0**（夫子·明察，6.7B / ChatGLM 底座）：新增兼容装载层 `lawgate/compat_chatglm.py` + 环境引导 `lawgate/env_setup.py`，修掉中文路径 sentencepiece 加载、旧 tokenizer `_pad` 签名、`AutoModelForCausalLM` 白名单、`GenerationMixin` 剥离、`DynamicCache` 与旧元组缓存不兼容、位置编码/掩码口径、预填-解码判据等 **14 处接口缝**；并把精度（CPU fp32→**fp16**，27 GB→12.5 GB）与模型选择（新增 `base.yaml: causal_model` 显式钉死）两处口径修正 | **是**（最终回答所用模型由 HF 缓存里的 0.5B 换成 6.7B 本地司法模型；历史 E0–E6 结果不可与新配置直接比较；真机自检 13/13 通过） |
| D30 | 最终回答问题的大模型换成 **DeepSeek 官方 API（`deepseek-v4-flash`）**：实测该模型**默认开思考**且小 `max_tokens` 会把预算吃光（content 为空），故答案默认**关思考**（单条 1–3 s）；门控草稿因此必须换源——API 的 logprobs 格式合法但**信号塌缩**（实测 u 全在 0.0000–0.0012，因为能拿到的只是套路化的推理前缀分布），故默认改用**本机 Qwen2.5-0.5B** 取 logprobs（u 有量级差异），换源过程逐条写进 `trace.draft_source` / `draft_attempts`；顺带让 `.env` 真正被代码读取、修好 TARG/人设基线与 API 后端的接线、以及流式冒烟里两处"CPU 大模型时代"的断言口径 | **是**（最终回答模型与门控信号来源同时改变；τ_b 未按新草稿重新校准——对 LegalGate 当前路由无影响但影响 TARG 基线，重跑 E1/E2 前须先看 u 分布；历史结果不可直接比较） |
| D31 | 代码瘦身：**案号知识**（正则 + 法院/类型映射 + 异常代字）从 5 个文件的 5 份拷贝**收敛为 `judgment_parser` 单一出处**（`intent`/`import_judgments`/`metrics`/`benchmark`/`rerank` 全改为 import，下游字段名逐字不变）；**DeepSeek 传输层**由手写 `requests`/手撕 SSE/手动重试换成**官方 `openai` SDK**（本机已装 2.44.0，零新增依赖；参数降级、退化检测、缓存、台账、人话报错全部保留） | 否（纯瘦身，离线自检 5 套改前改后**逐位一致**；仅新增 `openai` 运行时依赖 + `describe()` 一个信息性字段 `llm_http_client`） |
| D32 | 申报/论文口径修正：① README"不牺牲准确率"表述被 dev_calib 实测否证（hybrid 工作点 acc 0.3403 / RR 0.4931，b1/b4 桶 `feasible:false`），主打口径改为"效力盲 + 检索冗余"；② 意图召回引用过时值 0.9756 → **0.9512**（39/41，`intent_acceptance.md` 为准），"50 条" → 66 条；③ dev_calib 工作点数字（含 Always-RAG 引用失效法率 0.2847 / TVC 0.4583）首次成文为论文问题陈述来源；新增 `docs/大创与论文可行性评估.md` 与 `docs/大创申报书骨架.md` | 否（纯文档口径修正；唯一同步改动是 README/ACCEPTANCE 两处过时引用值；不改代码/配置/结果文件） |
| D33 | 实验补完：新口径三件套（deepseek-v4-flash / 本机 0.5B 草稿 / 192 tokens）下 **E1–E5 首次全部跑完**（E1 = test 全量 944，D19 子集限制解除）；修 5 处脚本缺陷（e0 草稿链、E2 单 τ 写死 0.30、E2 变体参数 TypeError、E3 SyntaxError、A4 缺席）；τ_b 按新草稿源重校准为 **0.29/1.0/1.0/1.0**（D30 警告解除，b1 仍 largest_feasible 兜底）；TARG 双臂（手册 τ=0.10 + 调参 τ*=0.01→退化为近似恒检索）；API 答案经内容缓存冻结（台账 3188 次 / 1,009,487 tokens）；旧口径产物归档 `results/_archive/2026-09-11_qwen1.5b_tok80/`。**核心断言 PASS：检索降 91%、acc 0.6144 显著优于 Always-RAG（Wilcoxon p=0.00205，Holm 拒绝）**；E5 TVC 0.9333 vs 0.35 | **是**（核心指标从 D32-1 时代"不成立"逆转为成立；须与 D34 判分修复一并引用；新旧口径数字禁止并排） |
| D34 | 判分器三层缺陷修复（失效法引用判据）：① 通道 B"【法律沿革】合同法→民法典"继承陈述被记非法引用（E1 误杀 61 条通道 B 满分警示答案）；② 《劳动合同法》子串误命中已废止《合同法》（判分器自己掉进 T3 易混法名陷阱）；③"不能继续引用《担保法》"否定警示被记非法引用。修复纪律：跑完才改、全方法统一重判（新增 `scripts/rescore_results.py`）、原件备份 `_prescore_backup/`、两轮报告落盘、下游产物全部重生成；修复前核心断言已 PASS（非结果驱动）。基线 correct/tvc 翻转仅 0–2 条、dev_baseline 零翻转（τ_b 不受影响） | **是**（E1 legalgate acc 0.553→0.6144、TVC 0.3333→0.9375；E5 TVC 0.3417→0.9333；方向性利好本项目已披露，判据修复理由独立于结果方向） |
| D35 | E3 多轮负结果两项：① turn2 acc=0.0 是**代理判分假象**（案号核验追问被按 mt/provision 词面判据打分，通道 B 简洁判定 0/20，golden 自身也不满足判据；**不再修判据**，避免移动球门）；② 槽位继承错误率 1.0——继承机制只认 history 里**经通道 B 确认的 trace 槽位**，离线基准 history 无 trace → 永不触发（对任何方法都不可达成；实时 UI 不受影响）；E1 b4 桶 0.3375 与之互证：多轮是当前最弱一环 | **是**（E3 为负结果；S6.7 槽位继承验收项离线协议下不达成；列为已知局限与未来工作） |
| D36 | **收尾阶段（纯文档/产物治理）**：① 重跑 `_final_numbers.py` 逐项核验权威数字（结论：实验已由 D33 补完，无缺失产物，剩余缺口全是外部资源类）；② 修掉三处"过时状态回归"——小白复现指南 §10.2 仍写"E1/E2/E3/E5 未跑完，数字不存在"、model_card §6.4 仍写"待生成的实验/阈值占位 0.1/实验用 80 token"、risk_register R9 仍写"未处置、calibrate_report 待生成"且 R21/R23 残留旧事实，并修答辩预问一处数字误引（0.2708→实测 acc 0.4792）；③ 探针与一次性日志归档（`scripts/_archive/2026-09-13_probes/`、`docs/_archive/2026-09-13_logs/`，活跃台账与 `_prescore_backup/` 有意保留）；④ 新增 `docs/最终总结_小白版.md` 并在 README 挂链接；⑤ 新增 **R24**（判分器测量效度 + 多轮离线协议） | **否**（不改代码/配置/结果文件与既成数字；只把"当前状态"类陈述对齐到 D33 之后的事实，历史性陈述原文保留） |
| D37 | **按用户要求删除全部 smoke 脚本与产物**（`scripts/smoke.py`、`scripts/smoke_stream.py`、`scripts/smoke_s0.py` 与 docs 下 7 个 smoke 产物，2026-09-16）：S3 三通道冒烟 20/20（D26）、流式真机冒烟（D28/D30）、S0.4 吞吐指纹（12.36 tok/s）均为**历史证据**，结论与缺陷记录原文保留在 D26/D28/D30 与本报告；现行可复跑回归改走 `scripts/quickcheck.py` + `scripts/check_stream.py` + `scripts/check_deepseek.py`（`--live`）+ `scripts/check_fuzi_e2e.py` | **否**（纯工具/产物治理：不改任何代码逻辑、配置、结果文件与既成数字；删除的是验收/回归工具，历史结论仍可追溯） |
| D38 | **回答+草稿模型全换成魔搭 Qwen3-4B**（用户 2026-09-13 要求；真机验证 2026-09-14）：causal/draft 都钉 `models/Qwen3-4B`、`llm_backend: hf`、`dtype: float16`、`local_thinking: false`（修 Qwen3 "不传 enable_thinking=开思考" 陷阱）。`check_qwen3_e2e.py` **17/17 PASS**（加载 7.1 GB float16、思考关、草稿源=answer_model、缓存/流式一致）。**但 `check_draft_u.py` 暴露门控信号塌缩**：u(margin) 六题全在 0.0000–0.0005（极差 0.0005），比 D30 的 0.5B 草稿（0.0161/0.0263/0.3632/0.0790）小 2–3 个数量级；现行 τ_b={0.29/1.0/1.0/1.0} 与 TARG τ=0.10 全部失效，**门控对所有查询都判"不检索"（通道 A），律核检索通道（B）实际永不触发** | **是**（重大：门控失效——不修则核心指标"检索降 91%"在 Qwen3-4B 口径下不成立；τ_b 必须重校准到 ≈0.0003 量级，或恢复小草稿作门控源；重校准/决策前 E1/E2 不可引用 Qwen3-4B 口径） |
| D39 | **Qwen3-4B 口径门控阈值重校准落地（route A，2026-09-14）**：e0_qwen3 全量 236 条 dev + need_retrieval 金标逐桶 Youden 校准。margin AUC=0.8964（绿灯，推翻 D38"门控失活"外推）。写回 `configs/thresholds.json`：hybrid 默认 τ_b={b1:0.0,b2:0.0002,b3:0.0,b4:0.0} → dev RR=0.8178（**检索降 18.2%**，b1/b4 复杂度门控退化为恒检索）；signal 模式（全桶 margin）τ_b={b1:0.0001,b2:0.0002,b3:0.0,b4:0.0002} → dev RR=0.5424（**检索降 45.8%**）。旧"检索降 91%"为 0.5B 历史口径、对 Qwen3-4B 不成立 | **是**（重大：核心指标数字须随草稿口径改写；"91%"不得再作为 Qwen3-4B 口径结论，须标 0.5B 历史口径并改 18.2%/45.8%；且本校准为信号判别力口径、非端到端 correct，标签构造+case 合成，阈值 ~1e-4 脆弱） |
| D40 | **门控默认模式简化：hybrid → complexity + 惰性草稿**（2026-09-16，用户要求简化设计）：依据 E2-A3 实测（全桶复杂度门控 `complexity_only` 与混合门控在 test_e1 300 条上 acc/RR/tvc **逐位一致**，0.7433/0.0533/0.9375，`results/e2/ablation_rows.json`），`RouterOptions.router_mode` 默认值改为 `complexity`、`signal_buckets` 默认改空；`router.py` 重排决策顺序为"分桶 → 复杂度评分 → 仅当模式需要信号才取草稿"（惰性草稿）——complexity 模式**每条省去草稿开销**（本机 Qwen3-4B 草稿 18-35s/条，check_draft_u_qwen3.json 实测），hybrid 模式也只对 signal_buckets 桶取草稿。trace 中 `u_signal/draft_*` 字段在未取草稿时写 `null/"skipped（complexity 门控无需草稿）"`，不静默缺列。**权衡如实披露**：D39 的 Qwen3 口径校准显示 signal 模式 dev RR=0.5424 低于 complexity 退化口径 0.8178（检索率更低），但该比较未计草稿成本（每条 18-35s ≫ 单次检索数秒），端到端延迟上 complexity 占优；E2-A3 端到端 acc 亦逐位一致。**验证**：① 300 条 test_e1 决策回放（complexity_score 复算 vs `results/e2_a3/legalgate_sig_cplx_seed0_test_e1.jsonl` 落盘 trace）61 条门控行 0 mismatch、239 条通道 B 行不受影响；② `quickcheck.py` 全过（意图 6/6、案号 6/6、通道 B 端到端正常）；③ 真机单条验证（"什么是离婚冷静期"）：gate_source=complexity、draft_source=skipped、答案正常生成 | **是**（默认路由行为改变：hybrid→complexity；既有 E1/E2 结果由显式传参产生、不受影响；需要信号门控时显式设 `router_mode="hybrid"/"signal"`） |
