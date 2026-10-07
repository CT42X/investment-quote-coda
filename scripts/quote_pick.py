#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""investment-quote-coda — 从金句池中抽取一句经典投资书籍的高引用金句。

用途：在投资 / 决策 / 认知类对话的回复结尾，偶尔附一句金句作为收尾。
池子来源：微信读书 151 本书（投资/决策/成长/时间/健康/创业）热门划线全量数据，经清洗、去重、金句感评分与人工审查。
"""
import argparse
import json
import math
import os
import random
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_PATH = os.path.join(BASE, "data", "quote_pool.json")
STATE_PATH = os.path.join(BASE, "state.json")
RECENT_MAX = 80          # 记住最近使用的 80 条，避免短期重复

TAG_LABEL = {
    "risk": "风险与不确定性",
    "allocation": "资产配置与组合",
    "cost": "指数与被动投资",
    "value": "价值投资与公司分析",
    "mind": "行为金融与决策",
    "cycle": "周期危机与宏观",
    "fundamental": "财报与估值",
    "wealth": "财富观与长期主义",
    "habit": "个人成长与效率",
    "life": "时间与生活方式",
    "health": "健康与身心",
    "startup": "创业与商业",
}


def load_pool():
    if not os.path.exists(POOL_PATH):
        sys.exit("找不到金句池: %s" % POOL_PATH)
    with open(POOL_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"recent": [], "total_used": 0, "last_used": None}


def save_state(s):
    s["recent"] = s["recent"][-RECENT_MAX:]
    json.dump(s, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def weight(q):
    """金句感评分 × 引用量对数权重：质量优先，兼顾大众共鸣度。"""
    return max(q.get("score", 7.0) - 5.0, 0.5) ** 1.5 * (1 + math.log10(q.get("count", 10) + 10))


def pick(quotes, n, tags=None, book=None, avoid=None):
    cands = quotes
    if tags:
        cands = [q for q in cands if q.get("tag") in tags]
    if book:
        cands = [q for q in cands if book in (q.get("book") or "")]
    if not cands:
        return []
    avoid = avoid or set()
    fresh = [q for q in cands if q.get("id") not in avoid]
    if len(fresh) < n:
        fresh = list(cands)
    picked, buf = [], list(fresh)
    for _ in range(min(n, len(buf))):
        w = [weight(q) for q in buf]
        q = random.choices(buf, weights=w, k=1)[0]
        picked.append(q)
        buf.remove(q)
    return picked


def fmt(q):
    """输出：分割线收束正文 + 整行斜体加粗金句。"""
    author = (q.get("author") or "").strip()
    tail = "《%s》" % q["book"]
    if author:
        tail += " %s" % author
    return "---\n\n***「%s」——%s***" % (q["text"], tail)


def main():
    ap = argparse.ArgumentParser(description="抽取一句投资经典金句（用于回复结尾引用）")
    ap.add_argument("--n", type=int, default=1, help="抽取条数，默认 1")
    ap.add_argument("--tag", help="主题过滤，逗号分隔：risk,allocation,cost,value,mind,cycle,fundamental,wealth,habit,life,health,startup")
    ap.add_argument("--book", help="按书名模糊过滤")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非格式化文本")
    ap.add_argument("--stats", action="store_true", help="显示金句池统计")
    ap.add_argument("--tags", action="store_true", help="列出全部可用主题标签")
    ap.add_argument("--reset", action="store_true", help="清空已使用记录")
    ap.add_argument("--no-track", action="store_true", help="不写入使用记录（仅预览）")
    args = ap.parse_args()

    pool = load_pool()

    if args.tags:
        counts = {}
        for q in pool["quotes"]:
            counts[q.get("tag")] = counts.get(q.get("tag"), 0) + 1
        print("可用主题标签（池子共 %d 条）：" % pool["total"])
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print("  %-12s %-24s %3d 条" % (k, TAG_LABEL.get(k, ""), v))
        return

    if args.stats:
        state = load_state()
        books = {}
        for q in pool["quotes"]:
            books[q["book"]] = books.get(q["book"], 0) + 1
        print("金句池：%d 条，覆盖 %d 本书" % (pool["total"], len(books)))
        print("建池日期：%s" % pool.get("built_at", "-"))
        print("累计引用：%d 次，最近一次：%s" % (state.get("total_used", 0), state.get("last_used") or "无"))
        sc = sorted(q["score"] for q in pool["quotes"])
        print("金句感评分：最高 %.1f / 中位 %.1f / 最低 %.1f" % (sc[-1], sc[len(sc) // 2], sc[0]))
        return

    if args.reset:
        save_state({"recent": [], "total_used": 0, "last_used": None})
        print("已清空使用记录。")
        return

    tags = [t.strip() for t in args.tag.split(",")] if args.tag else None
    state = load_state()
    got = pick(pool["quotes"], args.n, tags=tags, book=args.book, avoid=set(state.get("recent", [])))
    if not got:
        print("（无匹配金句：tag=%s book=%s）" % (args.tag, args.book))
        return

    if args.json:
        print(json.dumps(got, ensure_ascii=False, indent=1))
    else:
        for q in got:
            print(fmt(q))
            if args.n > 1:
                print("<!-- %s | %s | %d人划线 | score %.1f -->" % (q["book"], q["tag"], q["count"], q["score"]))

    if not args.no_track:
        state.setdefault("recent", []).extend(q["id"] for q in got)
        state["total_used"] = state.get("total_used", 0) + len(got)
        state["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_state(state)


if __name__ == "__main__":
    main()
