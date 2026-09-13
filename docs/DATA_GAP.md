# DATA_GAP —— 数据与算力缺口清单（下一个执行者的操作手册）

> 本文件回答一个问题：**换到"干净网络 + GPU + 有人工标注员"的环境后，要按什么顺序、
> 用什么命令、把哪些缺口补上，才能让本项目从"可运行的方法验证"升级为"可申报的实证结论"。**
>
> 与本文档配套的两份文档：
> - `docs/deviations.md`：**已经发生**的偏差（D0–D35）逐条登记；
> - `docs/ACCEPTANCE.md`：验收判定表（哪些指标已达成、哪些因缺口只能有条件达成）。
>
> 通用前置（每个缺口都用到）：
>
> ```powershell
> cd <仓库根>
> $env:KMP_DUPLICATE_LIB_OK   = "TRUE"
> $env:TOKENIZERS_PARALLELISM = "false"
> # 只有在"确认网络干净"后才取消离线模式：
> Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
> # 环境自检：历史探针 _probe_env.py 已归档，存量证据 docs/env_report.json 可直接复核；
> # 运行态自检用 python scripts/check_backend.py（按服务形态打印后端与模型）
> ```

## 0. 缺口总表（按"挡不挡结论"排序）

| # | 缺口 | 影响哪个验收指标 | 预计工作量 | 当前状态 |
|---|---|---|---|---|
| G1 | FLK 官方法律全文未导入（**民法典只有 62 条，非 1260 条**） | S2.4 条文权威性 / 抽验 ≥95% / 时效陷阱 E5 的替代条文覆盖 | 2–3 人日（含逐法核对） | ❌ 未做 |
| G2 | 真实裁判文书未导入（现为 600 条合成案号） | E6 的结论口径（合成库判别能力 → 真实文书库覆盖）/ case 类金标 | 3–5 人日（含 4 案由抽样与清洗） | ❌ 未做 |
| G3 | 无 GPU、无 7B 模型（现为 Qwen2.5-0.5B，CPU-only） | 全部准确性指标的可比性；门控信号质量需在大模型复测 | 0.5 人日（配置）+ 1–2 机日（重跑 E0/E1） | ❌ 未做 |
| G4 | 无人工标注员（标签全为规则化生成；κ 来自模拟标注员） | κ ≥ 0.7 的标注一致性验收；**E0/E1/E5 标签效度** | 5–8 人日（1180 条双标 + 仲裁） | ❌ 未做 |
| G5 | `docs/gpu_proof.png`（算力证明）与软著材料缺失 | 软著申报附件完整性 | 0.5 人日（有卡后截图即可） | ❌ 未做 |
| G6 | 法律专用模型基线缺失（现用 `LegalLLMPersona` 代理） | "通用模型 vs 法律专用模型"这一结论 | 0.5 人日（下载）+ 1 机日（重跑该臂） | ❌ 未做 |
| G7 | ~~E1 只跑了 test 的 31.8% 子集~~ **已解决（D33，2026-09-12）**：E1 主对比已跑满 **test 全量 944** | ~~E1 的 acc–RR 点估计精度~~ 已消除；剩余意义仅剩 GPU 环境复测 | 已无需补做（CPU 上已完成） | ✅ 已解决 |
| G8 | ~~桶级阈值未在 dev 上正式校准（占位 0.1）~~ **已解决（2026-09-11 首校 + D33-3 按新草稿源重校准）**：b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0 | ~~"检索调用下降 ≥40%" 的核心指标成立性~~ 指标已成立（RR 0.09，降 91%） | 已无需补做 | ✅ 已解决 |
| G9 | ~~E1/E2/E3/E5 结果尚未落盘~~ **已解决（D33/D34，2026-09-12/13）**：E1（test 944）/E2（test_e1 300）/E3（test_e4 60）/E5（120）全部落盘并经 D34 统一重判 | 论文全部主表与主图已可生成（`results/`、`figures/`） | 已无需补做 | ✅ 已解决 |

---

## G1 FLK 官方法律全文导入

