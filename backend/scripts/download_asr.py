"""Explicit opt-in model preparation; inference never calls this downloader."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

MODEL = 'Systran/faster-whisper-base'
REVISION = 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'
FILES = {'config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='../models/faster-whisper-base')
    parser.add_argument('--revision', default=REVISION)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata = json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{MODEL}/revision/{args.revision}?blobs=true', timeout=30))
    revision = metadata['sha']
    manifest = []
    for entry in metadata['siblings']:
        name = entry['rfilename']
        if name not in FILES:
            continue
        expected = entry.get('lfs', {}).get('sha256')
        path = output / name
        if path.exists() and path.stat().st_size == entry['size'] and expected and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            print('Verified existing', name, flush=True)
            manifest.append({'file': name, 'sha256': expected, 'size': entry['size']})
            continue
        temp = path.with_suffix(path.suffix + '.part')
        print('Downloading', name, entry['size'], 'bytes', flush=True)
        h = hashlib.sha256()
        with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{revision}/{name}', timeout=30) as response, temp.open('wb') as stream:
            total = 0
            while data := response.read(1024 * 1024):
                stream.write(data)
                h.update(data)
                total += len(data)
                if total % (20 * 1024 * 1024) == 0:
                    print(name, total, '/', entry['size'], flush=True)
        if total != entry['size'] or expected and expected != h.hexdigest():
            raise RuntimeError('Model download size/hash mismatch: ' + name)
        if not expected:
            content = temp.read_bytes()
            if hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() != entry['blobId']:
                raise RuntimeError('Git blob hash mismatch: ' + name)
        temp.replace(path)
        manifest.append({'file': name, 'sha256': h.hexdigest(), 'size': total})
    (output / 'download-manifest.json').write_text(json.dumps({'model': MODEL, 'revision': revision, 'files': manifest}, indent=2), encoding='utf-8')
    print('Model ready:', output, flush=True)


if __name__ == '__main__':
    main()
