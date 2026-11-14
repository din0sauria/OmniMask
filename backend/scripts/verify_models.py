"""验证 HaS_Text / HaS_Image 本地推理是否可用。

用法（先装 media 依赖并下载模型）：
    cd backend
    uv sync --group media
    uv run python scripts/verify_models.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import HAS_IMAGE_MODEL  # noqa: E402
from app.services.detect.image import detect_image  # noqa: E402
from app.services.detect.text import scan_pii_model  # noqa: E402
from app.services.models import image_yolo, text_llm  # noqa: E402

SAMPLE = "联系人张三，电话 13812345678，邮箱 zhang.san@example.com，地址北京市海淀区中关村大街 1 号。"


def main() -> None:
    print("== HaS_Text NER ==")
    llm = text_llm()
    print("模型加载：", "ok" if llm is not None else "失败（缺 llama-cpp-python 或 gguf）")
    if llm is not None:
        for h in scan_pii_model(SAMPLE, {"use_model": True}):
            print(f"  - [{h['type_label']}] {h['value']}")

    print("\n== HaS_Image YOLO ==")
    yolo = image_yolo()
    print("模型加载：", "ok" if yolo is not None else "失败（缺 ultralytics/torch 或 .pt）")
    if yolo is not None and HAS_IMAGE_MODEL.with_name("Figure.png").exists():
        hits, _ = detect_image(HAS_IMAGE_MODEL.with_name("Figure.png"), {"use_model": True})
        for h in hits:
            print(f"  - [{h['type_label']}] bbox={h['bbox']} conf={h['confidence']:.2f}")


if __name__ == "__main__":
    main()
