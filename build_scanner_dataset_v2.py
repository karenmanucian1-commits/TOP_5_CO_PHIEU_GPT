import os
from pathlib import Path
import numpy as np
import pandas as pd

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

UNIVERSE_FILE = BASE_DIR / "quality_universe.csv"
# Lượt 11:35 trỏ STOCK_CACHE_DIR=stock_cache_midday; mặc định là cache EOD
CACHE_DIR = Path(os.environ.get("STOCK_CACHE_DIR") or (BASE_DIR / "stock_cache"))

OUTPUT_FILE = BASE_DIR / "scanner_dataset_v2.csv"

MIN_VALUE20 = 1_000_000       # VCI: close nghin VND -> ~1 ty VND/ngay
MIN_HISTORY = 60

# 14 FEATURES V1
V1_FEATURES = [
    "RET1",
    "RET5",
    "RET10",
    "RET20",
    "RET60",
    "DIST_MA20",
    "DIST_MA50",
    "MA20_SLOPE5",
    "MA50_SLOPE10",
    "RSI14",
    "VOL_RATIO",
    "VOLATILITY20",
    "RANGE_POS20",
    "RANGE_POS60",
]

# 20 FEATURES V2
V2_FEATURES = [
    "MKT_RET1",
    "MKT_RET5",
    "MKT_RET20",
    "MKT_RET60",
    "MKT_VOL20",
    "BREADTH1",
    "BREADTH5",
    "BREADTH20",
    "MKT_RET20_MA20",
    "MKT_BREADTH20_MA20",
    "MKT_VOL20_MA20",
    "MKT_TREND_SCORE",
    "REL_RET5",
    "REL_RET10",
    "REL_RET20",
    "REL_RET60",
    "CS_RET5_RANK",
    "CS_RET20_RANK",
    "CS_RET60_RANK",
    "CS_VOLATILITY_RANK",
]

ALL_FEATURES = V1_FEATURES + V2_FEATURES


# ============================================================
# HELPERS
# ============================================================

def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    # Xu ly truong hop khong co loss
    result = result.where(avg_loss != 0, 100)

    return result


