from pathlib import Path
import pandas as pd
import numpy as np

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

HISTORY_FILE = BASE_DIR / "TOP5_V2_HISTORY.csv"
CACHE_DIR = BASE_DIR / "stock_cache"

OUTPUT_FILE = BASE_DIR / "TOP5_V2_FORWARD_RESULTS.csv"
SUMMARY_FILE = BASE_DIR / "TOP5_V2_FORWARD_SUMMARY.csv"

BUY_FEE = 0.0015
SELL_FEE = 0.0015
SELL_TAX = 0.0010
SLIPPAGE = 0.0010

HOLD_SESSION = 20


# ============================================================
# LOAD PRICE
# ============================================================

def load_price(symbol):

    path = CACHE_DIR / f"{symbol}.csv"

    if not path.exists():
        return None

    df = pd.read_csv(path)

    required = ["time", "open", "close"]

    if not all(c in df.columns for c in required):
        return None

    df["date"] = pd.to_datetime(
        df["time"],
        errors="coerce"
    ).dt.normalize()

    df["open"] = pd.to_numeric(
        df["open"],
        errors="coerce"
    )

    df["close"] = pd.to_numeric(
        df["close"],
        errors="coerce"
    )

    df = (
        df.dropna(subset=["date", "open", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# EVALUATE ONE SIGNAL
# ============================================================

def evaluate_signal(row, price):

    signal_date = pd.Timestamp(row["date"]).normalize()

    future = price[
        price["date"] > signal_date
    ].copy()

    # Chua co phien T+1
    if len(future) < 1:
        return {
            "ENTRY_DATE": pd.NaT,
            "ENTRY_OPEN": np.nan,
            "EXIT_DATE": pd.NaT,
            "EXIT_CLOSE": np.nan,
            "GROSS_RET": np.nan,
            "NET_RET": np.nan,
            "STATUS": "WAIT_T1",
        }

    entry = future.iloc[0]

    entry_date = entry["date"]
    entry_open = entry["open"]

    # T+20 theo logic backtest V2:
    # Signal T
    # Entry = Open cua phien ke tiep
    # Exit = Close cua phien thu 20 sau ngay signal
    if len(future) < HOLD_SESSION:
        return {
            "ENTRY_DATE": entry_date,
            "ENTRY_OPEN": entry_open,
            "EXIT_DATE": pd.NaT,
            "EXIT_CLOSE": np.nan,
            "GROSS_RET": np.nan,
            "NET_RET": np.nan,
            "STATUS": "OPEN",
        }

    exit_row = future.iloc[HOLD_SESSION - 1]

    exit_date = exit_row["date"]
    exit_close = exit_row["close"]

    if (
        pd.isna(entry_open)
        or pd.isna(exit_close)
        or entry_open <= 0
        or exit_close <= 0
    ):
        return {
            "ENTRY_DATE": entry_date,
            "ENTRY_OPEN": entry_open,
            "EXIT_DATE": exit_date,
            "EXIT_CLOSE": exit_close,
            "GROSS_RET": np.nan,
            "NET_RET": np.nan,
            "STATUS": "INVALID_PRICE",
        }

    # --------------------------------------------------------
    # EXECUTION ASSUMPTIONS
    # --------------------------------------------------------

    buy_price = entry_open * (1 + SLIPPAGE)
    sell_price = exit_close * (1 - SLIPPAGE)

    gross_ret = exit_close / entry_open - 1

    buy_cost = buy_price * (1 + BUY_FEE)

    sell_value = sell_price * (
        1 - SELL_FEE - SELL_TAX
    )

    net_ret = sell_value / buy_cost - 1

    return {
        "ENTRY_DATE": entry_date,
        "ENTRY_OPEN": entry_open,
        "EXIT_DATE": exit_date,
        "EXIT_CLOSE": exit_close,
        "GROSS_RET": gross_ret,
        "NET_RET": net_ret,
        "STATUS": "CLOSED",
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)
    print("TOP5 V2 - FORWARD PAPER EVALUATION")
    print("=" * 72)

    if not HISTORY_FILE.exists():
        raise FileNotFoundError(
            f"Khong tim thay {HISTORY_FILE.name}"
        )

    history = pd.read_csv(HISTORY_FILE)

    if history.empty:
        raise RuntimeError("History dang rong.")

    required = ["date", "symbol", "RANK"]

    missing = [
        c for c in required
        if c not in history.columns
    ]

    if missing:
        raise RuntimeError(
            f"History thieu cot: {missing}"
        )

    history["date"] = pd.to_datetime(
        history["date"],
        errors="coerce"
    ).dt.normalize()

    history["symbol"] = (
        history["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    history = history.dropna(
        subset=["date", "symbol"]
    )

    history = (
        history
        .sort_values(["date", "RANK"])
        .drop_duplicates(
            ["date", "symbol"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    print(f"Signal days: {history['date'].nunique()}")
    print(f"Signals: {len(history)}")

    results = []

    price_cache = {}

    for _, row in history.iterrows():

        symbol = row["symbol"]

        if symbol not in price_cache:
            price_cache[symbol] = load_price(symbol)

        price = price_cache[symbol]

        base = row.to_dict()

        if price is None or price.empty:

            base.update({
                "ENTRY_DATE": pd.NaT,
                "ENTRY_OPEN": np.nan,
                "EXIT_DATE": pd.NaT,
                "EXIT_CLOSE": np.nan,
                "GROSS_RET": np.nan,
                "NET_RET": np.nan,
                "STATUS": "NO_PRICE",
            })

        else:

            evaluation = evaluate_signal(
                row,
                price
            )

            base.update(evaluation)

        results.append(base)

    result = pd.DataFrame(results)

    result = result.sort_values(
        ["date", "RANK"]
    ).reset_index(drop=True)

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    # ========================================================
    # SUMMARY CLOSED TRADES ONLY
    # ========================================================

    closed = result[
        result["STATUS"] == "CLOSED"
    ].copy()

    print()
    print("=" * 72)
    print("FORWARD STATUS")
    print("=" * 72)

    print(
        result["STATUS"]
        .value_counts(dropna=False)
        .to_string()
    )

    print()

    if closed.empty:

        print("Chua co tin hieu nao du T+20.")
        print("Chua tinh Win Rate / Average Return.")
        print("Day la binh thuong voi history moi.")

        summary = pd.DataFrame([{
            "CLOSED_TRADES": 0,
            "AVG_GROSS_RET": np.nan,
            "AVG_NET_RET": np.nan,
            "MEDIAN_NET_RET": np.nan,
            "WIN_RATE": np.nan,
        }])

    else:

        avg_gross = closed["GROSS_RET"].mean()
        avg_net = closed["NET_RET"].mean()
        median_net = closed["NET_RET"].median()
        win_rate = (
            closed["NET_RET"] > 0
        ).mean()

        print(f"Closed trades: {len(closed)}")
        print(f"Avg Gross Return: {avg_gross:.2%}")
        print(f"Avg Net Return: {avg_net:.2%}")
        print(f"Median Net Return: {median_net:.2%}")
        print(f"Win Rate: {win_rate:.2%}")

        summary = pd.DataFrame([{
            "CLOSED_TRADES": len(closed),
            "AVG_GROSS_RET": avg_gross,
            "AVG_NET_RET": avg_net,
            "MEDIAN_NET_RET": median_net,
            "WIN_RATE": win_rate,
        }])

    summary.to_csv(
        SUMMARY_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print()
    print(f"Results: {OUTPUT_FILE.name}")
    print(f"Summary: {SUMMARY_FILE.name}")

    print()
    print("=" * 72)
    print("FIXED PAPER RULES")
    print("=" * 72)

    print("Signal: Frozen V2 Top5")
    print("Entry: Open T+1")
    print("Exit: Close T+20")
    print(f"Buy fee: {BUY_FEE:.2%}")
    print(f"Sell fee: {SELL_FEE:.2%}")
    print(f"Sell tax: {SELL_TAX:.2%}")
    print(f"Slippage each side: {SLIPPAGE:.2%}")

    print()
    print("Model retrained: NO")
    print("Forward result used to tune model: NO")
    print("Mode: PAPER TRACKING")

    print("=" * 72)


if __name__ == "__main__":
    main()