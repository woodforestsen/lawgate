# S3.1 意图检测验收报告

> 用例集：`data/benchmark/intent_cases_v1.json`（66 条，其中 5 条为 **已知边界** `known_gap`，计入报告但不计入召回分母）
> 脚本：`scripts/intent_accept.py`　机器可读结果：`results/intent_acceptance.json`

## 1. 判定口径

- **召回**（手册 S3.1 的「召回 ≥90%」）= 在『应当进通道 B』的样本中，`detect_intent()` 的 `slots_complete` 为 True 的比例。统计的是**路由后果**，不是分类标签：分类对了但路由错了的实现没有价值。
- **误触率** = 在『不应进通道 B』的样本（纯概念题、主题命中但没要求条文、寒暄、仅法律名/仅条号）中被误判为进 B 的比例。D10 修正的核心就是压这个数。
- **槽位子集断言**：用例里 `expect_slots` 列出的键必须与抽取结果一致（未列出的键不参与评分，例如主题词典归属）。

## 2. 总体结果

| 指标 | 值 | 门槛 | 判定 |
|---|---|---|---|
| 通道 B 路由召回 | 0.9512 (39/41) | ≥ 0.90 | **PASS** |
| 误触率（不该进 B 却进了） | 0.0500 (1/20) | 越低越好 | — |
| 槽位子集断言一致率 | 0.9344 | — | — |
| 单条全对率（路由 + 槽位） | 0.9016 | — | — |

## 3. 分组结果

| 分组 | 条数 | 已知边界 | 应进 B | 实际进 B | 召回 | 误触 | 单条全对 |
|---|---|---|---|---|---|---|---|
| G1 法条+条号 | 12 | 0 | 12 | 12 | 1.000 | 0 | 12 |
| G2 案号 | 10 | 0 | 10 | 9 | 0.900 | 0 | 9 |
| G3 法律效力/时效 | 10 | 1 | 9 | 8 | 0.889 | 0 | 8 |
| G4 主题+法条索取 | 10 | 0 | 10 | 10 | 1.000 | 0 | 10 |
| G5 纯概念（不得进 B） | 12 | 0 | 0 | 0 | — | 1 | 11 |
| G6 主题但无索取标记 | 8 | 0 | 0 | 0 | — | 0 | 5 |
| G7 多轮（已知边界） | 4 | 4 | 0 | 0 | — | 0 | 0 |

## 4. 明细（未通过的表在最前）

