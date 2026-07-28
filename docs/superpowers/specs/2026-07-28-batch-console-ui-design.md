# Riviu Batch Console UI Design

## Goal

Give `Khoidong.bat`, `capnhat.bat`, and `setup.bat` one consistent Riviu Reports console appearance while preserving their current startup, update, and installation behavior.

## Visual Design

- Use `RIVIU REPORTS` as the visible product heading and keep each script action as the subtitle.
- Use a black background with built-in console orange (`color 06`) as the universal fallback.
- When the script is running in Windows Terminal, use ANSI true-color orange `#ff6b00`, white body text, green success, yellow warning, red error, and dim secondary text.
- Keep all output ASCII-only so Windows code pages cannot corrupt labels or borders.
- Use the same 72-character divider, status labels, spacing, and completion panel in all three scripts.

## Stability Constraints

1. Each script remains self-contained. A missing shared helper must never block setup, startup, or update.
2. ANSI styling is enabled only when `WT_SESSION` is present. Classic CMD receives ordinary text under `color 06`; escape codes are never printed there.
3. Styling initialization uses built-in CMD commands only and has no network, registry, or PowerShell dependency.
4. Existing commands, labels, environment variables, branch logic, retry logic, exits, and pauses remain semantically unchanged.
5. No presentation command may be inserted between a command and its `if errorlevel` check.
6. User data and Git state handling in `capnhat.bat` remain unchanged.

## Script Structure

Each file defines the same color variables near the top. The rest of the script uses those variables in existing `echo` output:

- `UI_ORANGE`: brand headings and dividers.
- `UI_TEXT`: ordinary instructions.
- `UI_OK`: successful checks and completed steps.
- `UI_WARN`: recoverable warnings and prompts.
- `UI_ERROR`: terminal errors.
- `UI_DIM`: secondary information such as paths, ports, and versions.
- `UI_RESET`: restore the default before external commands, pauses, and exit.

The variables are empty in fallback mode, so the same output remains readable without ANSI support.

## Verification

- Static tests assert that all three files contain the Riviu heading, orange fallback, Windows Terminal ANSI gate, reset handling, and action-specific subtitle.
- Static tests also assert that critical business commands remain present, including server startup, Git fetch/reset/stash, virtual environment creation, dependency installation, and Playwright installation.
- Each script accepts an internal `--ui-check` argument that renders its real banner and exits successfully before executing network, installation, Git mutation, or server processes. Pytest invokes this mode through CMD and verifies both fallback and ANSI output.
- The complete Python test suite and `git diff --check` must pass.
