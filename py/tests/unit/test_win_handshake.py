"""Рукопожатие Windows записывается в проверенную закрытую папку."""

import os
import tempfile
from pathlib import Path

import pytest

from datum.server.handshake import handshake_dir, read_handshake, write_handshake

pytestmark = pytest.mark.skipif(os.name != "nt", reason="проверка DACL Windows")


def test_private_handshake_is_created_once_and_consumed():
    with tempfile.TemporaryDirectory(dir=handshake_dir()) as tmp:
        path = Path(tmp) / "handshake.json"
        write_handshake(port=12345, token="test", project="пример", path=path)
        with pytest.raises(FileExistsError):
            write_handshake(port=23456, token="other", project="пример", path=path)
        assert read_handshake(path, remove=False).port == 12345
        assert read_handshake(path).project == "пример"
        assert not path.exists()


def test_refuse_directory_readable_by_others():
    import win32security as security

    with tempfile.TemporaryDirectory(dir=handshake_dir()) as tmp:
        parent = Path(tmp) / "shared"
        parent.mkdir()
        sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            "D:P(A;;FA;;;OW)(A;;FR;;;WD)", security.SDDL_REVISION_1)
        security.SetFileSecurity(str(parent), security.DACL_SECURITY_INFORMATION, sd)
        with pytest.raises(PermissionError):
            write_handshake(port=12345, token="test", project="пример", path=parent / "info")
        assert not (parent / "info").exists()
