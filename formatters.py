"""
formatters.py -- 命盘文本格式化 & 通用工具函数
==============================================
从 main.py 抽取出的纯函数模块（无 astrbot / aiohttp 依赖，便于单测）。

包含：
  - 时辰/五行/性别 等通用工具
  - 八字 / 紫微 / 六爻 命盘 → Markdown 文本
  - 交叉印证信号 & 聊天上下文摘要
"""
from __future__ import annotations

# ============================================================
# 常量
# ============================================================

SHICHEN_NAMES = [
    "子", "丑", "寅", "卯", "辰", "巳",
    "午", "未", "申", "酉", "戌", "亥",
]

SHICHEN_RANGES = [
    "23:00-01:00", "01:00-03:00", "03:00-05:00", "05:00-07:00",
    "07:00-09:00", "09:00-11:00", "11:00-13:00", "13:00-15:00",
    "15:00-17:00", "17:00-19:00", "19:00-21:00", "21:00-23:00",
]

WUXING_ORDER = ["金", "木", "水", "火", "土"]


# ============================================================
# 通用工具
# ============================================================

def _clock_to_shichen(hour: int) -> int:
    return ((hour + 1) // 2) % 12

def _shichen_name(idx: int) -> str:
    return SHICHEN_NAMES[idx % 12]

def _shichen_range(idx: int) -> str:
    return SHICHEN_RANGES[idx % 12]

def _gender_label(g: str) -> str:
    return "男" if g == "male" else ("女" if g == "female" else str(g))

def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# 八字格式化
# ============================================================

def format_bazi(data: dict) -> str:
    c = data.get("chart", data)
    bi = c.get("birthInfo", {})
    dm = c.get("dayMaster", {})
    lines = ["\U0001F4CA **八字命盘**", ""]
    name = bi.get("name") or "命主"
    y, m, d = bi.get("year", "?"), bi.get("month", "?"), bi.get("day", "?")
    h_idx = _safe_int(bi.get("hour"))
    g = bi.get("gender", "male")
    lines.append(f"\U0001F464 {name}  |  {_gender_label(g)}")
    lines.append(
        f"\U0001F4C5 公历 {y}年{m}月{d}日 "
        f"{_shichen_name(h_idx)}时（{_shichen_range(h_idx)}）"
    )
    lines.append("")

    def _pil(p):
        g_ = p.get("gan", "?")
        z_ = p.get("zhi", "?")
        n = p.get("naYin", "")
        return f"{g_}{z_}{'（' + n + '）' if n else ''}"

    lines.append("\U0001F3DB **四柱**：")
    for k, lb in [("year", "年"), ("month", "月"), ("day", "日"), ("hour", "时")]:
        lines.append(f"  {lb}柱：{_pil(c.get(k, {}))}")
    lines.append("")

    elem = dm.get("element", "?")
    yy = str(dm.get("yinYang", "")).lower()
    yy_lb = "阳" if yy == "yang" else "阴"
    lines.append(f"\u2600\uFE0F **日主**：{dm.get('gan', '?')}{elem}（{yy_lb}{elem}）")
    lines.append("")

    wx = c.get("wuXing", {})
    if wx:
        # 后端 wuXing 是 {count:{金:n,木:n,...}, dominant:[...], weak:[...]}；
        # 兼容旧契约（直接 {金:n,...}），避免五行计数被读空。
        cnt = wx.get("count") if isinstance(wx.get("count"), dict) else wx
        parts = [f"{e}\u00D7{cnt.get(e, 0)}" for e in WUXING_ORDER if cnt.get(e)]
        lines.append(f"\U0001F4C8 **五行**：{'  '.join(parts) if parts else '--'}")
    lines.append("")

    st = c.get("strength", {})
    if st and st.get("level"):
        lines.append(f"\u2696\uFE0F **旺衰**：{st['level']}")
    pt = c.get("pattern", {})
    if pt and pt.get("name"):
        lines.append(f"\U0001F3C6 **格局**：{pt['name']}")
    ys = c.get("yongShen", {})
    if ys:
        parts = []
        # 后端字段为 yong/xi/ji（五行数组）；兼容旧契约 yongShen/xiShen/jiShen（字符串）。
        for k, lb, legacy in [
            ("yong", "用神", "yongShen"),
            ("xi", "喜神", "xiShen"),
            ("ji", "忌神", "jiShen"),
        ]:
            v = ys.get(k)
            if v is None:
                v = ys.get(legacy)
            if v:
                txt = "、".join(v) if isinstance(v, (list, tuple)) else str(v)
                parts.append(f"{lb}：{txt}")
        if parts:
            lines.append(f"\U0001F48E {'  |  '.join(parts)}")
    lines.append("")

    dayun = c.get("daYun", [])
    if dayun:
        lines.append("\U0001F4DC **大运**：")
        for dy in dayun[:10]:
            sa = _safe_int(dy.get("startAge"))
            ea = _safe_int(dy.get("endAge"))
            # 后端大运用单个 ganZhi 字段（如 "壬午"）；起运前的童限阶段 ganZhi 为空。
            gz = dy.get("ganZhi") or f"{dy.get('gan', '')}{dy.get('zhi', '')}"
            if not gz:
                gz = "童限"
            lines.append(f"  {sa}-{ea}岁  {gz}")
        cur_dy = c.get("currentDaYun")
        if cur_dy:
            gz = cur_dy.get("ganZhi") or f"{cur_dy.get('gan', '')}{cur_dy.get('zhi', '')}"
            lines.append(f"\n\U0001F4CD 当前大运：{gz}")
    cur_ln = c.get("currentLiuNian")
    if cur_ln:
        gz = cur_ln.get("ganZhi") or f"{cur_ln.get('gan', '')}{cur_ln.get('zhi', '')}"
        lines.append(f"\U0001F4CD 当前流年：{gz}年")

    # 命宫 / 身宫 / 胎元（后端返回干支字符串，如「甲申」）
    life = []
    for k, lb in [("mingGong", "命宫"), ("shenGong", "身宫"), ("taiYuan", "胎元")]:
        v = c.get(k)
        if v:
            life.append(f"{lb}：{v}")
    if life:
        lines.append("")
        lines.append("\U0001F3E0 " + "  ".join(life))
    return "\n".join(lines)


# ============================================================
# 紫微格式化
# ============================================================

def format_ziwei(data: dict) -> str:
    analysis = data.get("analysis", data)
    zw = analysis.get("ziwei", {})
    if not zw:
        zw = data
    bi = zw.get("birthInfo", {})
    palaces = zw.get("palaces", [])
    da_xians = zw.get("daXians", [])
    BRANCH = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    lines = ["\U0001F52E **紫微斗数命盘**", ""]
    name = bi.get("name") or "命主"
    y, m, d = bi.get("year", "?"), bi.get("month", "?"), bi.get("day", "?")
    h_idx = _safe_int(bi.get("hour"))
    g = bi.get("gender", "male")
    lines.append(f"\U0001F464 {name}  |  {_gender_label(g)}")
    lines.append(f"\U0001F4C5 公历 {y}年{m}月{d}日 {_shichen_name(h_idx)}时")
    wxj = zw.get("wuxingJuName", "")
    if wxj:
        lines.append(f"\U0001F31F 五行局：{wxj}")
    # 用 is not None 判断（命宫/身宫在「子」branch=0 时也能正常显示）
    if zw.get("mingGongBranch") is not None:
        mg = _safe_int(zw.get("mingGongBranch"))
        lines.append(f"\U0001F3E0 命宫：{BRANCH[mg % 12]}宫")
    if zw.get("shenGongBranch") is not None:
        sg = _safe_int(zw.get("shenGongBranch"))
        lines.append(f"\U0001F3E0 身宫：{BRANCH[sg % 12]}宫")
    lines.append("")
    if palaces:
        lines.append("\U0001F3DB **十二宫**：")
        for p in palaces:
            idx = _safe_int(p.get("branch"))
            bname = BRANCH[idx % 12]
            pname = p.get("name", "?")
            stars = p.get("stars", [])
            major = [s.get("name", "") for s in stars if s.get("type") == "major"]
            if p.get("isEmpty") and p.get("borrowedFromBranch") is not None:
                borrowed = BRANCH[_safe_int(p.get("borrowedFromBranch")) % 12]
                star_str = f"借{borrowed}宫星"
            else:
                star_str = "\u3001".join(major[:4]) if major else "（空宫）"
            lines.append(f"  {bname} [{pname}] {star_str}")
        lines.append("")
    if da_xians:
        lines.append("\U0001F4DC **大限**：")
        for dx in da_xians[:6]:
            sa = dx.get("startAge")
            ea = dx.get("endAge")
            age = (f"{sa}-{ea}岁"
                   if isinstance(sa, int) and isinstance(ea, int)
                   else (str(sa) if sa is not None else ""))
            gong = dx.get("palaceName", "?")
            lines.append(f"  {age}  ->  {gong}")
    return "\n".join(lines)


# ============================================================
# 六爻格式化
# ============================================================

def format_liuyao(data: dict) -> str:
    chart = data.get("chart", data)
    orig = chart.get("originalGua", {}) or {}
    hex_name = orig.get("name", "?")
    changed = chart.get("changedGua")
    lines_data = chart.get("lines", []) or []
    moving_positions = chart.get("movingPositions")
    if not isinstance(moving_positions, list):
        moving_positions = [
            ln.get("position") for ln in lines_data
            if isinstance(ln, dict) and ln.get("moving")
        ]
    question = chart.get("question", "")
    lines = ["\U0001F52F **六爻占卜**", ""]
    if question:
        lines.append(f"\u2753 所问：{question}")
        lines.append("")
    lines.append(f"\U0001F3F7 本卦：{hex_name}")
    if isinstance(changed, dict) and changed.get("name"):
        lines.append(f"\U0001F504 变卦：{changed.get('name')}")
    if moving_positions:
        lines.append(f"\U0001F525 动爻：第{'、'.join(str(p) for p in moving_positions)}爻")
    lines.append("")
    for ln in lines_data:
        if not isinstance(ln, dict):
            continue
        pos = ln.get("positionName") or f"第{ln.get('position', '?')}爻"
        yang = ln.get("yang")
        yao = "\u26CA" if yang else "\u26CB"
        nature = "阳" if yang else "阴"
        marker = " \u25C0\u52A8" if ln.get("moving") else ""
        tag = "（世）" if ln.get("isShi") else ("（应）" if ln.get("isYing") else "")
        extra = []
        if ln.get("liuQin"):
            extra.append(str(ln.get("liuQin")))
        if ln.get("liuShen"):
            extra.append(str(ln.get("liuShen")))
        extras = " ".join(extra)
        lines.append(f"  {pos} {yao}（{nature}）{marker}{tag}"
                     + (f" {extras}" if extras else ""))
    return "\n".join(lines)


def format_qimen(data: dict) -> str:
    """奇门遁甲起局摘要（真实 QimenChart 形状）。

    后端 /api/qimen 返回 { chart: QimenChart }；hour 为「时辰地支索引 0–11」，
    与出生信息的 _clock_to_shichen 口径一致，故直接经 _shichen_name 换名。
    """
    chart = data.get("chart", data) if isinstance(data, dict) else {}
    if not isinstance(chart, dict):
        chart = {}
    y = chart.get("year", "?")
    mo = chart.get("month", "?")
    d = chart.get("day", "?")
    h_idx = _safe_int(chart.get("hour"))
    lines = ["\U0001F30C **奇门遁甲起局**", ""]
    lines.append(f"\U0001F4C5 起局：公历 {y}年{mo}月{d}日 {_shichen_name(h_idx)}时")

    jieqi = chart.get("jieqi", "")
    dun = chart.get("dun", "")
    yuan = chart.get("yuan", "")
    ju = chart.get("juNumber", "")
    if jieqi or dun or ju or yuan:
        parts = [str(x) for x in (jieqi, dun and f"{dun}遁", ju and f"{ju}局", yuan) if x]
        lines.append(f"\U0001F30F 局象：{'·'.join(parts)}")

    gz = (chart.get("yearGanZhi"), chart.get("monthGanZhi"),
          chart.get("dayGanZhi"), chart.get("hourGanZhi"))
    if any(gz):
        lines.append("\U0001F3DB 四柱："
                     + " ".join(str(x) if x else "?" for x in gz))

    zfs = chart.get("zhiFuStar", "")
    zsd = chart.get("zhiShiDoor", "")
    if zfs or zsd:
        lines.append("\U0001F31F " + "   ".join(
            x for x in (f"值符：{zfs}" if zfs else "", f"值使：{zsd}" if zsd else "") if x))

    xunkou = chart.get("xunkou", "")
    if xunkou:
        lines.append(f"\U0001F512 旬首：{xunkou}")
    lines.append("")

    gongs = chart.get("gongs", []) or []
    if gongs:
        lines.append("\U0001F4AB **九宫**：")
        for g in gongs:
            if not isinstance(g, dict):
                continue
            luo = g.get("luo", "?")
            core = " ".join(x for x in (g.get("star", ""), g.get("door", ""), g.get("god", "")) if x)
            tian = g.get("tianPan", "")
            di = g.get("diPan", "")
            tag = ""
            if g.get("isKong"):
                tag = "空亡"
            if g.get("isMa"):
                tag = f"{tag}·马星" if tag else "马星"
            pan = f"（天盘{tian} 地盘{di}）" if (tian or di) else ""
            tail = f"〔{tag}〕" if tag else ""
            lines.append(f"  宫{luo}：{core or '—'}{pan}{tail}")
        lines.append("")

    ys = chart.get("yongShen")
    if isinstance(ys, dict) and ys:
        items = []
        for label, key in (
            ("日干", "riGanGong"), ("时干", "shiGanGong"), ("开门", "kaiMenGong"),
            ("生门", "shengMenGong"), ("休门", "xiuMenGong"),
            ("值符", "zhiFuGong"), ("值使", "zhiShiGong"),
        ):
            v = ys.get(key)
            if isinstance(v, int):
                items.append(f"{label}落宫{v}")
        if items:
            lines.append("\U0001F3AF **用神落宫**：" + "，".join(items))
            lines.append("")

    summary = chart.get("summary", "")
    if summary:
        lines.append(f"\U0001F4DC {summary}")
    return "\n".join(lines)


# ============================================================
# 紫占格式化
# ============================================================

def format_zizhan(data: dict) -> str:
    """紫占（紫微斗数占卜）摘要。

    后端 /api/zizhan 返回 { birthInfo, chart, threeLayer, prompt }。
    threeLayer 结构：
      direction(顺/逆), shiChenBranch(时辰地支索引), elapsedMin(时辰内分钟),
      mainPalaceName, main{name,summary,isEmpty,...},
      layer2{name,summary,slot,...}, layer3{name,summary,minutePhase,...}
    """
    tl = data.get("threeLayer", {}) if isinstance(data, dict) else {}
    if not isinstance(tl, dict):
        tl = {}
    lines = ["\U0001F9D9 **紫占（紫微斗数占卜）**", ""]

    # 起盘时空基准
    bi = data.get("birthInfo", {}) if isinstance(data, dict) else {}
    if isinstance(bi, dict) and bi:
        mode = "当下时空" if str(bi.get("name", "")).find("随机") < 0 else "真随机"
        lines.append(f"\U0001F4C5 起盘：{mode} · 公历 {bi.get('year','?')}年"
                     f"{bi.get('month','?')}月{bi.get('day','?')}日 "
                     f"{_shichen_name(_safe_int(bi.get('timeIndex')))}时")

    # 三层卦
    main_h = tl.get("main", {})
    l2_h = tl.get("layer2", {})
    l3_h = tl.get("layer3", {})
    dir_label = tl.get("direction", "顺")
    if isinstance(main_h, dict):
        lines.append(f"\U0001F3AF **三层卦**（大运{dir_label}行）")
        lines.append(f"  主卦·来因宫：{main_h.get('name','?')}"
                     f"　{main_h.get('summary','') or ''}".rstrip())
        if isinstance(l2_h, dict):
            lines.append(f"  二卦·十分钟：{l2_h.get('name','?')}"
                         f"　{l2_h.get('summary','') or ''}".rstrip())
        if isinstance(l3_h, dict):
            lines.append(f"  三卦·分钟卦：{l3_h.get('name','?')}"
                         f"　{l3_h.get('summary','') or ''}".rstrip())
        lines.append("")

    # 问卦时刻
    shi = _safe_int(tl.get("shiChenBranch"))
    el = _safe_int(tl.get("elapsedMin"))
    if shi or el:
        lines.append(f"\u23F1 问卦时刻：{_shichen_name(shi)}时（已过 {el} 分钟）")

    # 占断 prompt（后端组装好的 AI 解读引导，作为 grounding 一并返回）
    prompt = data.get("prompt", "") if isinstance(data, dict) else ""
    if isinstance(prompt, str) and prompt.strip():
        lines.append("")
        lines.append("\U0001F4DC **占断指引**")
        lines.append(prompt.strip())
    return "\n".join(lines)


# ============================================================
# 塔罗格式化
# ============================================================

def format_tarot(spread_result: dict) -> str:
    """把抽到的塔罗牌阵渲染为 Markdown 摘要（供 llm_tool 返回给 LLM）。"""
    cards = spread_result.get("cards", [])
    lines = ["\U0001F0CF **塔罗牌阵**", ""]
    if not cards:
        lines.append("（未抽到牌）")
        return "\n".join(lines)
    for c in cards:
        ori = "正位" if c.get("orientation") == "up" else "逆位"
        lines.append(
            f"\u2022 【{c.get('position', '')}】**{c.get('name', '')}**（{ori}）— {c.get('meaning', '')}"
        )
    return "\n".join(lines)


# ============================================================
# 健康生活指导格式化
# ============================================================

def format_health(plan: dict) -> str:
    """把结构化健康计划渲染为可读摘要（供 llm_tool 返回给 LLM）。"""
    lines = ["\U0001F4A1 **健康生活建议（已生成）**", ""]

    def _block(title: str, items):
        if not items:
            return
        lines.append(f"{title}")
        for t in items:
            lines.append(f"  \u2022 {t}")
        lines.append("")

    _block("\u23F0 作息：", plan.get("routine"))
    _block("\uD83C\uDF74 饮食：", plan.get("diet"))
    _block("\uD83C\uDFCB 运动：", plan.get("exercise"))
    if plan.get("conditions_tips"):
        _block("\uD83D\uDCED 专项提醒：", plan.get("conditions_tips"))
    disc = plan.get("disclaimer")
    if disc:
        lines.append(f"\u26A0\uFE0F {disc}")
    return "\n".join(lines)


# ============================================================
# 辅助
# ============================================================

def format_signals(signals: list) -> str:
    if not signals:
        return ""
    sl = ["\n\U0001F517 **交叉印证**："]
    for s in signals[:5]:
        if not isinstance(s, dict):
            sl.append(f"  \u2022 {s}")
            continue
        dimension = s.get("dimension", "合参")
        verdict = s.get("verdict", "")
        consistent = s.get("consistent")
        mark = "✅一致" if consistent is True else ("⚠️分歧" if consistent is False else "")
        head = f"【{dimension}】{verdict}".rstrip()
        if mark:
            head = f"{head}（{mark}）"
        sl.append(f"  \u2022 {head}")
        bz = s.get("baziView")
        zw = s.get("ziweiView")
        if bz:
            sl.append(f"    \u3002 八字：{bz}")
        if zw:
            sl.append(f"    \u3002 紫微：{zw}")
    return "\n".join(sl)


def _context_summary(msgs) -> str:
    """对话上下文摘要（兼容 字符串列表 与 结构化轮次 [{'role','text'}]）。

    结构化轮次中，user→「TA」、bot→「我」，便于 LLM 区分说话人、保持多轮连贯。
    """
    if not msgs:
        return ""
    # 兼容旧接口：纯字符串列表（一律当作用户侧）
    if isinstance(msgs[0], str):
        recent = msgs[-3:]
        items = "\n".join(f"  \u2022 {m[:80]}" for m in recent)
        return f"\n\U0001F4CB 用户近期在聊：\n{items}\n"

    # 结构化轮次
    turns = msgs[-4:]
    parts = []
    for t in turns:
        role = t.get("role", "user")
        label = "TA" if role == "user" else "我"
        parts.append(f"  {label}：{t.get('text', '')[:80]}")
    items = "\n".join(parts)
    return f"\n\U0001F4CB 近期对话（多轮）：\n{items}\n"


# ============================================================
# 技法知识库（/api/techniques）检索词提取 & 条目格式化
# ============================================================

def _dedupe_terms(terms) -> list:
    """去重保序，滤空值。"""
    seen, out = set(), []
    for t in terms:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_liuyao_terms(chart: dict) -> list:
    """从六爻卦象提取技法库检索词：本卦/变卦名 + 六亲 + 六神。

    这些词对齐 mingbu 后端技法库的 keywords（用神/六亲/六神/世应等），
    用于 /api/techniques?type=liuyao 的蒸馏技法检索。
    """
    terms = []
    orig = chart.get("originalGua", {}) or {}
    if orig.get("name"):
        terms.append(orig.get("name"))
    changed = chart.get("changedGua") or {}
    if isinstance(changed, dict) and changed.get("name"):
        terms.append(changed.get("name"))
    for ln in chart.get("lines", []) or []:
        if not isinstance(ln, dict):
            continue
        for key in ("liuQin", "liuShen"):
            v = ln.get(key)
            if v:
                terms.append(v)
    return _dedupe_terms(terms)


def extract_qimen_terms(chart: dict) -> list:
    """从奇门局象提取技法库检索词：值符/值使 + 九星/八门/八神 + 用神。

    奇门技法库仅含「通用易理基础」条目（art=common），故额外补通用检索词
    「用神/旺衰/五行/生克」，提升对 common 条目的召回。
    """
    terms = []
    for key in ("zhiFuStar", "zhiShiDoor", "dun"):
        v = chart.get(key)
        if v:
            terms.append(v)
    for g in chart.get("gongs", []) or []:
        if not isinstance(g, dict):
            continue
        for key in ("star", "door", "god"):
            v = g.get(key)
            if v:
                terms.append(v)
    ys = chart.get("yongShen") or {}
    if isinstance(ys, dict):
        for v in ys.values():
            if v:
                terms.append(v)
    # 通用易理基础词，确保命中奇门的 common 蒸馏条目
    terms.extend(["用神", "旺衰", "五行", "生克"])
    return _dedupe_terms(terms)


def format_technique_grounding(entries) -> str:
    """把 /api/techniques 返回的蒸馏技法条目格式化为「技法依据」文本。

    作为给 LLM 的专业解读依据（grounding），不是发给用户的最终答复；
    条目格式对齐后端 searchDistilled：·〔《book》·category〕content。
    """
    if not entries:
        return ""
    lines = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        content = str(e.get("content", "")).strip()
        if not content:
            continue
        book = str(e.get("book", "")).strip()
        cat = str(e.get("category", "")).strip()
        head = f"{book}·{cat}" if (book and cat) else (book or cat)
        lines.append(f"·〔{head}〕{content}" if head else f"·{content}")
    if not lines:
        return ""
    return "\U0001F4DA **命理技法依据（经典蒸馏，供解读参考）**\n" + "\n".join(lines)
