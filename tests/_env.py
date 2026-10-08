"""Isolate tests from the real machine: temp HOME (no config, no ledger), no API keys.

Import this before any project module.
"""
import atexit, os, shutil, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="ai-workers-test-")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)
ORIG_ENV = dict(os.environ)  # for opt-in live tests
KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")

os.environ["HOME"] = HOME
for k in KEY_VARS:
    os.environ.pop(k, None)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def clean_env():
    """Environment for a server subprocess: same isolation as this process."""
    env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
    env["HOME"] = HOME
    return env
