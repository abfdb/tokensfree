// Dependency-free DOM contract smoke: node --test tests/catalog_ui.test.cjs
// Covers the controller, not browser layout or native file-dialog mechanics.
'use strict';
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const { createContext, runInContext } = require('node:vm');
const directory = resolve(__dirname, '../static/catalog');
const read = name => readFileSync(resolve(directory, name), 'utf8');
const key = 'shiguang-ai-records-v1';

function setup(saved = '{}') {
  const document = { activeElement: null };
  class Node {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.attributes = {};
      this.handlers = {};
      this.value = '';
      this.textContent = '';
      this.classList = { add: (...names) => { this.className += ' ' + names.join(' '); } };
    }
    append(...children) { this.children.push(...children); }
    addEventListener(event, fn) { this.handlers[event] = fn; }
    setAttribute(key, value) { this.attributes[key] = value; }
    emit(event) { return this.handlers[event]?.(); }
    click() { this.clicked = true; return this.emit('click'); }
    focus() { document.activeElement = this; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  }

  const elements = new Map(Array.from(read('index.html').matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"/g),
    ([, tag, id]) => [id, new Node(tag)]));
  const byId = id => elements.get(id);
  document.getElementById = byId;
  document.createElement = tag => new Node(tag);
  const storage = new Map([[key, saved]]);
  const state = { approve: false };
  const context = createContext({
    document,
    localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) },
    confirm: () => state.approve,
    Blob,
    URL: { createObjectURL: blob => { state.download = blob; return 'blob:fixture'; }, revokeObjectURL() {} },
    setTimeout() {},
  });
  runInContext(read('data.js'), context);
  runInContext(read('app.js'), context);
  const visible = () => byId('cards').children.filter(card => !card.hidden);
  const find = (node, tag) => node.tag === tag ? node : node.children.map(child => find(child, tag)).find(Boolean);
  const file = contents => {
    byId('import').files = [{ size: contents.length, text: async () => contents }];
    byId('import').value = 'chosen';
    return byId('import').emit('change');
  };
  return { document, byId, storage, state, visible, find, file };
}

test('paid category, keyword matching, no results and accessible reset', () => {
  const { document, byId, visible, find } = setup();
  const total = visible().length;
  assert.equal(total, require(resolve(directory, 'data.js')).length);
  const paid = byId('filters').children.find(node => node.textContent === '付费订阅权益');
  paid.click();
  assert.equal(paid.attributes['aria-pressed'], 'true');
  assert.equal(visible().length, 1);
  assert.match(find(visible()[0], 'h3').textContent, /Inference Providers/);
  byId('search').value = 'gpu no-match';
  byId('search').emit('input');
  assert.equal(visible().length, 0);
  assert.equal(byId('empty').hidden, false);
  byId('reset').click();
  assert.equal(document.activeElement, byId('search'));
  assert.equal(paid.attributes['aria-pressed'], 'false');
  assert.equal(byId('reset').hidden, true);
  assert.equal(visible().length, total);
  byId('search').value = '文本 OPENROUTER';
  byId('search').emit('input');
  assert.equal(visible().length, 1);
  assert.equal(find(visible()[0], 'h3').textContent, 'OpenRouter');
});

test('saving preserves card controls and moves focus when a record disappears', () => {
  const { document, byId, visible, find, storage } = setup();
  const card = visible().find(node => find(node, 'h3').textContent === 'OpenRouter');
  const details = find(card, 'details');
  const status = find(card, 'select');
  details.open = true;
  status.value = '已启用';
  status.emit('change');
  assert.equal(byId('tracked').textContent, 1);
  byId('mine').checked = true;
  byId('mine').emit('change');
  assert.deepEqual(visible(), [card]);
  assert.equal(find(visible()[0], 'details').open, true);
  assert.deepEqual(JSON.parse(storage.get(key)).openrouter, { status: '已启用', expiry: '' });

  status.focus();
  status.value = '未申请';
  status.emit('change');
  assert.equal(visible().length, 0);
  assert.equal(document.activeElement, byId('mine'));
});

test('backup compatibility: import cancellation, malformed dates, merge, export and reload', async () => {
  const old = { groq: { status: '已启用', expiry: '' } };
  const { byId, storage, state, file } = setup(JSON.stringify(old));
  assert.equal(byId('import-trigger').tag, 'button');
  byId('import-trigger').click();
  assert.equal(byId('import').clicked, true);
  const incoming = { openrouter: { status: '已申请', expiry: '2000-01-09' } };
  const encoded = JSON.stringify({ version: 1, records: incoming });
  await file(encoded);
  assert.equal(byId('import').value, '');
  assert.deepEqual(JSON.parse(storage.get(key)), old);

  state.approve = true;
  await file(encoded.replace('2000-01-09', '2030-02-31'));
  assert.match(byId('message').textContent, /无效日期/);
  assert.deepEqual(JSON.parse(storage.get(key)), old);
  await file(encoded);
  const expected = { ...old, ...incoming };
  assert.deepEqual(JSON.parse(storage.get(key)), expected);
  assert.equal(byId('tracked').textContent, 2);
  assert.match(byId('due').textContent, /^1 项已到期或将在 7 天内到期$/);
  byId('export').click();
  const backup = JSON.parse(await state.download.text());
  assert.equal(backup.version, 1);
  assert.deepEqual(backup.records, expected);
  assert.equal(setup(storage.get(key)).byId('tracked').textContent, 2);
});
