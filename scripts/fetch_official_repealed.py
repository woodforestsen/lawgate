# -*- coding: utf-8 -*-
"""抓取 7 部**已废止**民事法律的官方全文，生成 `data/raw/<law_short>.txt`。

## 背景：为什么不能一条路走到底

* `scripts/fetch_laws_dataset.py` 走开源结构化数据集（laws-data），但该仓库
  **只收现行有效法**——7 部被《民法典》废止的法律（合同法/物权法/侵权责任法/
  婚姻法/继承法/收养法/担保法）不在其中。
* 国家法律法规数据库（FLK）收录这 7 部（bbbs 已确认），但详情 API 只返回目录树，
  正文文件指向内网 OSS，取不到（详见 fetch_laws_dataset.py 的说明）。
* 中国人大网（www.npc.gov.cn）有全部 7 部的官方全文页，但站点 2026-09 起对
  非浏览器客户端下发 JS 挑战页（WAF），urllib 取到的是 "Please enable
  JavaScript and refresh the page."。
* 国务院公报（www.gov.cn/gongbao）1985/1995/1999 三期只提供**扫描版 PDF**
  （无文本层，PyMuPDF 抽取为空），只能 OCR，代价与误差都不可接受。

## 本脚本的做法

对每部法登记**多个候选源**（均为政府/法院/官方公报网站），依次尝试，
取**条号连续性完全成立、且条数等于官方公布条数**的那一份。这样：
  * 单一来源失效（WAF/下线/改版）不会让整条链断掉；
  * 完整性由"条号必须 1..N 严格连续"客观判定，而不是靠人工目测。

## 官方条数（用于校验，非估计值）

    合同法 428 · 物权法 247 · 侵权责任法 92 · 婚姻法(2001修正) 51
    继承法 37 · 收养法(1998修正) 34 · 担保法 96

## 用法

    python scripts/fetch_official_repealed.py                      # 全部抓取并写盘
    python scripts/fetch_official_repealed.py --only 继承法
    python scripts/fetch_official_repealed.py --dry-run            # 只校验不写盘
    python scripts/fetch_official_repealed.py --show 继承法         # 打印抽到的正文片段
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lawgate.knowledge.flk_parser import cn2int  # noqa: E402

TODAY = date.today().isoformat()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 中国人大网官方法律全文页（2016-07-01 归档目录）。**首选源**：
# 人大网是这 7 部法最权威的公开载体。注意站点有 WAF，密集请求会返回
# "Please enable JavaScript and refresh the page."，故脚本对同一站点
# 请求之间强制 sleep（见 --delay）。
NPC_BASE = "http://www.npc.gov.cn/zgrdw/npc/lfzt/rlyw/2016-07/01/content_{}.htm"
NPC_ID = {
    "合同法": 1992739, "物权法": 1992736, "侵权责任法": 1992753,
    "婚姻法": 1992746, "继承法": 1992749, "收养法": 1992751, "担保法": 1992740,
}
NPC_LEVEL = "A（中国人大网官方全文页）"

# law_short -> {law_name, expect, cands:[(来源名, URL, 来源等级)]}
_SPECS: dict[str, tuple[str, int]] = {
    "合同法": ("中华人民共和国合同法", 428),
    "物权法": ("中华人民共和国物权法", 247),
    "侵权责任法": ("中华人民共和国侵权责任法", 92),
    "婚姻法": ("中华人民共和国婚姻法", 51),
    "继承法": ("中华人民共和国继承法", 37),
    "收养法": ("中华人民共和国收养法", 34),
    "担保法": ("中华人民共和国担保法", 96),
}

# 备选源：B = 国务院公报（国务院办公厅）；C = 其他政府/法院网站转载
_FALLBACK: dict[str, list[tuple[str, str, str]]] = {
    "合同法": [
        ("泰安市公安局（政务公开）",
         "https://gaj.taian.gov.cn/art/2018/2/24/art_63887_5812528.html", "C"),
        ("最高人民法院公报",
         "http://gongbao.court.gov.cn/Details/21954058e6efbfe695ec1f2e3c60ad.html", "C"),
    ],
    "物权法": [
        ("国务院公报（2007年第14号）",
         "https://www.gov.cn/gongbao/content/2007/content_609906.htm", "B"),
    ],
    "侵权责任法": [
        ("湖南省株洲市荷塘区人民法院",
         "http://htqfy.hunancourt.gov.cn/article/detail/2010/06/id/479452.shtml", "C"),
    ],
    "婚姻法": [
        ("国务院公报（2001年第21号）",
         "https://www.gov.cn/gongbao/content/2001/content_60891.htm", "B"),
    ],
    "继承法": [
        ("萍乡市人民政府",
         "https://www.pingxiang.gov.cn/zglh/c100098/pc/content/1984576989435699200/content_1984576989435699200.html", "C"),
    ],
    "收养法": [
        ("北京市人民政府门户网站",
         "https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/200711/t20071110_781250.html", "C"),
        ("三江侗族自治县人民政府",
         "http://www.sjx.gov.cn/sjzt/jczt/dqzt/szfspf/202102/t20210207_2533966.shtml", "C"),
    ],
    "担保法": [
        ("宜兴市人民政府",
         "https://www.yixing.gov.cn/doc/2016/09/13/530588.shtml", "C"),
        ("广州市中级人民法院",
         "https://www.gzcourt.gov.cn/other/yshj/flfg/hdxd/2020/05/26103126511.html", "C"),
    ],
}

SOURCES: dict[str, dict] = {
    k: {"law_name": ln, "expect": n,
        "cands": [("中国人大网（官方全文页）", NPC_BASE.format(NPC_ID[k]), NPC_LEVEL),
                  *_FALLBACK[k]]}
    for k, (ln, n) in _SPECS.items()
}

RE_ARTICLE = re.compile(r"^第([零〇一二三四五六七八九十百千0-9]+)条(?!之)")
RE_STRUCT = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+\s*[编章节]")
RE_ITEM = re.compile(r"^[（(][零〇一二三四五六七八九十0-9]+[)）]")

# 页面噪声（导航/版权/发布系统/手机版提示等）
NOISE_PAT = re.compile(
    r"(浏览字号|打印本页|打印此页|关闭窗口|责任编辑|来源[:：]|上一篇|下一篇|分享到|分享："
    r"|扫一扫|【字体|中国政府网|中国人大网|全国人大|网站地图|版权所有|使用帮助"
    r"|返回首页|延伸阅读|相关阅读|相关链接|相关推荐|字体[:：]|字号[:：]"
    r"|Produced By|大汉网络|大汉版通|联系方式|网站标识码|ICP备|京公网安备"
    r"|主办单位|承办单位|技术支持|网站声明|无障碍|长者版|智能问答"
    r"|^[-—–]?\s*\d{1,4}\s*[-—–]?$"
    r"|^[·•]\s*\d+\s*[·•]$"
    r"|^[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号\s*$)")
# dot leader（目录里的 "……(339)"）
RE_DOTS = re.compile(r"[.．。·、…\s]{3,}")

# 全角数字 → 半角。**仅做宽度归一，不改字符语义**：人大网正文页用全角
# 数字排版（"本法自１９９５年１０月１日起施行"），而 laws-data 与种子语料
# 均为半角，同库混用会造成检索/比对不一致，故统一为半角。
FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def fetch(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def html_to_lines(raw: str) -> list[str]:
    s = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<(br|hr|/p|/div|/tr|/h[1-6]|/li|/td|/table|/blockquote|/center)\s*/?>",
               "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = html_mod.unescape(s)
    out: list[str] = []
    for line in s.split("\n"):
        line = (line.replace("\u3000", " ").replace("\xa0", " ")
                    .replace("\u200b", "").replace("\r", "").replace("\t", " "))
        line = re.sub(r"\s{2,}", " ", line).strip().translate(FW_DIGITS)
        if line:
            out.append(line)
    return out


def _n(s: str) -> str:
    return re.sub(r"[\s.．。·、…]+", "", s)


def cut_body(lines: list[str], law_name: str) -> list[str]:
    """切除页头/目录/页脚，定位到法条正文起点。

    官方页面的结构是【标题 → 通过日期 → 目 录 → 正文】，目录与正文的首个
    编/章标题文字相同（仅空白/全角可能不同）。因此用**首个结构行最后一次
    出现**作锚点最稳：目录里的那次被跳过，正文的那次留下。

    注意：**不能**用"法律名最后一次出现"作锚点——公报页/政务页常在页脚再
    重复一次法律名，会把正文整段切掉（2026-09-13 实测踩到）。故仅在没有
    任何结构行时才退回标题锚点。
    """
    idxs = [i for i, ln in enumerate(lines) if RE_STRUCT.match(ln)]
    if idxs:
        k = _n(lines[idxs[0]])
        same = [i for i in idxs if _n(lines[i]) == k]
        return lines[same[-1]:]
    key = _n(law_name)
    hits = [i for i, ln in enumerate(lines) if _n(ln).startswith(key)]
    if hits:
        return lines[hits[0]:]
    return lines


def clean_body(lines: list[str], law_name: str) -> list[str]:
    key = _n(law_name)
    out: list[str] = []
    for ln in lines:
        if NOISE_PAT.search(ln):
            continue
        if _n(ln) == key:                       # 重复的法律名（页眉/页脚）
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", ln):
            continue
        if re.match(r"^[（(]?\s*(19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", ln):
            continue
        out.append(ln)
    return out


def extract(raw: str, law_name: str) -> tuple[list[str], dict]:
    lines = clean_body(cut_body(html_to_lines(raw), law_name), law_name)
    nos: list[int] = []
    for ln in lines:
        m = RE_ARTICLE.match(ln)
        if m:
            nos.append(cn2int(m.group(1)))
    uniq = sorted(set(nos))
    gaps = [n for n in range(1, (max(uniq) if uniq else 0) + 1) if n not in set(uniq)]
    return lines, {"n_rows": len(nos), "n_unique": len(uniq),
                   "max": max(uniq) if uniq else 0, "gaps": gaps}


def body_only(lines: list[str], expect: int) -> list[str]:
    """只保留 1..expect 的严格连续区间（丢掉附录、下一份文件、页脚残留）。"""
    out: list[str] = []
    next_no = 1
    started = False
    for ln in lines:
        m = RE_ARTICLE.match(ln)
        if m:
            n = cn2int(m.group(1))
            if n == next_no:
                started = True
                next_no += 1
                out.append(ln)
                continue
            if started:
                break                       # 条号跳变 -> 已越出本法
        if not started:
            # 正文开始前的编/章/节标题先缓存
            if RE_STRUCT.match(ln):
                out.append(ln)
            continue
        out.append(ln)                      # 条款/项/续行
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--only", default=None)
    ap.add_argument("--show", default=None, help="打印某部法抽到的正文（前 30 行 + 后 6 行）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="同一站点两次请求之间的间隔秒数（默认 8，规避人大网 WAF）")
    args = ap.parse_args()

    targets = {k: v for k, v in SOURCES.items()
               if (args.show and k == args.show) or (not args.show and
                                                     (args.only is None or k == args.only))}
    if not targets:
        print(f"[ERR] 无匹配法名：{args.show or args.only}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    report, bad = [], 0
    first_request = [True]
    for law_short, cfg in targets.items():
        law_name, expect = cfg["law_name"], cfg["expect"]
        chosen = None
        attempts = []
        for src_name, url, level in cfg["cands"]:
            if first_request[0]:
                first_request[0] = False
            else:
                time.sleep(args.delay)          # 对同一站点限速，规避 WAF
            try:
                raw = fetch(url)
            except Exception as exc:                      # noqa: BLE001
                attempts.append({"source": src_name, "url": url, "level": level,
                                 "ok": False,
                                 "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
                continue
            lines, st = extract(raw, law_name)
            body = body_only(lines, expect)
            ok = (st["n_unique"] == expect and not st["gaps"])
            attempts.append({"source": src_name, "url": url, "level": level,
                             "ok": ok, **st})
            if ok:
                chosen = (src_name, url, level, body)
                break

        if chosen is None:
            best = max(attempts, key=lambda a: a.get("n_unique", 0), default={})
            print(f"[FAIL] {law_short:12s} 无候选源通过校验（最好："
                  f"{best.get('source', '-')} {best.get('n_unique', 0)}/{expect}）")
            for a in attempts:
                if a.get("error"):
                    detail = a["error"]
                else:
                    detail = f"{a['n_unique']} 条, 缺口 {len(a['gaps'])}"
                print(f"         · {a['source'][:24]:24s} {detail}")
            report.append({"law_short": law_short, "ok": False, "expect": expect,
                           "attempts": attempts})
            bad += 1
            continue

        src_name, url, level, body = chosen
        if args.show:
            print(f"--- {law_short}  来源：{src_name}\n    {url}")
            for ln in body[:30]:
                print("   ", ln)
            print("    ...")
            for ln in body[-6:]:
                print("   ", ln)
            continue

        dest = out_dir / f"{law_short}.txt"
        if not args.dry_run:
            dest.write_text("\n".join(body) + "\n", encoding="utf-8")
        report.append({
            "law_short": law_short, "ok": True, "law_name": law_name,
            "source": src_name, "source_level": level, "url": url,
            "expect_articles": expect,
            "got_articles": len([1 for ln in body if RE_ARTICLE.match(ln)]),
            "lines": len(body), "bytes": len("\n".join(body).encode("utf-8")),
            "file": str(dest), "retrieval_date": TODAY, "dry_run": args.dry_run,
            "attempts": attempts,
        })
        print(f"[OK  ] {law_short:12s} {len(body):4d} 行  {expect} 条  "
              f"来源：{src_name}")

    if not args.show and not args.dry_run:
        out = Path("data/kb/fetch_official_repealed_report.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"generated": TODAY, "data": report},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
