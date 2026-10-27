from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from vnstock import Quote

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "scanner_dataset_v2.csv"

if not DATA.exists():
    raise SystemExit("ERROR: scanner_dataset_v2.csv not found")

df = pd.read_csv(DATA, usecols=["date"])
dataset_date = pd.to_datetime(df["date"], errors="coerce").max()
if pd.isna(dataset_date):
    raise SystemExit("ERROR: scanner dataset has no valid market date")
dataset_date = dataset_date.date()

start = (date.today() - timedelta(days=10)).isoformat()
end = (date.today() + timedelta(days=1)).isoformat()
idx = Quote(symbol="VNINDEX", source="VCI").history(start=start, end=end, interval="1D")
if idx is None or idx.empty or "time" not in idx.columns:
    raise SystemExit("ERROR: cannot verify latest completed EOD from VCI/VNINDEX")

idx["time"] = pd.to_datetime(idx["time"], errors="coerce")
idx = idx.dropna(subset=["time"])
idx = idx[idx["time"].dt.date < date.today()]
if idx.empty:
    raise SystemExit("ERROR: no completed VNINDEX EOD available for verification")

expected = idx["time"].max().date()
print(f"Expected completed EOD: {expected}")
print(f"Scanner dataset EOD   : {dataset_date}")

if dataset_date != expected:
    raise SystemExit(
        f"STALE EOD BLOCKED: expected {expected}, dataset has {dataset_date}. "
        "Do not score or send Telegram with stale data."
    )

print("EOD freshness check: OK")
