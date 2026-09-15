# -*- coding: utf-8 -*-
"""通道 B·确定性结构化主逻辑（手册 S3.4）。

四条路径（比手册多一条，见 D12）：
  P1 案号核验       slots.case_no_parsed 非空
  P2 法条+时效       law_short ∧ article_no
  P3 法律效力询问    law_short ∧ law_validity_query（「担保法现在还有用吗」）
  P4 主题条文检索    topic ∧ provision_seeking ∧ ¬article_no

返回 ``None`` 表示"查不到 / 槽位不全"→ 由 router 回落到门控（手册的 B 降级路径）。

连接口径（D28）：``conn`` 是**按线程**惰性建的属性，不是 ``__init__`` 里那一条共享连接。
sqlite3 的连接默认禁止跨线程使用，而 FastAPI 的同步端点（含 ``/chat/stream``）跑在
Starlette 的线程池里、评测的 ``workers>1`` 也是多线程——共享一条连接会直接抛
``ProgrammingError: SQLite objects created in a thread can only be used in that same thread``。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date

from lawgate.channel.b_case_verify import CaseNoVerifier
from lawgate.channel.b_temporal import TemporalChecker, TemporalVerdict
from lawgate.config import get_settings
from lawgate.gate.intent import TOPIC_KEYWORDS_FALLBACK


class ChannelB:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_settings().db_path
        self._local = threading.local()
        self.temporal = TemporalChecker(self.db_path)
        self.verifier = CaseNoVerifier(self.db_path)
        self._topic_kw = self._load_topic_keywords()
        self._causes = self._load_causes()

    @property
    def conn(self) -> sqlite3.Connection:
        """当前线程的连接（首次访问时建立）。"""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    # ------------------------------------------------------------------ 工具
    def _load_causes(self) -> list[str]:
        """案由词表（D26-2）：取自 case_registry 的 cause_action 去重值，长词优先。

        为什么不能拿 slots.topic 当"所称案由"：topic 是**主题标签**
        （民间借贷 / 劳动争议 /…），不是案由（民间借贷纠纷 / 房屋租赁合同纠纷 /…）。
        之前 ChannelB 直接把 slots.topic 传给核验器，于是
        「（2019）鲁01民终1242号 这个案子是劳动争议，对吗？」——这句话里
        "劳动争议"根本不在 topic_keyword 的关键词表里（那是 加班费/辞退/裁员/劳动合同…），
        slots.topic 为 None → 核验器拿不到 claimed_cause → 误报「核验通过」。
        UI 的演示预设③ 用的就是这句话，现场会与 README 承诺的「存在但案由不符」对不上。
        """
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT cause_action FROM case_registry "
                "WHERE cause_action IS NOT NULL AND cause_action <> ''").fetchall()
        except sqlite3.Error:
            return []
        return sorted((r[0] for r in rows), key=lambda s: -len(s))

    def _claimed_cause(self, query: str) -> str | None:
        """从提问里扫出用户**声称**的案由；没声称就返回 None（=不做案由比对）。"""
        for c in self._causes:
            if c in query:
                return c
        return None

    def _load_topic_keywords(self) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {}
        try:
            for r in self.conn.execute("SELECT topic, keyword FROM topic_keyword"):
                d.setdefault(r["topic"], []).append(r["keyword"])
        except sqlite3.Error:
            pass
        return d or {k: list(v) for k, v in TOPIC_KEYWORDS_FALLBACK.items()}

    def _topic_provisions(self, topic: str) -> list:
        """按主题词取"现行有效"条文（D26-3）。

        原先 P4 走 ``provision_fts MATCH``，但建库时用的是 ``tokenize='unicode61'``，
        而 unicode61 **不切分中文**——它把一整串连续汉字当成 **一个** token。实测：

            MATCH '利息'        → 0 行
            MATCH '"支付利息"'  → 0 行
            MATCH '民法典'      → 96 行   （law_short 列里"民法典"恰好是独立 token）

        而 ``_fts_query('民间借贷')`` 生成的是一长串中文 OR 条件，于是**永远 0 行**，
        ``topic_fts_miss->C`` 后被 ``return None`` 吃掉 —— P4 自建成起从未命中过一次。

        这里改用 ``provision_keywords`` 做**等值**匹配（该表是建库时按条文标注的主题词，
        没有分词问题），再回连 ``legal_provisions``。同一 (law_short, article_no)
        可能有多"款/项"行：按 item_no 排序后只取首行，避免同一张条文重复渲染。
        """
        kws = [k for k in (self._topic_kw.get(topic) or [topic]) if k]
        if not kws:
            return []
        ph = ",".join("?" * len(kws))
        try:
            rows = self.conn.execute(
                f"""SELECT p.law_short, p.article_label, p.article_no, p.item_no,
                           p.text, p.source_url, p.validity_status, k.weight
                    FROM provision_keywords k
                    JOIN legal_provisions p
                      ON p.law_short = k.law_short AND p.article_no = k.article_no
                    WHERE k.keyword IN ({ph})
                      AND p.validity_status = '现行有效'
                    ORDER BY k.weight DESC, p.law_short, p.article_no, p.item_no""",
                tuple(kws)).fetchall()
        except sqlite3.Error:
            return []
        picked, seen = [], set()
        for r in rows:
            key = (r["law_short"], r["article_no"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(r)
            if len(picked) >= 8:
                break
        return picked

    @staticmethod
    def _render(rows, max_rows: int = 8) -> str:
        out = []
        for r in rows[:max_rows]:
            head = f"《{r['law_short']}》{r['article_label']}"
            if r["item_no"]:
                head += f" {r['item_no']}"
            body = f"{head} {r['text']}"
            url = r["source_url"] if "source_url" in r.keys() else ""
            body += f"\n（来源：{url}）" if url else ""
            out.append(body)
        return "\n\n".join(out)

    def _compose_warning(self, tv: TemporalVerdict, rep: dict | None) -> str:
        parts = [tv.warning or ""]
        if rep:
            prov = self.temporal.fetch_current(rep["law_short"], rep["article_no"]) \
                if rep.get("article_no") else []
            if prov:
                parts.append(f"【现行规定】《{rep['law_short']}》"
                             f"{prov[0]['article_label']}：{prov[0]['text']}")
                if prov[0]["source_url"]:
                    parts.append(f"（来源：{prov[0]['source_url']}）")
            else:
                parts.append(f"【现行规定】《{rep.get('law_short', '')}》："
                             f"{rep.get('note', '')}")
            if rep.get("note"):
                parts.append(f"【替代说明】{rep['note']}")
        if tv.lifecycle_chain and len(tv.lifecycle_chain) > 1:
            parts.append(f"【法律沿革】{' → '.join(tv.lifecycle_chain)}")
        return "\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ 主逻辑
    def run(self, slots, raw_query: str = "", as_of: date | None = None) -> dict | None:
        trace: dict = {"channel": "B", "slots": slots.to_dict(), "steps": []}
        as_of = as_of or date.today()

        # ---------- P1 案号核验 ----------
        if slots.case_no_parsed or slots.case_no_raw:
            # D26-2：所称案由优先从提问里直接扫出来（案由词表来自 case_registry），
            # slots.topic 只作为兜底——它只是主题标签，粒度不等于案由。
            claimed = self._claimed_cause(raw_query) or slots.topic
            v = self.verifier.verify(slots.case_no_parsed, claimed)
            trace["case_verify"] = v.to_dict()
            trace["steps"].append("case_no_verify")
            trace["validity_status"] = None
            trace["source_url"] = (v.real_case or {}).get("source_url") or None
            ans = v.detail
            if v.passed and v.real_case:
                ans += (f"\n\n【文书信息】{v.real_case['court_name']}｜"
                        f"案由：{v.real_case['cause_action']}｜"
                        f"裁判日期：{v.real_case['judgment_date']}")
                ds = v.real_case.get("data_source")
                if ds == "SYNTHETIC":
                    ans += ("\n\n⚠️【数据溯源】本案号来自**合成案号库**"
                            "（data_source=SYNTHETIC），用于系统联调，"
                            "不对应真实案件，不得作为真实引用依据。")
                else:
                    src_url = v.real_case.get("source_url")
                    ans += ("\n\n📌【数据溯源】本案号来自**真实裁判文书库**"
                            f"（data_source={ds}）"
                            + (f"，来源：{src_url}" if src_url else "")
                            + "。引用请以上述案号在中国裁判文书网核验为准。")
            return {"answer": ans, "trace": trace,
                    "must_show_warning": not v.passed}

        # ---------- P2 法条 + 时效 ----------
        if slots.law_short and slots.article_no:
            tv = self.temporal.check_provision(slots.law_short, slots.article_no, as_of)
            trace["temporal"] = tv.to_dict()
            trace["validity_status"] = tv.status
            trace["steps"].append("temporal_check")
            if not tv.is_safe:
                reps = self.temporal.replacement_map(slots.law_short, slots.article_no)
                rep = None
                if reps:
                    rep = reps[0]
                    trace["replacement"] = reps
                trace["source_url"] = None
                return {"answer": self._compose_warning(tv, rep), "trace": trace,
                        "must_show_warning": True, "provision": None}

            rows = self.temporal.fetch_current(slots.law_short, slots.article_no)
            if not rows:
                trace["steps"].append("miss->C")
                trace["validity_status"] = "查无此条"
                return None
            trace["provision_hits"] = len(rows)
            trace["source_url"] = rows[0]["source_url"]
            trace["steps"].append("provision_render")
            # 只保留最新版本的行（fetch_current 已按 version DESC 排）
            latest = rows[0]["version"]
            rows = [r for r in rows if r["version"] == latest]
            return {"answer": self._render(rows), "trace": trace,
                    "must_show_warning": False,
                    "provision": [dict(r) for r in rows]}

        # ---------- P3 法律效力询问（D12）----------
        if slots.law_short and (slots.law_validity_query or
                                (slots.temporal_query and not slots.topic)):
            tv = self.temporal.check_law(slots.law_short, as_of)
            trace["temporal"] = tv.to_dict()
            trace["validity_status"] = tv.status
            trace["steps"].append("law_validity_check")
            if tv.is_safe:
                ans = (f"《{slots.law_short}》截至 {as_of.isoformat()} 为"
                       f"【{tv.status}】，可以继续作为现行依据引用。")
            else:
                ans = tv.warning or ""
                # 该法被废止/修订时，逐条列出最相关的替代条文（含原文与出处）
                reps = self.temporal.replacement_map(slots.law_short)
                if reps:
                    trace["replacement"] = reps
                    blocks = []
                    for rep in reps[:4]:
                        rows = self.temporal.fetch_current(
                            rep["law_short"], rep["article_no"])
                        if rows:
                            latest = rows[0]["version"]
                            rows = [r for r in rows if r["version"] == latest]
                            blocks.append(f"【现行规定】《{rep['law_short']}》"
                                          f"{rows[0]['article_label']}："
                                          f"{rows[0]['text']}")
                            if rows[0]["source_url"]:
                                blocks.append(f"（来源：{rows[0]['source_url']}）")
                        else:
                            blocks.append(f"【现行规定】《{rep['law_short']}》"
                                          f"第{rep['article_no']}条：{rep['note']}")
                    ans += "\n\n" + "\n\n".join(blocks)
                if tv.lifecycle_chain and len(tv.lifecycle_chain) > 1:
                    ans += f"\n\n【法律沿革】{' → '.join(tv.lifecycle_chain)}"
            return {"answer": ans, "trace": trace, "must_show_warning": not tv.is_safe,
                    "provision": None}

        # ---------- P4 主题条文检索 ----------
        if slots.topic and slots.provision_seeking:
            rows = self._topic_provisions(slots.topic)
            if rows:
                trace["steps"].append("topic_kw")
                trace["validity_status"] = "现行有效"
                trace["source_url"] = rows[0]["source_url"]
                trace["topic_hits"] = len(rows)
                head = (f"与「{slots.topic}」相关的现行有效条文如下：\n\n")
                return {"answer": head + self._render(rows), "trace": trace,
                        "must_show_warning": False,
                        "provision": [dict(r) for r in rows]}
            trace["steps"].append("topic_kw_miss->C")
        return None

    def close(self) -> None:
        """关闭**当前线程**的连接（别的线程的连接各自持有，由那条线程自己收尾）。"""
        c = getattr(self._local, "conn", None)
        if c is None:
            return
        try:
            c.close()
        except sqlite3.Error:
            pass
        finally:
            self._local.conn = None
