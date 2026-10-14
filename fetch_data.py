# -*- coding: utf-8 -*-
"""
fetch_data.py — A股行情数据抓取模块
数据源：东方财富公开接口（需带 Referer / User-Agent 头）

抓取内容：
  1. 涨停股池      getTopicZTPool   封板时间/连板/封板资金/炸板次数/行业
  2. 强势股池      getTopicQSPool   大涨股/量比/新高
  3. 炸板股池      getTopicZBPool   冲高回落，风控参考
  4. 跌停股池      getTopicDTPool   跌停股
  5. 个股详情      stock/get        量比/换手/市盈/流通市值
  6. 板块资金流    clist/get        行业主力净流入排名
  7. 个股新闻      search-api-web   标题+摘要，用于上涨原因归因
"""
from __future__ import annotations

import json
import re
import time
import datetime as dt
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------- 常量配置
UT_PUSH = "7eea3edcaed734bea9cbfc24409ed989"
UT_PUSH2 = "fa5fd1943c7b386f172d6893dbfba10b"
UT_CLIST = "b2884a393a59ad64002292a3e90d46a5"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

ZT_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
QS_URL = "https://push2ex.eastmoney.com/getTopicQSPool"
ZB_URL = "https://push2ex.eastmoney.com/getTopicZBPool"
DT_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"

SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)

# 熔断开关：某接口连续 403 时，本次运行内不再重试该接口
_BLOCKED: Dict[str, bool] = {}


# ---------------------------------------------------------------- 工具函数
def _get(url: str, params: Optional[dict] = None, timeout: int = 15,
         retries: int = 3, referer: Optional[str] = None) -> Optional[str]:
    """带重试的 GET 请求，返回文本。"""
    host = url.split("//")[-1].split("/")[0]
    if _BLOCKED.get(host):
        return None
    hdrs = {}
    if referer:
        hdrs["Referer"] = referer
    last_err = None
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r.text
            last_err = f"HTTP {r.status_code}"
            if r.status_code == 403:
                # 该 host 整体风控，本次运行内熔断，避免无谓重试
                _BLOCKED[host] = True
                break
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(0.6 * (i + 1))
    if last_err:
        print(f"  [!] 请求失败 {host} -> {last_err}")
    return None


