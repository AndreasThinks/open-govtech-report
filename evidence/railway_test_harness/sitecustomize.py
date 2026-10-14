"""Sandbox-only asyncio wakeup pipe; no application or test assertions changed."""
import asyncio
import os

class PipeEnd:
    def __init__(self, fd):
        self.fd = fd
    def fileno(self):
        return self.fd
    def setblocking(self, blocking):
        os.set_blocking(self.fd, blocking)
    def recv(self, size):
        return os.read(self.fd, size)
    def send(self, data):
        return os.write(self.fd, data)
    def close(self):
        os.close(self.fd)

def pipe_wakeup(self):
    read, write = os.pipe()
    self._ssock, self._csock = PipeEnd(read), PipeEnd(write)
    self._ssock.setblocking(False)
    self._csock.setblocking(False)
    self._internal_fds += 1
    self._add_reader(self._ssock.fileno(), self._read_from_self)

asyncio.SelectorEventLoop._make_self_pipe = pipe_wakeup
