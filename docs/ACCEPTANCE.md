# CA-LegalGate 验收判定表

> **本文件回答**：在当前仓库状态下，手册第 9 章列出的全部验收指标中，哪些已经满足、哪些有条件满足、哪些尚未满足，以及未满足项的阻塞原因和补做命令。
>
> 配套文档：
> - `docs/deviations.md`：已经发生的方法学偏差（D0–D35）
> - `docs/DATA_GAP.md`：数据与算力缺口及补做步骤（G1–G9）
> - `docs/risk_register.md`：22 条风险的处置台账
> - `docs/system_manual.md`：系统说明书

---

## 0. 判定原则

| 标记 | 含义 |
|---|---|
| ✅ **PASS** | 指标已满足，产物可复核 |
| ⚠️ **COND** | 指标在**当前限定条件下**满足，但存在必须披露的口径限制；结论有效性依赖于这些限定 |
| ❌ **FAIL** | 指标未满足，且在当前环境下无法立即补做 |
| ⏳ **PEND** | 指标依赖尚未运行的实验（如校准/E1），产物未落盘 |
| 🚫 **N/A** | 在当前降级环境下不适用或不可评价 |

**口径基准**：
- 硬件：CPU-only / 18 逻辑核 / 无 CUDA / torch 2.11.0+cpu
- 回答模型：**DeepSeek 官方 API 的 `deepseek-v4-flash`**（服务端回报 `model=deepseek-flash`，地址
  `https://api.deepseek.com`，密钥在仓库根 `.env` 的 `DEEPSEEK_API_KEY`，**不提交/不外传**），
  由 `configs/base.yaml: llm_backend: deepseek` + `deepseek_model` 指定（见 **D30**）；
  **关思考模式**（`LAWGATE_LLM_THINKING` 默认空=关），单条 192 token 实测约 **1–3 s**，
  命中内容缓存 ≈0.05 s；真机自检 `scripts/check_deepseek.py --live` 全通过。
- **门控草稿来源**：默认**本机 Qwen2.5-0.5B**（本机 HF 缓存快照，实测约 1.5–3 s），
  链为 local → api → 确定性伪分布，来源写进 `trace.draft_source` / `draft_attempts`（D30-2）。
- 离线兜底模型：**`models/fuzi-mingcha-v1_0`（夫子·明察，ChatGLM-6B 底座，6.7B，fp16/CPU）**，
  由 `configs/base.yaml: causal_model` 显式指定（见 **D29**）；用 `启动服务.ps1 -LlmBackend hf`
  或 `$env:LAWGATE_LLM_PROVIDER="hf"` 才生效，实测约 1 token/s、常驻约 12.5 GB，
  `scripts/check_fuzi_e2e.py` 13/13 通过（**验的是这条兜底路径，不是默认路径**）。
  **注意**：早期的 pilot/timing 与 E0 门控信号采集是在 **0.5B**、E1 主实验是在 **1.5B**
  上完成的，与本配置的模型口径不一致（**D21**、**D29**），这几组数字不可并列陈述。
- **τ_b 已按新草稿源重校准**（2026-09-12，D33-3）：`configs/thresholds.json` = b1 0.29 / b2 1.0 /
  b3 1.0 / **b4 1.0**（`dev_calib` 144 条、新口径 dev 基线重跑后校准、`largest_feasible`、`grid_max=1.0`；
  b1 仍 `feasible:false` → 0.29 为兜底值，D13 口径披露）。TARG 基线按 D33-4 报**双臂**
  （手册 τ=0.10 + 调参 τ*=0.01）。更换回答/草稿后端后须重走"看 u 分布 → 重校准"流程。
- **E1–E5 实验口径（D33）**：`max_tokens=192` 全实验统一；E1 = **test 全量 944**、E2 = test_e1 300（D24）、
  E3 = test_e4 60（D19）、E5 = temporal_trap 120。全部结果经 **D34 判分修复**后统一离线重判
  （原件备份 `results/_prescore_backup/`，翻转台账 `docs/rescore_report_round1.md` / `docs/rescore_report.md`）。
- 法条库：13 部法律、158 行、95 个去重 (law, article) 对、`MANUAL_TRANSCRIPT` + `PENDING_FLK_VERIFICATION`
- 案号库：600 条全部 `SYNTHETIC`
- 标签来源：规则化生成，非人工标注

---

## 1. 阶段验收判定

