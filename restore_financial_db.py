from pathlib import Path
import zipfile
root = Path(__file__).resolve().parent
db = root / "financial_v3.sqlite"
if db.exists():
    print("financial_v3.sqlite already exists")
    raise SystemExit(0)
parts = sorted(root.glob("financial_v3.sqlite.zip.part*"))
if not parts:
    raise SystemExit("Missing financial DB parts")
z = root / "financial_v3.sqlite.zip"
with z.open("wb") as out:
    for part in parts:
        out.write(part.read_bytes())
with zipfile.ZipFile(z) as f:
    f.extractall(root)
z.unlink()
print("financial_v3.sqlite restored", db.stat().st_size)
