"""Межпроцессная блокировка для правки файла от чтения до замены."""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(path: Path):
    """Все писатели открывают один постоянный файл рядом с данными."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as lock:
        if os.name == "nt":
            import msvcrt

            # Область может лежать за концом файла. Даже у непустого старого
            # замка все процессы должны брать один и тот же байт.
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno != errno.EACCES:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