### S0 环境与仓库

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| 环境配置完成 | ✅ | `docs/env_report.json` | 模型缓存可用，环境变量设置完整 |
| S0.4 冒烟测试通过 | ⚰️ 历史 通过（0.5B 12.36 tok/s，`scores_available=true`） | 证据文件 `docs/smoke_s0.json` 与生成脚本 `scripts/smoke_s0.py` 已于 2026-09-16 删除（用户要求，D37），结论保留为历史记录；1.5B 复测值原载该文件（D21） |
| 算力证明 | ❌ | 无 `docs/gpu_proof.png` | 本机无 CUDA，需到 GPU 环境后截图（G5） |

### S1 数据获取

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| 法条原始文件 | ⚠️ | `data/raw/*.txt`、`data/raw/sources.json` | 文件存在但均为**人工录入种子语料**，未从 FLK 官方下载（R1 / D0 / G1） |
| 现成基准数据 | ✅ | `data/lawbench/`、`data/cail2018/` | 已克隆；本项目实际使用 `data/benchmark/` 替代 |
| 裁判文书 / 案号 | ⚠️ | `data/judgments/` 为空，但 `case_registry` 已建 | 600 条**合成案号** + 600 篇合成文书（R2 / G2） |

### S2 知识库构建

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| SQLite 法条库存在 | ✅ | `data/kb/legal_facts.db` | 13 部法律、158 行 |
| 民法典条号连续性 | ❌ | `data/kb/qa_report.md` §2 | 种子语料仅 62 条，缺 1198 个条号 |
| 解析质量抽验 ≥95% | ⚠️ | `data/kb/qa_report.md` | 自动往返一致率 100%，但**不是**人工核对 FLK 原文 |
| 案号真值库 | ✅ | `data/kb/legal_facts.db` `case_registry` 600 行 | 全部为合成数据 |
| 向量库 | ✅ | `data/kb/chroma/`、`data/kb/vector_build.json` | 1295 文档、512 维、chromadb |
| 3 条手工查询通过 | ✅ | `docs/quickcheck.txt` §4 | 民法典 667、合同法 52、担保法、真实/编造案号 |

### S3 核心模块

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| 意图检测独立用例集召回 ≥90% | ✅ | `docs/intent_acceptance.md`、`results/intent_acceptance.json` | 独立用例集 **66 条**（`data/benchmark/intent_cases_v1.json`，其中 5 条为已标注的已知边界，不计入召回分母）：**通道 B 路由召回 0.9512（39/41）≥ 0.90 → PASS**（D32 修正：旧值 0.9756/40/41 未同步）；误触率 0.05（1/20）、槽位子集一致率 0.9344。已知缺口：`消保法` 别名未随初始化写入库（G3-08）；提示词中裸「规定」会让「什么是格式条款？对消费者有什么影响？」误触主题路线（G5-09） |
| 时效性状态机 20 条 | ✅ | `docs/quickcheck.txt` §2 | 6 组边界用例全部正确 |
| 案号核验 40 条 | ✅ | `docs/quickcheck.txt` §3 | 6 组边界用例全部正确；E6 混淆矩阵对角 |
| 三通道冒烟 20 组 | ⚰️ 历史 **20/20**（2026-09-11）；脚本与产物 2026-09-16 删除（D37），现无可复跑脚本，回归改走 `quickcheck.py` + `check_stream.py` + `check_deepseek.py` | `scripts/smoke.py` → `docs/smoke_report.md`、`docs/smoke.json`、`docs/smoke_raw.jsonl`（均已删除，结论留档于 D26/D37）。首轮为 **14/20**，6 组失败定位到 **D26** 的四个缺陷（① 条号形式不成时静默降级、通道 A 原文引用已废止法律且无警示；② UI 预设③的「存在但案由不符」实测为「核验通过」；③ 通道 B 的 **P4 主题 FTS 恒不命中**；④ `app.py` 的 `Turn` 模型丢掉 `trace`，多轮槽位继承在 **HTTP `/chat`** 上永不触发）。**四处已全部修复后复测 20/20**；改动后重跑校准，τ_b 逐位不变，`intent_accept` 无回归。断言按设计契约写死、不随实测回填 |

