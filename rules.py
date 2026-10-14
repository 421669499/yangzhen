# -*- coding: utf-8 -*-
"""
rules.py — 涨停归因 / 风险 / 趋势 规则引擎

输入：fetch_data.fetch_all() 的原始数据
输出：结构化的分析结果，每条记录含六要素：
  涨停时间 / 股票代码 / 涨停或大涨时间 / 上涨原因 / 涨后主要风险 / 预测趋势
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fetch_data import hhmmss

# ============================================================ 题材关键词库
# 通用题材关键词 -> (归因标签, 强度)
KEYWORDS: Dict[str, List[str]] = {
    "资产重组": ["重组", "重整", "借壳", "资产注入", "收购", "并购", "易主", "入主",
               "股权转让", "控股权", "要约收购", "吸收合并"],
    "业绩预增": ["预增", "扭亏", "业绩大增", "净利增", "超预期", "利润增长", "业绩预喜"],
    "订单中标": ["中标", "订单", "签约", "合同", "供货", "大单", "采购"],
    "政策利好": ["政策", "补贴", "规划", "试点", "获批", "牌照", "国资委", "国常会",
               "支持", "指导意见", "十五五", "国家级"],
    "技术突破": ["突破", "量产", "研发成功", "专利", "认证", "首创", "下线", "投产"],
    "涨价提价": ["涨价", "提价", "价格上涨", "供不应求", "紧缺", "断供", "扩产"],
    "回购增持": ["回购", "增持", "员工持股", "股权激励"],
    "合作合资": ["合作", "合资", "战略协议", "框架协议", "联合"],
    "传闻概念": ["传闻", "或将", "有望", "拟", "计划", "传言", "蹭"],
}

# 板块 -> 概念题材映射（用于归因增强）
SECTOR_THEME = {
    "影视院线": "传媒娱乐", "出版": "传媒娱乐", "文化传媒": "传媒娱乐",
    "软件开发": "AI/信创", "互联网服务": "AI应用", "半导体": "国产替代",
    "电子元件": "消费电子", "光学光电子": "消费电子", "电池": "新能源",
    "光伏设备": "新能源", "风电设备": "新能源", "汽车零部件": "汽车产业链",
    "航天航空": "军工", "国防军工": "军工", "房地产开发": "地产",
    "证券": "大金融", "银行": "大金融", "保险": "大金融",
    "化学制品": "化工", "化学制药": "医药", "生物制品": "医药",
    "电网设备": "电力设备", "电力行业": "电力", "一般零售": "消费",
    "装修建材": "地产链", "专用设备": "高端制造", "通用设备": "高端制造",
}


# ============================================================ 工具
def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_time_int(v: Any) -> int:
    """把 fbt 转成 HHMMSS 整数，便于比较。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ============================================================ 1. 上涨原因