### 需要的法律清单与期望条数

| 法律（`law_short`） | 版本 | 期望条数上限 | 备注 |
|---|---|---|---|
| 民法典 | 2020 | **1260** | 手册硬验收指标；当前种子语料只有 **62** 条 |
| 公司法 | 2023修订 | 按官方文本（约 266 条） | 2024-07-01 生效；当前只有 7 条 |
| 公司法(2018修正) | 2018修正 | 按官方文本（约 218 条） | 用于 T2"已修订"陷阱；当前只有 1 条（第 26 条） |
| 民事诉讼法 | 2023修正 | 按官方文本 | 当前只有 1 条（5 行） |
| 民法典时间效力规定 | 2020 | **3**（已完整） | 最高法司法解释；当前 3 条已收全 |
| 劳动合同法 | 2012修正 | 按官方文本（98 条） | 当前 7 条 |
| 已废止法：合同法 / 物权法 / 侵权责任法 / 婚姻法 / 继承法 / 收养法 / 担保法 | 各自最后版本 | 按官方文本 | 当前各 1–6 条，时效陷阱的"旧法"侧证据 |

> 条数**上限**必须逐法核对官方文本后确定；`--expect-max` 只给"民法典=1260"这一项是
> `scripts/import_law_text.py` 的既有默认行为（`--all` 时对民法典自动取 1260）。

### 具体操作步骤

```powershell
# 1) 在干净网络环境访问国家法律法规数据库（FLK）
#    官方入口（本仓库 data/raw/sources.json 中记录的就是该库；民法典详情页 URL 亦由此而来）：
#      https://flk.npc.gov.cn/
#    民法典在库内的检索页（种子语料的 source_url 即指向此处）：
#      https://flk.npc.gov.cn/detail2.html?ZmY4MDgxODE3Yjk3MDgxMDEzZGExNzVjYTlkYjQ2Mw==
#    ⚠️ 该站点是 SPA：直接 GET 其 /api/* 会返回 HTML（本机实测 552 字节 HTML、
#       POST 返回 405）。请用浏览器交互下载，或用浏览器 DevTools 抓取真实接口。
#
# 2) 下载官方文本（.docx 优先；FLK 的"下载"通常给 docx）
#    落到 data/raw/ 下，文件名必须与被替换的 law_short 完全对应：
#      data/raw/民法典.docx          （或 民法典.txt，UTF-8）
#      data/raw/公司法.docx          data/raw/公司法(2018修正).docx
#      data/raw/民事诉讼法.docx      data/raw/民法典时间效力规定.docx
#      data/raw/合同法.docx 物权法.docx 侵权责任法.docx 婚姻法.docx 继承法.docx 收养法.docx 担保法.docx
#    ⚠️ 民法典时间效力规定在库内的 source_url 指向 https://www.court.gov.cn/（最高法），
#       该文件从最高法官网获取同样有效，但需在 sources.json 中保留真实来源。

# 3) 先干跑（只报告、不写库），确认条数与缺口
python scripts/import_law_text.py --law-short 民法典 --expect-max 1260 `
    --file data/raw/民法典.docx --verified-by <核对人姓名> --dry-run

# 4) 逐法入库（写库时只替换该 law_short + version 的行，并重建其 FTS）
python scripts/import_law_text.py --law-short 民法典 --expect-max 1260 `
    --file data/raw/民法典.docx --verified-by <核对人姓名>

# 5) 其余法律（--all 会对所有 LAW_SPECS 逐法处理，仅民法典自动带 --expect-max 1260）
python scripts/import_law_text.py --all --verified-by <核对人姓名>

# 6) 重建依赖库（法条文本变了，FTS/关键词/向量库必须重建）
python -m lawgate.knowledge.build_sqlite --from-raw
python -m lawgate.knowledge.build_vector

# 7) 重跑依赖实验
python scripts/quickcheck.py
python scripts/build_benchmark.py --seed 42          # provision 类金标换代（260 条可一条一题）
python scripts/e0_diagnostic.py --split dev
python scripts/run_pipeline.py --max-tokens 80 --threads 14
```

