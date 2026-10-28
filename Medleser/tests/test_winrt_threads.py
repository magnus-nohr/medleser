"""Regression test: WinRT use from short-lived threads must not crash the process.

Runs in a subprocess so a crash shows up as a failed test instead of killing pytest.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent(
    """
    import sys, threading
    sys.path.insert(0, {root!r})
    from medleser.speech.providers.windows_provider import WindowsVoiceProvider

    provider = WindowsVoiceProvider()

    def use():
        provider.status()
        provider.list_voices("nb")

    # Each round: a fresh thread uses WinRT and exits before the next one starts
    # (this is how the web server's worker threads behave).
    for _ in range(6):
        t = threading.Thread(target=use)
        t.start()
        t.join()
    print("OK")
    """
)


ORDER_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})
    from medleser.runtime_fix import preload_cpp_runtime
    print("runtime:", preload_cpp_runtime())
    # WinRT first (loads pywinrt's own msvcp140.dll if nothing newer is loaded) ...
    from medleser.speech.providers.windows_provider import WindowsVoiceProvider
    WindowsVoiceProvider().status()
    # ... then onnxruntime (Piper). This used to crash with an access violation.
    import onnxruntime
    print("onnxruntime", onnxruntime.__version__, "OK")
    """
)


@pytest.mark.windows
def test_onnxruntime_after_winrt_does_not_crash():
    if sys.platform != "win32":
        pytest.skip("Bare Windows")
    proc = subprocess.run([sys.executable, "-c", ORDER_SCRIPT.format(root=str(ROOT))],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-2000:]}"
    assert "OK" in proc.stdout


@pytest.mark.windows
def test_winrt_from_sequential_short_lived_threads():
    if sys.platform != "win32":
        pytest.skip("Bare Windows")
    proc = subprocess.run([sys.executable, "-c", SCRIPT.format(root=str(ROOT))],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-2000:]}"
    assert "OK" in proc.stdout