def load_universe():
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Khong tim thay: {UNIVERSE_FILE}")

    u = pd.read_csv(UNIVERSE_FILE)

    if "symbol" not in u.columns:
        raise ValueError("quality_universe.csv khong co cot symbol")

    u["symbol"] = (
        u["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    u = u.drop_duplicates("symbol")

    return u


def load_one_symbol(symbol, exchange=None):
    path = CACHE_DIR / f"{symbol}.csv"

    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    if not all(c in df.columns for c in required):
        return None

    df = df[required].copy()

    df["date"] = pd.to_datetime(
        df["time"],
        errors="coerce"
    ).dt.normalize()

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(
        subset=["date", "open", "high", "low", "close", "volume"]
    )

    df = (
        df.sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )

    if len(df) < MIN_HISTORY:
        return None

    df["symbol"] = symbol

    if exchange is not None:
        df["exchange"] = exchange

    return df


# ============================================================
# BASE FEATURES
# ============================================================

def add_base_features(df):
    df = df.copy()
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    g = df.groupby("symbol", group_keys=False)

    # Returns
    df["RET1"] = g["close"].pct_change(1)
    df["RET5"] = g["close"].pct_change(5)
    df["RET10"] = g["close"].pct_change(10)
    df["RET20"] = g["close"].pct_change(20)
    df["RET60"] = g["close"].pct_change(60)

    # Moving averages
    df["MA20"] = g["close"].transform(
        lambda x: x.rolling(20).mean()
    )

    df["MA50"] = g["close"].transform(
        lambda x: x.rolling(50).mean()
    )

    df["DIST_MA20"] = df["close"] / df["MA20"] - 1
    df["DIST_MA50"] = df["close"] / df["MA50"] - 1

    df["MA20_SLOPE5"] = (
        df.groupby("symbol")["MA20"].pct_change(5)
    )

    df["MA50_SLOPE10"] = (
        df.groupby("symbol")["MA50"].pct_change(10)
    )

    # RSI
    df["RSI14"] = (
        df.groupby("symbol")["close"]
        .transform(lambda x: rsi(x, 14))
    )

    # Volume ratio
    df["VOL_MA20"] = (
        df.groupby("symbol")["volume"]
        .transform(lambda x: x.rolling(20).mean())
    )

    df["VOL_RATIO"] = (
        df["volume"] /
        df["VOL_MA20"].replace(0, np.nan)
    )

    # Volatility
    df["VOLATILITY20"] = (
        df.groupby("symbol")["RET1"]
        .transform(lambda x: x.rolling(20).std())
    )

    # Range position
    low20 = (
        df.groupby("symbol")["low"]
        .transform(lambda x: x.rolling(20).min())
    )

    high20 = (
        df.groupby("symbol")["high"]
        .transform(lambda x: x.rolling(20).max())
    )

    low60 = (
        df.groupby("symbol")["low"]
        .transform(lambda x: x.rolling(60).min())
    )

    high60 = (
        df.groupby("symbol")["high"]
        .transform(lambda x: x.rolling(60).max())
    )

    df["RANGE_POS20"] = (
        (df["close"] - low20) /
        (high20 - low20).replace(0, np.nan)
    )

    df["RANGE_POS60"] = (
        (df["close"] - low60) /
        (high60 - low60).replace(0, np.nan)
    )

    # Historical liquidity
    # VCI close = nghin VND
    df["VALUE"] = df["close"] * df["volume"]

    df["VALUE20"] = (
        df.groupby("symbol")["VALUE"]
        .transform(lambda x: x.rolling(20).mean())
    )

    return df


# ============================================================
# MARKET / REGIME FEATURES
# ============================================================

def add_market_features(df):
    df = df.copy()

    # Cross-sectional market return by date
    market = (
        df.groupby("date")
        .agg(
            MKT_RET1=("RET1", "mean"),
            MKT_RET5=("RET5", "mean"),
            MKT_RET20=("RET20", "mean"),
            MKT_RET60=("RET60", "mean"),
            MKT_VOL20=("VOLATILITY20", "mean"),
        )
        .sort_index()
    )

    # Breadth = proportion stocks with positive return
    breadth1 = (
        df.assign(_x=(df["RET1"] > 0).astype(float))
        .groupby("date")["_x"]
        .mean()
    )

    breadth5 = (
        df.assign(_x=(df["RET5"] > 0).astype(float))
        .groupby("date")["_x"]
        .mean()
    )

    breadth20 = (
        df.assign(_x=(df["RET20"] > 0).astype(float))
        .groupby("date")["_x"]
        .mean()
    )

    market["BREADTH1"] = breadth1
    market["BREADTH5"] = breadth5
    market["BREADTH20"] = breadth20

    market["MKT_RET20_MA20"] = (
        market["MKT_RET20"].rolling(20).mean()
    )

    market["MKT_BREADTH20_MA20"] = (
        market["BREADTH20"].rolling(20).mean()
    )

    market["MKT_VOL20_MA20"] = (
        market["MKT_VOL20"].rolling(20).mean()
    )

    # Same formula used by V2 research:
    # trend strength from medium-term market return + breadth
    market["MKT_TREND_SCORE"] = (
        market["MKT_RET20_MA20"]
        + market["MKT_BREADTH20_MA20"]
        - 0.5
    )

    market = market.reset_index()

    df = df.merge(
        market,
        on="date",
        how="left"
    )

    # Relative strength
    df["REL_RET5"] = df["RET5"] - df["MKT_RET5"]
    df["REL_RET10"] = df["RET10"] - (
        df.groupby("date")["RET10"].transform("mean")
    )
    df["REL_RET20"] = df["RET20"] - df["MKT_RET20"]
    df["REL_RET60"] = df["RET60"] - df["MKT_RET60"]

    # Cross-sectional ranks
    df["CS_RET5_RANK"] = (
        df.groupby("date")["RET5"]
        .rank(pct=True)
    )

    df["CS_RET20_RANK"] = (
        df.groupby("date")["RET20"]
        .rank(pct=True)
    )

    df["CS_RET60_RANK"] = (
        df.groupby("date")["RET60"]
        .rank(pct=True)
    )

    df["CS_VOLATILITY_RANK"] = (
        df.groupby("date")["VOLATILITY20"]
        .rank(pct=True)
    )

    return df


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("BUILD SCANNER DATASET V2")
    print("=" * 70)

    universe = load_universe()

    print(f"Universe: {len(universe):,} symbols")

    frames = []
    missing = []

    for i, row in enumerate(universe.itertuples(index=False), start=1):

        symbol = str(row.symbol).upper()

        exchange = getattr(row, "exchange", None)

        df = load_one_symbol(symbol, exchange)

        if df is None:
            missing.append(symbol)
            print(
                f"[{i:04d}/{len(universe):04d}] "
                f"{symbol:<6} -> SKIP"
            )
            continue

        frames.append(df)

        print(
            f"[{i:04d}/{len(universe):04d}] "
            f"{symbol:<6} -> OK | "
            f"Rows={len(df):,} | "
            f"Last={df['date'].max().date()}"
        )

    if not frames:
        raise RuntimeError("Khong load duoc du lieu cache.")

    data = pd.concat(frames, ignore_index=True)

    data = (
        data.sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )

    print()
    print(f"Raw rows: {len(data):,}")
    print(f"Symbols loaded: {data['symbol'].nunique():,}")
    print(f"Date range: {data['date'].min().date()} -> {data['date'].max().date()}")

    # --------------------------------------------------------
    # 1. BASE FEATURES
    # --------------------------------------------------------

    print()
    print("Tinh 14 base features...")

    data = add_base_features(data)

    # --------------------------------------------------------
    # 2. DYNAMIC LIQUIDITY FILTER
    # --------------------------------------------------------

    before = len(data)

    data = data[
        (data["VALUE20"] >= MIN_VALUE20)
        & (data["volume"] > 0)
    ].copy()

    after = len(data)

    print(
        f"Dynamic liquidity: "
        f"{before:,} -> {after:,} rows "
        f"({after / before:.2%})"
    )

    # --------------------------------------------------------
    # 3. V2 FEATURES
    # --------------------------------------------------------

    print()
    print("Tinh 20 V2 market/regime/relative features...")

    data = add_market_features(data)

    # --------------------------------------------------------
    # 4. SAFETY / CLEANUP
    # --------------------------------------------------------

    data = (
        data.sort_values(["date", "symbol"])
        .drop_duplicates(["symbol", "date"], keep="last")
        .reset_index(drop=True)
    )

    missing_features = [
        c for c in ALL_FEATURES
        if c not in data.columns
    ]

    if missing_features:
        raise RuntimeError(
            f"Thieu feature: {missing_features}"
        )

    # Khong drop NaN toan bo lich su.
    # Frozen pipeline/model se xu ly missing theo pipeline da train.
    latest_date = data["date"].max()

    latest = data[data["date"] == latest_date]

    print()
    print("=" * 70)
    print("SCANNER DATASET SUMMARY")
    print("=" * 70)

    print(f"Rows: {len(data):,}")
    print(f"Symbols: {data['symbol'].nunique():,}")
    print(
        f"Date range: "
        f"{data['date'].min().date()} -> "
        f"{data['date'].max().date()}"
    )

    print(f"Latest market date: {latest_date.date()}")
    print(f"Eligible latest symbols: {len(latest):,}")

    print(f"V1 features: {len(V1_FEATURES)}")
    print(f"V2 features: {len(V2_FEATURES)}")
    print(f"Total model features: {len(ALL_FEATURES)}")

    print(f"Missing cache symbols: {len(missing)}")

    # --------------------------------------------------------
    # 5. SAVE
    # --------------------------------------------------------

    data.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print(f"DA TAO: {OUTPUT_FILE.name}")

    print()
    print("SAFETY:")
    print("- Khong retrain model.")
    print("- Khong sua TOP5_V2_FROZEN_MODEL.joblib.")
    print("- Khong ghi de training_dataset_v2.csv.")
    print("- Du lieu 2026 chi dung cho forward/paper scanner.")
    print("- Khong tao target tuong lai.")
    print("- Dynamic liquidity dung VALUE20 lich su.")

    print()
    print("BUILD SCANNER DATASET V2 HOAN TAT")
    print("=" * 70)


if __name__ == "__main__":
    main()