### 关于 `scripts/import_law_text.py`：**该脚本已存在**（192 行），无需另写

已核实其实际行为（`scripts/import_law_text.py`）：

- 从 `data/raw/` 按 `.docx` → `.txt` → `.md` 顺序查找同名文件（`find_file()`），也支持 `--file` 指定路径；
- 用 `lawgate.knowledge.flk_parser.parse_law_file()` 解析（docx 走 python-docx；txt 走逐行状态机）；
- 用 `validate_continuity(provs, expected_max=...)` 做条号连续性检查，并**逐条列出**新增/删除的条号
  （`articles_added` / `articles_removed`）；
- 文本源可读时（`.txt`/.md）还会跑 `audit_law()` 得到 `roundtrip_rate`（往返一致率）；
- `--dry-run` 只报告不写库；
- 写库时**只替换该 `law_short` + `version` 的行**，同步删除并重建其 `provision_fts` 行；
- 把 `ingest_provenance` 升级为 `source_kind='FLK_OFFICIAL'`、`verification='VERIFIED'`，
  并写入 `verified_by` / `verified_date`；
- 产出 `data/kb/import_report.md`（或 dry-run 时 `import_report_dryrun.md`）。

> 已知限制（执行时注意）：
> 1. `--file` 只在单法模式（未加 `--all`）下生效，`--all` 时一律按文件名在 `data/raw/` 里找；
> 2. docx 路径不做往返一致性审计（`audit_law` 需要可读文本），因此 docx 导入后
>    请**额外**把官方文本另存为 `data/raw/<law>.txt` 再跑一次 `--dry-run` 以取得 `roundtrip_rate`；
> 3. 导入后 `data/kb/manual_audit.csv` 的清单会被 `build_sqlite` 重写（按 10% 抽样，
>    民法典应达到约 126 条），**人工抽验仍需人做**，脚本只生成清单与空白 verdict 列。

### 完成后如何验证

```powershell
python -c "import sqlite3;c=sqlite3.connect('data/kb/legal_facts.db');print(c.execute(\"select law_short,count(distinct article_no) from legal_provisions group by 1\").fetchall());print(c.execute(\"select law_short,verification,verified_by from ingest_provenance\").fetchall())"
```

验收标准（全部满足才算完成）：

- `legal_provisions` 中民法典去重条号数 = **1260**，`continuity_missing` 为空；
- 所有 13 部法律的 `ingest_provenance.verification` = `VERIFIED` 且 `verified_by` 非空；
- 新 `data/kb/qa_report.md` 的"往返一致率最低值"≥ 95%；
- `data/kb/manual_audit.csv` 由法学生逐条签核，准确率 ≥ 95%（**人工步骤，不可用程序替代**）；
- `python scripts/quickcheck.py` 仍 6/6 + 6/6；
- `data/benchmark/counts.json` 中 `provision_construction.article_reuse_factor` 显著下降到接近 1.0
  （因为现行有效条文将远超 260 条）。

**预计工作量**：下载与格式转换 0.5 人日；逐法连续性排错 0.5–1 人日；
人工抽验 126 条 1–1.5 人日（法学生）。合计 **2–3 人日**。

---

## G2 真实裁判文书导入（替换合成案号库）

**影响**：E6 的结论口径（`results/e6/prf.json` 的 `threshold_note` 明确写着
"本机案号库为合成数据，指标仅反映核验器判别能力"）；`case` 类的 `golden_source`（现全为 `null`）；
以及**最关键的、目前完全无法测量的指标**：真实场景中"库里没有但确实存在"的**假阴性**。

### 具体操作步骤

