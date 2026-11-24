import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("OMNIMASK_DATA", ROOT / "data"))
TASKS_DIR = DATA_DIR / "tasks"
POLICY_FILE = DATA_DIR / "policies.json"
KEYLEAK_DIR = ROOT / "keyleak"
RULES_FILE = KEYLEAK_DIR / "data" / "regex" / "merged_rules.json"
MODELS_DIR = Path(os.environ.get("OMNIMASK_MODELS", ROOT / "models"))
HAS_TEXT_MODEL = MODELS_DIR / "HaS_Text_0209_0.6B_Q8" / "has_text_model.gguf"
HAS_IMAGE_MODEL = MODELS_DIR / "HaS_Image_0209_FP32" / "sensitive_seg_best.pt"

TEXT_EXTS = {".txt", ".csv", ".md", ".log", ".html", ".htm", ".rst"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}

SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

for d in (DATA_DIR, TASKS_DIR):
    d.mkdir(parents=True, exist_ok=True)
