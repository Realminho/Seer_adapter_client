import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from web.credentials import WebUiCredentials, load_credentials


def _write(tmp_path, text):
    p = tmp_path / "web-credentials.toml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid(tmp_path):
    path = _write(tmp_path, 'username = "admin"\npassword = "a-strong-pass-123"\n')
    cred = load_credentials(path)
    assert isinstance(cred, WebUiCredentials)
    assert cred.username == "admin"
    assert cred.password == "a-strong-pass-123"


def test_load_credentials_falls_back_to_tomli_when_tomllib_unavailable(tmp_path):
    path = _write(tmp_path, 'username = "admin"\npassword = "a-strong-pass-123"\n')
    tomli_stub_dir = tmp_path / "tomli_stub"
    tomli_stub_dir.mkdir()
    (tomli_stub_dir / "tomli.py").write_text(
        textwrap.dedent(
            """
            def load(fh):
                data = {}
                for raw in fh.read().decode().splitlines():
                    if "=" not in raw:
                        continue
                    key, value = raw.split("=", 1)
                    data[key.strip()] = value.strip().strip('"')
                return data
            """
        ),
        encoding="utf-8",
    )
    adapter_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{tomli_stub_dir}{os.pathsep}{adapter_root}"
    code = textwrap.dedent(
        f"""
        import builtins
        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "tomllib":
                raise ModuleNotFoundError("No module named 'tomllib'")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import
        from web.credentials import load_credentials
        cred = load_credentials({str(path)!r})
        assert cred.username == "admin"
        assert cred.password == "a-strong-pass-123"
        """
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_rejects_short_password(tmp_path):
    path = _write(tmp_path, 'username = "admin"\npassword = "short"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


@pytest.mark.parametrize("pw", ["set-me-please", "changeme1234", "passwordpass", "admin-admin99"])
def test_rejects_placeholder(tmp_path, pw):
    path = _write(tmp_path, f'username = "admin"\npassword = "{pw}"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError):
        load_credentials(str(tmp_path / "nope.toml"))


def test_rejects_missing_username(tmp_path):
    path = _write(tmp_path, 'password = "a-strong-pass-123"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


def test_rejects_empty_username(tmp_path):
    path = _write(tmp_path, 'username = ""\npassword = "a-strong-pass-123"\n')
    with pytest.raises(ValueError):
        load_credentials(path)


def test_rejects_missing_password(tmp_path):
    path = _write(tmp_path, 'username = "admin"\n')
    with pytest.raises(ValueError):
        load_credentials(path)
