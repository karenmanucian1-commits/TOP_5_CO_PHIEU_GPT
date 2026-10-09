from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_FILE = BASE_DIR / "scanner_dataset_v2.csv"
MODEL_FILE = BASE_DIR / "TOP5_V2_FROZEN_MODEL.joblib"
OUTPUT_FILE = BASE_DIR / "TOP5_V2_TODAY.csv"
HISTORY_FILE = BASE_DIR / "TOP5_V2_HISTORY.csv"
TOP_N = 5

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

FEATURES = V1_FEATURES + V2_FEATURES


# ============================================================
# LOAD FROZEN MODEL
# ============================================================

def load_frozen_model():

    if not MODEL_FILE.exists():
        raise FileNotFoundError(
            "\nKHONG TIM THAY FROZEN MODEL:\n"
            f"{MODEL_FILE}\n\n"
            "Scanner da bi khoa retrain.\n"
            "Khong duoc tu dong train model moi."
        )

    obj = joblib.load(MODEL_FILE)

    print(f"Loaded frozen model: {MODEL_FILE.name}")

    # Model file trước đây được lưu dưới dạng dictionary.
    if isinstance(obj, dict):

        model = (
            obj.get("model")
            or obj.get("pipeline")
            or obj.get("estimator")
        )

        if model is None:
            raise RuntimeError(
                "Frozen model file la dictionary "
                "nhung khong tim thay model/pipeline/estimator."
            )

        saved_features = obj.get("features")

        if saved_features is not None:
            saved_features = list(saved_features)

            if saved_features != FEATURES:
                raise RuntimeError(
                    "FEATURE LIST KHONG KHOP FROZEN MODEL.\n"
                    "Scanner dung de tranh sai mo hinh."
                )

        train_end = obj.get("train_end")

        alpha = obj.get("ridge_alpha", obj.get("alpha"))

        print(f"Frozen train end: {train_end}")
        print(f"Frozen alpha: {alpha}")

    else:
        model = obj

    return model


# ============================================================
# LOAD SCANNER DATA
# ============================================================

def load_scanner_data():

    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"Khong tim thay {DATA_FILE.name}"
        )

    df = pd.read_csv(DATA_FILE)

    if "date" not in df.columns:
        raise ValueError("Dataset khong co cot date.")

    if "symbol" not in df.columns:
        raise ValueError("Dataset khong co cot symbol.")

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df = df.dropna(subset=["date", "symbol"])

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    missing = [
        c for c in FEATURES
        if c not in df.columns
    ]

    if missing:
        raise RuntimeError(
            f"Scanner dataset thieu features: {missing}"
        )

    return df


# ============================================================
# REGIME LABEL - DISPLAY ONLY
# ============================================================

