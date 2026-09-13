# -*- coding: utf-8 -*-
"""快速链路自检：不依赖评测框架，直接跑手册 S3.9 的关键用例。

用法：python scripts/quickcheck.py
产出：docs/quickcheck.txt（UTF-8，控制台 GBK 乱码时看这个文件）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.channel.b_case_verify import CaseNoVerifier  # noqa: E402
from lawgate.channel.b_structured import ChannelB  # noqa: E402
from lawgate.channel.b_temporal import TemporalChecker  # noqa: E402
from lawgate.gate.intent import detect_intent  # noqa: E402
from lawgate.knowledge.build_sqlite import connect  # noqa: E402

OUT = Path("docs/quickcheck.txt")
LINES: list[str] = []


def log(*parts) -> None:
    LINES.append(" ".join(str(p) for p in parts))


def real_case_no() -> str:
    conn = connect("data/kb/legal_facts.db")
    r = conn.execute("SELECT case_no, cause_action FROM case_registry "
                     "WHERE cause_action='民间借贷纠纷' LIMIT 1").fetchone()
    conn.close()
    return r["case_no"]


def main() -> int:
    log("=" * 78)
    log("CA-LegalGate 链路自检", time.strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 78)

    # ---------------- 1. 意图检测 ----------------
    log("\n## 1. 意图检测与槽位抽取\n")
    cases = [
        ("民法典第六百六十七条", "law+article", True),
        ("《合同法》第52条规定哪些情形合同无效？", "law+article(废止)", True),
        ("担保法现在还有用吗", "law_validity", True),
        ("什么是离婚冷静期", "concept（不应进通道B）", False),
        ("我朋友借我10万不还，适用哪条法律？", "topic+provision_seeking", True),
        ("（2022）沪01民终12345号这个案子什么案由", "case_no", True),
    ]
    ok_intent = 0
    for q, expect, want_complete in cases:
        it = detect_intent(q)
        hit = it.slots_complete == want_complete
        ok_intent += hit
        log(f"[{'PASS' if hit else 'FAIL'}] {q}")
        log(f"        期望={expect} complete={want_complete} "
            f"实际 complete={it.slots_complete} reason={it.route_b_reason}")
        log(f"        slots={json.dumps(it.slots.to_dict(), ensure_ascii=False)}")
    log(f"\n意图判定：{ok_intent}/{len(cases)}")

    # ---------------- 2. 时效状态机 ----------------
    log("\n## 2. 时效性状态机（手册 S3.2 验收）\n")
    tc = TemporalChecker()
    from datetime import date

    checks = [
        ("合同法 law-level", tc.check_law("合同法")),
        ("民法典667（越界不存在的条号另测）", tc.check_provision("民法典", 667)),
        ("公司法47 @2024-06-30", tc.check_provision("公司法", 47, date(2024, 6, 30))),
        ("公司法47 @2024-07-02", tc.check_provision("公司法", 47, date(2024, 7, 2))),
        ("公司法(2018修正)26", tc.check_provision("公司法(2018修正)", 26)),
        ("婚姻法32", tc.check_provision("婚姻法", 32)),
        ("担保法 law-level", tc.check_law("担保法")),
        ("民法典 第9999条（查无）", tc.check_provision("民法典", 9999)),
    ]
    for name, v in checks:
        log(f"[{v.status:6s} safe={v.is_safe}] {name}")
        if v.warning:
            log(f"        {v.warning[:150]}")
        if v.lifecycle_chain:
            log(f"        chain={' → '.join(v.lifecycle_chain)}")
    log("\n替代映射（合同法第52条）:")
    for r in tc.replacement_map("合同法", 52):
        log(f"        → 《{r['law_short']}》第{r['article_no']}条：{r['note']}")

    # ---------------- 3. 案号核验 ----------------
    log("\n## 3. 案号三级核验\n")
    rc = real_case_no()
    log(f"真实案号样本：{rc}")
    vf = CaseNoVerifier()
    from lawgate.gate.intent import extract_case_no

    trials = [
        (rc + " 这个案子什么案由", "民间借贷纠纷", "核验通过"),
        (rc + " 这个案子什么案由", "劳动争议", "存在但案由不符"),
        ("（2099）沪01民终12345号", None, "格式非法"),
        ("（2022）沪01民终999999999号", None, "不存在"),
        ("2022沪01民终12345号", None, "格式非法"),
        ("（2022）沪01测12345号", None, "格式非法"),
    ]
    ok_v = 0
    for q, cause, expect in trials:
        p = extract_case_no(q)
        v = vf.verify(p if p else ({"normalized": q[:40]} if "沪01" in q else None),
                      cause)
        hit = v.level == expect
        ok_v += hit
        log(f"[{'PASS' if hit else 'FAIL'}] 期望={expect} 实际={v.level}  {q[:50]}")
        log(f"        {v.detail[:130]}")
    log(f"\n案号核验：{ok_v}/{len(trials)}")

    # ---------------- 4. 通道 B 端到端 ----------------
    log("\n## 4. 通道 B 端到端\n")
    B = ChannelB()
    for q in ["民法典第六百六十七条", "《合同法》第52条规定哪些情形合同无效？",
              "担保法现在还有用吗", rc + " 这个案子什么案由"]:
        it = detect_intent(q)
        res = B.run(it.slots, q) if it.slots_complete else None
        log(f"\nQ: {q}")
        if res is None:
            log("   → 通道 B 未命中（会降级到门控）")
            continue
        log(f"   steps={res['trace'].get('steps')}")
        log(f"   A: {res['answer'][:400].replace(chr(10), ' | ')}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(LINES), encoding="utf-8")
    print(f"written {OUT} ({len(LINES)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
