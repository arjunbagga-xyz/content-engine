"""Minimal HTTP file server with HTTP Range support (Meta's transcoder
fetches videos via byte-range requests; Python's http.server does not)."""
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangedHandler(SimpleHTTPRequestHandler):
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
    os.chdir(directory)
    return ThreadingHTTPServer(("127.0.0.1", port), RangedHandler)
