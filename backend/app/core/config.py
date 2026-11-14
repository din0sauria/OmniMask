import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("OMNIMASK_DATA", ROOT / "data"))
TASKS_DIR = DATA_DIR / "tasks"
POLICY_FILE = DATA_DIR / "policies.json"
KEYLEAK_DIR = ROOT / "keyleak"
RULES_FILE = KEYLEAK_DIR / "data" / "regex" / "merged_rules.json"

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SCAN_BYTES = 1024 * 1024
MAX_FILES = 500
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

for d in (DATA_DIR, TASKS_DIR):
    d.mkdir(parents=True, exist_ok=True)