```powershell
# 1) 在干净网络环境从中国裁判文书网采集文书
#    官方入口：https://wenshu.court.gov.cn/
#    目标：按 4 类案由各 500 篇（合计 2000 篇）：
#      民间借贷纠纷 / 劳动争议 / 离婚纠纷 / 房屋租赁合同纠纷
#    ⚠️ 该站点有访问频率限制与登录要求，请遵守其使用条款与 robots 约定；
#       批量采集前确认合规性（本项目不提供爬虫，需自行编写或使用合规的公开数据集）。
#
# 2) 每篇整理为一个 JSON，字段如下（缺什么补什么）：
#      {"case_no": "（2022）沪01民终12345号",
#       "court_name": "上海市第一中级人民法院",
#       "cause_action": "民间借贷纠纷",
#       "judgment_date": "2022-06-01",
#       "full_text": "……",
#       "source_url": "https://wenshu.court.gov.cn/..."}
#    放到 data/judgments/（可含子目录，脚本会 rglob 遍历 *.json）。
#
# 3) 先干跑看解析成功率
python scripts/import_judgments.py --src data/judgments --dry-run

# 4) 正式导入并**删除全部合成行**
python scripts/import_judgments.py --src data/judgments --replace-synthetic

# 5) 重建向量库（文书分块 300/50 进入通道 C 语料）
python -m lawgate.knowledge.build_vector

# 6) 重跑依赖实验
python scripts/e6_case_verify.py
python scripts/build_benchmark.py --seed 42
python scripts/run_pipeline.py --only e5 e6
```

### 关于 `scripts/import_judgments.py`：**该脚本已存在**（175 行），无需另写

已核实其实际行为：

- `load_sources()` 用 `rglob("*.json")` 读取目录（也接受"一个 JSON 文件里是数组"的形式）；
- 案号优先取 `case_no` 字段，缺失时用 `lawgate.knowledge.judgment_parser.RE_CASE_NO` 从 `full_text` 抽取；
- `parse_case_no()` 结构化出年份 / 法院代字 / 案件类型 / 序号，并归一化为
  `（年）法院代字+类型代字+序号号`；
- 入库 `case_registry`，`data_source` 标记为 **`CJWS`**；同时把文书按 300/50 分块写入 `judgments`；
- `--replace-synthetic` 会 `DELETE FROM case_registry/judgments WHERE data_source='SYNTHETIC'`；
- `--dry-run` 只统计不写库；
- 产出 `data/kb/judgment_import_report.json`，其中 `next_steps` 列明后续重建步骤，
  `missing_causes` 直接指出 4 类案由里哪一类没采到。

> 已知限制（执行时注意）：
> 1. 脚本 docstring 里提到的 `scripts/verify_case_no.py --sample 200`（抽样人工核验）
>    **在本仓库中不存在**，需要自行编写（约 40 行：随机抽 200 条，输出人工核验表）；
> 2. 脚本**不下载**文书，只做导入；下载与合规采集必须由执行者完成；
> 3. `case_registry.doc_hash` 用 `md5(full_text)` 记录，可用于去重检查。

### 完成后如何验证

```powershell
python -c "import sqlite3;c=sqlite3.connect('data/kb/legal_facts.db');print(c.execute('select data_source,count(*) from case_registry group by 1').fetchall());print(c.execute('select count(*) from judgments').fetchone())"
```

验收标准：

- `case_registry` 与 `judgments` 中 `data_source='SYNTHETIC'` 的行数 = **0**，`CJWS` 行数 ≥ 2000；
- 4 类案由在 `judgment_import_report.json` 的 `causes` 中均有非零计数，`missing_causes` 为空；
- 重跑 E6 后，`results/e6/prf.json` 的 `threshold_note` 口径需人工改写为"真实文书库覆盖"，
  且 P/R/F1 **必然下降**——这时的数字才是可申报的真实指标；
- **新增可测指标**：从真实案号池中抽取"库外但确实存在"的案号，测**假阴性率**
  （当前合成库下该指标无法测量，见 `docs/deviations.md` D15）；
- 重跑 `build_benchmark.py` 后，`case` 类条目的 `golden_source` 应为真实 URL（不再是 `null`）。

**预计工作量**：合规采集与清洗 2–3 人日；导入与排错 0.5 人日；E6 + 评测集重跑 0.5–1 人日；
假阴性专项测试 1 人日。合计 **3–5 人日**。

