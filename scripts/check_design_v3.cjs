/* Source and mocked-DOM checks only. Does not open a browser or perform I/O from the page. */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const file = path.join(root, 'docs/design/benchagent-v3-prototype.html');
const source = fs.readFileSync(file, 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/i)[1];
const nodes = [];
const ids = new Map();
const checks = [];
const benchStart = source.indexOf('id="bench-tabs"');
const benchEnd = source.indexOf('</div>', benchStart);
for (const match of source.matchAll(/<([a-z][\w-]*)([^<>]*?)>/gi)) {
  const attrs = {};
  for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
    attrs[attr[1]] = attr[2] === undefined ? '' : attr[2];
  }
  const classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
  const handlers = {};
  const node = {
    tag: match[1], id: attrs.id, attrs, classes, handlers,
    hidden: Object.hasOwn(attrs, 'hidden'),
    disabled: Object.hasOwn(attrs, 'disabled'),
    checked: Object.hasOwn(attrs, 'checked'),
    value: attrs.value || '', textContent: '', dataset: {},
    inBench: match.index > benchStart && match.index < benchEnd,
    setAttribute(key, value) { this.attrs[key] = value; },
    removeAttribute(key) { delete this.attrs[key]; },
    addEventListener(event, fn) { handlers[event] = fn; },
    checkValidity() {
      const number = Number(this.value);
      return this.value !== '' && Number.isFinite(number) &&
        number >= Number(attrs.min) && number <= Number(attrs.max) && Number.isInteger(number);
    },
    fire(event) {
      if (event === 'click' && this.disabled) return;
      if (handlers[event]) handlers[event].call(this, {target: this});
    },
    classList: {
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const active = force === undefined ? !classes.has(name) : force;
        if (active) classes.add(name); else classes.delete(name);
      },
    },
  };
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith('data-')) node.dataset[name.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = value;
  }
  if (node.id) {
    assert(!ids.has(node.id), 'duplicate id: ' + node.id);
    ids.set(node.id, node);
  }
  nodes.push(node);
}
function query(selector) {
  const dataAttr = selector.match(/^\[data-([\w-]+)\]$/);
  if (dataAttr) return nodes.filter(n => Object.hasOwn(n.attrs, 'data-' + dataAttr[1]));
  if (selector === '#bench-tabs button') return nodes.filter(n => n.inBench && n.tag === 'button');
  if (selector === '.frame-check:checked') return nodes.filter(n => n.classes.has('frame-check') && n.checked);
  if (selector.startsWith('.')) return nodes.filter(n => n.classes.has(selector.slice(1)));
  throw new Error('Unknown selector: ' + selector);
}
const document = {
  title: '',
  getElementById(id) {
    assert(ids.has(id), 'missing element: ' + id);
    return ids.get(id);
  },
  querySelectorAll: query,
};
const location = {hash: ''};
const window = {addEventListener() {}};
const history = {replaceState(state, title, hash) { location.hash = hash; }};
function passed(name) { checks.push({name, status: 'PASS'}); }
assert(source.includes('<html lang="zh-CN">'));
for (const match of source.matchAll(/aria-controls="([^"]+)"/g)) assert(ids.has(match[1]));
passed('唯一元素标识与标签目标');
assert(!/\b(fetch|XMLHttpRequest|WebSocket)\s*\(/.test(script));
assert(!/<script[^>]*\bsrc=|<link[^>]*\bhref=|<form\b|\burl\s*\(\s*(?!#)/i.test(source));
assert(source.includes('不连接仿真或模型'));
passed('页面不包含远端资源、网络调用或提交表单');
new vm.Script(script).runInNewContext({document, location, window, history});
const pageIds = ['home','config','running','results','history','reports','report','settings'];
function visiblePage() { return pageIds.filter(id => !ids.get('page-' + id).hidden); }
assert.deepEqual(visiblePage(), ['home']);
passed('脚本语法与初始唯一页面');
for (const route of pageIds) {
  const button = query('[data-route]').find(n => n.dataset.route === route);
  assert(button, 'no route button: ' + route);
  button.fire('click');
  assert.deepEqual(visiblePage(), [route]);
}
passed('八个页面的本地导航');
assert(query('.frame-check').every(n => n.checked && n.disabled));
assert.equal(ids.get('plan-total').textContent, '170 帧');
passed('标准套件锁定三项与总目标');
ids.get('custom-choice').fire('click');
assert(query('.frame-check').every(n => !n.disabled));
const frames = query('.frame-check');
frames.find(n => n.value === '50').checked = false;
frames[0].fire('change');
assert.equal(ids.get('plan-total').textContent, '120 帧');
assert.equal(ids.get('preview-run-button').disabled, false);
passed('自定义白名单子集与总目标');
frames.forEach(n => {n.checked = false;});
frames[0].fire('change');
assert.equal(ids.get('preview-run-button').disabled, true);
frames[0].checked = true; frames[0].fire('change');
ids.get('timeout-input').value = '0'; ids.get('timeout-input').fire('input');
assert.equal(ids.get('preview-run-button').disabled, true);
ids.get('timeout-input').value = '3600';
ids.get('standard-choice').fire('click');
assert(query('.frame-check').every(n => n.checked && n.disabled));
passed('空选、非法超时拒绝与标准模式恢复');
ids.get('result-case').value = '20'; ids.get('result-case').fire('change');
assert(query('[data-selected-frame]').every(n => n.textContent === '20'));
assert(query('[data-result-row]').find(n => n.dataset.resultRow === '20').classList.contains('selected'));
passed('用例选择同步更新标签和高亮');
const metricsTab = query('[data-report-tab]').find(n => n.dataset.reportTab === 'metrics');
metricsTab.fire('click');
assert.equal(ids.get('report-metrics').hidden, false);
assert.equal(ids.get('report-overview').hidden, true);
assert.equal(metricsTab.attrs['aria-selected'], 'true');
passed('报告标签页状态与可访问属性');
const result = {method: 'source inspection and mocked DOM execution; no browser rendering',
  browser_visual_acceptance: 'NOT_PERFORMED', checks, passed: checks.length, failed: 0,
  new_simulations: 0, model_calls: 0};
fs.writeFileSync(path.join(root, 'docs/design/V3_原型逻辑检查.json'), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({passed:checks.length,failed:0,browser_visual_acceptance:result.browser_visual_acceptance}));
