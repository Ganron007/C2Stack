const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  querySelector() { return new Element(); }
  addEventListener() {}
}

const elements = new Map();
const document = {
  addEventListener() {},
  createElement() { return new Element(); },
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
};
const window = { fetch: async () => ({ status: 200 }) };
const store = { getItem: () => null, setItem() {} };
const context = vm.createContext({
  window,
  document,
  sessionStorage: store,
  localStorage: store,
  URL,
  Headers,
  console,
  setTimeout,
  clearTimeout,
});

const source = fs.readFileSync('Docker/portal/static/portal.js', 'utf8');
vm.runInContext(source, context, { filename: 'portal.js' });

const hostile = '<img src=x onerror=alert(1)>" autofocus';
context.opsRenderSessions([{
  id: hostile,
  backend: hostile,
  hostname: hostile,
  username: hostile,
  process: hostile,
  transport: hostile,
}]);
const sessionHtml = elements.get('ops-tbody').children[0].innerHTML;
assert.ok(sessionHtml.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(!/<img\b/i.test(sessionHtml));

context.opsRenderChips({
  havoc: { ok: false, error: hostile },
});
const chipsHtml = elements.get('backend-chips').children[2].innerHTML;
assert.ok(chipsHtml.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(!/<img\b/i.test(chipsHtml));

console.log('portal session and backend metadata render as escaped text');
