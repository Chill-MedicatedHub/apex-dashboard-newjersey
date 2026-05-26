"""Background loop that re-runs scraper.py every REFRESH_MINUTES.

Run alongside `python -m http.server 8000` to keep the dashboard
data current. Stop with Ctrl+C.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REFRESH_MINUTES = int(os.environ.get("REFRESH_MINUTES", "15"))
SCRAPER = Path(__file__).parent / "scraper.py"


def main() -> None:
    if not SCRAPER.exists():
        sys.exit(f"ERROR: {SCRAPER} not found")
    print(f"Refresh loop started — pulling every {REFRESH_MINUTES} minute(s). Ctrl+C to stop.")
    while True:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{stamp}] running scraper...")
        try:
            subprocess.run([sys.executable, str(SCRAPER)], check=False)
        except KeyboardInterrupt:
            print("\nStopping.")
            return
        time.sleep(REFRESH_MINUTES * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping.")
