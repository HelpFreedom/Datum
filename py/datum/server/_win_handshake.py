"""Файл рукопожатия Windows. Корень доверия — LocalAppData текущей учётной записи."""

from __future__ import annotations

import ctypes
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

import ntsecuritycon as rights
import pywintypes
import win32api
import win32con as flags
import win32file
import win32security as security
import winerror
from win32com.shell import shell, shellcon


@contextmanager
def _file_errors():
    try:
        yield
    except pywintypes.error as exc:
        raise ctypes.WinError(exc.winerror) from exc


def local_data() -> Path:
    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_LOCAL_APPDATA, None, 0))


def _user():
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), flags.TOKEN_QUERY)
    try:
        return security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()


def _attributes(user):
    sid = security.ConvertSidToStringSid(user)
    # Защищённая DACL задаётся при создании, до первого байта с токеном.
    sddl = f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, security.SDDL_REVISION_1)
    return attrs


def _verify(handle, path, user, *, directory: bool, private: bool):
    attrs = win32file.GetFileInformationByHandle(handle)[0]
    if attrs & flags.FILE_ATTRIBUTE_REPARSE_POINT or bool(attrs & flags.FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise PermissionError(f"неподходящий тип файла рукопожатия: {path}")
    sd = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
                                  security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    trusted = (user, security.CreateWellKnownSid(security.WinLocalSystemSid),
               security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid))
    acl = sd.GetSecurityDescriptorDacl()
    if sd.GetSecurityDescriptorOwner() not in trusted or acl is None:
        raise PermissionError(f"чужой владелец или отсутствует DACL: {path}")
    # OWNER RIGHTS ссылается на уже проверенного владельца объекта.
    trusted += (security.ConvertStringSidToSid("S-1-3-4"),)
    readonly = rights.FILE_GENERIC_READ | rights.FILE_GENERIC_EXECUTE
    for idx in range(acl.GetAceCount()):
        header, mask, *rest = acl.GetAce(idx)
        kind, _ = header
        if kind == security.ACCESS_DENIED_ACE_TYPE:
            continue
        if kind == security.ACCESS_ALLOWED_ACE_TYPE and (
                rest[0] in trusted or (not private and mask & ~readonly == 0)):
            continue
        raise PermissionError(f"права других учётных записей не подходят для рукопожатия: {path}")


def _component(name: str) -> None:
    if (not name or name in (".", "..") or ":" in name or name.endswith((" ", "."))
            or Path(name).is_reserved()):
        raise ValueError(f"неподходящее имя в пути рукопожатия: {name!r}")


@contextmanager
def directory(path: Path, *, create: bool = False):
    """Проверить и удерживать каталоги от подмены до конца обращения к файлу."""
    base = local_data()
    if not path.is_relative_to(base) or path == base or base.drive.startswith("\\\\"):
        raise PermissionError("файл рукопожатия Windows должен находиться в закрытой папке LocalAppData")
    user = _user()
    parts = path.relative_to(base).parts
    for part in parts:
        _component(part)
    here = base
    with _file_errors(), ExitStack() as stack:
        for index in range(len(parts) + 1):
            if index:
                here = here / parts[index - 1]
            if create and here == path:
                try:
                    win32file.CreateDirectory(str(here), _attributes(user))
                except pywintypes.error as exc:
                    if exc.winerror != winerror.ERROR_ALREADY_EXISTS:
                        raise
            # Без FILE_SHARE_DELETE: пока идёт операция, этот каталог нельзя переименовать.
            handle = win32file.CreateFile(
                str(here), flags.READ_CONTROL | rights.FILE_READ_ATTRIBUTES,
                flags.FILE_SHARE_READ | flags.FILE_SHARE_WRITE, None, flags.OPEN_EXISTING,
                win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32file.FILE_FLAG_BACKUP_SEMANTICS, None)
            stack.callback(handle.Close)
            _verify(handle, here, user, directory=True, private=here == path)
        yield user


def write(target: Path, payload: dict) -> None:
    _component(target.name)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > 65536:
        raise ValueError("слишком большой файл рукопожатия")
    with directory(target.parent) as user:
        handle = win32file.CreateFile(
            str(target), flags.GENERIC_WRITE, 0, _attributes(user), flags.CREATE_NEW,
            win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        try:
            _, count = win32file.WriteFile(handle, data)
            if count != len(data):
                raise OSError("неполная запись файла рукопожатия")
        except BaseException:
            handle.Close()
            target.unlink(missing_ok=True)
            raise
        else:
            handle.Close()


def read(target: Path, *, remove: bool) -> dict:
    _component(target.name)
    with directory(target.parent) as user:
        handle = win32file.CreateFile(
            str(target), flags.GENERIC_READ | flags.READ_CONTROL, flags.FILE_SHARE_READ, None,
            flags.OPEN_EXISTING, win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        try:
            _verify(handle, target, user, directory=False, private=True)
            size = win32file.GetFileSize(handle)
            if size > 65536:
                raise ValueError("слишком большой файл рукопожатия")
            _, data = win32file.ReadFile(handle, size)
            payload = json.loads(data.decode("utf-8"))
        finally:
            handle.Close()
        if remove:
            target.unlink()
        return payload
