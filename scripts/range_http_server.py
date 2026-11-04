"""Static file server with byte-range support.

python3 -m http.server answers every Range request with the whole file (200),
and a browser cannot seek in a video served that way: the Time Machine player
seeks each clip to the playhead, the seek is ignored, and the clip sits frozen
on its first frame.

    range_http_server.py <port> <directory>
"""
import os
import re
import sys
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')
CHUNK = 64 * 1024


class RangeRequestHandler(SimpleHTTPRequestHandler):
    byte_range = None

    def end_headers(self):
        # ensure_file_server.sh checks for this to tell this server from http.server
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def send_head(self):
        self.byte_range = None
        header = self.headers.get('Range')
        path = self.translate_path(self.path)
        match = RANGE.match(header.strip()) if header else None
        # multiple ranges, or none at all: the whole file is a valid answer
        if not match or os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
            return None

        size = os.fstat(f.fileno()).st_size
        first, last = match.groups()
        if first:
            start, end = int(first), int(last) if last else size - 1
        elif last:
            start, end = max(0, size - int(last)), size - 1   # suffix: the final N bytes
        else:
            start, end = 0, -1
        end = min(end, size - 1)
        if start > end or start >= size:
            f.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None

        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Last-Modified', self.date_time_string(os.fstat(f.fileno()).st_mtime))
        self.end_headers()
        f.seek(start)
        self.byte_range = (start, end)
        return f

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1] - self.byte_range[0] + 1
        while remaining > 0:
            chunk = source.read(min(CHUNK, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def handle(self):
        # players abandon range requests constantly while seeking
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    port, directory = int(sys.argv[1]), sys.argv[2]
    server = ThreadingHTTPServer(('', port), partial(RangeRequestHandler, directory=directory))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    main()
