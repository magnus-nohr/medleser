"""Start Medleser in developer mode (browser, fixed port, separate data folder).

Usage: .venv\\Scripts\\python.exe scripts\\dev_server.py
Then open http://127.0.0.1:8765/start?token=medleser-dev-token-0001
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medleser.app_desktop import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["--browser", "--no-open", "--port", "8765", "--data-dir", str(ROOT / ".devdata"),
                   "--dev-token", "medleser-dev-token-0001", *sys.argv[1:]]))
