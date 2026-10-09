import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Runs desktop/build_sidecar.py with, in order: $PYTHON; `uv run --locked`
// (syncs .venv to uv.lock, dev group included, so PyInstaller is present);
// the .venv interpreter; `python` from PATH.
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const buildArgs = ['desktop/build_sidecar.py', ...process.argv.slice(2)];
const venvPython = process.platform === 'win32'
  ? join(root, '.venv', 'Scripts', 'python.exe')
  : join(root, '.venv', 'bin', 'python');

function hasUv() {
  const probe = spawnSync('uv', ['--version'], { cwd: root, stdio: 'ignore' });
  return !probe.error && probe.status === 0;
}

let command;
let args;
if (process.env.PYTHON) {
  [command, args] = [process.env.PYTHON, buildArgs];
} else if (hasUv()) {
  [command, args] = ['uv', ['run', '--locked', 'python', ...buildArgs]];
} else {
  [command, args] = [existsSync(venvPython) ? venvPython : 'python', buildArgs];
}

const result = spawnSync(command, args, {
  cwd: root,
  stdio: 'inherit',
});

if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