### S4 评测集

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| 总量 1080 条 | ✅ | `data/benchmark/all.jsonl` | 实际 1180 条（含多轮 300 条） |
| 字段完整 / 真值可溯源 | ✅ | `data/benchmark/counts.json`、`construction_log.md` | 全部字段与 caveat 已披露 |
| Cohen's κ ≥ 0.7 | ⚠️ | `data/benchmark/kappa_report.md` | κ 来自**模拟标注员**（噪声 0.12），非真实人工；流水线可运行 |
| 时效陷阱 120 条法学生签字 | ❌ | 无 | 无人工标注员；陷阱模板由规则生成（G4） |

### S5/S6 实验（2026-09-12 D33 首次全部跑完；2026-09-13 D34 判分修复后全量统一重判）

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| E0 AUC ≥ 0.60 | ❌ | `figures/e0/e0_auc.json` | 四个神经信号汇总 AUC 均 < 0.60（0.492–0.557），红灯为**真实负结果**；已按桶切换 hybrid 路由（b2 桶 variance AUC 0.8199、确定性复杂度评分 b2 0.9578） |
| E1 检索降幅 ≥40% | ✅ | `results/e1/core_assertion.json`、`summary.csv` | **PASS（D34 重判后）**：legalgate RR **0.09** vs alwaysrag 1.0 → 检索降 **91%**（断言阈值 0.6×RR_ar=0.6，0.09 ≤ 0.6）；test 全量 944、六方法全部落盘 |
| E1 准确率 ≥ Always-RAG − 1% | ✅ | `results/e1/summary.csv`、`stats.json` | **0.6144 vs 0.5551**：不仅守住 −1% 下限，反而**显著更优**——配对 Wilcoxon p=0.00205、Holm(4) 阈值 0.0125 拒绝 H0、bootstrap 95% CI [+0.0212, +0.0985]、Cohen's d 0.1008 |
| E2 消融（A1–A4） | ✅ | `results/e2/ablation_rows.json` | test_e1 300：A1 去通道 B acc 0.7433→**0.3233**（−42pp，通道 B 为主贡献源）；A2 单全局 τ 0.7467/RR 0.0467 与逐桶 τ 相当；A3 五信号臂结果一致（signal_only 0.7367/RR 0）；A4 草稿长度 k=8..64 不敏感 |
| E3 多轮准确率 | ❌ | `results/e3/e3_report.json` | **诚实负结果（D35）**：legalgate 0.4167 < neverrag 0.5667 / alwaysrag 0.5333（test_e4 60）；turn2 acc=0.0 为代理判分假象，**决定不再修判据**（避免移动球门）；与 E1 b4 桶 0.3375 互证：多轮是当前最弱一环 |
| S6.7 槽位继承错误率 | 🚫 | `results/e3/e3_report.json`、`docs/deviations.md` D35-2 | 离线基准的 history 不带 trace → 继承机制对任何方法都**永不触发**（错误率恒 1.0），该验收项在离线协议下**不可评价**，列未来工作；实时 UI 路径（history 带 trace）不受影响 |
| E5 TVC = 1.0（T1–T3） | ⚠️ | `results/e5/{e5_report.json,tvc_by_trap.json}` | **0.9333 ≠ 1.0，未严格达标**但显著优于全部基线（alwaysrag 0.35 / neverrag 0.2667 / legal_llm 0.3417）；分陷阱 T1 **1.0** / T2 0.8667 / T3 0.8667 / T4 **1.0**（alwaysrag T1=0.0）；残余 8 条失败：4×《公司法(2018修正)》第26条**种子语料未收录**（G1）→ 通道 B 降级无时效警示、4× 通道 A 生成未警示 |
| E6 P/R ≥ 0.95 | ✅ | `results/e6/prf.json` | P = R = F1 = 1.0（tp30 / fp0 / fn0 / tn70，n=100），但**构造性满分**（合成案号库），需加口径限制（R14） |
| 统计检验 | ✅ | `results/e1/stats.json` | 全组 Wilcoxon + bootstrap + Holm 已执行：legalgate p=0.00205（拒绝）；neverrag p=0.0005、targ p=0.00724（拒绝）；complexity p=0.13057、legal_llm p=0.26127（不拒绝） |

### S7 演示系统

