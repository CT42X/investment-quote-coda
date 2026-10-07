# -*- coding: utf-8 -*-
"""从微信读书全量热门划线中筛出适合做「一句话结尾」的金句池。

运行方式：
    python3 build/build_pool.py

输入：data/quotes_final.json   （151 本书的全部热门划线，74,776 条）
输出：data/quote_pool.json     （金句池，供 scripts/quote_pick.py 使用）
      build/pool_raw.json      （含评分的中间产物，便于人工复核）
      build/candidates.json    （配额前的候选，人工复核用）

调参提示：全量近似去重最贵（约 4.5 分钟），结果缓存于 build/stage1.json；
仅调阈值/配额/黑白名单时，用 REUSE_STAGE1=1 秒级重跑。
"""
import json, re, unicodedata, difflib, sys, os, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "data", "quotes_final.json")

# 类目 -> 主题标签
CAT2TAG = {
    "风险与不确定性": "risk",
    "资产配置与组合": "allocation",
    "指数与被动投资": "cost",
    "价值投资与公司分析": "value",
    "行为金融与决策": "mind",
    "周期危机与宏观": "cycle",
    "财报与估值": "fundamental",
    "财富观与长期主义": "wealth",
    # —— 2026-10-06 扩展：个人成长 / 时间 / 健康 / 创业 ——
    "个人成长与效率": "habit",
    "时间与生活方式": "life",
    "健康与身心": "health",
    "创业与商业": "startup",
}

MIN_LEN, MAX_LEN = 13, 46

# 每本书的进池配额（默认 DEFAULT_QUOTA；金句密集的书可单独提高）
DEFAULT_QUOTA = 8
QUOTA_OVERRIDE = {
    "金钱心理学": 22,          # 用户指定：该书金句密集
    "穷查理宝典": 14,
    "纳瓦尔宝典": 12,
    "反脆弱": 12,
    "随机漫步的傻瓜": 12,
    "原则": 12,
    "思考，快与慢": 12,
    "被讨厌的勇气": 12,
    "认知觉醒": 12,
    "掌控习惯": 12,
    "四千周": 12,
    "心流": 10,
    "我们为什么要睡觉": 10,
    "从0到1": 10,
    "鞋狗": 10,
}

# —— 排除规则（硬） ——
BAD_PATTERNS = [
    r"第[一二三四五六七八九十百零\d]{1,4}[章节部分回]",       # 章节引用
    r"[图表]\s*\d",                                          # 图表编号
    r"见图|见下表|如下表|如下图所示|如表\d",
    r"http|www\.|@|\.com",
    r"�",                                                    # 乱码
    r"^\s*[0-9０-９]",
    r"^\s*[（(【\[]{0,2}[一二三四五六七八九十\d]{1,3}[）)】\]]{0,2}\s*[、．.）)]?\s*$",
    r"[＝=]\s*[0-9]",                                        # 公式
    r"[0-9]+\s*%\s*[）×)]",                                 # 算式片段
    r"^\s*[A-Za-z0-9\s,.\-—＋%()]+$",                        # 纯英数
    r"(摘|注|译)\s*[:：]",
    r"^\s*(目录|前言|序言|后记|致谢|参考文献|附录)\s*$",
    # —— 元叙述 / 非格言内容 ——
    r"本书|全书|这一章|这一节|本章|上一章|下文|上文|笔者|本书作者",
    r"读者|亲爱的读者|感谢|谢谢|敬请|批评指正|再版|勘误|出版社|编辑",
    r"版权|印次|字数|开本|定价|书号|ISBN",
    r"^[^。！？]{0,6}(主编|著|译)\s*$",
    r"我们将(在|于)?(本章|下章|后文)|下面(我们)?(来|将)|接下来(我们)?(来|将)",
    r"数据来源|资料来源|原载|转引自|参见|详见",
    # —— 公式 / 符号 / 承接 ——
    r"&lt;|&gt;|&amp;|&#|\\u",
    r"[\u0370-\u03ff\u2200-\u22ff\u2a00-\u2aff]",   # 希腊字母与数学符号
    r"Δ|Σ|ρ|λ|σ|μ|α|β|γ|θ",
    r"如前所述|如上所述|前文|前面提到|前面说过|上文提到|下述|下述内容",
    r"^[^。！？]{0,8}(表示|指的是)[^。！？]{0,8}$",
    # —— 残句形态 ——
    r"^[，。；：、·•\-—）)】]",
    r"[\u00b7\u2022]{1}",
    r"^[（(][^）)]{0,20}[）)]\s*[，。]?$",
    # —— 政治 / 政策立场表述（不适合做个人投资提示语） ——
    r"台海|台湾|一国两制|霸权|政治制度|共产党|社会主义|资本主义制度",
]
BAD_RE = [re.compile(p) for p in BAD_PATTERNS]

