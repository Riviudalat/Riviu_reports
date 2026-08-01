import { readFile, writeFile } from 'node:fs/promises';

const version = process.argv[2];
if (!/^\d+\.\d+\.\d+$/.test(version || '')) {
  throw new Error('Expected a semantic version such as 0.1.42.');
}

const packagePath = new URL('../package.json', import.meta.url);
const tauriConfigPath = new URL('../src-tauri/tauri.conf.json', import.meta.url);

for (const filePath of [packagePath, tauriConfigPath]) {
  const file = JSON.parse(await readFile(filePath, 'utf8'));
  file.version = version;
  await writeFile(filePath, `${JSON.stringify(file, null, 2)}\n`, 'utf8');
}

console.log(`Desktop version set to ${version}`);