def analyze_reason(row: dict, ctx: dict) -> Dict[str, Any]:
    """多标签归因：结合新闻关键词、板块效应、连板、封板强度。

    返回 {标签: [...], 主因: str, 依据: str, 新闻: [...]}
    """
    name = row.get("n", "")
    sector = row.get("hybk", "") or ""
    lbc = int(_num(row.get("lbc"), 1))
    fbt = _parse_time_int(row.get("fbt"))
    zttj = row.get("zttj") or {}
    zt_days = int(_num(zttj.get("days"), 1))
    fund = _num(row.get("fund"))
    ltsz = _num(row.get("ltsz")) or 1
    hs = _num(row.get("hs"))
    news = row.get("_news") or []

    labels: List[str] = []
    evidences: List[str] = []
    matched_news: List[str] = []

    # ---- 1) 消息刺激型：新闻标题/摘要命中关键词
    blob = " ".join([f"{a.get('标题','')} {a.get('摘要','')}" for a in news])
    hit_kw: List[str] = []
    for label, kws in KEYWORDS.items():
        for kw in kws:
            if kw and kw in blob:
                if label not in labels:
                    labels.append(label)
                if kw not in hit_kw:
                    hit_kw.append(kw)
    if hit_kw:
        labels.insert(0, "消息刺激型")
        evidences.append(f"新闻命中关键词：{'、'.join(hit_kw[:4])}")
        for a in news:
            t = a.get("标题", "")
            if any(k in f"{t} {a.get('摘要','')}" for k in hit_kw):
                matched_news.append(f"{a.get('时间','')}｜{t}（{a.get('来源','')}）")

    # ---- 2) 板块联动型：所属行业当日板块涨幅居前 / 同板块多股涨停
    sec_rank = ctx.get("sector_rank", {})
    if sector in sec_rank and sec_rank[sector]["rank"] <= 8:
        labels.append("板块联动型")
        evidences.append(
            f"所属【{sector}】板块当日主力净流入排名第{sec_rank[sector]['rank']}"
            f"（板块涨幅 {sec_rank[sector]['chg']:+.2f}%）")
    same = ctx.get("sector_zt_count", {}).get(sector, 0)
    if same >= 3:
        if "板块联动型" not in labels:
            labels.append("板块联动型")
        evidences.append(f"同板块【{sector}】当日共 {same} 只涨停，板块效应显著")

    # ---- 3) 连板加速型
    if lbc >= 3:
        labels.append("高度连板型")
        evidences.append(f"已晋级 {lbc} 连板，属市场高度标的，资金博弈情绪强")
    elif lbc == 2:
        labels.append("连板加速型")
        evidences.append("2 连板，短线资金开始接力")

    # ---- 4) 资金抢筹型：封板资金占流通市值比例
    fund_ratio = fund / ltsz
    if fund_ratio >= 0.02:
        labels.append("资金抢筹型")
        evidences.append(f"封板资金/流通市值 = {fund_ratio*100:.2f}%，封单力度极强")

    # ---- 5) 一字/T字板型：集合竞价即封板
    if 0 < fbt <= 92530:
        labels.append("一字/T字板型")
        evidences.append(f"集合竞价即封板（{hhmmss(fbt)}），资金一致性极强")
    elif 0 < fbt <= 100000:
        evidences.append(f"早盘快速封板（{hhmmss(fbt)}），承接意愿强")
    elif fbt >= 140000:
        evidences.append(f"尾盘才封板（{hhmmss(fbt)}），封板质量偏弱")

    # ---- 6) 低位补涨型：首板 + 换手温和
    if zt_days == 1 and lbc == 1 and hs < 15:
        labels.append("低位补涨型")
        evidences.append(f"首板启动、换手 {hs:.1f}% 相对温和，属低位补涨特征")

    if not labels:
        labels.append("资金驱动型")
        evidences.append("未匹配到明确题材或消息，以资金推动为主")

    # 去重保序；并做标签收敛，避免单只股票堆叠过多标签稀释重点
    seen, ordered = set(), []
    for x in labels:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    # 主标签（驱动类）优先保留，条件类标签按优先级截取
    PRIMARY = ["消息刺激型", "资产重组", "业绩预增", "订单中标", "政策利好",
               "技术突破", "涨价提价", "板块联动型", "高度连板型", "连板加速型",
               "资金抢筹型", "一字/T字板型", "低位补涨型"]
    ordered.sort(key=lambda x: PRIMARY.index(x) if x in PRIMARY else 99)
    if len(ordered) > 4:
        ordered = ordered[:4]

    theme = SECTOR_THEME.get(sector, sector)
    main = ordered[0]
    summary = f"{main}｜{theme}"
    if matched_news:
        summary += f"｜{matched_news[0].split('｜')[-1][:40]}"

    return {
        "标签": ordered,
        "主因": summary,
        "板块题材": theme,
        "归因依据": evidences,
        "关联新闻": matched_news[:3],
        "新闻数量": len(news),
    }


