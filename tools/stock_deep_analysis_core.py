#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any


TW_TZ = timezone(timedelta(hours=8))
USER_AGENT = "Mozilla/5.0 (OpenClaw Stock Deep Analysis)"
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "cb_tool" / "data" / "cb_tool.db"
DEFAULT_REPORT_DIR = Path.home() / "Desktop" / "報告專夾"

TWSE_BWIBBU_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

US_LEADERS_BY_INDUSTRY: list[tuple[str, list[str]]] = [
    ("半導體", ["NVDA", "AMD", "AVGO", "QCOM", "TSM"]),
    ("電腦及週邊設備", ["DELL", "HPQ", "SMCI", "AAPL", "LOGI"]),
    ("電子零組件", ["JBL", "GLW", "APH", "TEL", "CLS"]),
    ("通信網路", ["CSCO", "ANET", "CIEN", "COMM", "JNPR"]),
    ("光電", ["LITE", "COHR", "OLED", "AAOI", "IPGP"]),
]

SUPPLY_CHAIN_HINTS: list[tuple[str, dict[str, Any]]] = [
    (
        "半導體",
        {
            "upstream": ["矽晶圓", "光罩", "先進製程設備", "化學材料"],
            "downstream": ["AI 伺服器", "手機晶片", "車用電子", "高速運算"],
            "drivers": ["AI 資本支出", "先進製程稼動率", "高效能運算需求"],
            "risks": ["終端需求修正", "地緣政治限制", "產能利用率回落"],
        },
    ),
    (
        "電腦及週邊設備",
        {
            "upstream": ["CPU/GPU", "記憶體", "機殼散熱", "連接器與 PCB"],
            "downstream": ["NB/PC", "伺服器", "企業 IT 換機", "AI PC"],
            "drivers": ["商用換機週期", "AI PC 題材", "雲端伺服器採購"],
            "risks": ["消費性需求疲弱", "庫存調整", "產品 ASP 壓力"],
        },
    ),
    (
        "電子零組件",
        {
            "upstream": ["銅箔基板", "金屬材料", "IC 元件", "化工材料"],
            "downstream": ["伺服器", "網通設備", "車用電子", "工業電腦"],
            "drivers": ["高速傳輸升級", "AI 伺服器擴產", "車用滲透率提升"],
            "risks": ["報價競爭", "客戶拉貨遞延", "終端需求轉弱"],
        },
    ),
]


@dataclass
class StockContext:
    code: str
    name: str
    market: str
    industry: str
    symbol: str


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()


def _http_get_json(url: str) -> Any:
    return json.loads(_http_get(url).decode("utf-8", errors="replace"))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "N/A", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}"


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{digits}f}%"


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = _safe_mean(gains[:period])
    avg_loss = _safe_mean(losses[:period])
    if avg_gain is None or avg_loss is None:
        return None
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_macd_hist(closes: list[float]) -> float | None:
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd, 9)
    return macd[-1] - signal[-1] if signal else None


def _roc_to_ad(date_text: str) -> str:
    clean = date_text.replace(" ", "")
    year_text, month_text, day_text = clean.split("/")
    return f"{int(year_text) + 1911:04d}-{int(month_text):02d}-{int(day_text):02d}"


def _roc_numeric_to_ad(date_text: str) -> str:
    text = str(date_text).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) != 7 or not text.isdigit():
        return text
    return f"{int(text[:3]) + 1911:04d}-{int(text[3:5]):02d}-{int(text[5:7]):02d}"


def _google_news(query: str, limit: int = 8) -> list[dict[str, str]]:
    params = urllib.parse.urlencode(
        {
            "q": f"{query} when:30d",
            "hl": "zh-TW",
            "gl": "TW",
            "ceid": "TW:zh-Hant",
        }
    )
    xml_text = _http_get(f"{GOOGLE_NEWS_RSS}?{params}").decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published_at = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        items.append({"title": title, "link": link, "published_at": published_at})
        if len(items) >= limit:
            break
    return items


def _resolve_yahoo_symbol(code: str) -> tuple[str, str]:
    for suffix, market in ((".TW", "TWSE"), (".TWO", "TPEx")):
        symbol = f"{code}{suffix}"
        url = YAHOO_CHART_URL.format(symbol=symbol) + "?range=1mo&interval=1d"
        try:
            payload = _http_get_json(url)
        except Exception:
            continue
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if isinstance(result, dict):
            return symbol, market
    raise RuntimeError(f"無法解析代碼 {code} 的 Yahoo symbol")