---

## G3 GPU + 手册指定的大模型（7B / 1.5B）

**影响**：全部准确性指标的可比性（当前 1.5B 是相对手册 7B 的降级，`docs/deviations.md`
D9/D21）；门控信号质量（E0 红灯结论必须在更大模型上复测，且现有 E0 数字来自 0.5B）。

### 现状（实测）

- `docs/env_report.json`：`torch.cuda_available = false`、`cuda_device_count = 0`、`torch: 2.11.0+cpu`；
- `vllm` **未安装**（`vllm.available = false`，`ModuleNotFoundError`）；
- 已存在可用本地缓存：`Qwen/Qwen2.5-0.5B-Instruct`（`has_weights=true`）；
- `models/qwen2.5-1.5b-instruct/` **已下载成功**（`model.safetensors` 3087467144 字节，
  `docs/model_download_report.json` 记录 `ok: true`、耗时 1707.7 s、约 1.81 MB/s），
  且**已经在用**：`lawgate/config.py` 的候选顺序里它排第一，`_first_existing_dir()`
  命中它，因此 2026-09-11 起的校准与 E1 全部是 **1.5B** 的结果；
  **但早期的 `results/pilot`、`results/timing`、`figures/e0/e0_signals_*` 是 0.5B 产物**
  （证据见 `docs/deviations.md` D21），引用 E0 时必须注明该口径。

### 具体操作步骤

```powershell
# 1) 装 CUDA 版 torch（版本需与驱动匹配；示例）
pip uninstall -y torch
pip install torch --index-url https://download.pytorch.org/whl/cu124

# 2) 确认 CUDA 可见
python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# 3) 配置 configs/base.yaml（三处必改；文件内注释已给出对应行）
#    device: cuda:0
#    load_in_4bit: true        # bitsandbytes 0.49.2 本机已安装
#    llm_backend: vllm         # 需 pip install vllm；HFLLM 走 4bit 亦可
#    同时把 max_new_tokens 从 80 恢复到 192（甚至 ≥256，手册 S3.8 值）

# 4) 指定模型：优先用本地目录（config.py 的 CAUSAL_MODEL_CANDIDATES 顺序）
$env:LAWGATE_MODEL = "models/qwen2.5-1.5b-instruct"
# 或 7B：先下载到 models/qwen2.5-7b-instruct/，再同样指定
#   （HF_HUB_OFFLINE 取消后才可下载；官方仓库 https://huggingface.co/Qwen/Qwen2.5-7B-Instruct）

# 5) 全链路复跑
#    （原 python scripts/smoke_s0.py 记录 tok/s——该脚本 2026-09-16 已删，D37；
#     换模型后直接看 e0_diagnostic 的 provenance 与实测延迟即可）
python scripts/e0_diagnostic.py --split dev      # ★ 红灯结论必须在此复测
python scripts/make_subsets.py
python scripts/run_pipeline.py --max-tokens 256 --threads 8
```

### 完成后如何验证

- ~~`docs/smoke_s0.json` 的 `cuda` = `true`、`tokens_per_second` 显著上升（当前 12.36 tok/s）~~ → 该文件与脚本 2026-09-16 已删（D37），改看 `e0_diagnostic` 产物的 provenance（`causal_model` 字段）与实测 tok/s；
- `docs/env_report.json` 的 `vllm.available` = `true`（若安装 vllm）；
- 每次实验结果的文件名/trace 中 `llm_backend` 与模型名随之改变（`Settings.provenance()` 自动写入）；
- **关键判定**：重跑 E0 后，若四个神经信号的**汇总** AUC 仍 < 0.60，则 hybrid 路由的必要性得到跨模型验证；
  若升到 ≥ 0.75，则可以切回 `router_mode="signal"` 并重跑 E1/E2 对照。

**预计工作量**：环境配置 0.5 人日；E0 复测 0.5 小时；E1 全量（944 条 × 6 方法）在单卡上约 1–2 机日。

---

## G4 人工标注员（替换规则化标签、取得真实 Cohen's κ）