| 验收项 | 判定 | 依据 / 产物 | 说明 |
|---|---|---|---|
| FastAPI 服务 | ✅ | `lawgate/api/app.py` | `/chat`、`/verify_case`、`/temporal`、`/health` 等接口完整 |
| 流式输出（SSE） | ✅ | `POST /chat/stream`；`scripts/check_stream.py` → `docs/check_stream.txt`（离线可复跑）；**本地兜底后端复核**：`scripts/check_fuzi_e2e.py`（需 `$env:LAWGATE_LLM_PROVIDER="hf"`）→ `docs/fuzi_e2e.txt`；**真机流式冒烟 `scripts/smoke_stream.py` → `docs/smoke_stream.md` / `docs/smoke_stream_http.md` 已于 2026-09-16 删除（D37），其结论留档于 D28/D30** | `stage`* → `delta`* → `done`[→`error`]；离线自检（秒级）通过；期间查出并修复通道 B 的 sqlite 跨线程缺陷（D28）；**D29 接入本地 6.7B 司法模型后复测：流式 `delta` 拼接与 `done.answer` 逐字一致、同问二轮缓存 0.0 s**；**D30 换成 DeepSeek API 后复测**：流式 38 片 1.08 s 且与整段一致，HTTP 分片 169 片、跨度 922 ms（API 后端下逐片判据"≥5 片且首末片跨度 ≥50 ms"，本地后端仍走老规则；该断言原由已删的 smoke_stream.py 承担，现留档为历史记录） |
| 最终回答模型（默认可核验） | ✅ | `scripts/check_backend.py` → 按服务形态分行打印；`GET /health` 的 `llm` 块 | **默认：DeepSeek API 的 `deepseek-v4-flash`**（`llm_backend: deepseek`，关思考，单条 192 token ≈1–3 s，缓存命中 ≈0.05 s）；`/health.llm` 给出 provider/model/api_base/thinking/key_present/draft_source/draft_model；**离线兜底**：`启动服务.ps1 -LlmBackend hf` → `models/fuzi-mingcha-v1_0`（约 1 token/s，192 token 单栏 2–4 分钟，D29） |
| 门控草稿来源可解释 | ✅ | `trace.draft_source` / `trace.draft_attempts` / `trace.draft_seconds`；`GET /trace/schema` 有字段说明；UI Trace 面板新增"草稿来源""草稿耗时"两行；对照实测 `docs/check_deepseek_gate.txt` | 回答模型与门控草稿**不再必然同源**（D30）：默认链 **本机 Qwen2.5-0.5B → API logprobs → 确定性伪分布**，取值 `api` / `local` / `rule`，通道 B 时为 `channel_b（未取草稿）`；每次尝试的来源、失败原因与耗时逐条留痕。实测 API 草稿的 u 塌缩到 **0.0000–0.0012**（无区分度），本机 0.5B 草稿为 0.016 / 0.026 / **0.363** / 0.079，故默认用本机 0.5B |
| Gradio 双栏演示 | ✅ | `lawgate/api/ui.py` | 双栏 + Trace 面板 + 6 个预设按钮；两栏**同时流式**（`respond_stream`，左栏后台线程 + 队列），且**两栏生成长度上限统一为 192 token**（`-UiMaxTokens` / `LAWGATE_UI_MAXTOK`；见 D28-3，离线 §5 与真机 §F 各有一道断言） |
| 30 秒视频 | ⚠️ | `video/lawgate_demo.mp4`（**实测 58.0 s**，1280×720，30 fps，2.76 MB，ffprobe 复核） | **已有演示短片但非严格 30 s**：PIL 逐帧渲染（`scripts/make_demo_video.py`），内容全部取自仓库真实产物（README 定位/架构、2026-09-11 冒烟 20/20、`configs/base.yaml`）；2026-09-11 生成，早于 D33 新口径实验数字，片中不含实验结论。如需严格 30 s 可调分镜时长重渲染或对现有片裁剪（**不复制成"30s.mp4"以免命名与实际时长不符**） |
| 软著材料 | ❌ | 无受理通知书 | 未提交（G5） |

---

## 2. 关键指标汇总

