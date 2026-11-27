"""Нативный сервер Windows: рукопожатие, встроенная операция и выход с родителем."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from datum.server.handshake import read_handshake

pytestmark = pytest.mark.skipif(os.name != "nt", reason="проверка нативного сервера Windows")


def test_native_server(tmp_path):
    from datum.server._win_files import local_data

    # Временные файлы этого сервера лежат в закрытой папке текущего пользователя.
    with tempfile.TemporaryDirectory(dir=local_data()) as run:
        env = {**os.environ, "XDG_RUNTIME_DIR": run, "PYTHONUTF8": "1"}
        params = tmp_path / "params"
        params.mkdir()
        path = params / "base.yaml"
        path.write_text('L: {value: 5, unit: mm, provenance: chosen, reason: "тест"}\n',
                        encoding="utf-8")
        target = Path(run) / "handshake.json"
        owner = subprocess.Popen([sys.executable, "-I", "-c", "input()"], stdin=subprocess.PIPE)
        server = subprocess.Popen(
            [sys.executable, "-I", "-m", "datum.cli", "serve", str(tmp_path),
             "--handshake", str(target), "--parent-pid", str(owner.pid)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 20
            while not target.exists() and time.monotonic() < deadline:
                assert server.poll() is None, server.stderr.read()
                time.sleep(0.05)
            assert target.exists()
            info = read_handshake(target)
            with httpx.Client(base_url=f"http://127.0.0.1:{info.port}", trust_env=False,
                              timeout=30) as client:
                assert client.get("/healthz").status_code == 200
                assert client.get("/api/ops").status_code == 403
                client.headers["X-Datum-Token"] = info.token
                assert client.post("/api/ops/datum_status", json={}).status_code == 403
                client.headers["X-Datum-Client"] = "1"
                ops = client.get("/api/ops").json()["ops"]
                assert {op["name"] for op in ops} == {"datum_status", "check_list", "fem_verify", "param_set"}
                for name, args in (("datum_status", {}), ("check_list", {}),
                                   ("fem_verify", {"modulus": 3000}),
                                   ("param_set", {"name": "L", "value": 7})):
                    response = client.post(f"/api/ops/{name}", json=args)
                    assert response.status_code == 200
                    assert response.json()["status"] == "done", response.json().get("result")
                assert "value: 7" in path.read_text("utf-8")
                assert client.post("/api/ops/part_build", json={}).status_code == 501
                assert client.post("/api/jobs", json={"op": "part_build"}).status_code == 501
            owner.communicate(b"\n", timeout=5)
            assert server.wait(timeout=12) == 0
            assert not list((Path(run) / "datum" / "servers").glob("*.json"))
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=5)
            if server.poll() is None:
                server.kill()
            server.wait(timeout=5)

@pytest.mark.parametrize("flags", [[], ["--in-process"]])
def test_cli_refuses_script_execution(tmp_path, flags):
    result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "datum.cli", "build",
                             str(tmp_path), *flags], capture_output=True, encoding="utf-8",
                            timeout=20)
    assert result.returncode != 0
    assert "защищённого исполнителя" in result.stderr