def _get_json(url: str, params: Optional[dict] = None, **kw) -> Optional[dict]:
    txt = _get(url, params, **kw)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # jsonp 包裹
        m = re.search(r"^[^(]*$(.*)$[^)]*$", txt.strip(), re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


def _pool(url: str, date: str, sort: str, pagesize: int = 300) -> List[dict]:
    """抓取一个股池（ZTPool / QSPool / ZBPool / DTPool）。"""
    params = {
        "ut": UT_PUSH, "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": pagesize,
        "sort": sort, "date": date, "_": int(time.time() * 1000),
    }
    data = _get_json(url, params)
    if not data or data.get("rc") != 0:
        return []
    pool = (data.get("data") or {}).get("pool") or []
    return pool


def hhmmss(v: Any) -> str:
    """整数 92500 -> '09:25:00'"""
    if v in (None, "", 0, "0"):
        return "--"
    try:
        s = str(int(v)).zfill(6)
        return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
    except (ValueError, TypeError):
        return "--"


def secid(code: str, market: Any = None) -> str:
    """生成东财 secid。市场 0=深/北, 1=沪"""
    if market is not None:
        return f"{int(market)}.{code}"
    code = str(code)
    if code.startswith(("5", "6", "9", "68", "11", "13")):
        return f"1.{code}"
    return f"0.{code}"


# ---------------------------------------------------------------- 抓取函数
def fetch_limit_up(date: str) -> List[dict]:
    """1. 涨停股池"""
    print("  [1/7] 抓取涨停股池 ...")
    pool = _pool(ZT_URL, date, "fbt:asc")
    print(f"       涨停 {len(pool)} 只")
    return pool


def fetch_strong(date: str) -> List[dict]:
    """2. 强势股池（大涨）"""
    print("  [2/7] 抓取强势股池（大涨）...")
    pool = _pool(QS_URL, date, "zdp:desc")
    print(f"       强势股 {len(pool)} 只")
    return pool


def fetch_broken(date: str) -> List[dict]:
    """3. 炸板股池"""
    print("  [3/7] 抓取炸板股池 ...")
    pool = _pool(ZB_URL, date, "fbt:asc")
    print(f"       炸板 {len(pool)} 只")
    return pool


def fetch_limit_down(date: str) -> List[dict]:
    """4. 跌停股池"""
    print("  [4/7] 抓取跌停股池 ...")
    pool = _pool(DT_URL, date, "fund:asc")
    print(f"       跌停 {len(pool)} 只")
    return pool


def fetch_stock_detail(code: str, market: Any = None) -> dict:
    """5. 个股详情（量比/换手/市盈/行业）

    该接口（push2.eastmoney.com）风控较严，可能间歇性 403。
    属增强数据，失败时返回空 dict，由分析层优雅降级。
    """
    fields = ("f43,f47,f48,f50,f51,f52,f57,f58,f60,f116,f117,"
              "f127,f128,f162,f167,f168,f169,f170")
    params = {"secid": secid(code, market), "fields": fields, "ut": UT_PUSH2,
              "_": int(time.time() * 1000)}
    try:
        data = _get_json(STOCK_URL, params, retries=1)
    except Exception:  # noqa: BLE001
        return {}
    return (data or {}).get("data") or {}


def fetch_sector_flow(top: int = 30, date: Optional[str] = None) -> List[dict]:
    """6. 行业板块资金流排名（主力净流入降序）

    主用 datacenter-web 的 RPT_INDUSTRY_FUNDFLOW 报表；
    备用 push2.eastmoney.com 的 clist 接口（可能 403）。
    """
    print("  [6/7] 抓取行业板块资金流 ...")
    out = _sector_flow_datacenter(top, date)
    if not out:
        print("       datacenter 无数据，尝试备用接口 ...")
        out = _sector_flow_push2(top)
    print(f"       板块 {len(out)} 个")
    return out


def _sector_flow_datacenter(top: int, date: Optional[str]) -> List[dict]:
    """datacenter-web 板块资金流报表。"""
    if not date:
        date = dt.date.today().strftime("%Y-%m-%d")
    else:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    params = {
        "reportName": "RPT_INDUSTRY_FUNDFLOW",
        "columns": "ALL",
        "pageSize": top,
        "pageNumber": 1,
        "sortColumns": "NET_INFLOW",
        "sortTypes": -1,
        "filter": f"(TRADE_DATE='{date}')",
        "source": "WEB", "client": "WEB",
    }
    data = _get_json(DATACENTER_URL, params,
                     referer="https://data.eastmoney.com/")
    rows = ((data or {}).get("result") or {}).get("data") or []
    out = []
    for d in rows:
        out.append({
            "板块代码": d.get("BOARD_CODE"),
            "板块名称": d.get("BOARD_NAME"),
            "涨跌幅": d.get("CHANGE_RATE"),
            "主力净流入": d.get("NET_INFLOW"),
            "主力净占比": d.get("NET_INFLOW_RATIO"),
            "领涨股": d.get("MAX_NETINFLOW_SEC"),
        })
    return out


def _sector_flow_push2(top: int) -> List[dict]:
    """备用：push2 clist 接口。"""
    params = {
        "pn": 1, "pz": top, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f62", "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f62,f104,f105,f128,f136",
        "ut": UT_CLIST, "_": int(time.time() * 1000),
    }
    data = _get_json(CLIST_URL, params, referer="https://data.eastmoney.com/",
                     retries=1)
    diff = (data or {}).get("data", {}).get("diff") or []
    out = []
    for d in diff:
        out.append({
            "板块代码": d.get("f12"),
            "板块名称": d.get("f14"),
            "涨跌幅": d.get("f3"),
            "主力净流入": d.get("f62"),
            "上涨家数": d.get("f104"),
            "下跌家数": d.get("f105"),
            "领涨股": d.get("f128"),
        })
    return out


def _get_urllib(url: str, referer: str = "", timeout: int = 15,
                retries: int = 3) -> Optional[str]:
    """用 urllib 发起请求。

    为什么不用 requests：东财新闻搜索接口对 requests 的 HTTP 指纹
    （默认 Accept-Encoding / 连接复用特征）会判定为爬虫并只返回
    passportWeb 空结果；urllib 的默认指纹可正常通过。
    """
    import urllib.request
    headers = {"User-Agent": BASE_HEADERS["User-Agent"], "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(1.0 * (i + 1))
    print(f"  [!] urllib 请求失败 -> {last_err}")
    return None


def fetch_news(name: str, size: int = 5) -> List[dict]:
    """7. 个股相关新闻（标题+摘要）

    两个关键点（均已实测）：
    1. param 必须紧凑 JSON（separators 去空格）+ 预编码后拼进 URL，
       用 requests 的 params= 会被二次编码，服务端返回空。
    2. 必须用 urllib 而非 requests，否则被风控只返回 passportWeb。
    """
    from urllib.parse import quote
    param = {
        "uid": "", "keyword": name,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": size}},
    }
    param_str = quote(
        json.dumps(param, ensure_ascii=False, separators=(",", ":")), safe="")
    url = f"{NEWS_URL}?cb=cb&param={param_str}&_={int(time.time() * 1000)}"

    def _parse(txt: Optional[str]) -> List[dict]:
        if not txt:
            return []
        try:
            m = re.search(r"^[^(]*$(.*)$[^)]*$", txt.strip(), re.S)
            data = json.loads(m.group(1)) if m else json.loads(txt)
        except (json.JSONDecodeError, AttributeError):
            return []
        arts = ((data or {}).get("result") or {}).get("cmsArticleWebOld") or []
        out = []
        for a in arts[:size]:
            out.append({
                "时间": a.get("date", ""),
                "标题": re.sub(r"</?em>", "", a.get("title", "")),
                "摘要": re.sub(r"</?em>", "", a.get("content", "")),
                "来源": a.get("mediaName", ""),
                "链接": a.get("url", ""),
            })
        return out

    out = _parse(_get_urllib(url, referer="https://so.eastmoney.com/"))
    if not out:  # 退避重试一次
        time.sleep(2.0)
        out = _parse(_get_urllib(url, referer="https://so.eastmoney.com/"))
    return out


def fetch_all(date: Optional[str] = None, news_limit: int = 40) -> Dict[str, Any]:
    """一次性抓取全部数据。date 格式 YYYYMMDD，默认取最近交易日。"""
    if not date:
        date = dt.date.today().strftime("%Y%m%d")

    print(f"\n===== 开始抓取 {date} 行情数据 =====")
    zt = fetch_limit_up(date)
    qs = fetch_strong(date)
    zb = fetch_broken(date)
    dtd = fetch_limit_down(date)
    sectors = fetch_sector_flow(date=date)

    # 对涨停股补充个股详情 + 新闻（限量，避免请求过多）
    limit = min(len(zt), 60)
    print(f"  [5/7] 补充个股详情（前 {limit} 只）...")
    got_detail = 0
    for row in zt[:limit]:
        if _BLOCKED.get("push2.eastmoney.com"):
            print("       详情接口被风控熔断，跳过（不影响核心分析）")
            break
        d = fetch_stock_detail(row.get("c"), row.get("m"))
        if d:
            row["_detail"] = d
            got_detail += 1
        time.sleep(0.12)
    print(f"       成功获取 {got_detail}/{limit} 只个股详情")

    print(f"  [7/7] 抓取个股新闻（前 {min(len(zt), news_limit)} 只）...")
    got = 0
    for row in zt[:news_limit]:
        ns = fetch_news(row.get("n", ""), size=4)
        row["_news"] = ns
        if ns:
            got += 1
        time.sleep(1.2)  # 该接口风控较严，放慢节奏
    print(f"       成功获取 {got}/{min(len(zt), news_limit)} 只个股新闻")

    return {
        "date": date,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "limit_up": zt,
        "strong": qs,
        "broken": zb,
        "limit_down": dtd,
        "sectors": sectors,
    }


if __name__ == "__&#8203;main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    res = fetch_all(d)
    print(json.dumps(res, ensure_ascii=False)[:600])

