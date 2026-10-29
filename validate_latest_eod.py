from datetime import date, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "scanner_dataset_v2.csv"

if not DATA.exists():
    raise SystemExit("ERROR: scanner_dataset_v2.csv not found")

df = pd.read_csv(DATA, usecols=["date"])
dataset_date = pd.to_datetime(df["date"], errors="coerce").max()
if pd.isna(dataset_date):
    raise SystemExit("ERROR: scanner dataset has no valid market date")
dataset_date = dataset_date.date()

# Hard rule for the morning EOD pipeline:
# require the immediately preceding weekday. A data provider is NOT allowed
# to redefine an older bar as "latest" and let stale Telegram pass.
expected = date.today() - timedelta(days=1)
while expected.weekday() >= 5:
    expected -= timedelta(days=1)

print(f"Required completed EOD: {expected}")
print(f"Scanner dataset EOD  : {dataset_date}")

if dataset_date != expected:
    raise SystemExit(
        f"STALE EOD BLOCKED: required {expected}, dataset has {dataset_date}. "
        "Do not score or send Telegram."
    )

print("EOD freshness check: OK")
