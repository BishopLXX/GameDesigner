from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from gamedesigner.pixel_site_downloader import main


if __name__ == "__main__":
    raise SystemExit(main())