| 指标 | 目标值 | 当前值 | 判定 |
|---|---|---|---|
| 法条解析自动一致率 | ≥95% | 100%（往返） | ✅ 满足 |
| 民法典条号连续性 | 1–1260 无缺口 | 62/1260 | ❌ 不满足 |
| 案号库规模 | ≥500（降级）/ 2000（目标） | 600（合成） | ⚠️ 数量满足但非真实 |
| 意图检测 recall | ≥90% | 0.9512（66 条独立用例、39/41，`docs/intent_acceptance.md`；D32 修正旧值 0.9756） | ✅ 满足 |
| 时效性状态机正确率 | 100%（20 条） | 100%（边界 6 条） | ✅ 满足 |
| 案号核验正确率 | 100%（40 条） | 100%（边界 6 条 + E6 100 条） | ✅ 满足 |
| 三通道冒烟 | 20/20 | **20/20**（2026-09-11 走真实 `/chat`；首轮 14/20，四处缺陷修复后复测，见 D26） | ✅ 满足 |
| 评测集 κ | ≥0.7 | 模拟 0.7263 | ⚠️ 非真实 |
| E0 最佳信号 AUC | ≥0.60 | 汇总 < 0.60，分桶 b2=0.8199 | ❌ 汇总红灯，已切换 hybrid |
| E1 检索降幅 | ≥40% | **91%**（RR 0.09 vs alwaysrag 1.0；test 全量 944，D34 重判后；`results/e1/core_assertion.json` = PASS） | ✅ 满足 |
| E1 准确率 | ≥ Always-RAG − 1% | **0.6144 vs 0.5551（+5.93pp）**，配对 Wilcoxon p=0.00205 显著更优（`results/e1/stats.json`） | ✅ 满足（超出目标） |
| E5 通道 B TVC（T1–T3） | = 1.0 | **0.9333**（T1 1.0 / T2 0.8667 / T3 0.8667 / T4 1.0；vs alwaysrag 0.35） | ⚠️ 未达 1.0，残余 8 条失败原因见 S5/S6 表 |
| E6 P / R | ≥0.95 | 1.0（合成库） | ✅ 满足（构造性） |
| 单轮延迟 P95 | 记录实测值 | 18.9 s（128 token，CPU-only，**本地权重后端**）；**当前默认后端（DeepSeek API）单条 192 token 实测 1–3 s、缓存命中 ≈0.05 s**（D30） | ✅ 已记录 |

---

## 3. 阻塞项与下一步命令

| 阻塞项 | 影响 | 补做命令 / 条件 |
|---|---|---|
| `configs/thresholds.json` 曾为占位值 | 核心指标“检索降幅”未校准 | **已解决并两次校准**：2026-09-11 首次校准（b4 0.49）→ **2026-09-12 按新草稿源重校准（D33-3）：b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0**（`results/calibrate_report.json`）；复跑命令 `python scripts/calibrate.py --split dev_calib --grid-max 1.0` |
| 缺 GPU / 7B 模型 | 绝对准确率不可比、E0 结论需复测 | 配置 CUDA 后重跑 `scripts/run_pipeline.py --max-tokens 256` |
| 缺 FLK 官方原文 | 法条权威性、民法典 1260 条 | `python scripts/import_law_text.py --all`（需干净网络） |
| 缺真实裁判文书 | E6 真实 P/R、case 类金标 | `python scripts/import_judgments.py --replace-synthetic`（需干净网络） |
| 缺人工标注 | κ、E0/E1 标签效度 | 按 `docs/annotation_guide.md` 双盲标注 |
| 缺软著/视频 | 申报附件 | 拍摄演示视频并提交软著 |

---

## 4. 当前可申报的最小结论集

在**不补做任何外部数据/算力**的前提下，当前仓库已经可以支撑以下结论，但必须在论文/申报书中附带本文件的口径限制：

1. **三通道异构架构可运行**：通道 B（结构化）+ 通道 A（直接生成）+ 通道 C（语义检索）的代码实现、路由逻辑、trace 可解释性均完整。
2. **E0 红灯 + hybrid 路由的决策链完整**：神经不确定性信号在法律域的汇总判别力不足，但分桶后 b2 有效；项目据此切换为 hybrid 并在代码中完整实现。
3. **案号核验器在合成数据上达到 100% 四级判定**：但**必须同时披露**“本库为合成数据，不代表真实裁判文书网覆盖能力”。
4. **评测集构建流水线可复现**：1180 条、分层划分、κ 流水线、校准脚本全部可用。
5. **系统在 CPU-only 条件下端到端可运行（默认回答在云端 API，本机跑门控与检索）**：延迟、缓存、
   降级链路均经过实测；最终回答默认由 **DeepSeek 官方 API 的 `deepseek-v4-flash`** 生成
   （关思考，单条 192 token 约 1–3 s，`scripts/check_deepseek.py` 离线 **29/29**、真机全通过，D30），
   门控草稿来自本机 Qwen2.5-0.5B 并逐条写进 `trace.draft_source`；
   **离线兜底**为 `models/fuzi-mingcha-v1_0`（fp16/CPU，`启动服务.ps1 -LlmBackend hf` 切回，
   `scripts/check_fuzi_e2e.py` **13/13 通过**，D29），兜底路径代价是约 1 token/s。
   ~~τ_b 未按新草稿源重新校准~~ → **已由 D33-3 重校准解除**（2026-09-12：b1 0.29 / b2–b4 1.0，
   `dev_calib` 144 条、`largest_feasible`；更换回答/草稿后端后仍须重走"看 u 分布 → 重校准"流程）。