def _fetch_yahoo_history(symbol: str, range_name: str = "1y") -> list[dict[str, Any]]:
    url = YAHOO_CHART_URL.format(symbol=symbol) + "?" + urllib.parse.urlencode(
        {"range": range_name, "interval": "1d", "includePrePost": "false"}
    )
    payload = _http_get_json(url)
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return []
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    rows: list[dict[str, Any]] = []
    for ts, open_v, high_v, low_v, close_v, vol_v in zip(
        timestamps,
        quote.get("open") or [],
        quote.get("high") or [],
        quote.get("low") or [],
        quote.get("close") or [],
        quote.get("volume") or [],
    ):
        close_num = _to_float(close_v)
        if close_num is None:
            continue
        rows.append(
            {
                "trade_date": datetime.fromtimestamp(int(ts), tz=TW_TZ).strftime("%Y-%m-%d"),
                "open_price": _to_float(open_v),
                "high_price": _to_float(high_v),
                "low_price": _to_float(low_v),
                "close_price": close_num,
                "volume": _to_float(vol_v),
            }
        )
    return rows


def _fetch_twse_history(code: str, months: int = 12) -> list[dict[str, Any]]:
    now = datetime.now(TW_TZ)
    year = now.year
    month = now.month
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for _ in range(months):
        url = (
            "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?"
            + urllib.parse.urlencode({"date": f"{year:04d}{month:02d}01", "stockNo": code, "response": "json"})
        )
        try:
            payload = _http_get_json(url)
        except Exception:
            break
        if str(payload.get("stat", "")).upper() == "OK":
            for record in payload.get("data", []):
                if len(record) < 7:
                    continue
                trade_date = _roc_to_ad(record[0])
                if trade_date in seen:
                    continue
                seen.add(trade_date)
                rows.append(
                    {
                        "trade_date": trade_date,
                        "open_price": _to_float(record[3]),
                        "high_price": _to_float(record[4]),
                        "low_price": _to_float(record[5]),
                        "close_price": _to_float(record[6]),
                        "volume": _to_float(record[1]),
                    }
                )
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    rows.sort(key=lambda row: row["trade_date"])
    return rows


def _latest_twse_valuation_row(code: str) -> dict[str, Any] | None:
    for row in _http_get_json(TWSE_BWIBBU_URL):
        if str(row.get("Code", "")).strip() == code:
            return row
    return None


def _latest_twse_revenue_row(code: str) -> dict[str, Any] | None:
    for row in _http_get_json(TWSE_REVENUE_URL):
        if str(row.get("公司代號", "")).strip() == code:
            return row
    return None


def _latest_twse_inst_row(code: str) -> dict[str, Any] | None:
    today = datetime.now(TW_TZ).date()
    for delta in range(7):
        query_date = (today - timedelta(days=delta)).strftime("%Y%m%d")
        url = (
            "https://www.twse.com.tw/rwd/zh/fund/T86?"
            + urllib.parse.urlencode({"date": query_date, "selectType": "ALLBUT0999", "response": "json"})
        )
        try:
            payload = _http_get_json(url)
        except Exception:
            continue
        if str(payload.get("stat", "")).upper() != "OK":
            continue
        for row in payload.get("data", []):
            if row and str(row[0]).strip() == code:
                foreign_net = _to_float(row[4])
                investment_net = _to_float(row[7])
                total_net = _to_float(row[-1])
                dealer_net = None
                if total_net is not None and foreign_net is not None and investment_net is not None:
                    dealer_net = total_net - foreign_net - investment_net
                return {
                    "trade_date": _roc_numeric_to_ad(payload.get("date", query_date)),
                    "foreign_net": foreign_net,
                    "investment_trust_net": investment_net,
                    "dealer_net": dealer_net,
                    "total_net": total_net,
                }
    return None


