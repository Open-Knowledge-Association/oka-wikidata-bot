"""Members of a large zip on a web server, read with HTTP range requests, so that one layer of a 3.6 GB dataset is
fetched without downloading the rest (swissTLM3D's lakes are a few files of its shapefile zip).

    python remote_zip.py URL                      list the members and their sizes
    python remote_zip.py URL PATTERN FOLDER       extract the members whose name contains PATTERN into FOLDER
"""
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from common import UA


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over HTTP: each read is one range request (zipfile reads the directory at the end,
    then each member's own bytes)."""

    def __init__(self, url):
        self.url, self.pos = url, 0
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + n, self.size) - 1
        req = urllib.request.Request(self.url, headers={"User-Agent": UA, "Range": f"bytes={self.pos}-{end}"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def extract(url, pattern, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BufferedReader(RangeFile(url), buffer_size=1 << 20)) as z:
        for info in z.infolist():
            if pattern.lower() in info.filename.lower() and not info.is_dir():
                target = folder / Path(info.filename).name
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                print(f"{info.filename}: {info.file_size:,} bytes -> {target}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        with zipfile.ZipFile(io.BufferedReader(RangeFile(sys.argv[1]), buffer_size=1 << 20)) as z:
            for info in z.infolist():
                print(f"{info.file_size:>14,}  {info.filename}")
    else:
        extract(*sys.argv[1:4])