6. **核心断言已成立（D33/D34，2026-09-12/13）**：test 全量 944 上，检索降 **91%**
   （RR 0.09 vs 1.0）且 acc **0.6144 显著优于** Always-RAG 0.5551（Wilcoxon p=0.00205、
   Holm 拒绝）；TARG 双臂（τ=0.10 / τ*=0.01）与 complexity、legal_llm 均被显著超过或无显著差异。
7. **通道 B 是主贡献源（E2-A1）**：去掉通道 B 后 acc 0.7433→0.3233（test_e1 300，**−42pp**）。
8. **时效陷阱 TVC 0.9333 vs 0.35（E5）**：确定性时效处理的卖点成立；残余 8 条失败如实披露
   （其中 4 条根源是 G1 种子语料缺《公司法(2018修正)》第 26 条）。
9. **多轮是已知弱项（E3 负结果，D35）**：legalgate 0.4167 低于两个基线；S6.7 槽位继承在
   离线协议下不可测。申报材料必须同时呈现强结果（E1/E5）与此负结果，不得只报好的一面。

---

## 5. 结论

| 维度 | 状态 |
|---|---|
| 代码/系统 | **可运行** ✅ |
| 知识库 | **方法验证可用，权威性未满足** ⚠️ |
| 评测集 | **构建完成，标注真实性未满足** ⚠️ |
| E0/E6 | **已有结果，口径已限制** ⚠️ |
| E1/E2/E3/E5 | **已完成**（2026-09-12 D33 在新口径下首次全部跑完——2026-09-11 的旧流水线曾在 E1 `neverrag` 第 6/6 片被外部信号中断，该中断已由 D33 完整重跑解决；2026-09-13 D34 判分修复后全量统一重判）：E1 核心断言 **PASS**（检索降 91%、acc 0.6144 显著更优 p=0.00205）、E2 通道 B 贡献 −42pp、E5 TVC 0.9333；**E3 为诚实负结果（D35）** ✅/⚠️ |
| 校准 | **已完成并重校准**（2026-09-12 D33-3：b1 0.29 / b2 1.0 / b3 1.0 / **b4 1.0**，`dev_calib` 144 条，`largest_feasible`，见 `results/calibrate_report.json`；2026-09-11 首校值 b4 0.49 已被取代）✅ |
| 申报材料 | **部分就绪** ⚠️：大创四件套已有（`docs/大创与论文可行性评估.md`、`docs/大创申报书骨架.md`、`docs/答辩预问与标准答法.md`、`docs/system_manual.md`）；缺严格 30 s 视频（现有 58 s 演示片 `video/lawgate_demo.mp4`）与软著受理材料 |

**建议下一步**（2026-09-13 更新：实验与文档同步均已完成，剩余全部为外部资源项）：
1. ~~运行 `scripts/calibrate.py` 校准~~ —— 已完成（2026-09-11 首校 + 2026-09-12 D33-3 按新草稿源重校）；
2. ~~决定 D26 四个缺陷修不修~~ —— 已全部修复并复测 20/20（见 S3 表），τ_b 重校后逐位不变；
3. ~~跑 E1–E5~~ —— 已完成（D33/D34，见 S5/S6 表与 §2 指标汇总）；
4. 外部缺口补做（按 DATA_GAP 优先级）：**G4 人工标注**（κ 做实）→ **G1 FLK 官方法条原文**
   （同时补《公司法》第 26 条，消除 E5 残余失败）→ **G2 真实裁判文书** → **G5 GPU 算力证明**；
5. 如申报硬性要求严格 30 秒视频：调 `scripts/make_demo_video.py` 分镜时长重渲染，
   或对现有 58 s 片裁剪；
6. 准备并提交软著材料。
