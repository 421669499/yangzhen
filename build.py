# -*- coding: utf-8 -*-
"""
build.py — 涨停跟踪分析器 唯一入口

用法：
    python3 build.py                # 抓取最近交易日数据
    python3 build.py 20261013       # 指定日期
    python3 build.py 20261013 --skip-news   # 跳过新闻抓取（更快）
    python3 build.py --pages        # 额外输出 docs/index.html（GitHub Pages 用）

产出：
    output/data.json                 看板数据源
    output/涨停跟踪分析_<日期>.xlsx    四工作表 Excel
    index_share.html                 单文件看板（数据已内嵌）
    docs/index.html                  GitHub Pages 发布版（加 --pages）
"""
from __future__ import annotations

import json
import os
import sys
import datetime as dt
from typing import Any, Dict, List

import fetch_data
import rules

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------- Excel 输出
def _auto_width(ws, min_w: int = 8, max_w: int = 46) -> None:
    """根据内容自适应列宽（中文字符按 2 计）。"""
    for col in ws.columns:
        letter = col[0].column_letter
        width = min_w
        for cell in col:
            v = cell.value
            if v is None:
                continue
            s = str(v)
            w = sum(2 if ord(ch) > 127 else 1 for ch in s)
            width = max(width, min(w + 2, max_w))
        ws.column_dimensions[letter].width = width


