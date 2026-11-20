// Run with node --test tests/web_console_interaction.test.cjs.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const web = path.join(__dirname, '../tokensfree/edge/web');
const html = readFileSync(path.join(web, 'index.html'), 'utf8');
const app = readFileSync(path.join(web, 'app.js'), 'utf8');

const tick = () => new Promise(resolve => setImmediate(resolve));

async function fixture() {
  class Element {
    constructor() {
      this.children = [];
      this.events = {};
      this.value = '';
      this.textContent = '';
      this.classList = { toggle() {} };
    }
    addEventListener(type, fn) { this.events[type] = fn; }
    emit(type, options = {}) {
      const event = { preventDefault() { this.prevented = true; }, ...options };
      this.events[type](event);
      return event;
    }
    appendChild(node) { this.children.push(node); }
    insertBefore(node) { this.children.unshift(node); }
    querySelector() { return null; }
    remove() {
      transcript.children = transcript.children.filter(node => node !== this);
    }
  }
  const els = Object.fromEntries(Array.from(html.matchAll(/ id="([^"]+)"/g), ([, id]) => [id, new Element()]));
  const transcript = els.transcript;
  els['chat-form'].requestSubmit = () => els['chat-form'].emit('submit');
  const requests = [];
  const fetch = async (url, options) => {
    if (url === '/v1/chat/completions') {
      return new Promise((resolve, reject) => {
        const signal = options.signal;
        requests.push({ payload: JSON.parse(options.body), resolve });
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    }
    const response = {
      '/api/session': { token: 'fixture', base_url: 'http://127.0.0.1/v1' },
      '/v1/models': { data: [] },
      '/api/status': {},
      '/api/profiles': { ok: true, enabled: false },
    };
    assert.ok(url in response, url);
    return { ok: true, json: async () => response[url] };
  };
  vm.runInNewContext(app, {
    document: { getElementById: id => els[id], createElement: () => new Element() },
    window: { addEventListener() {} },
    setInterval: () => 1,
    AbortController,
    TextDecoder,
    fetch,
  });
  await tick();
  return { els, requests, transcript };
}

test('Enter submits, but composition confirmation and Shift+Enter do not', async () => {
  const { els, requests } = await fixture();
  els.prompt.value = '你好';
  assert.equal(els.prompt.emit('keydown', { key: 'Enter', isComposing: true }).prevented, undefined);
  assert.equal(els.prompt.emit('keydown', { key: 'Enter', keyCode: 229 }).prevented, undefined);
  assert.equal(els.prompt.emit('keydown', { key: 'Enter', shiftKey: true }).prevented, undefined);
  assert.equal(requests.length, 0);
  assert.equal(els.prompt.value, '你好');
  assert.equal(els.prompt.emit('keydown', { key: 'Enter' }).prevented, true);
  assert.equal(requests[0].payload.messages[0].content, '你好');
});

for (const stream of [false, true]) {
  test(`busy guard, stop and recovery (stream=${stream})`, async () => {
    const { els, requests, transcript } = await fixture();
    els['stream-toggle'].checked = stream;
    els.prompt.value = 'first';
    els['chat-form'].emit('submit');
    assert.equal(els['send-btn'].disabled, true);
    assert.equal(els['stop-btn'].disabled, false);
    assert.equal(requests[0].payload.stream, stream);
    els.prompt.value = 'second';
    els['chat-form'].emit('submit');
    assert.equal(requests.length, 1);
    assert.equal(els.prompt.value, 'second');
    els['stop-btn'].emit('click');
    await tick();
    assert.equal(transcript.children.at(-1).textContent, '已停止等待响应');
    assert.equal(els['send-btn'].disabled, false);
    assert.equal(els['stop-btn'].disabled, true);
    els['chat-form'].emit('submit');
    const finish = stream
      ? { body: { getReader: () => {
        const chunks = [
          'data: {"choices":[{"delta":{"content":"fixture response"}}]}\n\n',
          'data: [DONE]\n\n',
        ];
        return { read: async () => chunks.length
          ? { value: Buffer.from(chunks.shift()), done: false }
          : { done: true } };
      } } }
      : { json: async () => ({ choices: [{ message: { content: 'fixture response' } }] }) };
    requests[1].resolve({ ok: true, ...finish });
    await tick();
    assert.equal(transcript.children.at(-2).textContent, 'fixture response');
    assert.equal(els['send-btn'].disabled, false);
  });
}