# 人工审查黑名单（build/blacklist.json），按文本前缀匹配
BLACKLIST_PATH = os.path.join(BASE, "build", "blacklist.json")
# 人工白名单（build/whitelist.json）：绕过阈值与配额强制入选的手挑金句
WHITELIST_PATH = os.path.join(BASE, "build", "whitelist.json")

# 结尾残缺（截断句 / 话没说完）
BAD_TAIL = tuple("：，、；：（(【《‘“-—和与以及的了是就都还也把被对为")

# 「格言性」加分词（投资 + 认知）
GEM_WORDS = ["风险", "不确定", "长期", "耐心", "情绪", "成本", "价值", "价格",
             "复利", "时间", "纪律", "理性", "常识", "概率", "周期", "恐惧",
             "贪婪", "错误", "失败", "人性", "本质", "原则", "安全边际",
             "分散", "指数", "波动", "预期", "资产", "收益", "亏损", "现金"]

# 领域语汇加权（按类目给分，最多 +2）——通用评分偏向投资语汇，
# 会让个人成长/时间/健康/创业类的好句子系统性落榜，故按领域补正。
TAG_WORDS = {
    "habit": ["习惯", "专注", "成长", "思维", "行动", "选择", "自律", "改变",
              "观念", "刻意", "练习", "注意力", "意志力", "舒适区", "耐心"],
    "life": ["时间", "自由", "注意力", "忙碌", "效率", "当下", "选择", "生命",
             "人生", "现实", "浪费", "节奏"],
    "health": ["睡眠", "运动", "身体", "健康", "大脑", "情绪", "精力", "饮食",
               "血糖", "寿命", "久坐", "休息", "呼吸"],
    "startup": ["创业", "创新", "增长", "产品", "用户", "竞争", "团队", "商业",
                "市场", "客户", "破坏性", "价值", "生意", "企业"],
}

# 各类目入围门槛（新类目语汇密度低，门槛略降；纯陈述句已由 SPECIFIC/DEF 规则单独扣分）
SCORE_MIN = 6.5
SCORE_MIN_BY_TAG = {"habit": 6.2, "life": 6.0, "health": 6.0, "startup": 6.2}

# 说教 / 承接句特征（硬排除：不像箴言，像操作指导或行文过渡）
SERMON_RE = re.compile(
    r"^(所以|因此|那么|因而|从而|于是|这样|这就|可见|综上|总之|换句话说)"
    r"|我们(应|要|需要|可以|必须|得|最好)"
    r"|应该(是|要)?|建议(你|大家|投资)|有必要|不妨|请大家|大家要|你需要|记住要|切勿"
    r"|本章|接下来|前面(我们)?|下面(我们)?"
)

# 「金句感」正向词
GOLD_WORDS = ["唯一", "永远", "从来", "绝不", "真正的", "本质", "其实", "恰恰",
              "反而", "注定", "必然", "悖论", "荒谬", "愚蠢", "智慧", "代价",
              "自由", "谦卑", "幸运", "偶然", "失败", "错误"]

# —— 纯陈述 / 信息型句子识别（2026-10-06 新增）——
# 特征：指代某个具体对象 + 罗列经营/财务指标 + 不含任何普遍性主语或判断
# 例："这类生意虽然规模较小，边际收益很高，但其总体收益常常很低。"
SPECIFIC_RE = re.compile(
    r"(这类|这种|这些|该|这家|此类|某家|某一|一家|上述|前述)"
    r"[^。！？]{0,4}(生意|企业|公司|行业|业务|产品|品牌|股票|基金|债券|资产|指数|市场|模式|项目)")
