"""Minimal HTTP file server with HTTP Range support (Meta's transcoder
fetches videos via byte-range requests; Python's http.server does not).

IMPORTANT: serve() must NOT os.chdir() the global process working directory
-- that corrupts relative paths (e.g. post.media_path) for the rest of the
daemon. We serve from an explicit directory instead.
"""
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangedHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self._directory = directory
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            return super().send_head()
        fs = os.fstat(f.fileno())
        size = fs.st_size
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                start_s, end_s = rng[6:].split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                if end >= size:
                    end = size - 1
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(path))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                f.seek(start)
                self.wfile.write(f.read(length))
                f.close()
                return None
            except (ValueError, OSError):
                f.close()
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(f.read())
        f.close()
        return None


def serve(directory: str, port: int):
    """Start a ThreadingHTTPServer on 127.0.0.1:port serving `directory`.

    Does NOT chdir the process -- serves from an explicit directory so the
    caller's working directory is untouched.
    """
    handler = partial(RangedHandler, directory=directory)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
