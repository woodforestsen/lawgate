# 归档：2026-09-11 时代（Qwen2.5-1.5B 回答 + max_tokens=80）的实验产物

**归档日期**：2026-09-12（D33）
**归档原因**：这些产物全部产生于**旧口径**——回答模型是本地 Qwen2.5-1.5B-Instruct、
生成长度上限 80 token、门控草稿与回答同源（1.5B）。D30 之后回答模型换成
DeepSeek API 的 `deepseek-v4-flash`、草稿换成本机 Qwen2.5-0.5B，τ_b 需要按新口径
重新校准（D30 风险第 4 条）。**新旧口径的数字不可直接比较**（D21/D29/D30 的既有披露），
因此在重跑 dev 基线与校准之前，把旧产物整体移到这里，防止：

1. 旧分片被 `--merge` 误并进新结果（`results/e1/shards/` 只有 neverrag 的 5/6 片，
   是 2026-09-11 流水线被外部信号杀掉的中断现场）；
2. 旧 `calibrate_report.json` / `thresholds` 被当成现行校准引用；
3. 旧 dev 基线（1.5B 的 correct_if_retrieve / correct_if_direct）污染新 τ_b 校准。

**内容清单**：

| 文件/目录 | 原位置 | 说明 |
|---|---|---|
| `e1/` | `results/e1/` | E1 中断现场：neverrag × test_e1 的 5/6 分片 + 分片 summary（1.5B、80 token） |
| `dev_baseline/` | `results/dev_baseline/` | 校准输入：neverrag/alwaysrag × dev_calib（1.5B、80 token） |
| `dev_calib/` | `results/dev_calib/` | 更早一轮的 neverrag × dev_calib |
| `calibrate_report.json` | `results/` | 2026-09-11 的 τ_b 校准报告（always_acc=0.3889 / target=0.3839，1.5B） |
| `calibrate_{hybrid,complexity,signal}_thresholds.json` | `results/` | 三种门控模式各自的逐桶 τ |
| `thresholds_2026-09-11.json` | `configs/thresholds.json`（副本） | 旧 τ_b：b1 0.29 / b2 1.0 / b3 1.0 / b4 0.49 |
| `pipeline_steps_2026-09-11.json` | `docs/pipeline_steps.json`（副本） | 中断那次的步骤台账（5 步 ok 后被杀） |

**引用规则**：本目录里的任何数字**只能**以"历史口径（1.5B / 80 token / 2026-09-11）"
的身份出现在偏差记录或对照说明里，**不得**作为系统当前性能证据；当前性能一律以
`results/` 下 2026-09-12 之后重新生成的产物为准（口径三件套见 `docs/deviations.md` D33）。

**没有归档的**：`results/e6/`（纯通道 B、零生成，不受回答后端影响，重跑仅做回归验证）、
`results/pilot/`、`results/timing/`（0.5B/1.5B 吞吐试跑，本就带口径标注）、
`results/_demo/`（绘图自检夹具）、`data/kb/gen_cache.db`（内容寻址缓存按模型键隔离，
新旧互不污染，保留它既是审计线索也让重跑不重复计费）。