# ============================================================ 2. 涨后风险
def analyze_risk(row: dict, ctx: dict) -> Dict[str, Any]:
    """累加分值制风险评估。

    风险点 -> 分值。累计 >=5 高风险，3-4 中风险，<=2 低风险。
    """
    risks: List[str] = []
    score = 0.0

    zbc = int(_num(row.get("zbc")))                 # 炸板次数
    fbt = _parse_time_int(row.get("fbt"))           # 首封时间
    lbt = _parse_time_int(row.get("lbt"))           # 末封时间
    hs = _num(row.get("hs"))                        # 换手率
    ltsz = _num(row.get("ltsz"))                    # 流通市值
    lbc = int(_num(row.get("lbc"), 1))
    fund = _num(row.get("fund"))
    sector = row.get("hybk", "") or ""

    # 1) 炸板次数：反复开板，封板不稳
    if zbc >= 3:
        risks.append(f"当日炸板 {zbc} 次，封板极不牢固，多空分歧巨大")
        score += 3
    elif zbc >= 1:
        risks.append(f"当日炸板 {zbc} 次，封板稳定性一般")
        score += 1.5

    # 2) 封板时间：尾盘封板持续性差
    if 0 < fbt >= 143000:
        risks.append(f"尾盘才封板（{hhmmss(fbt)}），次日高开低走风险大")
        score += 2
    elif 0 < fbt >= 133000:
        risks.append(f"下午封板（{hhmmss(fbt)}），封板质量偏弱")
        score += 1
    # 封板后又开板，末封晚于首封且差距大
    if 0 < fbt and 0 < lbt and (lbt - fbt) >= 20000:
        risks.append(f"首封 {hhmmss(fbt)} 至末封 {hhmmss(lbt)} 间隔过长，承接反复")
        score += 0.5

    # 3) 换手率
    if hs >= 25:
        risks.append(f"换手率高达 {hs:.1f}%，筹码剧烈换手，获利盘抛压大")
        score += 2
    elif hs <= 1.5:
        risks.append(f"换手率仅 {hs:.1f}%，流动性差，一旦开板易连续跌停")
        score += 1
    elif hs >= 15:
        risks.append(f"换手率 {hs:.1f}% 偏高，短线获利盘较多")
        score += 1

    # 4) 连板高度：越高越危险
    if lbc >= 5:
        risks.append(f"已 {lbc} 连板，处于高位加速末期，随时闪崩")
        score += 3
    elif lbc >= 3:
        risks.append(f"{lbc} 连板，高位换手后接力风险上升")
        score += 2
    elif lbc == 2:
        score += 0.5

    # 5) 流通市值：过小易操纵，过大难持续
    if ltsz < 30e8:
        risks.append(f"流通市值仅 {ltsz/1e8:.1f} 亿，盘子小易被资金操纵、波动剧烈")
        score += 1
    elif ltsz > 300e8:
        risks.append(f"流通市值 {ltsz/1e8:.0f} 亿，体量偏大，持续拉升需巨量资金")
        score += 1

    # 6) 封单强度
    fund_ratio = fund / (ltsz or 1)
    if fund_ratio < 0.004:
        risks.append(f"封单资金/流通市值仅 {fund_ratio*100:.2f}%，封单薄弱，易被砸开")
        score += 1.5

    # 7) 板块资金流向（净流出）
    sec_flow = ctx.get("sector_flow", {})
    if sector in sec_flow and _num(sec_flow[sector]) < 0:
        risks.append(f"所属【{sector}】板块当日主力资金净流出，缺乏板块合力支撑")
        score += 1.5

    # 8) 纯概念无业绩支撑
    pe = None
    d = row.get("_detail") or {}
    if d.get("f162") is not None:
        try:
            pe = float(d["f162"]) / 100.0
        except (TypeError, ValueError):
            pe = None
    if pe is not None and (pe < 0 or pe > 150):
        risks.append("估值缺乏业绩支撑（市盈率为负或极高），纯题材炒作")
        score += 1

    if score >= 5:
        level, color = "高风险", "high"
    elif score >= 3:
        level, color = "中风险", "mid"
    else:
        level, color = "低风险", "low"

    if not risks:
        risks.append("暂未发现显著风险点，但涨停股普遍存在次日的情绪波动风险")

    return {"风险等级": level, "风险色": color, "风险分值": round(score, 1),
            "风险点": risks}