| id | 问句 | 期望进 B | 实际进 B | reason | 失败原因 |
|---|---|---|---|---|---|
| G2-04 | （2022）沪01测12345号这个案子的判决是什么 | True | False | — | 路由 |
| G3-08 | 消保法最新修订是哪一年 | True | False | — | 路由 / 槽位: law_short: 期望 '消保法' 实际 None; law_validity_query: 期望 True 实际 False |
| G5-09 | 什么是格式条款？对消费者有什么影响？ | False | True | topic+provision_seeking | 路由 |
| G6-04 | 他签合同的时候骗了我，这个合同我不想履行了 | False | False | — | 槽位: topic: 期望 '合同效力' 实际 None |
| G6-05 | 对方把我推倒摔伤了，医药费谁来出 | False | False | — | 槽位: topic: 期望 '侵权责任' 实际 None |
| G6-07 | 股东没有实际出资，公司能怎么办 | False | False | — | 槽位: topic: 期望 '公司治理' 实际 None |
| G7-02 | 这一条对公司注册资本是怎么规定的？ | False | True | topic+provision_seeking | （已知边界）路由 |
| G1-01 | 民法典第六百六十七条 | True | True | law+article | OK |
| G1-02 | 《合同法》第52条规定哪些情形合同无效？ | True | True | law+article | OK |
| G1-03 | 《民法典》第一千零七十九条 | True | True | law+article | OK |
| G1-04 | 劳动合同法第八十二条第二款 | True | True | law+article | OK |
| G1-05 | 公司法第47条第（三）项怎么规定注册资本 | True | True | law+article | OK |
| G1-06 | 民事诉讼法第一百二十二条原文 | True | True | law+article | OK |
| G1-07 | 担保法第十九条内容是什么 | True | True | law+article | OK |
| G1-08 | 民法典时间效力规定第二条 | True | True | law+article | OK |
| G1-09 | 民法典第1165条第一款第（一）项 | True | True | law+article | OK |
| G1-10 | 劳动合同法第四十七条经济补偿怎么算？ | True | True | law+article | OK |
| G1-11 | 《中华人民共和国公司法》第26条 | True | True | law+article | OK |
| G1-12 | 民法典 第六百六十七 条 是什么内容 | True | True | law+article | OK |
| G2-01 | （2022）沪01民终12345号这个案子什么案由 | True | True | case_no | OK |
| G2-02 | （2099）沪01民终12345号是真的吗 | True | True | case_no | OK |
| G2-03 | （2022）沪01民终999999999号 | True | True | case_no | OK |
| G2-05 | （2023）京02民初567号 请核验案号是否存在 | True | True | case_no | OK |
| G2-06 | （2021）粤03民终8888号与民间借贷纠纷有关吗 | True | True | case_no | OK |
| G2-07 | （2020）浙01行终321号 | True | True | case_no | OK |
| G2-08 | 帮我查（2024）苏05民申999号 | True | True | case_no | OK |
| G2-09 | （2019）鲁02执1234号执行到位了吗 | True | True | case_no | OK |
| G2-10 | （2022）津01民终456号这个案子的案由是不是劳动争议 | True | True | case_no | OK |
| G3-01 | 担保法现在还有用吗 | True | True | law_validity | OK |
| G3-02 | 合同法现在还有效吗 | True | True | law_validity | OK |
| G3-03 | 婚姻法是否已经废止 | True | True | law_validity | OK |
| G3-04 | 公司法2024年修订后第47条怎么规定的 | True | True | law+article | OK |
| G3-05 | 侵权责任法还能用吗 | True | True | law_validity | OK |
| G3-06 | 物权法现在还有效吗 | True | True | law_validity | OK |
| G3-07 | 旧公司法第26条与现行公司法有什么不同 | True | True | law+article | OK |
| G3-09 | 我2020年签的合同，当时适用的法律是哪部？现在还有效吗 | False | False | — | （已知边界）OK |
| G3-10 | 民法典施行前发生的侵权，民法典第1165条能适用吗 | True | True | law+article | OK |
| G4-01 | 我朋友借我10万不还，适用哪条法律？ | True | True | topic+provision_seeking | OK |
| G4-02 | 被公司辞退，依据哪条法律可以要经济补偿？ | True | True | topic+provision_seeking | OK |
| G4-03 | 房东不退押金，依据什么法条维权？ | True | True | topic+provision_seeking | OK |
| G4-04 | 违约金约定过高，哪个法条可以要求调整？ | True | True | topic+provision_seeking | OK |
| G4-05 | 合同无效的情形，法律条文是怎么规定的？ | True | True | topic+provision_seeking | OK |
| G4-06 | 用人单位责任在民法典哪一条？ | True | True | topic+provision_seeking | OK |
| G4-07 | 股东抽逃出资，适用哪条规定？ | True | True | topic+provision_seeking | OK |
| G4-08 | 离婚时夫妻共同财产怎么分割，法律有哪条规定？ | True | True | topic+provision_seeking | OK |
| G4-09 | 高利贷的利息法律上怎么规定？ | True | True | topic+provision_seeking | OK |
| G4-10 | 竞业限制补偿金的规定是哪一条？ | True | True | topic+provision_seeking | OK |
| G5-01 | 什么是离婚冷静期 | False | False | — | OK |
| G5-02 | 什么是表见代理？ | False | False | — | OK |
| G5-03 | 合同解除和合同撤销有什么区别？ | False | False | — | OK |
| G5-04 | 法律援助是什么意思？ | False | False | — | OK |
| G5-05 | 善意取得需要满足哪些条件？ | False | False | — | OK |
| G5-06 | 定金和订金在法律上有什么区别？ | False | False | — | OK |
| G5-07 | 劳动合同和劳务合同有什么区别？ | False | False | — | OK |
| G5-08 | 离婚需要冷静多久？ | False | False | — | OK |
| G5-10 | 精神损害赔偿一般能赔多少？ | False | False | — | OK |
| G5-11 | 你好 | False | False | — | OK |
| G5-12 | 谢谢你的帮助 | False | False | — | OK |
| G6-01 | 我去年借给同事5万，有借条，现在他不还 | False | False | — | OK |
| G6-02 | 公司把我辞退了，还没有给经济补偿 | False | False | — | OK |
| G6-03 | 房东说我弄坏了家具，不退押金 | False | False | — | OK |
| G6-06 | 我俩离婚了，孩子抚养权归谁 | False | False | — | OK |
| G6-08 | 他答应给我的违约金太高了 | False | False | — | OK |
| G7-01 | 那第1079条呢？ | False | False | — | （已知边界）OK |
| G7-03 | 民法典 | False | False | — | （已知边界）OK |
| G7-04 | 第667条 | False | False | — | （已知边界）OK |

共 7 条未通过（其中已知边界 1 条）。
