import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "Khoidong.bat": "KHOI DONG HE THONG",
    "capnhat.bat": "CAP NHAT PHIEN BAN",
    "setup.bat": "CAI DAT HE THONG",
}

CRITICAL_MARKERS = {
    "Khoidong.bat": [
        r"call .venv\Scripts\activate.bat",
        "Get-NetTCPConnection -LocalPort 1231",
        '"%VENV_PY%" app.py',
    ],
    "capnhat.bat": [
        'stash push -u -m "capnhat-auto-stash"',
        "fetch --all --prune",
        'reset --hard "origin/%CUR_BRANCH%"',
        "pip install -r requirements.txt --upgrade --quiet",
    ],
    "setup.bat": [
        '"%PY%" -m venv .venv',
        "pip install -r requirements.txt",
        "-m playwright install chromium",
        "import fastapi, uvicorn, pandas, openpyxl",
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