# ============================================================ 3. 趋势预测
def analyze_trend(row: dict, ctx: dict, risk: Dict[str, Any]) -> Dict[str, Any]:
    """三因子加权：封板强度 S + 板块效应 E + 连板动能 M。

    Score > 0 偏多，< 0 偏空。置信度由三因子一致性决定。
    """
    fbt = _parse_time_int(row.get("fbt"))
    zbc = int(_num(row.get("zbc")))
    hs = _num(row.get("hs"))
    lbc = int(_num(row.get("lbc"), 1))
    fund = _num(row.get("fund"))
    ltsz = _num(row.get("ltsz")) or 1
    sector = row.get("hybk", "") or ""
    signals: List[str] = []

    # --- 封板强度 S（-3 ~ +3）
    S = 0.0
    if 0 < fbt <= 93500:
        S += 2.0
        signals.append("集合竞价→早盘秒板，封板强度极高")
    elif 0 < fbt <= 103000:
        S += 1.2
        signals.append("早盘快速封板，承接积极")
    elif 0 < fbt <= 133000:
        S += 0.3
    elif fbt >= 143000:
        S -= 1.5
        signals.append("尾盘封板，次日延续性存疑")
    if zbc == 0:
        S += 1.0
        signals.append("全天零炸板，封板质量优秀")
    elif zbc >= 3:
        S -= 2.0
        signals.append(f"炸板 {zbc} 次，封板质量差")
    elif zbc >= 1:
        S -= 0.8
    fr = fund / ltsz
    if fr >= 0.02:
        S += 0.8
        signals.append("封单资金占比高，买盘意愿强")
    elif fr < 0.004:
        S -= 0.8

    # --- 板块效应 E（-2 ~ +2）
    E = 0.0
    sec_rank = ctx.get("sector_rank", {})
    if sector in sec_rank and sec_rank[sector]["rank"] <= 5:
        E += 1.5
        signals.append(f"所属板块居资金流入前列（第{sec_rank[sector]['rank']}名），有板块合力")
    elif sector in sec_rank and sec_rank[sector]["rank"] <= 10:
        E += 0.8
    sec_flow = ctx.get("sector_flow", {})
    if sector in sec_flow:
        if _num(sec_flow[sector]) < 0:
            E -= 1.2
            signals.append("所属板块资金净流出，缺乏支撑")
        elif _num(sec_flow[sector]) > 5e8:
            E += 0.5
    same = ctx.get("sector_zt_count", {}).get(sector, 0)
    if same >= 4:
        E += 0.7
        signals.append(f"同板块 {same} 股涨停，形成板块效应")

    # --- 连板动能 M（-3 ~ +2）
    M = 0.0
    if lbc >= 5:
        M -= 2.0
        signals.append(f"{lbc} 连板高位，动能透支、博弈风险大")
    elif lbc >= 3:
        M += 0.8
        signals.append(f"{lbc} 连板，市场高度标的，情绪溢价仍在")
    elif lbc == 2:
        M += 1.2
        signals.append("2 连板，处于加速初期，动能向上")
    else:
        if hs < 15:
            M += 0.6
            signals.append(f"首板换手温和（{hs:.1f}%），次日有连板预期")
        else:
            M -= 0.3
    # 高位巨量换手 -> 风险
    if lbc >= 3 and hs >= 25:
        M -= 1.2
        signals.append("高位高换手，资金分歧加剧")

    raw = S + E + M

    # --- 归一化到 -100 ~ 100
    score = max(-100, min(100, raw / 8.0 * 100))

    if score >= 35:
        direction = "看多"
    elif score >= 10:
        direction = "偏多震荡"
    elif score > -10:
        direction = "震荡观望"
    elif score > -35:
        direction = "偏空"
    else:
        direction = "看空"

    # --- 置信度：三因子同号程度
    signs = [1 if x > 0.3 else (-1 if x < -0.3 else 0) for x in (S, E, M)]
    nonzero = [s for s in signs if s != 0]
    if len(nonzero) >= 2 and len(set(nonzero)) == 1:
        conf = "高"
    elif nonzero and len(set(nonzero)) <= 1:
        conf = "中高"
    elif len(set(nonzero)) == 2:
        conf = "中"
    else:
        conf = "低"

    # 与风险等级交叉校验
    if risk["风险等级"] == "高风险":
        if direction == "看多":
            direction = "偏多（高风险）"
        conf = "低" if conf in ("高", "中高") else conf

    # --- 关键观察信号
    watch: List[str] = []
    if zbc >= 1:
        watch.append("次日开盘竞价是否高开及首笔成交承接力度")
    if 0 < fbt >= 143000:
        watch.append("次日是否低开补跌确认尾盘板属性")
    if lbc >= 3:
        watch.append("连板高度是否被卡位、题材是否出现分歧")
    if sector in sec_flow:
        watch.append(f"关注【{sector}】板块次日资金是否延续流入")
    if hs >= 25:
        watch.append("高换手后警惕获利盘集中抛售")
    if not watch:
        watch.append("关注次日集合竞价强度与板块整体情绪")

    return {
        "趋势": direction,
        "趋势分": round(score, 1),
        "置信度": conf,
        "因子": {"封板强度S": round(S, 2), "板块效应E": round(E, 2), "连板动能M": round(M, 2)},
        "关键信号": signals,
        "观察点": watch,
    }


