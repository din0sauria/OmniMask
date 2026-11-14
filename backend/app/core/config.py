import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("OMNIMASK_DATA", ROOT / "data"))
TASKS_DIR = DATA_DIR / "tasks"
POLICY_FILE = DATA_DIR / "policies.json"
KEYLEAK_DIR = ROOT / "keyleak"
RULES_FILE = KEYLEAK_DIR / "data" / "regex" / "merged_rules.json"
MODELS_DIR = Path(os.environ.get("OMNIMASK_MODELS", ROOT / "models"))

TEXT_EXTS = {".txt", ".csv", ".md", ".log", ".html", ".htm", ".rst"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SCAN_BYTES = 1024 * 1024
MAX_FILES = 500
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

for d in (DATA_DIR, TASKS_DIR):
    d.mkdir(parents=True, exist_ok=True)
