import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "Khoidong.bat": "KHOI DONG HE THONG",
    "capnhat.bat": "CAP NHAT PHIEN BAN",
    "setup.bat": "CAI DAT HE THONG",
    "thuvien.bat": "CAI THU VIEN PYTHON",
}

CRITICAL_MARKERS = {
    "Khoidong.bat": [
        r"call .venv\Scripts\activate.bat",
        r'fc /b "requirements.txt" ".venv\.riviu-requirements.txt"',
        'call "%~dp0thuvien.bat" --called',
        "Get-NetTCPConnection -LocalPort 1231",
        '"%VENV_PY%" -m riviu',
        "from riviu.proxy_utils import PROXY_TEST_BUILD",
    ],
    "capnhat.bat": [
        'stash push -u -m "capnhat-auto-stash"',
        "fetch --all --prune",
        'reset --hard "origin/%CUR_BRANCH%"',
        'call "%~f0" --after-update',
        'call "%~dp0thuvien.bat" --called',
    ],
    "setup.bat": [
        'call "%~dp0thuvien.bat" --called',
        "winget install Python.Python.3.13",
        "import fastapi, uvicorn, pandas, openpyxl",
    ],
    "thuvien.bat": [
        "where uv",
        "irm https://astral.sh/uv/install.ps1 | iex",
        "winget install --id astral-sh.uv",
        'call "%UV_EXE%" sync --locked --no-dev',
        "%PY% -m venv .venv",
        "-m ensurepip --upgrade",
        '"%VENV_PY%" -m pip install -r requirements.txt',
        '"%VENV_PY%" -m playwright install chromium',
        r'copy /y "requirements.txt" ".venv\.riviu-requirements.txt"',
    ],
}


def read_script(filename):
    return (ROOT / filename).read_text(encoding="utf-8")


def read_script_bytes(filename):
    return (ROOT / filename).read_bytes()


