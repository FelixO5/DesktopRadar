"""Render the RadarScope app icon to a PNG: python packaging/make_icon.py OUT.png [SIZE]"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen;minimal")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

from radarscope.main import render_icon  # noqa: E402

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    app = QApplication([])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if not render_icon(size).save(out):
        sys.exit(f"could not write {out}")
    print(out)
