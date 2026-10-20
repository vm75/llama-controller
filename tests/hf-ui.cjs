// Run with: node tests/hf-ui.cjs (no packages required).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('server/templates/index.html', 'utf8');
const start = html.indexOf('    async function downloadFromHf()');
const end = html.indexOf('    function formatSize(', start);

async function check(response, expected) {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {disabled: false, style: {}, classList: {add() {}, remove() {}}});
      return elements.get(id);
    },
    querySelectorAll() { return [{dataset: {paths: '["model.gguf"]', role: 'model'}}]; }
  };
  const context = vm.createContext({document, hfManifest: {repo: 'owner/repo', revision: 'main'}, fetch: async () => response, TextDecoder});
  vm.runInContext(html.slice(start, end), context);
  await context.downloadFromHf();
  for (const id of ['hf-download-btn', 'hf-source', 'hf-inspect-btn']) assert.equal(document.getElementById(id).disabled, false);
  assert.match(document.getElementById('hf-status').innerText, expected);
}
(async () => {
  await check({ok: false, json: async () => ({message: 'Repository unavailable'})}, /Repository unavailable/);
  await check({ok: true, body: {getReader: () => ({read: async () => ({done: true})})}}, /ended before completion/);
  console.log('HF UI HTTP error and interrupted-stream regressions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