def regime_label(row):

    trend = row.get("MKT_TREND_SCORE", np.nan)
    breadth = row.get("BREADTH20", np.nan)

    if pd.isna(trend) or pd.isna(breadth):
        return "UNKNOWN"

    if trend > 0.05 and breadth >= 0.55:
        return "POSITIVE"

    if trend < -0.05 and breadth <= 0.45:
        return "STRONG NEGATIVE"

    if trend < 0:
        return "NEGATIVE"

    return "MEDIUM"


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)
    print("TOP5 AI - V2 FROZEN DAILY PAPER SCANNER")
    print("=" * 72)

    # --------------------------------------------------------
    # 1. MODEL
    # --------------------------------------------------------

    model = load_frozen_model()

    # --------------------------------------------------------
    # 2. DATA
    # --------------------------------------------------------

    df = load_scanner_data()

    latest_date = df["date"].max()

    today = (
        df[df["date"] == latest_date]
        .copy()
        .reset_index(drop=True)
    )

    print()
    print(f"Latest market date: {latest_date.date()}")
    print(f"Eligible symbols before feature check: {len(today):,}")

    # --------------------------------------------------------
    # 3. FEATURE VALIDITY
    # --------------------------------------------------------

    # Frozen pipeline may contain imputer, therefore NaN is allowed.
    # But rows with ALL 34 features missing are invalid.
    all_missing = today[FEATURES].isna().all(axis=1)

    if all_missing.any():
        print(
            f"Removed rows with all features missing: "
            f"{int(all_missing.sum())}"
        )

        today = today.loc[~all_missing].copy()

    if len(today) < TOP_N:
        raise RuntimeError(
            f"Chi con {len(today)} ma hop le, "
            f"khong du de chon Top {TOP_N}."
        )

    # --------------------------------------------------------
    # 4. SCORE
    # --------------------------------------------------------

    X = today[FEATURES].copy()

    try:
        scores = model.predict(X)

    except Exception as e:

        print()
        print("MODEL PREDICT ERROR:")
        print(str(e))

        print()
        print(
            "Scanner dung lai. "
            "KHONG retrain va KHONG thay doi model."
        )

        sys.exit(1)

    today["SCORE"] = scores

    # --------------------------------------------------------
    # 5. TOP 5
    # --------------------------------------------------------

    top = (
        today
        .sort_values(
            ["SCORE", "symbol"],
            ascending=[False, True]
        )
        .head(TOP_N)
        .copy()
    )

    top.insert(
        0,
        "RANK",
        range(1, len(top) + 1)
    )

    top["REGIME"] = top.apply(
        regime_label,
        axis=1
    )

    # --------------------------------------------------------
    # 6. OUTPUT COLUMNS
    # --------------------------------------------------------

    wanted = [
        "RANK",
        "date",
        "symbol",
        "exchange",
        "close",
        "volume",
        "VALUE20",
        "SCORE",
        "RET5",
        "RET20",
        "RET60",
        "REL_RET5",
        "REL_RET20",
        "REL_RET60",
        "CS_RET5_RANK",
        "CS_RET20_RANK",
        "CS_RET60_RANK",
        "BREADTH20",
        "MKT_RET20",
        "MKT_TREND_SCORE",
        "REGIME",
    ]

    output_cols = [
        c for c in wanted
        if c in top.columns
    ]

    result = top[output_cols].copy()

    # --------------------------------------------------------
    # 7. SAVE
    # --------------------------------------------------------

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )
    # --------------------------------------------------------
    # 7B. SAVE FORWARD HISTORY
    # --------------------------------------------------------

    history_new = result.copy()

    if HISTORY_FILE.exists():

        history_old = pd.read_csv(HISTORY_FILE)

        if not history_old.empty and "date" in history_old.columns:

            history_old["date"] = pd.to_datetime(
                history_old["date"],
                errors="coerce"
            )

            # Neu chay lai cung ngay:
            # xoa ket qua cu cua ngay do va ghi lai ket qua moi.
            history_old = history_old[
                history_old["date"].dt.normalize()
                != latest_date.normalize()
            ].copy()

        history = pd.concat(
            [history_old, history_new],
            ignore_index=True
        )

    else:
        history = history_new.copy()

    history["date"] = pd.to_datetime(
        history["date"],
        errors="coerce"
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

    history.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(
        f"History: {HISTORY_FILE.name} | "
        f"Days={history['date'].nunique()} | "
        f"Rows={len(history):,}"
    )
    # --------------------------------------------------------
    # 8. DISPLAY
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print(f"TOP {TOP_N} - {latest_date.date()}")
    print("=" * 72)

    display_cols = [
        c for c in [
            "RANK",
            "symbol",
            "exchange",
            "close",
            "SCORE",
            "REL_RET20",
            "CS_RET20_RANK",
            "BREADTH20",
            "MKT_TREND_SCORE",
            "REGIME",
        ]
        if c in result.columns
    ]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    print(
        result[display_cols]
        .to_string(index=False)
    )

    print()
    print(f"Output: {OUTPUT_FILE.name}")

    print()
    print("=" * 72)
    print("SAFETY CHECK")
    print("=" * 72)

    print("Frozen model retrained: NO")
    print("2026 used for model training: NO")
    print("2026 used for forward scoring: YES")
    print("Orders sent to broker: NO")
    print("Mode: PAPER / FORWARD SCANNER")

    print()
    print(
        "Luu y: Day la tin hieu paper scanner, "
        "khong phai khuyen nghi mua ban."
    )

    print("=" * 72)


if __name__ == "__main__":
    main()