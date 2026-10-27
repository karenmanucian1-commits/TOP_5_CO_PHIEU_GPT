from pathlib import Path
from datetime import date, timedelta
import time
import shutil
import argparse
import pandas as pd
import kbs_client

# Secondary EOD source: only used when KBS has not published the latest completed bar yet.
try:
    from vnstock import Quote
except Exception:
    Quote = None

# ============================================================
# TOP5_AI - UPDATE SCANNER MARKET DATA
# PAPER / FORWARD DATA UPDATE ONLY
#
# IMPORTANT:
# - Chi cap nhat stock_cache
# - Khong train model
# - Khong sua frozen model
# - Khong xoa lich su cu
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

QUALITY_UNIVERSE_FILE = BASE_DIR / "quality_universe.csv"
FULL_UNIVERSE_FILE = BASE_DIR / "universe.csv"
CACHE_DIR = BASE_DIR / "stock_cache"
SUMMARY_FILE = BASE_DIR / "universe_liquidity.csv"

MODEL_FILE = BASE_DIR / "TOP5_V2_FROZEN_MODEL.joblib"

CACHE_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

END_DATE = date.today() + timedelta(days=1)

# Neu mot ma CHUA co cache thi khong tu dong tao
# cache ngan 500 ngay nua.
#
# Lay tu 2022 de dam bao du warm-up / feature history.
FALLBACK_START_DATE = date(2022, 1, 1)

# Lay lai mot khoang ngan de API co the sua/cap nhat
# cac phien gan nhat.
REFRESH_DAYS = 7

REQUEST_DELAY = 0.7

# Quality-universe rules agreed for production.
MIN_AVG_VOLUME_20 = 10_000
MIN_TRADED_DAYS_20 = 15
MAX_STALE_DAYS = 7

parser = argparse.ArgumentParser(
    description="Update TOP5 stock cache in daily or full-market mode."
)
parser.add_argument(
    "--full",
    action="store_true",
    help="Refresh full market and rebuild quality_universe.csv.",
)
args = parser.parse_args()

FULL_MODE = args.full
UNIVERSE_FILE = FULL_UNIVERSE_FILE if FULL_MODE else QUALITY_UNIVERSE_FILE
MODE_NAME = "FULL MARKET REFRESH" if FULL_MODE else "DAILY QUALITY UPDATE"

print("=" * 78)
print("TOP5_AI - UPDATE SCANNER DATA")
print(MODE_NAME)
print("=" * 78)

# ============================================================
# 1. SAFETY - FROZEN MODEL MUST EXIST
# ============================================================

if not MODEL_FILE.exists():

    raise FileNotFoundError(
        "Khong tim thay TOP5_V2_FROZEN_MODEL.joblib. "
        "Dung update va kiem tra lai frozen model."
    )

print("\nFrozen model found:")
print(MODEL_FILE.name)

print("\nMODEL POLICY:")
print("- Khong train model trong file nay.")
print("- Khong ghi de frozen model.")
print("- Chi cap nhat du lieu thi truong.")

# ============================================================
# 2. LOAD UNIVERSE
# ============================================================

if not UNIVERSE_FILE.exists():

    raise FileNotFoundError(
        f"Khong tim thay {UNIVERSE_FILE.name}"
    )

universe = pd.read_csv(
    UNIVERSE_FILE
)

required_cols = [
    "symbol",
    "exchange",
]

for col in required_cols:

    if col not in universe.columns:

        raise ValueError(
            f"{UNIVERSE_FILE.name} thieu cot: {col}"
        )

universe["symbol"] = (
    universe["symbol"]
    .astype(str)
    .str.strip()
    .str.upper()
)

universe["exchange"] = (
    universe["exchange"]
    .astype(str)
    .str.strip()
    .str.upper()
)

universe = (
    universe
    .drop_duplicates("symbol")
    .reset_index(drop=True)
)

print(
    f"\nMode: {MODE_NAME}"
)

print(
    f"Input universe: {UNIVERSE_FILE.name} | {len(universe):,} ma"
)

print(
    f"Update den: {END_DATE}"
)

# ============================================================
# 3. NORMALIZE HISTORY
# ============================================================