def export_excel(result: Dict[str, Any], path: str) -> None:
    """导出四工作表 Excel。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("  [!] 未安装 openpyxl，跳过 Excel 导出（pip install openpyxl）")
        return

    summary = result["summary"]
    records: List[dict] = result["records"]

    wb = Workbook()

    # 样式
    head_fill = PatternFill("solid", fgColor="B4232A")      # 红底（涨）
    head_font = Font(color="FFFFFF", bold=True, size=11)
    green_fill = PatternFill("solid", fgColor="E8F5E9")
    red_fill = PatternFill("solid", fgColor="FDECEA")
    mid_fill = PatternFill("solid", fgColor="FFF8E1")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="top", wrap_text=True)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def write_sheet(ws, headers, rows, wraps=None):
        wraps = wraps or set()
        for c, h in enumerate(headers, 1):
            cell = ws.cell(1, c, h)
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = center
            cell.border = border
        for r, row in enumerate(rows, 2):
            for c, h in enumerate(headers, 1):
                v = row.get(h, "")
                if isinstance(v, (list, dict)):
                    v = "；".join(v) if isinstance(v, list) else json.dumps(v, ensure_ascii=False)
                cell = ws.cell(r, c, v)
                cell.alignment = left if h in wraps else center
                cell.border = border
        ws.freeze_panes = "A2"
        _auto_width(ws)
        for c in range(1, len(headers) + 1):
            if headers[c - 1] in wraps:
                ws.column_dimensions[get_column_letter(c)].width = 52

    # ---- Sheet1 涨停明细
    ws1 = wb.active
    ws1.title = "涨停明细"
    h1 = ["代码", "名称", "市场", "首封时间", "涨停时间", "末封时间", "连板数",
          "涨停统计", "涨跌幅", "最新价", "换手率", "炸板次数", "封板资金万元",
          "成交额亿元", "流通市值亿元", "所属行业", "板块题材"]
    write_sheet(ws1, h1, records)
    for r in range(2, len(records) + 2):
        ws1.cell(r, 1).alignment = Alignment(horizontal="center")
        zdp = ws1.cell(r, 9).value
        if isinstance(zdp, (int, float)):
            ws1.cell(r, 9).font = Font(color="C00000" if zdp > 0 else "008000", bold=True)

    # ---- Sheet2 涨因归因
    ws2 = wb.create_sheet("涨因归因")
    h2 = ["代码", "名称", "首封时间", "连板数", "板块题材", "上涨原因",
          "原因标签", "归因依据", "关联新闻"]
    write_sheet(ws2, h2, records, wraps={"上涨原因", "原因标签", "归因依据", "关联新闻"})

    # ---- Sheet3 风险评估
    ws3 = wb.create_sheet("风险评估")
    h3 = ["代码", "名称", "首封时间", "连板数", "换手率", "炸板次数",
          "封板资金万元", "流通市值亿元", "风险等级", "风险分值", "涨后主要风险"]
    write_sheet(ws3, h3, records, wraps={"涨后主要风险"})
    for r in range(2, len(records) + 2):
        lvl = ws3.cell(r, 9).value
        fill = red_fill if lvl == "高风险" else (mid_fill if lvl == "中风险" else green_fill)
        for c in range(1, len(h3) + 1):
            ws3.cell(r, c).fill = fill

    # ---- Sheet4 趋势预测
    ws4 = wb.create_sheet("趋势预测")
    h4 = ["代码", "名称", "首封时间", "连板数", "风险等级", "预测趋势",
          "趋势分", "置信度", "预测因子", "趋势关键信号", "观察点"]
    write_sheet(ws4, h4, records, wraps={"预测因子", "趋势关键信号", "观察点"})
    for r in range(2, len(records) + 2):
        t = str(ws4.cell(r, 6).value or "")
        ws4.cell(r, 6).font = Font(
            color="C00000" if "看多" in t else ("008000" if "看空" in t else "666666"),
            bold=True)

    # ---- Sheet5 市场概览
    ws5 = wb.create_sheet("市场概览")
    ws5.cell(1, 1, "指标").fill = head_fill
    ws5.cell(1, 1).font = head_font
    ws5.cell(1, 2, "数值").fill = head_fill
    ws5.cell(1, 2).font = head_font
    overview = [
        ("交易日", summary.get("日期", "")),
        ("生成时间", summary.get("生成时间", "")),
        ("涨停家数", summary.get("涨停家数")),
        ("炸板家数", summary.get("炸板家数")),
        ("跌停家数", summary.get("跌停家数")),
        ("炸板率(%)", summary.get("炸板率")),
        ("最高连板", summary.get("最高连板")),
        ("高风险个股数", summary.get("高风险个股数")),
        ("领涨板块", summary.get("领涨板块", "")),
        ("领涨板块涨幅(%)", summary.get("领涨板块涨幅")),
        ("领涨板块主力净流入(亿)", summary.get("领涨板块主力净流入亿元")),
    ]
    for i, (k, v) in enumerate(overview, 2):
        ws5.cell(i, 1, k).border = border
        ws5.cell(i, 2, v).border = border
        ws5.cell(i, 2).alignment = center
    # 连板梯队
    row0 = len(overview) + 3
    ws5.cell(row0, 1, "连板梯队").font = Font(bold=True)
    for i, (k, v) in enumerate((summary.get("连板梯队") or {}).items(), row0 + 1):
        ws5.cell(i, 1, k).border = border
        ws5.cell(i, 2, "、".join(v)).border = border
        ws5.cell(i, 2).alignment = left
    ws5.column_dimensions["A"].width = 24
    ws5.column_dimensions["B"].width = 70

    wb.save(path)
    print(f"  [OK] Excel 已导出：{path}")


# ---------------------------------------------------------------- 内联 HTML
TEMPLATE_NAME = "index.template.html"


def export_html(result: Dict[str, Any], out_path: str) -> bool:
    """把分析结果注入 HTML 模板，产出**自包含**单文件看板。

    数据以 <script id="inlineData"> 形式内嵌，无需 fetch，
    可直接双击打开，也可直接发布到 GitHub Pages / 静态托管。
    """
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            TEMPLATE_NAME)
    if not os.path.exists(tpl_path):
        print(f"  [!] 未找到模板 {TEMPLATE_NAME}，跳过 HTML 输出")
        return False
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()

    data_min = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    marker = "<!--INLINE_DATA-->"
    if marker not in html:
        print("  [!] 模板缺少 <!--INLINE_DATA--> 占位符，跳过 HTML 输出")
        return False
    html = html.replace(
        marker,
        f'<script id="inlineData" type="application/json">{data_min}</script>')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = round(len(html.encode()) / 1024, 1)
    print(f"  [OK] 单文件看板已导出：{out_path}（{size_kb} KB，数据已内嵌）")
    return True


# ---------------------------------------------------------------- 主流程
def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else None
    skip_news = "--skip-news" in sys.argv

    if skip_news:
        # 猴子补丁：跳过新闻抓取
        fetch_data.fetch_news = lambda name, size=5: []  # type: ignore

    raw = fetch_data.fetch_all(date)
    print("\n===== 运行规则引擎 =====")
    result = rules.analyze(raw)

    # 输出 data.json（供本地看板 index.html 读取）
    json_path = os.path.join(OUT_DIR, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"  [OK] 看板数据已导出：{json_path}")

    # 输出 Excel
    xlsx_path = os.path.join(OUT_DIR, f"涨停跟踪分析_{result['summary']['日期']}.xlsx")
    export_excel(result, xlsx_path)

    # 输出自包含单文件看板（GitHub Pages 直接发布用）
    share_html = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "index_share.html")
    export_html(result, share_html)

    # GitHub Pages：同时输出到 docs/index.html
    docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    if os.path.isdir(docs_dir) or "--pages" in sys.argv:
        os.makedirs(docs_dir, exist_ok=True)
        export_html(result, os.path.join(docs_dir, "index.html"))

    s = result["summary"]
    print("\n===== 完成 =====")
    print(f"  交易日    : {s['日期']}")
    print(f"  涨停/炸板 : {s['涨停家数']} / {s['炸板家数']}  （炸板率 {s['炸板率']}%）")
    print(f"  最高连板  : {s['最高连板']}")
    print(f"  高风险    : {s['高风险个股数']} 只")
    print(f"  看板文件  : index.html / index_share.html（打开即用）")


if __name__ == "__main__":
        main()
