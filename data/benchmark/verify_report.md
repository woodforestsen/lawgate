# 评测集校验报告

目标目录：`D:/桌面/lawgate/data/benchmark`

结论：**PASS**

## 通过项

- 模板键检查通过：1180 条，键集与模板完全一致
- 条数检查：与 counts.json 一致
- 案号真值检查通过：V1 30/30 符合预期；V2 30/30 符合预期；V3 25/25 符合预期；V4 15/15 符合预期
- 多轮分组检查通过：100 组均未跨 dev/test
- qid 唯一性检查通过：1180 个 qid 无重复

## 统计

```json
{
  "total": 1180,
  "by_category": {
    "concept": 200,
    "provision": 260,
    "case": 200,
    "multi-turn": 300,
    "temporal_trap": 120,
    "case_verify": 100
  },
  "by_split": {
    "dev": 236,
    "test": 944
  },
  "case_verify_truth": {
    "V1": "30/30",
    "V2": "30/30",
    "V3": "25/25",
    "V4": "15/15"
  }
}
```
