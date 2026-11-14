"""Read and write source files while preserving the original encoding."""
from pathlib import Path


def read_text(path: Path) -> tuple[str, str]:
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(text, encoding=encoding, newline="")
    except (UnicodeEncodeError, LookupError):
        path.write_text(text, encoding="utf-8", newline="")


def count_lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0