METRIC_RE = re.compile(
    r"(规模|边际收益|毛利率|净利率|利润率|营业收入|销售额|销量|库存|周转率|"
    r"市盈率|市净率|负债率|增长率|市场份额|份额|成本|收益|利润)")
# 普遍性标记：出现这些词说明句子在讲通则，而非某个特例
UNIVERSAL_RE = re.compile(
    r"(任何人|所有|一切|永远|从不|通常|往往|总是|绝不是|真正的|本质上|"
    r"你|我们|人们|每个人|一个人|大多数|少数|人性)")

# 定义 / 术语解释句（如"指数基金是一种……"），信息量低
DEF_RE = re.compile(r"(是一种|就是指|指的是|是指|被称为|叫做|称之为|定义为)")

# 对举 / 转折结构（箴言的典型形态）
CONTRADICTS = [r"不是[^，。]{1,12}而是", r"并非[^，。]{0,10}而是", r"不在于[^，。]{1,14}在于",
               r"越[^，。]{1,10}越", r"只[^，。]{1,12}却", r"虽然[^，。]{1,14}(但是|但|却)",
               r"没有[^，。]{1,12}只有", r"，但", r"却", r"而不是"]

def clean(t: str) -> str:
    t = unicodedata.normalize("NFC", t or "")          # 保留中文全角标点
    t = t.replace("\u3000", " ").replace("\n", " ").replace("\r", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip("'\" ")
    return t

def digit_ratio(t):
    n = len(re.findall(r"[0-9０-９]", t))
    return n / max(len(t), 1)

def ok(t: str):
    if len(t) < MIN_LEN or len(t) > MAX_LEN:
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", t)) < 8:      # 中文太少
        return False
    for r in BAD_RE:
        if r.search(t):
            return False
    if SERMON_RE.search(t):                             # 说教 / 承接句
        return False
    if re.findall(r"[A-Za-z]", t) and len(re.findall(r"[A-Za-z]", t)) > len(t) * 0.35:
        return False
    # 连续英文词（多为外国人名 / 术语对照），但放过 ETF、GDP 这类缩写
    en_words = re.findall(r"[A-Za-z]{2,}", t)
    if len(en_words) >= 2 and sum(len(w) for w in en_words) >= 8:
        return False
    if digit_ratio(t) > 0.14:
        return False
    if len(re.findall(r"[0-9]", t)) >= 6:
        return False
    if t.endswith(BAD_TAIL):
        return False
    if re.search(r"[，。！？；：]", t) is None:          # 无标点，多半是残片
        return False
    for a, b in [("（", "）"), ("(", ")"), ("《", "》"), ("“", "”")]:
        if t.count(a) != t.count(b):
            return False
    return True

def gem_score(t, count, tag="value"):
    """金句感评分：越高越像一句可以独立引用、值得玩味的箴言。"""
    import math
    s = 0.0
    s += sum(1.0 for w in GEM_WORDS if w in t)                     # 主题相关性
    s += min(sum(1.0 for w in TAG_WORDS.get(tag, []) if w in t), 2.0)   # 领域相关性
    s += min(sum(1.2 for w in GOLD_WORDS if w in t), 3.0)          # 金句感
    if any(re.search(p, t) for p in CONTRADICTS):
        s += 1.5                                                   # 对举/转折
    if 15 <= len(t) <= 38:
        s += 1.5                                                   # 长度适中
    if not re.search(r"[0-9]", t):
        s += 1.0                                                   # 无数字
    if re.search(r"[。！？]$", t):
        s += 0.5
    s += min(math.log10(max(count, 1)) / 2.0, 2.0)                 # 引用量对数权重
    if len(t) > 42:
        s -= 1.0
    # —— 减分：纯陈述 / 定义句 ——
    if SPECIFIC_RE.search(t) and METRIC_RE.search(t) and not UNIVERSAL_RE.search(t):
        s -= 2.5                                                   # 讲某个特例的经营指标，无迁移价值
    if DEF_RE.search(t):
        s -= 1.5                                                   # 术语解释
    return round(s, 2)

def norm(t):
    return re.sub(r"[^\u4e00-\u9fff]", "", t)

def dedup(items, sim=0.86):
    """近似去重：精确去重 + 长度邻域分桶比对，避免 O(n^2)。"""
    items = sorted(items, key=lambda x: -x["count"])
    exact = set()
    kept_by_len = collections.defaultdict(list)   # len -> [(item, norm)]
    kept = []
    for it in items:
        k = norm(it["text"])
        if k in exact:
            continue
        L = len(k)
        dup = False
        for L2 in range(max(0, L - 3), L + 4):
            bucket = kept_by_len.get(L2)
            if not bucket:
                continue
            for _, k2 in bucket:
                if len(k2) == L:
                    if difflib.SequenceMatcher(None, k, k2).ratio() >= sim:
                        dup = True; break
                else:
                    if k in k2 or k2 in k:
                        dup = True; break
            if dup:
                break
        if dup:
            continue
        exact.add(k)
        kept_by_len[L].append((it, k))
        kept.append(it)
    return kept

def clean_author(a: str) -> str:
    """清理作者名：去掉 [美] / （法） 国别前缀；多作者只保留到第一位含中文的作者。"""
    a = a or ""
    a = re.sub(r"[\[（(【][^\]）)】]{1,6}[\]）)】]", "", a)   # 去国别标记
    a = re.sub(r"[\s\u3000]+", " ", a).strip()
    parts = [p for p in a.split(" ") if p]
    for i, p in enumerate(parts):
        if re.search(r"[\u4e00-\u9fff]", p):                # 截到第一位中文作者
            a = " ".join(parts[:i + 1])
            break
    return a.strip(" ··")

STAGE1_PATH = os.path.join(BASE, "build", "stage1.json")

# —— 形态类硬排除（作用于 stage2，可在不重建缓存的前提下调整）——
# 承接句开头（句子从半截开始，脱离上下文无法理解）
HARD_START_RE = re.compile(
    r"^(而|但|它|他|她|该|其|此|这|那|也就是说|换句话|意思就是|指|即|所谓|"
    r"第一种|第二种|第三种|首先|其次|再次|然后|最后|另外|此外|同时|并且|而且|"
    r"因此|但是|不过|然而|事实上|实际上|总之|可见|综上|换言之|于是|所以|因为|由于|正是)")

def shape_ok(t: str) -> bool:
    """句法形态检查：排除承接残句、对话片段、引文残片。"""
    if HARD_START_RE.match(t):
        return False
    if "……" in t or "..." in t:                     # 省略号＝被截断
        return False
    if t[0] in "“\"'‘「『":                        # 以引号开头＝对话片段
        return False
    if re.search(r"^[^，。！？]{0,4}[：:]\s*$", t):     # 只有冒号前的引导语
        return False
    return True


def build_stage1():
    """昂贵阶段：解析 → 清洗 → 硬过滤 → 评分 → 全量近似去重。结果缓存到 stage1.json。"""
    src = json.load(open(SRC, encoding="utf-8"))
    raw = []
    for b in src:
        tag = CAT2TAG.get(b["cat"], "value")
        for q in (b.get("all") or []):
            t = clean(q.get("text"))
            if not ok(t):
                continue
            cnt = int(q.get("count") or 0)
            raw.append({
                "text": t,
                "book": b["key"],
                "author": clean_author((b.get("author") or "").split("/")[0].strip()),
                "cat": b["cat"],
                "tag": tag,
                "count": cnt,
                "score": gem_score(t, cnt, tag),
            })
    print("初筛通过:", len(raw))
    raw = dedup(raw)
    print("去重后:", len(raw))
    json.dump(raw, open(STAGE1_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return raw


def main():
    reuse = os.environ.get("REUSE_STAGE1") == "1" and os.path.exists(STAGE1_PATH)
    if reuse:
        raw = json.load(open(STAGE1_PATH, encoding="utf-8"))
        print("复用 stage1 缓存:", len(raw), "条（跳过去重）")
    else:
        raw = build_stage1()
    raw_all = list(raw)          # 供白名单回溯（含未过阈值的条目）

    # 形态类排除（承接残句 / 对话片段）
    before = len(raw)
    raw = [x for x in raw if shape_ok(x["text"])]
    print("形态排除:", before - len(raw), "（剩余", len(raw), "）")

    # 阈值（按类目）
    def keep(x):
        return x["score"] >= SCORE_MIN_BY_TAG.get(x["tag"], SCORE_MIN)
    before = len(raw)
    raw = [x for x in raw if keep(x)]
    print("金句感入围:", len(raw), "（自", before, "）")

    # 人工审查黑名单
    if os.path.exists(BLACKLIST_PATH):
        bl = json.load(open(BLACKLIST_PATH, encoding="utf-8"))
        bl_keys = [b["text"][:14] for b in bl]
        before = len(raw)
        raw = [x for x in raw if x["text"][:14] not in bl_keys]
        print("黑名单剔除:", before - len(raw), "（剩余", len(raw), "）")

    # 候选集（配额前）落盘，供人工复核
    raw_sorted = sorted(raw, key=lambda x: (-x["score"], -x["count"]))
    json.dump(raw_sorted, open(os.path.join(BASE, "build", "candidates.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 每本配额：按 score 排序，避免高划线书垄断；金句密集的书单独提高配额
    by_book = collections.defaultdict(list)
    for x in raw_sorted:
        by_book[x["book"]].append(x)
    pool = []
    for bk, arr in by_book.items():
        q = QUOTA_OVERRIDE.get(bk, DEFAULT_QUOTA)
        pool.extend(arr[:q])
    pool.sort(key=lambda x: (-x["score"], -x["count"]))
    print("配额后池子:", len(pool))

    # 人工白名单：手挑的金句，绕过阈值与配额直接入池
    if os.path.exists(WHITELIST_PATH):
        wl = json.load(open(WHITELIST_PATH, encoding="utf-8"))
        def _k(s):
            return re.sub(r"[\s\u3000]+", "", s)[:16]
        idx = {}
        for x in raw_all:
            idx.setdefault(_k(x["text"]), x)
        have = {_k(x["text"]) for x in pool}
        n_add = 0
        for w in wl:
            key = _k(w["text"])
            if key in have:
                continue
            src = idx.get(key) or next((x for x in raw_all if _k(x["text"]).startswith(key[:10])), None)
            if src is None:
                print("  !! 白名单未匹配:", w["text"][:26])
                continue
            # 手挑金句的自动评分往往偏低，抬到本类目门槛，使其在抽取时与自动入选条目同等权重
            src = dict(src)
            src["score"] = max(src["score"], SCORE_MIN_BY_TAG.get(src["tag"], SCORE_MIN))
            pool.append(src)
            have.add(key)
            n_add += 1
        pool.sort(key=lambda x: (-x["score"], -x["count"]))
        print("白名单新增:", n_add, "（池子", len(pool), "）")

    # 主题分布
    c = collections.Counter(x["tag"] for x in pool)
    for k, v in c.most_common():
        print("   %-12s %3d" % (k, v))
    print("书目覆盖:", len(set(x["book"] for x in pool)), "/", len(set(x["book"] for x in raw_sorted)))

    json.dump(pool, open(os.path.join(BASE, "build", "pool_raw.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # —— 输出最终池（带稳定 id），供技能脚本读取 ——
    import hashlib
    final = []
    for x in pool:
        x = dict(x)
        x["id"] = hashlib.md5(x["text"].encode("utf-8")).hexdigest()[:8]
        final.append(x)
    outdir = os.path.join(BASE, "data")
    os.makedirs(outdir, exist_ok=True)
    outfile = os.path.join(outdir, "quote_pool.json")
    nbooks = len(json.load(open(SRC, encoding="utf-8")))
    nquotes = sum(len(b.get("all") or []) for b in json.load(open(SRC, encoding="utf-8")))
    json.dump({
        "schema": 1,
        "built_at": __import__("datetime").date.today().isoformat(),
        "source": "微信读书热门划线全量数据（%d 本，%d 条）经清洗、去重、金句感评分与人工审查后筛选"
                  % (nbooks, nquotes),
        "total": len(final),
        "quotes": final,
    }, open(outfile, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写入:", outfile, "条数:", len(final))

    print("\n=== 抽样：金句感评分最高的 30 条 ===")
    for x in pool[:30]:
        print("  %4.1f | %6d | %-10s | %s" % (x["score"], x["count"], x["book"][:10], x["text"][:54]))

if __name__ == "__main__":
    main()
