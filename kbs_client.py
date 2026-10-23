"""KBS public API client for TOP5 stock data."""

from datetime import datetime

import pandas as pd
import requests


_IIS_BASE_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
_SEARCH_URL = f"{_IIS_BASE_URL}/stock/search/data"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,vi-VN;q=0.8,vi;q=0.7",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
}


def _get(url, params=None, timeout=30):
    r = requests.get(
        url,
        headers=_HEADERS,
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def danh_sach_co_phieu():
    """Lay danh sach co phieu thuong tu KBS."""
    data = _get(_SEARCH_URL)

    stocks = data if isinstance(data, list) else data.get("data", [])

    df = pd.DataFrame(stocks)

    if df.empty:
        return []

    df = df[df["type"] == "stock"]

    return df["symbol"].tolist()


def lich_su_gia(symbol, start, end):
    """
    Lay lich su OHLCV theo ngay tu KBS.

    start/end: YYYY-MM-DD
    """

    def fmt(d):
        return datetime.strptime(
            d,
            "%Y-%m-%d"
        ).strftime("%d-%m-%Y")

    url = f"{_IIS_BASE_URL}/stocks/{symbol}/data_day"

    params = {
        "sdate": fmt(start),
        "edate": fmt(end),
    }

    data = _get(url, params=params)

    ohlc = data.get("data_day")

    if not ohlc:
        return None

    df = pd.DataFrame(ohlc)

    df = df.rename(
        columns={
            "t": "time",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        }
    )

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
    )

    df = (
        df
        .dropna(subset=["time"])
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        ) / 1000

    df["volume"] = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    df["volume"] = df["volume"].astype(int)

    return df

def gia_hien_tai(symbols):
    """Lay snapshot gia hien tai tu bang gia KBS /stock/iss.

    Tra ve DataFrame: symbol, price, reference, open, high, low, volume.
    Price duoc chuan hoa ve nghin dong/co phieu, cung don vi voi stock_cache.
    """
    if isinstance(symbols, str):
        symbols = [symbols]

    symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not symbols:
        return pd.DataFrame()

    url = f"{_IIS_BASE_URL}/stock/iss"
    headers = dict(_HEADERS)
    headers["x-lang"] = "vi"

    r = requests.post(
        url,
        headers=headers,
        json={"code": ",".join(symbols)},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    rows = data if isinstance(data, list) else data.get("data", [])
    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    rename = {
        "SB": "symbol",
        "CP": "price",
        "RE": "reference",
        "OP": "open",
        "HI": "high",
        "LO": "low",
        "TT": "volume",
    }
    out = out.rename(columns=rename)

    if "symbol" not in out.columns or "price" not in out.columns:
        return pd.DataFrame()

    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()

    for col in ["price", "reference", "open", "high", "low"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") / 1000.0

    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce")

    return out