**影响**：这是本项目**效力最关键的缺口**。当前所有 `need_retrieval` / `golden_answer` /
`golden_provisions` / `gold_pass` 都是规则化生成（`data/benchmark/construction_log.md` §6），
条目的 `annotators=['A','B']` 与 `arbitrated=false` 是**占位字段**；
`data/benchmark/kappa_report.md` 的 κ（`need_retrieval` 0.7263、`golden_provisions` 0.8920）
来自**模拟**标注员（噪声率 0.12），**不得**作为一致性证据。

### 具体操作步骤

```powershell
# 1) 按 docs/annotation_guide.md 培训两名标注员（互相独立、不得交流）
#    分工：A 标全部 1180 条；B 盲标其中 ≥50%（建议全标，κ 才有统计意义）
#    产出格式（JSONL，每行一条）：
#      {"qid": "prov_00001", "need_retrieval": false,
#       "golden_provisions": [{"law_short": "公司法", "article_no": 1}],
#       "golden_answer": "……", "gold_pass": null}
#    存放：data/benchmark/votes_A.jsonl / votes_B.jsonl

# 2) 计算一致性（真实投票，绝不加 --simulate）
python scripts/kappa.py --a data/benchmark/votes_A.jsonl --b data/benchmark/votes_B.jsonl
#    退出码 0 = PASS（两项 κ 均 ≥ 0.7）；1 = FAIL → 必须回到标注规范重新对齐

# 3) κ < 0.7 时：按 docs/annotation_guide.md §7 的分歧仲裁流程复盘，
#    修订规范 → 重标分歧条目 → 重算，直到 ≥ 0.7（最多 3 轮，否则收敛性存疑需上报）

# 4) 用人工金标替换规则金标（评测集重建后 items 的 annotators 应为真人标识、
#    arbitrated=true；本步骤需按 docs/annotation_guide.md §6 的字段口径回填）

# 5) 重跑全部依赖实验
python scripts/e0_diagnostic.py --split dev
python scripts/run_pipeline.py --max-tokens 80 --threads 14
```

### 验收标准（硬性）

| 指标 | 门槛 | 现状 |
|---|---|---|
| `need_retrieval` 的 Cohen's κ | **≥ 0.7** | 0.7263（**模拟**，无效） |
| `golden_provisions` 的 Cohen's κ | **≥ 0.7** | 0.8920（**模拟**，无效） |
| 标注覆盖率（两名标注员共同 qid 数） | ≥ 1180（全量） | 300（模拟） |
| 仲裁字段 | `arbitrated=true` 且记录仲裁人与仲裁结论 | `false`（占位） |

### 完成后如何验证

- `data/benchmark/kappa_report.md` 标题自动变为 **HUMAN** 版本（脚本按输入来源生成标题），
  且正文不再出现"SIMULATED"字样；
- 用人工标签重算 E0：`figures/e0/e0_auc.json` 的 `dataset_caveat` 需同步改写为"人工标注"；
- **预期结果**：`need_retrieval` 与桶的相关性会显著减弱（规则标签的类别决定性是当前
  汇总 AUC 异常低的主因），因此 E0 与 hybrid 路由的必要性都必须在人工标签下重新论证。

**预计工作量**：规范培训 0.5 人日；1180 条双盲双标（两条线并行）每人 **3–5 人日**；
仲裁与规范迭代 1–2 人日。合计 **5–8 人日**。

---

## G5 `docs/gpu_proof.png`（算力证明）与软著材料

**影响**：软件著作权申报附件完整性（技术环境与算力支撑的证明材料）。

### 具体操作步骤

```powershell
# 在有 GPU 的机器上：
nvidia-smi                                        # 拿到驱动/型号/显存
# （历史探针 _probe_env.py 已归档；有 GPU 后可用 python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))" 复核 cuda 字段）

# 把 nvidia-smi 输出窗口截图另存为：
#   docs/gpu_proof.png
# 建议同时截入：显存容量、CUDA 版本、Driver Version，以及一张训练/推理进程占用显存的画面
#   （证明"算力真实使用过"，而不是只证明"机器有卡"）
```

