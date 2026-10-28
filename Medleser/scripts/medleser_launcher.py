"""Entry script for the packaged Windows app (PyInstaller)."""

import sys

from medleser.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
