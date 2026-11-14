"""Optional model inference: HaS_Text (GGUF via llama.cpp) + HaS_Image (YOLO11 via ultralytics)."""
import functools

from ..core.config import HAS_TEXT_MODEL, HAS_IMAGE_MODEL


@functools.lru_cache(maxsize=1)
def text_llm():
    if not HAS_TEXT_MODEL.exists():
        return None
    try:
        from llama_cpp import Llama
        return Llama(str(HAS_TEXT_MODEL), n_ctx=8192, verbose=False)
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def image_yolo():
    if not HAS_IMAGE_MODEL.exists():
        return None
    try:
        from ultralytics import YOLO
        return YOLO(str(HAS_IMAGE_MODEL))
    except Exception:
        return None