### 完成后如何验证

- `docs/gpu_proof.png` 存在且肉眼可读（型号 + 显存 + 驱动版本清晰）；
- `docs/env_report.json` 的 `torch.cuda_available=true`、`cuda_device_count ≥ 1`，
  与截图型号一致；
- 软著材料清单（**建议**，需按受理机构最新要求复核）：
  1. 源程序前 30 页 + 后 30 页（连续打印）；
  2. 用户手册 / 设计说明书（本项目可直接使用 `docs/system_manual.md` 的 20–30 页版本）；
  3. 环境与算力证明（本项截图）+ 运行截图（`lawgate/api/ui.py` 的双栏界面 + Trace 面板）。

**预计工作量**：有卡后 0.5 人日。

---

## G6 真实 LawGPT_zh / ChatLaw 权重（替换 `LegalLLMPersona` 代理基线）

**影响**：`LegalLLMPersona` **不是**法律微调模型，因此当前**无法**支持
"通用模型 vs 法律专用模型"的结论（`docs/deviations.md` D16）。

### 具体操作步骤

```powershell
# 1) 在干净网络环境下载开源法律模型权重（仓库地址以模型官方发布页为准）
#    候选：LawGPT_zh（LawGPT_zh 系列）、ChatLaw（ChatLaw 系列）等
#    落到 models/ 下，例如 models/lawgpt-zh/、models/chatlaw/

# 2) 在 lawgate/eval/baselines.py 中新增一个真实法律模型方法类
#    （接口与现有 Method 一致：query(item) -> {"answer":..., "trace":{...}}）
#    并在 build_methods() 的返回字典里注册，例如：
#      "legal_llm_real": LegalLLMReal(llm, retriever, **common)

# 3) 与现有 6 个方法同条件重跑（同 test_e1 子集、同 max_new_tokens、同 top_k/rerank）
python scripts/run_exp.py --exp e1 --method legal_llm_real --seed 0 --split test_e1 `
    --max-tokens 80 --threads 14
python scripts/e1_plot.py --split test_e1
```

### 完成后如何验证

- 结果表与图中"`Legal-LLM*`"的星号**可以去掉**（星号当前表示"代理基线"）；
- 报告的结论口径由"仅换系统提示、不加检索与结构化通道的效果上界"升级为
  "真实法律专用模型 baseline 的对比"，此时才允许写"通用模型 vs 法律专用模型"的论断；
- 需归档模型许可与版本信息（写进 `docs/model_card.md` 的"数据与模型来源"表）。

**预计工作量**：下载与适配 0.5 人日（含大文件下载时间）；重跑该臂 1 机日（CPU 上更久）。

---

## G7 全量 test 集跑完 E1–E5 —— ✅ 已解决（D33，2026-09-12）

**现状**：E1 主对比已跑满 **test 全量 944**（D19 子集限制解除，`max_tokens` 统一 192），
E2/E3/E5 亦全部落盘（见 G9）；全部记录经 D34 判分修复后**统一离线重判**
（原件备份 `results/_prescore_backup/`）。下述操作步骤保留作历史参考/复跑命令。

**影响（已消除）**：E1 的 acc–RR 点估计精度不再受子集限制；
`results/e1/summary.csv` 每行 `n`=944；`figures/e1/pareto_rr_acc.png` 图注无"31.8% 子集"限定语；
`docs/ACCEPTANCE.md` 的 E1 判定已升级为无条件 ✅。

### 具体操作步骤

```powershell
# A. 有 GPU 时（推荐）：恢复全量与完整长度
#    configs/base.yaml: max_new_tokens: 256（或 ≥192）
python scripts/run_exp.py --exp e1 --method <m> --seed 0 --split test --max-tokens 256 --threads 8
#    6 个方法：neverrag alwaysrag targ complexity legal_llm legalgate
python scripts/e1_plot.py --split test
python scripts/e5_temporal.py --max-tokens 256        # temporal_trap 全量 120 条
python scripts/e3_multiturn.py                        # 多轮全量
python scripts/e2_ablation.py --group A1              # 依次 A1..A4

