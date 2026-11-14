"""简单对比 OmniMask 文本 PII 规则 与 Presidio（baseline）。

用法（可选安装 baseline 后运行）：
    cd backend
    uv sync --group media          # 可选，包含 presidio
    uv run python scripts/compare_presidio.py

未安装 presidio-analyzer 时只输出 OmniMask 自身的识别结果。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.detect.text import scan_pii  # noqa: E402

SAMPLE = (
    "联系人：张三，电话 13812345678，邮箱 zhang.san@example.com，"
    "身份证 11010519491231002X，银行卡 4111111111111111，"
    "地址：北京市海淀区中关村大街 1 号。"
)


def run_omnimask(text: str) -> list[str]:
    hits = scan_pii(text, {})
    return sorted({f"{h['type_label']}:{h['value']}" for h in hits})


def run_presidio(text: str) -> list[str]:
    try:
        from presidio_analyzer import AnalyzerEngine
    except Exception:
        return []
    try:
        engine = AnalyzerEngine()
        results = engine.analyze(text=text, language="zh", entities=["PHONE_NUMBER", "EMAIL_ADDRESS", "ID"])
        return sorted({f"{r.entity_type}:{text[r.start:r.end]}" for r in results})
    except Exception as e:
        print(f"  (presidio 运行失败：{e})")
        return []


def main() -> None:
    print("样本：", SAMPLE, "\n")
    om = run_omnimask(SAMPLE)
    print("OmniMask 识别：")
    for x in om:
        print("  -", x)
    if not om:
        print("  (无)")
    print()

    ps = run_presidio(SAMPLE)
    if ps:
        print("Presidio 识别（中文，若 zh 模型缺失可能为空）：")
        for x in ps:
            print("  -", x)
    else:
        print("Presidio：未安装（pip/uv 安装 presidio-analyzer 后可对比）。")
    print()


if __name__ == "__main__":
    main()