def normalize_history(df):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if "time" not in df.columns:
        return pd.DataFrame()

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce",
        utc=True
    )

    # Vnstock 5.x tra timestamp co timezone.
    # Chuyen ve tz-naive de tuong thich cache cu.
    df["time"] = (
        df["time"]
        .dt.tz_convert("Asia/Ho_Chi_Minh")
        .dt.tz_localize(None)
        .dt.normalize()
    )

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric_cols:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    if not all(
        col in df.columns
        for col in required
    ):
        return pd.DataFrame()

    df = df.dropna(
        subset=[
            "time",
            "close",
            "volume",
        ]
    )

    df = (
        df
        .sort_values("time")
        .drop_duplicates(
            "time",
            keep="last"
        )
        .reset_index(drop=True)
    )

    return df


# ============================================================
# 4. UPDATE ONE SYMBOL
# ============================================================

def update_symbol(symbol):

    cache_file = (
        CACHE_DIR / f"{symbol}.csv"
    )

    old = pd.DataFrame()

    # --------------------------------------------------------
    # LOAD EXISTING CACHE
    # --------------------------------------------------------

    if cache_file.exists():

        try:

            old = pd.read_csv(
                cache_file
            )

            old = normalize_history(
                old
            )

        except Exception as e:

            raise RuntimeError(
                f"Khong doc duoc cache cu: {e}"
            )

    # --------------------------------------------------------
    # DETERMINE FETCH START
    # --------------------------------------------------------

    if not old.empty:

        old_first = (
            old["time"]
            .min()
            .date()
        )

        old_last = (
            old["time"]
            .max()
            .date()
        )

        fetch_start = (
            old_last
            - timedelta(
                days=REFRESH_DAYS
            )
        )

    else:

        old_first = None
        old_last = None

        # IMPORTANT:
        # Khong lap lai loi 500 ngay.
        fetch_start = (
            FALLBACK_START_DATE
        )

    # --------------------------------------------------------
    # STRICT EOD DATA POLICY
    #
    # This updater feeds Frozen V2, so it accepts completed daily
    # bars only. Current-day partial candles are always excluded.
    # Midday prices are handled by midday_decision_top5.py.
    # --------------------------------------------------------

    today_ts = pd.Timestamp(date.today())

    # Clean any partial current-day candle left by an older updater.
    if not old.empty:
        old = old[old["time"] < today_ts].copy()

        if not old.empty:
            old_first = old["time"].min().date()
            old_last = old["time"].max().date()
            fetch_start = old_last - timedelta(days=REFRESH_DAYS)

    # --------------------------------------------------------
    # API - REFRESH KBS, KEEP COMPLETED EOD BARS ONLY
    # --------------------------------------------------------

    new = kbs_client.lich_su_gia(
        symbol=symbol,
        start=fetch_start.isoformat(),
        end=END_DATE.isoformat(),
    )

    new = normalize_history(new)

    if not new.empty:
        new = new[new["time"] < today_ts].copy()

    # KBS can lag one completed EOD session in the early morning.
    # If its newest bar is older than the previous weekday, retry via VCI.
    expected_eod = date.today() - timedelta(days=1)
    while expected_eod.weekday() >= 5:
        expected_eod -= timedelta(days=1)

    kbs_last = new["time"].max().date() if not new.empty else None
    if (kbs_last is None or kbs_last < expected_eod) and Quote is not None:
        try:
            vci = Quote(symbol=symbol, source="VCI").history(
                start=fetch_start.isoformat(),
                end=END_DATE.isoformat(),
                interval="1D",
            )
            vci = normalize_history(vci)
            if not vci.empty:
                vci = vci[vci["time"] < today_ts].copy()
                vci_last = vci["time"].max().date()
                if kbs_last is None or vci_last > kbs_last:
                    new = vci
                    print(f" [VCI fallback -> {vci_last}]", end="")
        except Exception as e:
            print(f" [VCI fallback error: {e}]", end="")

    # --------------------------------------------------------
    # IMPORTANT SAFETY
    #
    # API error / empty response must NOT destroy old cache.
    # --------------------------------------------------------

    if new.empty:

        if not old.empty:

            return {
                "data": old,
                "status": "NO_NEW_DATA",
                "old_first": old_first,
                "old_last": old_last,
                "new_last": old_last,
                "added_rows": 0,
            }

        return {
            "data": pd.DataFrame(),
            "status": "NO_DATA",
            "old_first": None,
            "old_last": None,
            "new_last": None,
            "added_rows": 0,
        }

    # --------------------------------------------------------
    # MERGE
    # --------------------------------------------------------

    if old.empty:

        combined = new.copy()

    else:

        combined = pd.concat(
            [
                old,
                new,
            ],
            ignore_index=True
        )

        combined = (
            combined
            .sort_values("time")
            .drop_duplicates(
                "time",
                keep="last"
            )
            .reset_index(drop=True)
        )

    # --------------------------------------------------------
    # SAFETY: HISTORY MUST NOT SHRINK
    # --------------------------------------------------------

    if (
        not old.empty
        and len(combined) < len(old)
    ):

        raise RuntimeError(
            "History shrink detected."
        )

    new_first = (
        combined["time"]
        .min()
        .date()
    )

    new_last = (
        combined["time"]
        .max()
        .date()
    )

    # Existing historical start must remain intact.
    if (
        old_first is not None
        and new_first > old_first
    ):

        raise RuntimeError(
            "Historical start date was lost."
        )

    added_rows = (
        len(combined) - len(old)
    )

    # --------------------------------------------------------
    # BACKUP ONLY THE FILE BEING REPLACED
    # --------------------------------------------------------

    if cache_file.exists():

        backup_file = (
            cache_file.with_suffix(
                ".csv.bak"
            )
        )

        shutil.copy2(
            cache_file,
            backup_file
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_df = combined.copy()

    save_df["time"] = (
        save_df["time"]
        .dt.strftime("%Y-%m-%d")
    )

    save_df.to_csv(
        cache_file,
        index=False,
        encoding="utf-8-sig"
    )

    return {
        "data": combined,
        "status": "UPDATED",
        "old_first": old_first,
        "old_last": old_last,
        "new_last": new_last,
        "added_rows": added_rows,
    }


# ============================================================
# 5. LIQUIDITY STATS
# ============================================================

def liquidity_stats(
    symbol,
    exchange,
    df
):

    if df.empty:
        return None

    d20 = df.tail(20)
    d60 = df.tail(60)

    latest = df.iloc[-1]

    # VCI close theo nghin dong.
    value20 = (
        d20["close"]
        * 1000
        * d20["volume"]
    )

    value60 = (
        d60["close"]
        * 1000
        * d60["volume"]
    )

    avg_value_20 = (
        value20.mean()
    )

    avg_value_60 = (
        value60.mean()
    )

    return {
        "symbol":
            symbol,

        "exchange":
            exchange,

        "latest_date":
            latest["time"].date(),

        "close":
            latest["close"],

        "history_rows":
            len(df),

        "avg_volume_20":
            d20["volume"].mean(),

        "avg_volume_60":
            d60["volume"].mean(),

        "avg_value_20_vnd":
            avg_value_20,

        "avg_value_60_vnd":
            avg_value_60,

        "avg_value_20_billion":
            avg_value_20
            / 1_000_000_000,

        "avg_value_60_billion":
            avg_value_60
            / 1_000_000_000,

        "traded_days_20":
            int(
                (d20["volume"] > 0)
                .sum()
            ),

        "traded_days_60":
            int(
                (d60["volume"] > 0)
                .sum()
            ),

        "zero_volume_20":
            int(
                (d20["volume"] <= 0)
                .sum()
            ),

        "zero_volume_60":
            int(
                (d60["volume"] <= 0)
                .sum()
            ),
    }


# ============================================================
# 5B. SAVE LIQUIDITY / REBUILD QUALITY UNIVERSE
# ============================================================

def save_liquidity_summary(current_results):
    """
    DAILY mode:
      merge refreshed quality-symbol stats into any existing summary.

    FULL mode:
      write a fresh full-market summary from the current full run.
    """
    if not current_results:
        return

    current = pd.DataFrame(current_results).copy()
    current["symbol"] = (
        current["symbol"].astype(str).str.strip().str.upper()
    )

    if FULL_MODE:
        summary = current
    else:
        if SUMMARY_FILE.exists():
            try:
                full = pd.read_csv(SUMMARY_FILE)
            except Exception:
                full = pd.DataFrame()
        else:
            full = pd.DataFrame()

        if not full.empty and "symbol" in full.columns:
            full["symbol"] = (
                full["symbol"].astype(str).str.strip().str.upper()
            )
            full = full[
                ~full["symbol"].isin(set(current["symbol"]))
            ].copy()
            summary = pd.concat(
                [full, current],
                ignore_index=True,
                sort=False,
            )
        else:
            summary = current

    if "avg_value_20_billion" in summary.columns:
        summary = (
            summary
            .sort_values(
                "avg_value_20_billion",
                ascending=False,
                na_position="last",
            )
            .reset_index(drop=True)
        )

    summary.to_csv(
        SUMMARY_FILE,
        index=False,
        encoding="utf-8-sig",
    )


def rebuild_quality_universe(full_results):
    """
    Rebuild the tracked quality_universe.csv after a FULL market refresh.

    Rules:
      - avg_volume_20 >= 10,000 shares/session
      - traded_days_20 >= 15/20
      - latest data not stale by more than 7 calendar days
    """
    if not FULL_MODE:
        return

    if not full_results:
        raise RuntimeError(
            "FULL refresh khong co liquidity result; "
            "khong ghi de quality_universe.csv."
        )

    full = pd.DataFrame(full_results).copy()

    full["latest_date"] = pd.to_datetime(
        full["latest_date"],
        errors="coerce",
    )
    full["avg_volume_20"] = pd.to_numeric(
        full["avg_volume_20"],
        errors="coerce",
    )
    full["traded_days_20"] = pd.to_numeric(
        full["traded_days_20"],
        errors="coerce",
    )

    # Use the newest completed market date observed in this full run.
    # This avoids wrongly penalizing the whole market for weekends/holidays.
    market_date = full["latest_date"].max()

    if pd.isna(market_date):
        raise RuntimeError(
            "Khong xac dinh duoc market_date de rebuild quality universe."
        )

    stale_days = (
        market_date.normalize()
        - full["latest_date"].dt.normalize()
    ).dt.days

    quality = full[
        (full["avg_volume_20"].fillna(0) >= MIN_AVG_VOLUME_20)
        & (full["traded_days_20"].fillna(0) >= MIN_TRADED_DAYS_20)
        & (stale_days.fillna(9999) <= MAX_STALE_DAYS)
    ].copy()

    quality["latest_date"] = (
        quality["latest_date"].dt.strftime("%Y-%m-%d")
    )

    # Keep stable output order for clean Git diffs.
    quality = (
        quality
        .sort_values(["exchange", "symbol"])
        .reset_index(drop=True)
    )

    if quality.empty:
        raise RuntimeError(
            "Quality universe rong sau FULL refresh; "
            "khong ghi de file production."
        )

    quality.to_csv(
        QUALITY_UNIVERSE_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 78)
    print("QUALITY UNIVERSE REBUILT")
    print("=" * 78)
    print(f"Full liquidity rows : {len(full):,}")
    print(f"Quality symbols     : {len(quality):,}")
    print(f"Market date         : {market_date.date()}")
    print(
        "Rules               : "
        f"avg_volume_20 >= {MIN_AVG_VOLUME_20:,}; "
        f"traded_days_20 >= {MIN_TRADED_DAYS_20}; "
        f"stale <= {MAX_STALE_DAYS} days"
    )


# ============================================================
# 6. RUN UPDATE
# ============================================================

results = []
update_report = []

total = len(universe)

updated_count = 0
no_new_count = 0
cache_count = 0
no_data_count = 0
error_count = 0
total_added_rows = 0

for position, row in enumerate(
    universe.itertuples(index=False),
    start=1
):

    symbol = str(row.symbol)
    exchange = str(row.exchange)

    print(
        f"\n[{position:04d}/{total:04d}] "
        f"{symbol:<6} {exchange:<6}",
        end=""
    )

    try:

        info = update_symbol(
            symbol
        )

        df = info["data"]
        status = info["status"]

        if status == "UPDATED":
            updated_count += 1

        elif status == "NO_NEW_DATA":
            no_new_count += 1

        elif status == "CACHE":
            cache_count += 1

        elif status == "NO_DATA":
            no_data_count += 1

        total_added_rows += (
            info["added_rows"]
        )

        if not df.empty:

            stats = liquidity_stats(
                symbol,
                exchange,
                df
            )

            if stats is not None:
                results.append(stats)

        update_report.append({
            "symbol":
                symbol,

            "exchange":
                exchange,

            "status":
                status,

            "old_first":
                info["old_first"],

            "old_last":
                info["old_last"],

            "new_last":
                info["new_last"],

            "added_rows":
                info["added_rows"],

            "history_rows":
                len(df),
        })

        if df.empty:

            print(
                f" -> {status}"
            )

        else:

            print(
                f" -> {status}"
                f" | Rows={len(df):,}"
                f" | Last="
                f"{info['new_last']}"
                f" | Added="
                f"{info['added_rows']:,}"
            )

    except Exception as e:

        error_count += 1

        update_report.append({
            "symbol":
                symbol,

            "exchange":
                exchange,

            "status":
                "ERROR",

            "old_first":
                None,

            "old_last":
                None,

            "new_last":
                None,

            "added_rows":
                0,

            "history_rows":
                None,

            "error":
                f"{type(e).__name__}: {e}",
        })

        print(
            f" -> ERROR: "
            f"{type(e).__name__}: {e}"
        )

    # --------------------------------------------------------
    # CHECKPOINT
    # --------------------------------------------------------

    if results and not FULL_MODE:
        save_liquidity_summary(results)

    pd.DataFrame(
        update_report
    ).to_csv(
        BASE_DIR
        / "scanner_update_report.csv",
        index=False,
        encoding="utf-8-sig"
    )

    time.sleep(
        REQUEST_DELAY
    )


# ============================================================
# 7. FINAL SAVE
# ============================================================

if results:
    save_liquidity_summary(results)

    if FULL_MODE:
        rebuild_quality_universe(results)


# ============================================================
# 8. FINAL REPORT
# ============================================================

print("\n" + "=" * 78)
print("UPDATE SUMMARY")
print("=" * 78)

print(
    f"Input universe : {total:,}"
)

print(
    f"UPDATED        : {updated_count:,}"
)

print(
    f"NO_NEW_DATA    : {no_new_count:,}"
)

print(
    f"CACHE          : {cache_count:,}"
)

print(
    f"NO_DATA        : {no_data_count:,}"
)

print(
    f"ERROR          : {error_count:,}"
)

print(
    f"New rows added : "
    f"{total_added_rows:,}"
)

if results:

    latest_dates = pd.to_datetime(
        pd.DataFrame(results)[
            "latest_date"
        ],
        errors="coerce"
    )

    print(
        f"Newest market date : "
        f"{latest_dates.max().date()}"
    )

    today = date.today()
    if today.weekday() == 0:
        expected_eod = today - timedelta(days=3)
    elif today.weekday() <= 4:
        expected_eod = today - timedelta(days=1)
    elif today.weekday() == 5:
        expected_eod = today - timedelta(days=1)
    else:
        expected_eod = today - timedelta(days=2)

    newest_market_date = latest_dates.max().date()
    print(f"Expected EOD date  : {expected_eod}")
    if newest_market_date < expected_eod:
        raise RuntimeError(
            f"STALE EOD DATA: newest={newest_market_date}, expected>={expected_eod}. "
            "Stop before scanner/Telegram."
        )

print("\nDA TAO:")
print("- scanner_update_report.csv")
if FULL_MODE:
    print("- universe_liquidity.csv (fresh full-market summary)")
    print("- quality_universe.csv (rebuilt)")
else:
    print("- universe_liquidity.csv (merged; existing rows preserved)")
print("- stock_cache/*.csv updated")

print("\nSAFETY:")
print("- Existing historical cache preserved.")
print("- Empty API response does not erase old cache.")
print("- New symbol starts from 2022, not 500-day history.")
print("- Current-day partial candle is NEVER used by Frozen V2.")
if FULL_MODE:
    print("- Full mode rebuilt quality_universe.csv from fresh full-market data.")
else:
    print("- Daily mode preserves liquidity rows outside quality universe.")
print("- Frozen model was NOT modified.")
print("- Frozen model was NOT retrained.")

print("\nUPDATE SCANNER DATA HOAN TAT")
print("=" * 78)