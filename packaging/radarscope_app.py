"""Entry script for packaged builds (PyInstaller); same as ``python -m radarscope``."""

import sys

from radarscope.main import main

if __name__ == "__main__":
    sys.exit(main())
