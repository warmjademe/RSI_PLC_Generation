"""Download the hash-pinned GGUF with bounded parallel range requests."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

ROOT = Path('/home/qyb/qwen38-27b-quantized')
NAME = 'Qwen3.8-27B-Q4_K_M.gguf'
SIZE = 18973870432
SHA = '31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34'
URL = 'https://modelscope.cn/models/ggml-org/Qwen3.8-27B-GGUF/resolve/c627fc44e9c888bf2d8e23c3671288c8fa808189/' + NAME
CHUNK = 32 * 1024 * 1024


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def main():
    target = ROOT / 'models' / NAME
    partial = target.with_suffix('.gguf.part')
    statefile = ROOT / 'ops/download_state.json'
    if target.exists():
        raise RuntimeError('completed target already exists')
    count = (SIZE + CHUNK - 1) // CHUNK
    if statefile.exists():
        state = json.loads(statefile.read_text())
        assert state['expected_sha256'] == SHA and state['chunk_size'] == CHUNK
    else:
        # Existing sequential download prefix is reused only at whole chunk boundaries.
        prefix = partial.stat().st_size if partial.exists() else 0
        state = {'expected_sha256': SHA, 'size': SIZE, 'chunk_size': CHUNK,
                 'started_epoch': time.time(), 'completed_chunks': list(range(min(prefix // CHUNK, count)))}
        save(statefile, state)
    done = set(state['completed_chunks'])
    lock = threading.Lock()
    fd = os.open(partial, os.O_CREAT | os.O_RDWR, 0o644)
    os.ftruncate(fd, SIZE)

    def fetch(index):
        start = index * CHUNK
        end = min(start + CHUNK, SIZE) - 1
        for attempt in range(6):
            try:
                request = urllib.request.Request(URL, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(request, timeout=40) as response:
                    assert response.status == 206
                    assert response.headers['Content-Range'] == f'bytes {start}-{end}/{SIZE}'
                    offset = start
                    while offset <= end:
                        block = response.read(min(1024 * 1024, end - offset + 1))
                        if not block:
                            raise OSError('incomplete range')
                        view = memoryview(block)
                        while view:
                            written = os.pwrite(fd, view, offset)
                            offset += written
                            view = view[written:]
                with lock:
                    done.add(index)
                    state.update(completed_chunks=sorted(done), epoch=time.time(),
                        downloaded_bytes=sum(min(CHUNK, SIZE - x * CHUNK) for x in done))
                    save(statefile, state)
                return
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 16))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch, [i for i in range(count) if i not in done]))
        os.fsync(fd)
    finally:
        os.close(fd)
    state.update(phase='verifying_sha256', epoch=time.time()); save(statefile, state)
    digest = hashlib.file_digest(partial.open('rb'), 'sha256').hexdigest()
    if digest != SHA:
        raise ValueError('model checksum mismatch; incomplete file retained')
    partial.replace(target)
    state.update(phase='complete', verified_sha256=digest, epoch=time.time())
    save(statefile, state)
    print(json.dumps({'status': 'verified', 'sha256': digest, 'size': SIZE}), flush=True)


if __name__ == '__main__':
    main()