def _load_cb_note(code: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        master_rows = conn.execute(
            """
            SELECT cb_code, cb_name, issue_date, maturity_date, status
            FROM cb_master
            WHERE underlying_stock_code = ?
            ORDER BY issue_date DESC
            """,
            (code,),
        ).fetchall()
        if not master_rows:
            return None
        cb_code = master_rows[0]["cb_code"]
        term = conn.execute(
            """
            SELECT conversion_price, issue_price, redemption_price, updated_at
            FROM cb_terms
            WHERE cb_code = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (cb_code,),
        ).fetchone()
        balance = conn.execute(
            """
            SELECT report_month, outstanding_amount
            FROM cb_balance_monthly
            WHERE cb_code = ?
            ORDER BY report_month DESC
            LIMIT 1
            """,
            (cb_code,),
        ).fetchone()
        return {
            "bonds": [dict(row) for row in master_rows],
            "term": dict(term) if term else None,
            "balance": dict(balance) if balance else None,
        }
    finally:
        conn.close()


def _load_recent_local_news(code: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT a.published_at, a.title, a.url, a.source
            FROM news_articles a
            JOIN news_entity_map m ON m.article_id = a.id
            WHERE m.entity_code = ?
            ORDER BY a.published_at DESC
            LIMIT 6
            """,
            (code,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _query_stock_name_from_db(code: str, db_path: Path = DB_PATH) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT stock_name FROM stock_master WHERE stock_code = ? LIMIT 1", (code,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _guess_context(code: str) -> StockContext:
    valuation = _latest_twse_valuation_row(code)
    revenue = _latest_twse_revenue_row(code)
    if valuation or revenue:
        return StockContext(
            code=code,
            name=str((valuation or {}).get("Name") or (revenue or {}).get("公司名稱") or code).strip(),
            market="TWSE",
            industry=str((revenue or {}).get("產業別") or "未分類").strip(),
            symbol=f"{code}.TW",
        )
    symbol, market = _resolve_yahoo_symbol(code)
    return StockContext(code=code, name=code, market=market, industry="未分類", symbol=symbol)


def _technical_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close_price"] for row in rows if row.get("close_price") is not None]
    highs = [row["high_price"] for row in rows if row.get("high_price") is not None]
    lows = [row["low_price"] for row in rows if row.get("low_price") is not None]
    volumes = [row["volume"] for row in rows if row.get("volume") is not None]
    latest = closes[-1] if closes else None
    prev = closes[-2] if len(closes) >= 2 else None
    ma20 = _safe_mean(closes[-20:]) if len(closes) >= 20 else None
    ma60 = _safe_mean(closes[-60:]) if len(closes) >= 60 else None
    rsi14 = _compute_rsi(closes, 14)
    macd_hist = _compute_macd_hist(closes)
    bias_score = 0
    if latest is not None and ma20 is not None and latest > ma20:
        bias_score += 1
    if ma20 is not None and ma60 is not None and ma20 > ma60:
        bias_score += 1
    if rsi14 is not None and rsi14 >= 55:
        bias_score += 1
    if macd_hist is not None and macd_hist > 0:
        bias_score += 1
    bias = "bullish" if bias_score >= 3 else "neutral" if bias_score >= 1 else "bearish"
    return {
        "latest_close": latest,
        "prev_close": prev,
        "change_pct": None if latest is None or prev in {None, 0} else ((latest / prev) - 1.0) * 100.0,
        "high_52w": max(highs) if highs else None,
        "low_52w": min(lows) if lows else None,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "macd_hist": macd_hist,
        "avg_volume_20d": _safe_mean(volumes[-20:]) if len(volumes) >= 20 else None,
        "latest_volume": volumes[-1] if volumes else None,
        "bias": bias,
        "latest_trade_date": rows[-1]["trade_date"] if rows else None,
    }


def _same_type_peers(context: StockContext, revenue_row: dict[str, Any] | None) -> list[dict[str, str]]:
    if context.market != "TWSE" or revenue_row is None:
        return []
    industry = str(revenue_row.get("產業別") or "").strip()
    if not industry:
        return []
    peers: list[dict[str, str]] = []
    for row in _http_get_json(TWSE_REVENUE_URL):
        if str(row.get("產業別", "")).strip() != industry:
            continue
        code = str(row.get("公司代號", "")).strip()
        if not code or code == context.code:
            continue
        peers.append({"code": code, "name": str(row.get("公司名稱", "")).strip()})
        if len(peers) >= 5:
            break
    return peers


def _peer_strength(peers: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for peer in peers:
        history = _fetch_yahoo_history(f"{peer['code']}.TW", "6mo")
        closes = [row["close_price"] for row in history if row.get("close_price") is not None]
        if len(closes) < 12:
            continue
        latest = closes[-1]
        ma10 = _safe_mean(closes[-10:])
        dev10 = None if ma10 in {None, 0} else ((latest / ma10) - 1.0) * 100.0
        ret20 = None if len(closes) < 21 else ((latest / closes[-21]) - 1.0) * 100.0
        ret60 = None if len(closes) < 61 else ((latest / closes[-61]) - 1.0) * 100.0
        score = 0
        if dev10 is not None and dev10 > 0:
            score += 2
        if ret20 is not None and ret20 > 0:
            score += 1
        if ret60 is not None and ret60 > 0:
            score += 1
        rows.append(
            {
                "code": peer["code"],
                "name": peer["name"],
                "latest_close": latest,
                "ma10": ma10,
                "dev10_pct": dev10,
                "ret20_pct": ret20,
                "ret60_pct": ret60,
                "score": score,
                "bias": "strong_up" if score >= 3 else "up" if score >= 1 else "down",
            }
        )
    rows.sort(key=lambda item: item.get("dev10_pct") or -9999, reverse=True)
    return rows


def _us_leader_strength(industry: str) -> list[dict[str, Any]]:
    tickers: list[str] = []
    for keyword, mapped in US_LEADERS_BY_INDUSTRY:
        if keyword in industry:
            tickers = mapped
            break
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        history = _fetch_yahoo_history(ticker, "6mo")
        closes = [row["close_price"] for row in history if row.get("close_price") is not None]
        if len(closes) < 12:
            continue
        latest = closes[-1]
        ma10 = _safe_mean(closes[-10:])
        dev10 = None if ma10 in {None, 0} else ((latest / ma10) - 1.0) * 100.0
        ret20 = None if len(closes) < 21 else ((latest / closes[-21]) - 1.0) * 100.0
        ret60 = None if len(closes) < 61 else ((latest / closes[-61]) - 1.0) * 100.0
        score = 0
        if dev10 is not None and dev10 > 0:
            score += 2
        if ret20 is not None and ret20 > 0:
            score += 1
        if ret60 is not None and ret60 > 0:
            score += 1
        rows.append(
            {
                "ticker": ticker,
                "latest_close": latest,
                "dev10_pct": dev10,
                "ret20_pct": ret20,
                "ret60_pct": ret60,
                "score": score,
            }
        )
    return rows


def _broker_signals(code: str, name: str) -> list[dict[str, Any]]:
    items = _google_news(f"{code} {name} 目標價", limit=10)
    rows: list[dict[str, Any]] = []
    for item in items:
        title = item["title"]
        if not any(keyword in title for keyword in ["目標價", "評等", "券商", "投顧"]):
            continue
        rows.append(
            {
                "title": title,
                "link": item["link"],
                "published_at": item["published_at"],
                "targets": re.findall(r"(\d{2,4}(?:\.\d+)?)", title)[:3],
            }
        )
        if len(rows) >= 6:
            break
    return rows


def _market_news(code: str, name: str) -> list[dict[str, Any]]:
    return _load_recent_local_news(code) or _google_news(f"{code} {name} 台股", limit=6)


def _supply_chain_view(industry: str, revenue_row: dict[str, Any] | None) -> dict[str, Any]:
    out = {
        "upstream": [],
        "downstream": [],
        "drivers": [],
        "risks": [],
        "demand_bias": "neutral",
        "reasoning": [],
    }
    for keyword, item in SUPPLY_CHAIN_HINTS:
        if keyword in industry:
            out.update(item)
            break
    yoy = _to_float((revenue_row or {}).get("營業收入-去年同月增減(%)"))
    mom = _to_float((revenue_row or {}).get("營業收入-上月比較增減(%)"))
    ytd = _to_float((revenue_row or {}).get("累計營業收入-前期比較增減(%)"))
    score = 0
    if yoy is not None:
        out["reasoning"].append(f"年增 {_fmt_pct(yoy)}")
        score += 1 if yoy > 10 else -1 if yoy < 0 else 0
    if mom is not None:
        out["reasoning"].append(f"月增 {_fmt_pct(mom)}")
        score += 1 if mom > 5 else -1 if mom < 0 else 0
    if ytd is not None:
        out["reasoning"].append(f"累計年增 {_fmt_pct(ytd)}")
        score += 1 if ytd > 5 else -1 if ytd < 0 else 0
    out["demand_bias"] = "strong_up" if score >= 2 else "up" if score == 1 else "strong_down" if score <= -2 else "down" if score == -1 else "neutral"
    return out


def analyze(code: str, mode: str = "deep") -> dict[str, Any]:
    code = code.strip()
    if not code:
        raise ValueError("stock code is required")

    context = _guess_context(code)
    db_name = _query_stock_name_from_db(code)
    if db_name:
        context.name = db_name

    price_rows = _fetch_twse_history(code, 12) if context.market == "TWSE" else []
    if len(price_rows) < 40:
        price_rows = _fetch_yahoo_history(context.symbol, "1y")
    if len(price_rows) < 40:
        raise RuntimeError(f"{code} 無法取得足夠的價格資料")

    technical = _technical_summary(price_rows)
    valuation_row = _latest_twse_valuation_row(code) if context.market == "TWSE" else None
    revenue_row = _latest_twse_revenue_row(code) if context.market == "TWSE" else None
    inst_row = _latest_twse_inst_row(code) if context.market == "TWSE" else None
    news_rows = _market_news(code, context.name)
    broker_rows = _broker_signals(code, context.name)
    peer_rows = _peer_strength(_same_type_peers(context, revenue_row))
    us_rows = _us_leader_strength(context.industry)
    cb_note = _load_cb_note(code)
    supply_chain = _supply_chain_view(context.industry, revenue_row)

    latest_pe = _to_float((valuation_row or {}).get("PEratio"))
    latest_pb = _to_float((valuation_row or {}).get("PBratio"))
    latest_yield = _to_float((valuation_row or {}).get("DividendYield"))
    latest_revenue = _to_float((revenue_row or {}).get("營業收入-當月營收"))
    latest_mom = _to_float((revenue_row or {}).get("營業收入-上月比較增減(%)"))
    latest_yoy = _to_float((revenue_row or {}).get("營業收入-去年同月增減(%)"))
    latest_ytd_yoy = _to_float((revenue_row or {}).get("累計營業收入-前期比較增減(%)"))

    fragments = ["技術面偏強" if technical["bias"] == "bullish" else "技術面偏弱" if technical["bias"] == "bearish" else "技術面中性"]
    if latest_yoy is not None:
        fragments.append("營收動能偏正向" if latest_yoy > 10 else "營收動能仍待修復" if latest_yoy < 0 else "營收動能中性")
    if latest_pb is not None:
        fragments.append("評價不算昂貴" if latest_pb <= 1.3 else "評價已不低")
    fragments.append("目前未見明確 CB 壓力" if not cb_note else "需留意可轉債稀釋")

    result = {
        "generated_at": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "context": context,
        "technical": technical,
        "valuation": {
            "date": _roc_numeric_to_ad((valuation_row or {}).get("Date", "")) if valuation_row else None,
            "pe": latest_pe,
            "pb": latest_pb,
            "yield_pct": latest_yield,
        },
        "revenue": {
            "month": str((revenue_row or {}).get("資料年月", "")).strip() if revenue_row else None,
            "industry": str((revenue_row or {}).get("產業別", "")).strip() if revenue_row else None,
            "value": latest_revenue,
            "mom_pct": latest_mom,
            "yoy_pct": latest_yoy,
            "ytd_yoy_pct": latest_ytd_yoy,
            "date": _roc_numeric_to_ad((revenue_row or {}).get("出表日期", "")) if revenue_row else None,
        },
        "chip_flow": inst_row,
        "news": news_rows,
        "broker": broker_rows,
        "peer_strength": peer_rows,
        "us_leaders": us_rows,
        "supply_chain": supply_chain,
        "cb_note": cb_note,
        "one_liner": "、".join(fragments[:4]) + "。",
    }
    result["report_text"] = build_report(result)
    return result


def _section(title: str, lines: list[str]) -> list[str]:
    return [title, *(lines or ["- N/A"]), ""]


def build_report(result: dict[str, Any]) -> str:
    context: StockContext = result["context"]
    technical = result["technical"]
    valuation = result["valuation"]
    revenue = result["revenue"]
    chip = result["chip_flow"] or {}
    news_rows = result["news"] or []
    broker = result["broker"] or []
    peers = result["peer_strength"] or []
    us_rows = result["us_leaders"] or []
    supply = result["supply_chain"] or {}
    cb_note = result["cb_note"]

    latest = technical["latest_close"]
    high_52w = technical["high_52w"]
    low_52w = technical["low_52w"]
    dist_high = None if latest is None or high_52w in {None, 0} else ((latest / high_52w) - 1.0) * 100.0
    dist_low = None if latest is None or low_52w in {None, 0} else ((latest / low_52w) - 1.0) * 100.0

    lines: list[str] = [
        f"# {context.code} {context.name} 深度研究報告",
        "",
        f"更新時間：{result['generated_at']}（Asia/Taipei）",
        "",
        "## 一句話結論",
        f"**{result['one_liner']}**",
        "",
    ]

    lines.extend(
        _section(
            "## 1. 公司定位與市場認知",
            [
                f"- 公司：{context.name}（{context.code}）",
                f"- 市場：{context.market}",
                f"- 產業：{context.industry or '未分類'}",
                "- 市場通常先交易價格結構、營收動能與事件面，再決定是否願意上修評價。",
            ],
        )
    )
    lines.extend(
        _section(
            "## 2. 股價位階與技術面判讀",
            [
                f"- 最新收盤：{_fmt_num(latest)}",
                f"- 前一日漲跌幅：{_fmt_pct(technical['change_pct'])}",
                f"- 52 週高 / 低：{_fmt_num(high_52w)} / {_fmt_num(low_52w)}",
                f"- 距 52 週高點：{_fmt_pct(dist_high)}",
                f"- 距 52 週低點：{_fmt_pct(dist_low)}",
                f"- SMA20 / SMA60：{_fmt_num(technical['ma20'])} / {_fmt_num(technical['ma60'])}",
                f"- RSI14 / MACD Histogram：{_fmt_num(technical['rsi14'])} / {_fmt_num(technical['macd_hist'], 3)}",
                f"- 20 日均量 / 最新量：{_fmt_num(technical['avg_volume_20d'], 0)} / {_fmt_num(technical['latest_volume'], 0)}",
                f"- Pattern DB Bias（近似判定）：{technical['bias']}",
            ],
        )
    )
    lines.extend(
        _section(
            "## 3. 基本面與營收品質分析",
            [
                f"- 本益比 PE：{_fmt_num(valuation['pe'])}",
                f"- 股價淨值比 PB：{_fmt_num(valuation['pb'])}",
                f"- 殖利率：{_fmt_num(valuation['yield_pct'])}%",
                f"- 最新月營收：{_fmt_num(revenue['value'], 0)}",
                f"- 月增 / 年增 / 累計年增：{_fmt_pct(revenue['mom_pct'])} / {_fmt_pct(revenue['yoy_pct'])} / {_fmt_pct(revenue['ytd_yoy_pct'])}",
                f"- 官方產業別：{revenue['industry'] or context.industry}",
            ],
        )
    )
    lines.extend(_section("## 4. 成長主軸與題材追蹤", [f"- {row.get('title', '').strip()}" for row in news_rows[:4]]))

    risk_lines: list[str] = []
    if cb_note:
        bond = cb_note["bonds"][0]
        risk_lines.append(f"- 可轉債：{bond['cb_code']} {bond['cb_name']}，狀態 {bond['status']}")
        if cb_note.get("term"):
            risk_lines.append(f"- 轉換價：{_fmt_num(cb_note['term'].get('conversion_price'))}")
        if cb_note.get("balance"):
            risk_lines.append(
                f"- 最新餘額（月）：{cb_note['balance'].get('report_month')} / {_fmt_num(cb_note['balance'].get('outstanding_amount'), 0)}"
            )
    else:
        risk_lines.append("- 可轉債（CB）狀態：目前未見明確流通中的 CB 資料。")
    if chip:
        risk_lines.append(
            f"- 三大法人：{chip.get('trade_date')} / 外資 {_fmt_num(chip.get('foreign_net'), 0)} / 投信 {_fmt_num(chip.get('investment_trust_net'), 0)} / 自營商 {_fmt_num(chip.get('dealer_net'), 0)} / 合計 {_fmt_num(chip.get('total_net'), 0)}"
        )
    else:
        risk_lines.append("- 籌碼面：本次未抓到官方三大法人明細，需改以量價結構補判。")
    risk_lines.append("- 主要風險仍在基本面是否跟上股價、題材是否延續，以及外部市場風險傳導。")
    lines.extend(_section("## 5. 籌碼與潛在風險 (含 CB 狀態)", risk_lines))

    lines.extend(
        [
            "## 6. 綜合結論與操作建議 (決策表)",
            "| 面向 | 判斷 | 簡要說明 |",
            "|---|---|---|",
            f"| 技術面 | {'正向' if technical['bias']=='bullish' else '偏弱' if technical['bias']=='bearish' else '中性'} | 現價相對均線與動能指標的組合目前偏向 {technical['bias']}。 |",
            f"| 營收動能 | {'正向' if (revenue['yoy_pct'] or -999) > 10 else '偏弱' if (revenue['yoy_pct'] or 0) < 0 else '中性'} | 最近月營收年增 {_fmt_pct(revenue['yoy_pct'])}、月增 {_fmt_pct(revenue['mom_pct'])}，後續仍要看月報延續。 |",
            f"| 估值 | {'中性偏低' if (valuation['pb'] or 999) <= 1.3 else '中性' if valuation['pb'] is not None else '待確認'} | PB {_fmt_num(valuation['pb'])}、PE {_fmt_num(valuation['pe'])}，評價不是無條件便宜，但也未必已經過熱。 |",
            f"| 事件面 | {'正向' if news_rows else '中性'} | 近期市場關注點主要圍繞價格結構、產業景氣與事件催化。 |",
            "",
            "### 投資人分級操作劇本",
            f"- 保守型：等 {_fmt_num(technical['ma20'])} 附近支撐與下一次月營收方向一起確認。",
            "- 平衡型：把它視為價格結構與基本面修復是否同步的觀察股，分批而不是追價。",
            "- 積極型：只在技術偏多且題材延續時參與，失守中期均線就要重新評估。",
            "",
        ]
    )

    peer_lines: list[str] = []
    if peers:
        leader = peers[0]
        peer_lines.append(f"- 台股同產業 10MA 乖離領先股：{leader['code']} {leader['name']} / {_fmt_pct(leader['dev10_pct'])}")
        for row in peers[:4]:
            peer_lines.append(
                f"- {row['code']} {row['name']}：10MA 乖離 {_fmt_pct(row['dev10_pct'])} / 20D {_fmt_pct(row['ret20_pct'])} / 60D {_fmt_pct(row['ret60_pct'])}"
            )
    else:
        peer_lines.append("- 同類股映射不足，這版先跳過完整 peer ranking。")
    if us_rows:
        peer_lines.append("- US leaders snapshot:")
        for row in us_rows[:4]:
            peer_lines.append(
                f"- {row['ticker']}：10MA 乖離 {_fmt_pct(row['dev10_pct'])} / 20D {_fmt_pct(row['ret20_pct'])} / 60D {_fmt_pct(row['ret60_pct'])}"
            )
    lines.extend(_section("## 7. 同類股強弱與供應鏈需求補充", peer_lines))
    lines.extend(
        _section(
            "## 8. 新聞與券商觀點補充",
            [
                f"- {row['title']}" + (f" | 目標價片段: {', '.join(row['targets'])}" if row["targets"] else "")
                for row in broker[:4]
            ],
        )
    )
    lines.extend(
        _section(
            "## 9. 供應鏈與短期需求",
            [
                f"- Demand bias：{supply.get('demand_bias', 'neutral')}",
                f"- Upstream：{', '.join(supply.get('upstream') or ['N/A'])}",
                f"- Downstream：{', '.join(supply.get('downstream') or ['N/A'])}",
                f"- Drivers：{', '.join(supply.get('drivers') or ['N/A'])}",
                f"- Risks：{', '.join(supply.get('risks') or ['N/A'])}",
                f"- 判定依據：{' / '.join(supply.get('reasoning') or ['N/A'])}",
            ],
        )
    )
    lines.extend(
        _section(
            "## 10. Evidence List",
            [
                f"- Yahoo Finance chart：{context.symbol} / 抓取時間 {result['generated_at']}",
                f"- TWSE valuation（listed only）：{valuation.get('date') or 'N/A'}",
                f"- TWSE monthly revenue（listed only）：{revenue.get('date') or 'N/A'}",
                f"- TWSE T86（三大法人，listed only）：{chip.get('trade_date') if chip else 'N/A'}",
                "- Google News RSS：與報告生成時間同步抓取",
                "- 本地 CB DB：cb_tool.db（若無資料則顯示無明確 CB）",
            ],
        )
    )
    return "\n".join(lines).strip() + "\n"


def save_report(result: dict[str, Any], report_dir: Path | None = None) -> Path:
    target_dir = report_dir or DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{result['context'].code}.MD"
    path.write_text(result["report_text"], encoding="utf-8")
    return path
