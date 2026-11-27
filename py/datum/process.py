"""Проверка состояния процесса без изменения его состояния."""

import os


def process_alive(pid: int, *, on_denied: bool = False) -> bool:
    """Жив ли процесс. При отказе в доступе решение остаётся за вызывающим."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    try:
        if os.name == "nt":
            import _winapi

            # У процесса запрашивается право ожидания. Завершённый процесс
            # остаётся доступен по дескриптору, но ожидание уже сработало.
            try:
                handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
            except OSError as exc:
                if exc.winerror == 87:  # ERROR_INVALID_PARAMETER: такого PID нет
                    return False
                raise
            try:
                return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT
            finally:
                _winapi.CloseHandle(handle)
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return on_denied
    return True