# B. 纯 CPU 时：用分片并行（每片一个独立进程，不要用线程并发——D19 实测为负收益）
python scripts/run_exp.py --exp e1 --method legalgate --seed 0 --split test `
    --shard 0 --nshards 6 --max-tokens 80 --threads 14
#  … 另开 5 个终端跑 --shard 1..5，然后合并：
python scripts/run_exp.py --exp e1 --method legalgate --seed 0 --split test --merge
#    全量 944 条 × 6 方法在 CPU 上需 12–13 机时（subsets_manifest.json 的 why 字段）
```

### 完成后如何验证

- `results/e1/summary.csv` 中每行 `n` = 944（test 全量），而不是 300；
- `figures/e1/pareto_rr_acc.png` 的图注不再需要"31.8% 子集"的限定语；
- `docs/ACCEPTANCE.md` 中"E1 基于子集"的条件性判定条目可以升级为无条件判定。

**预计工作量**：GPU 上 1–2 机日；纯 CPU 上 12–13 机时（可夜间跑）。

---

## G8 桶级阈值正式校准 —— ✅ 已解决（2026-09-11 首校 + 2026-09-12 D33-3 按新草稿源重校准）

**现状**：`configs/thresholds.json` = **b1 0.29 / b2 1.0 / b3 1.0 / b4 1.0**
（`dev_calib` 144 条、新口径 dev 基线重跑后校准、`rule='largest_feasible'`、`grid_max=1.0`；
b1 仍 `feasible:false` → 0.29 为兜底值，D13 口径披露）。"检索调用下降 ≥40%"已在校准工作点上
成立（RR 0.09，降 **91%**，`results/e1/core_assertion.json` = PASS）。

```powershell
# 1) dev 集基线（校准需要每条"检索则是否正确"与"直答则是否正确"）
python scripts/run_exp.py --exp dev_calib --method neverrag  --seed 0 --split dev_calib --max-tokens 80 --threads 14
python scripts/run_exp.py --exp dev_calib --method alwaysrag --seed 0 --split dev_calib --max-tokens 80 --threads 14
# 2) 复用 E0 已算好的 dev 草稿信号 → 逐桶求 τ_b（同时网格搜索 TARG 的单全局 τ）
python scripts/calibrate.py
```

验证标准：

- `configs/thresholds.json` 的 b1–b4 不再是 0.1（变为 dev 上按 `rule='largest_feasible'` 求得的
  逐桶 τ 值），且不再含 `_note` 占位说明；
- `results/calibrate_report.json` 中三种门控模式（signal / complexity / hybrid）的校准结果
  **全部写入**，且报告包含 `tau_min_feasible` / `tau_max_feasible` / `rr_at_tau` / `acc_at_tau`，
  便于核对 D13 的方向修正；
- 校准后 E1 才能给出可信的 acc–RR 工作点。

**预计工作量**：1–2 机时（dev_calib 仅 144 条，且草稿信号已在 `figures/e0/e0_signals_dev.jsonl` 中）。

---

## 缺口修复顺序建议（依赖关系）

```
G3 环境（GPU/大模型）─┐
G1 官方法条全文 ──────┼─→ （G8 ✅ 已完成 → G7/G9 ✅ 已完成）→ 论文主表/主图（已可生成）
G4 人工标注 ──────────┤                    ↑
G2 真实裁判文书 ──────┘                    │
G6 法律专用模型基线 ──────────────────────┘（仅影响该对照臂）
G5 算力证明 ─→ 软著材料（与实验并行，互不阻塞）
```

**剩余缺口的推荐顺序（2026-09-13 更新）**：G4 → G1 → G2 → G5（G8/G7/G9 已完成；
G3/G6 仅影响"大模型可比性"与法律专用模型对照臂）。在 G4/G1/G2 完成前，
论文/申报书必须保留本文件与 `docs/deviations.md` 的限定语。
