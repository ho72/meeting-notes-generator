const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const downloadSource = source.slice(source.indexOf('  function downloadMd('), source.indexOf('  function syncDownloads('));

test('Markdown export preserves edited Korean text and filename, and delays Blob cleanup', async () => {
  let blob, attached = false, clicked = false, removed = false, revoked = false, cleanup;
  const anchor = {
    click() { assert.equal(attached, true); assert.equal(revoked, false); clicked = true; },
    remove() { removed = true; },
  };
  const context = {
    Blob,
    URL: {
      createObjectURL(value) { blob = value; return 'blob:test-export'; },
      revokeObjectURL(value) { assert.equal(value, 'blob:test-export'); revoked = true; },
    },
    document: {
      createElement(tag) { assert.equal(tag, 'a'); return anchor; },
      body: { appendChild(value) { assert.equal(value, anchor); attached = true; } },
    },
    setTimeout(callback, delay) { assert.ok(delay > 0); cleanup = callback; },
  };
  vm.createContext(context);
  vm.runInContext(downloadSource, context);
  const edited = '# 수정 회의록\n\n기한: 다음 주 화요일\n';
  context.downloadMd('회의_meeting_notes.md', edited);
  assert.equal(await blob.text(), edited);
  assert.equal(blob.type, 'text/markdown;charset=utf-8');
  assert.equal(anchor.download, '회의_meeting_notes.md');
  assert.equal(anchor.href, 'blob:test-export');
  assert.equal(clicked, true);
  assert.equal(removed, true);
  assert.equal(revoked, false);
  cleanup();
  assert.equal(revoked, true);
});