def run_ui_check(filename, *, windows_terminal=False):
    environment = os.environ.copy()
    if windows_terminal:
        environment["WT_SESSION"] = "pytest"
    else:
        environment.pop("WT_SESSION", None)
    return subprocess.run(
        ["cmd", "/d", "/c", str(ROOT / filename), "--ui-check"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(("filename", "subtitle"), SCRIPTS.items())
def test_batch_script_uses_stable_riviu_console_theme(filename, subtitle):
    content = read_script(filename)

    assert "RIVIU REPORTS" in content
    assert subtitle in content
    assert "color 06" in content
    assert "if defined WT_SESSION" in content
    assert "38;2;255;107;0m" in content
    assert 'set "UI_OK=' in content
    assert 'set "UI_WARN=' in content
    assert 'set "UI_ERROR=' in content
    assert 'set "UI_RESET=' in content
    assert '"--ui-check"' in content


@pytest.mark.parametrize(("filename", "markers"), CRITICAL_MARKERS.items())
def test_batch_script_keeps_critical_commands(filename, markers):
    content = read_script(filename)

    for marker in markers:
        assert marker in content


@pytest.mark.parametrize(("filename", "markers"), CRITICAL_MARKERS.items())
def test_batch_script_ui_check_precedes_critical_commands(filename, markers):
    content = read_script(filename)
    gate_index = content.index('if /i "%~1"=="--ui-check" exit /b 0')

    for marker in markers:
        assert gate_index < content.index(marker)


@pytest.mark.parametrize("filename", SCRIPTS)
def test_batch_script_uses_windows_line_endings_without_bom(filename):
    content = read_script_bytes(filename)
    content_without_crlf = content.replace(b"\r\n", b"")

    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in content_without_crlf
    assert b"\r" not in content_without_crlf


@pytest.mark.parametrize("filename", SCRIPTS)
def test_batch_script_visible_echo_output_is_ascii(filename):
    echo_lines = [
        line
        for line in read_script(filename).splitlines()
        if line.lstrip().casefold().startswith("echo")
    ]

    for line in echo_lines:
        line.encode("ascii")


@pytest.mark.parametrize("filename", SCRIPTS)
def test_batch_script_does_not_echo_an_empty_reset_command(filename):
    lines = [line.strip().casefold() for line in read_script(filename).splitlines()]

    assert "echo %ui_reset%" not in lines


@pytest.mark.parametrize(("filename", "subtitle"), SCRIPTS.items())
def test_batch_script_ui_check_parses_in_classic_cmd(filename, subtitle):
    result = run_ui_check(filename)

    assert result.returncode == 0, result.stderr
    assert "RIVIU REPORTS" in result.stdout
    assert subtitle in result.stdout
    assert "\x1b[" not in result.stdout


@pytest.mark.parametrize("filename", SCRIPTS)
def test_batch_script_ui_check_emits_orange_in_windows_terminal(filename):
    result = run_ui_check(filename, windows_terminal=True)

    assert result.returncode == 0, result.stderr
    assert "\x1b[38;2;255;107;0m" in result.stdout


# Byte offsets at which released capnhat.bat versions resume reading after their
# `git reset --hard` (or `git pull`) replaced the file. cmd.exe reads a batch file
# one statement at a time from a saved offset, so an old capnhat.bat continues in
# the NEW capnhat.bat at these offsets. Each version lists its CRLF checkout (the
# Git for Windows default) and its LF checkout (core.autocrlf=false). Never remove
# an entry: machines still on those versions update through them.
OLD_CAPNHAT_RESUME_OFFSETS = {
    "e3a5ddb, 9e71451 (git pull)": [1674, 1611],
    "e3a5ddb, 9e71451 (reset block)": [1985, 1911],
    "95466a4": [3044, 3606, 2946, 3493],
    "8ce8422": [2982, 3553, 2890, 3447],
    "d970cbf, 74c282d (release e3f5f33)": [4281, 4935, 4154, 4794],
}
OLD_OFFSETS = sorted({offset for offsets in OLD_CAPNHAT_RESUME_OFFSETS.values() for offset in offsets})


def batch_environment(tmp_path, path_dirs, **extra):
    """A cmd environment that cannot see this machine's uv, py launcher or Pythons."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    system32 = Path(system_root) / "System32"
    environment = {
        "SystemRoot": system_root,
        "ComSpec": os.environ.get("ComSpec", str(system32 / "cmd.exe")),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "USERPROFILE": str(tmp_path / "profile"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "PATH": os.pathsep.join([*map(str, path_dirs), str(system32)]),
    }
    environment.update(extra)
    return environment


def run_batch(script, environment, *args, timeout=60):
    return subprocess.run(
        ["cmd", "/d", "/c", str(script), *args],
        cwd=script.parent,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.mark.parametrize("offset", OLD_OFFSETS)
def test_capnhat_old_resume_offsets_fall_in_the_colon_landing_zone(offset):
    content = read_script_bytes("capnhat.bat")
    line_start = content.rfind(b"\r\n", 0, offset) + 2
    line_end = content.index(b"\r\n", offset)

    assert content[line_start:line_end].startswith(b"::::")
    assert content[line_start:line_end].strip(b":") == b""


@pytest.mark.parametrize("offset", [OLD_OFFSETS[0], 4281, OLD_OFFSETS[-1]])
def test_old_capnhat_resuming_in_the_new_file_runs_the_new_update_steps(tmp_path, offset):
    # old.bat overwrites itself with the new capnhat.bat exactly where an old
    # capnhat.bat's reset statement ends, as `git reset --hard` does.
    (tmp_path / "new.bat").write_bytes(read_script_bytes("capnhat.bat"))
    (tmp_path / "thuvien.bat").write_bytes(b"@echo STUB-THUVIEN %*\r\n@exit /b 0\r\n")
    prefix = b'@echo off\r\nsetlocal EnableExtensions\r\nset "DID_STASH=0"\r\n'
    overwrite = b'copy /y "%~dp0new.bat" "%~f0" >nul\r\n'
    padding = offset - len(prefix) - len(overwrite)
    old = prefix + b"REM " + b"x" * (padding - 6) + b"\r\n" + overwrite
    assert len(old) == offset
    (tmp_path / "old.bat").write_bytes(old)

    result = run_batch(tmp_path / "old.bat", batch_environment(tmp_path, []))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB-THUVIEN --called" in result.stdout
    assert "CAP NHAT HOAN TAT" in result.stdout
    # Landing outside the zone runs half a line, which cmd reports on stderr.
    assert "not recognized" not in result.stderr


@pytest.fixture
def isolated_thuvien(tmp_path):
    """thuvien.bat in an empty project folder, with a fake uv on PATH that logs its arguments."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "thuvien.bat").write_bytes(read_script_bytes("thuvien.bat"))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "uv-calls.txt"
    (fake_bin / "uv.cmd").write_text(f'@echo %*>>"{log}"\r\n@exit /b %FAKE_UV_EXIT%\r\n', encoding="ascii")
    return project / "thuvien.bat", fake_bin, log


def test_thuvien_syncs_from_the_lock_with_uv_and_then_uses_the_project_venv(tmp_path, isolated_thuvien):
    script, fake_bin, log = isolated_thuvien
    # An empty venv standing in for the one `uv sync` would create.
    venv_python = script.parent / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, venv_python)
    base_home = Path(getattr(sys, "_base_executable", sys.executable)).parent
    (venv_python.parents[1] / "pyvenv.cfg").write_text(f"home = {base_home}\n", encoding="utf-8")

    # A fresh shell: VENV_PY is not inherited and must be resolved by the helper itself.
    result = run_batch(script, batch_environment(tmp_path, [fake_bin], FAKE_UV_EXIT="0"), "--called")

    assert log.read_text().split() == ["sync", "--locked", "--no-dev"]
    assert "requirements.txt" not in result.stdout
    # The next step ran the project venv's Python, which has no Playwright here.
    assert "No module named playwright" in result.stderr, result.stdout + result.stderr
    assert result.returncode == 1


def test_thuvien_falls_back_to_pip_when_uv_sync_fails(tmp_path, isolated_thuvien):
    script, fake_bin, log = isolated_thuvien

    result = run_batch(script, batch_environment(tmp_path, [fake_bin], FAKE_UV_EXIT="1"), "--called")

    assert log.read_text().split() == ["sync", "--locked", "--no-dev"]
    assert "requirements.txt" in result.stdout
    # No Python on this PATH either: exit code 2 tells setup.bat to install Python.
    assert result.returncode == 2


def test_thuvien_skip_uv_uses_pip_without_running_uv(tmp_path, isolated_thuvien):
    script, fake_bin, log = isolated_thuvien
    environment = batch_environment(tmp_path, [fake_bin], FAKE_UV_EXIT="0", RIVIU_SKIP_UV="1")

    result = run_batch(script, environment, "--called")

    assert not log.exists()
    assert "requirements.txt" in result.stdout
    assert result.returncode == 2