# ============================================================ 主流程
def build_context(raw: dict) -> dict:
    """构建上下文：板块资金流排名、板块涨停家数统计。"""
    sectors = raw.get("sectors") or []
    sector_flow: Dict[str, float] = {}
    sector_rank: Dict[str, dict] = {}
    for i, s in enumerate(sectors, start=1):
        nm = s.get("板块名称")
        if not nm:
            continue
        flow = _num(s.get("主力净流入"))
        sector_flow[nm] = flow
        sector_rank[nm] = {"rank": i, "chg": _num(s.get("涨跌幅")),
                           "flow": flow, "领涨股": s.get("领涨股")}

    sector_zt_count: Dict[str, int] = {}
    for row in raw.get("limit_up") or []:
        hb = row.get("hybk") or ""
        if hb:
            sector_zt_count[hb] = sector_zt_count.get(hb, 0) + 1

    return {"sector_flow": sector_flow, "sector_rank": sector_rank,
            "sector_zt_count": sector_zt_count}


def analyze(raw: dict) -> Dict[str, Any]:
    """对抓取结果做完整分析，返回结构化结果。"""
    ctx = build_context(raw)
    records: List[dict] = []

    for row in raw.get("limit_up") or []:
        market = row.get("m")
        code = row.get("c", "")
        lbc = int(_num(row.get("lbc"), 1))
        zttj = row.get("zttj") or {}
        zt_days = int(_num(zttj.get("days"), 1))
        zt_times = int(_num(zttj.get("ct"), 1))
        hs = _num(row.get("hs"))
        fund = _num(row.get("fund"))
        ltsz = _num(row.get("ltsz")) or 1

        reason = analyze_reason(row, ctx)
        risk = analyze_risk(row, ctx)
        trend = analyze_trend(row, ctx, risk)

        records.append({
            "代码": code,
            "名称": row.get("n", ""),
            "市场": "沪市" if market == 1 else ("北交所" if str(code).startswith(("8", "9")) else "深市"),
            "最新价": round(_num(row.get("p")) / 1000.0, 2),
            "涨跌幅": round(_num(row.get("zdp")), 2),
            "涨停时间": hhmmss(row.get("fbt")),
            "首封时间": hhmmss(row.get("fbt")),
            "末封时间": hhmmss(row.get("lbt")),
            "连板数": lbc,
            "涨停统计": f"{zt_days}天{zt_times}板",
            "炸板次数": int(_num(row.get("zbc"))),
            "换手率": round(hs, 2),
            "封板资金": fund,
            "封板资金万元": round(fund / 1e4, 1),
            "成交额亿元": round(_num(row.get("amount")) / 1e8, 2),
            "流通市值亿元": round(ltsz / 1e8, 2),
            "所属行业": row.get("hybk", ""),
            "板块题材": reason["板块题材"],
            "上涨原因": reason["主因"],
            "原因标签": reason["标签"],
            "归因依据": reason["归因依据"],
            "关联新闻": reason["关联新闻"],
            "风险等级": risk["风险等级"],
            "风险色": risk["风险色"],
            "风险分值": risk["风险分值"],
            "涨后主要风险": risk["风险点"],
            "预测趋势": trend["趋势"],
            "趋势分": trend["趋势分"],
            "置信度": trend["置信度"],
            "预测因子": trend["因子"],
            "趋势关键信号": trend["关键信号"],
            "观察点": trend["观察点"],
        })

    # ---- 全局统计
    zt = raw.get("limit_up") or []
    zb = raw.get("broken") or []
    dt_ = raw.get("limit_down") or []
    max_lbc = max([int(_num(r.get("lbc"), 1)) for r in zt], default=0)
    high_risk = sum(1 for r in records if r["风险等级"] == "高风险")
    top_sector = (raw.get("sectors") or [{}])[0]

    summary = {
        "日期": raw.get("date", ""),
        "生成时间": raw.get("generated_at", ""),
        "涨停家数": len(zt),
        "炸板家数": len(zb),
        "跌停家数": len(dt_),
        "炸板率": round(len(zb) / (len(zt) + len(zb)) * 100, 1) if (zt or zb) else 0,
        "最高连板": max_lbc,
        "高风险个股数": high_risk,
        "领涨板块": top_sector.get("板块名称", ""),
        "领涨板块涨幅": top_sector.get("涨跌幅", 0),
        "领涨板块主力净流入亿元": round(_num(top_sector.get("主力净流入")) / 1e8, 2),
        "板块资金流": raw.get("sectors") or [],
        "强势股数量": len(raw.get("strong") or []),
        "炸板股": [{"代码": r.get("c"), "名称": r.get("n"),
                   "涨跌幅": round(_num(r.get("zdp")), 2),
                   "行业": r.get("hybk")} for r in zb[:30]],
        "跌停股": [{"代码": r.get("c"), "名称": r.get("n"),
                   "涨跌幅": round(_num(r.get("zdp")), 2),
                   "行业": r.get("hybk")} for r in dt_[:30]],
    }

    # 涨停时间分布（按小时段）
    dist = {"09:25竞价": 0, "09:30-10:00": 0, "10:00-11:30": 0,
            "13:00-14:00": 0, "14:00-15:00": 0}
    for r in records:
        t = r["首封时间"]
        if t == "--":
            continue
        hhmm = t.replace(":", "")[:4]
        ti = int(hhmm) if hhmm.isdigit() else 0
        if ti <= 925:
            dist["09:25竞价"] += 1
        elif ti <= 1000:
            dist["09:30-10:00"] += 1
        elif ti <= 1130:
            dist["10:00-11:30"] += 1
        elif ti <= 1400:
            dist["13:00-14:00"] += 1
        else:
            dist["14:00-15:00"] += 1
    summary["涨停时间分布"] = dist

    # 连板梯队
    ladder: Dict[int, List[str]] = {}
    for r in records:
        ladder.setdefault(r["连板数"], []).append(f"{r['名称']}({r['代码']})")
    summary["连板梯队"] = {f"{k}连板": v for k, v in sorted(ladder.items(), reverse=True)}

    return {"summary": summary, "records": records}
