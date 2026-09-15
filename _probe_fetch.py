# -*- coding: utf-8 -*-
"""临时探测脚本：拉取 Chinese-Laws-folk 样本法条 + Legal-DC README，确认格式。"""
import json
import os
import urllib.parse
import urllib.request

ROOT = r"D:\桌面\lawgate"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read()


# 1) 宪法样本
api = "https://api.github.com/repos/taburise/Chinese-Laws-folk/contents/" + urllib.parse.quote("中华人民共和国宪法.txt")
info = json.loads(fetch(api))
dl = info["download_url"]
law = fetch(dl).decode("utf-8", "replace")
with open(os.path.join(ROOT, ".sample_law.txt"), "w", encoding="utf-8") as f:
    f.write(law)

# 2) Legal-DC README
api2 = "https://api.github.com/repos/legal-dc/Legal-DC/contents/" + urllib.parse.quote("Legal-DC README.md")
info2 = json.loads(fetch(api2))
readme = fetch(info2["download_url"]).decode("utf-8", "replace")
with open(os.path.join(ROOT, ".legaldc_readme.txt"), "w", encoding="utf-8") as f:
    f.write(readme)

# 3) 摘要写入 probe2
with open(os.path.join(ROOT, ".probe2.txt"), "w", encoding="utf-8") as f:
    f.write("law bytes=%d\n" % len(law))
    f.write("readme bytes=%d\n\n" % len(readme))
    f.write("===== 宪法样本前 1200 字 =====\n")
    f.write(law[:1200])
    f.write("\n\n===== Legal-DC README 前 1500 字 =====\n")
    f.write(readme[:1500])
print("OK law=%d readme=%d" % (len(law), len(readme)))
