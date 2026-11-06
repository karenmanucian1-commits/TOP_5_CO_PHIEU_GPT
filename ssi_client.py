"""SSI FastConnect V3 OHLCV in the scanner's price and time units."""

import os
from functools import lru_cache

import pandas as pd
from ssi_sdk import Auth, Config, Data

COLUMNS = ["time", "open", "high", "low", "close", "volume"]


@lru_cache(maxsize=1)
def market():
    auth = Auth(Config(api_key=os.environ["SSI_API_KEY"],
                       api_secret=os.environ["SSI_API_SECRET"]))
    auth.authenticate()
    return Data(auth).market_data


def candles(symbol, start, end, *, morning=False):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    api = market()
    fetch = (api.get_ohlc_1minute_historical if morning
             else api.get_ohlc_1day_historical)
    rows = []
    page_size = 500
    for page in range(1, 101):
        bars = fetch(symbol=symbol, from_date=start.strftime("%Y/%m/%d %H:%M:%S"),
                     to_date=end.strftime("%Y/%m/%d %H:%M:%S"), page=page, size=page_size)
        rows.extend((b.trading_date, b.open_price, b.high_price, b.low_price,
                     b.close_price, b.volume) for b in bars)
        if len(bars) < page_size:
            break
    else:
        raise RuntimeError(f"SSI pagination did not finish for {symbol}")
    result = pd.DataFrame(rows, columns=COLUMNS)
    if result.empty:
        return result
    timestamps = pd.to_datetime(result["time"], format="mixed", errors="raise")
    if timestamps.dt.tz is not None:
        timestamps = timestamps.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
    result["time"] = timestamps if morning else timestamps.dt.normalize()
    for col in COLUMNS[1:]:
        result[col] = pd.to_numeric(result[col], errors="raise")
    if symbol.upper() != "VNINDEX":
        result[COLUMNS[1:5]] = result[COLUMNS[1:5]] / 1000
    return result.sort_values("time").drop_duplicates("time", keep="last")


def lich_su_gia(symbol, start, end):
    end = pd.Timestamp(end).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59)
    result = candles(symbol, start, end)
    return result[result["time"].between(pd.Timestamp(start).normalize(), end)]


def morning_bar(symbol, day):
    day = pd.Timestamp(day).normalize()
    start = day + pd.Timedelta(hours=9)
    end = day + pd.Timedelta(hours=11, minutes=30)
    data = candles(symbol, start, end, morning=True)
    data = data[data["time"].between(start, end)]
    if data.empty:
        return None
    return dict(open=data.iloc[0]["open"], high=data["high"].max(),
                low=data["low"].min(), close=data.iloc[-1]["close"],
                volume=data["volume"].sum())
