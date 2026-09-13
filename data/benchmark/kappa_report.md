# ⚠️ 警告：本报告的 kappa 来自【模拟标注员】，**不是**真实标注者间一致性证据

**数据来源：SIMULATED (NOT HUMAN)** —— 由脚本按噪声率 0.12 从 `D:/桌面/lawgate/data/benchmark/all.jsonl` 程序化生成两份假投票，仅用于验证 kappa 流水线可运行。

## 判定阈值

Cohen's kappa >= 0.7 记为 PASS，否则 FAIL → 重新对齐标注规范。

## 输入

- 标注员 A 投票文件：`D:/桌面/lawgate/data/benchmark/simulated_annotators/simulated_annotator_A_noise0.12.jsonl`
- 标注员 B 投票文件：`D:/桌面/lawgate/data/benchmark/simulated_annotators/simulated_annotator_B_noise0.12.jsonl`
- 共同 qid 数：300
- 仅 A 有：0 个
- 仅 B 有：0 个

## 结果

| 指标 | Cohen's kappa | Po | Pe | n | 判定 |
|---|---|---|---|---|---|
| need_retrieval | 0.7263 | 0.8667 | 0.5128 | 300 | PASS |
| golden_provisions（集合规范化后） | 0.8920 | 0.8967 | 0.0431 | 300 | PASS |

> 说明：`golden_provisions` 一致性先按 `law_short#article_no` 规范化、去重、排序，再拼成字符串比较——顺序与重复不计入分歧。

### 如何解读这些数字（尤其是仿真模式）

- 仿真噪声率 = 0.12：B 的 `need_retrieval` 以该概率翻转，`golden_provisions` 以该概率被删/增一个条目，因此 kappa 会随噪声率单调下降；
- kappa 对**类别不平衡**敏感：本评测集 `need_retrieval` 的边际分布偏斜，期望一致率 Pe 较高，同样的观测一致率 Po 会得到更低的 kappa；若把两类样本配平，噪声率 0.12 下的 kappa 会明显低于本表数值；
- `golden_provisions` 的 Pe 很低（本项目金标条文组合高度分散），因此其 kappa 偏高属于指标性质，不等于标注质量一定更高；
- 结论：**仿真数值不具备任何标注质量含义**，只用于证明脚本可运行、可复现。

## 分歧清单

- `need_retrieval` 分歧 40 个：`case_00023`, `case_00035`, `case_00050`, `case_00165`, `con_00021`, `con_00032`, `con_00078`, `con_00087`, `con_00096`, `con_00123`, `con_00170`, `con_00191`, `cv_00035`, `cv_00075`, `cv_00088`, `mt_00010_t2`, `mt_00013_t1`, `mt_00028_t1`, `mt_00036_t1`, `mt_00050_t2`, `mt_00052_t1`, `mt_00053_t2`, `mt_00053_t3`, `mt_00074_t1`, `mt_00091_t1`, `mt_00091_t3`, `mt_00092_t2`, `prov_00031`, `prov_00068`, `prov_00097`, `prov_00118`, `prov_00176`, `prov_00210`, `prov_00224`, `prov_00251`, `tt_t1_00004`, `tt_t1_00027`, `tt_t2_00020`, `tt_t3_00025`, `tt_t4_00010`
- `golden_provisions` 分歧 31 个：`case_00042`, `case_00082`, `case_00108`, `case_00120`, `case_00140`, `case_00158`, `case_00196`, `con_00014`, `con_00096`, `con_00099`, `con_00105`, `con_00123`, `con_00188`, `cv_00062`, `mt_00002_t1`, `mt_00002_t2`, `mt_00005_t1`, `mt_00020_t3`, `mt_00021_t3`, `mt_00063_t1`, `mt_00063_t3`, `mt_00076_t2`, `mt_00079_t3`, `prov_00045`, `prov_00049`, `prov_00068`, `prov_00075`, `prov_00137`, `prov_00199`, `tt_t3_00004`, `tt_t3_00014`

## 结论口径（必须照抄进论文，禁止改写）

- 本报告中的 kappa 来自**程序化模拟标注员**，只证明 kappa 流水线可运行、结果可复现，
- **不得**表述为「标注者间一致性已达到 kappa=X」；
- **不得**作为「人工标注已完成」的证据；
- 申报前必须由两名真人独立标注，并执行：
  `python scripts/kappa.py --a votes_A.jsonl --b votes_B.jsonl`
  以真实投票文件重新生成本报告（届时报告标题会自动变为 HUMAN 版本）。

## 环境口径

- 阈值：0.7
- 仿真噪声率：0.12
- 随机种子：见命令行（默认 42）
- 复现命令（仿真）：`python scripts/kappa.py --simulate --noise 0.12`
