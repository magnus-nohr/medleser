"""``python -m medleser`` starts the desktop app."""

from __future__ import annotations

import sys


def main() -> int:
    from .runtime_fix import preload_cpp_runtime

    preload_cpp_runtime()  # must happen before winrt/onnxruntime are imported
    from .app_desktop import main as run

    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
