import os
from pathlib import Path


for name in ('ffmpeg', 'ffprobe'):
    path = Path(__file__).resolve().parents[2] / '.tools' / 'ffmpeg' / (name + '.exe')
    if path.exists():
        os.environ.setdefault('OMNIMASK_' + name.upper(), str(path))
