/* Source checks and mocked DOM execution only; never opens a browser or starts tasks. */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const dir = path.join(root, 'docs/design');
const source = fs.readFileSync(path.join(dir, 'benchagent-v3-1-prototype.html'), 'utf8');
const script = fs.readFileSync(path.join(dir, 'benchagent-v3-1.js'), 'utf8');
const css = fs.readFileSync(path.join(dir, 'benchagent-v3-1.css'), 'utf8');
const nodes = [], ids = new Map(), checks = [];
const navStart = source.indexOf('<nav class="nav"'), navEnd = source.indexOf('</nav>', navStart);
for (const match of source.matchAll(/<([a-z][\w-]*)([^<>]*?)>/gi)) {
  const attrs = {};
  for (const item of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
    attrs[item[1]] = item[2] === undefined ? '' : item[2];
  }
  const classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
  const handlers = {};
  const node = {
    tag: match[1], id: attrs.id, attrs, handlers, classes, dataset: {},
    textContent: '', value: attrs.value || '',
    hidden: Object.hasOwn(attrs, 'hidden'), disabled: Object.hasOwn(attrs, 'disabled'),
    inNav: match.index > navStart && match.index < navEnd,
    addEventListener(event, fn) { handlers[event] = fn; },
    setAttribute(name, value) { attrs[name] = value; },
    removeAttribute(name) { delete attrs[name]; },
    fire(event) { if (event === 'click' && this.disabled) return; if (handlers[event]) handlers[event].call(this, {target:this}); },
    classList: {
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const active = force === undefined ? !classes.has(name) : force;
        if (active) classes.add(name); else classes.delete(name);
      },
    },
  };
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('data-')) node.dataset[key.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = value;
  }
  if (node.id) { assert(!ids.has(node.id), 'duplicate id: ' + node.id); ids.set(node.id, node); }
  nodes.push(node);
}
function get(id) { assert(ids.has(id), 'missing element: ' + id); return ids.get(id); }
function query(selector) {
  if (selector === '.nav [data-route]') return nodes.filter(n => n.inNav && Object.hasOwn(n.attrs, 'data-route'));
  const match = selector.match(/^\[data-([\w-]+)\]$/);
  if (match) return nodes.filter(n => Object.hasOwn(n.attrs, 'data-' + match[1]));
  throw new Error('Unsupported test selector: ' + selector);
}
const window = {handlers:{}, addEventListener(event, fn) {this.handlers[event] = fn;}};
const location = {hash:''};
const history = {replaceState(state, title, hash) { location.hash = hash; }};
const document = {title:'', getElementById:get, querySelectorAll:query};
function pass(name) { checks.push({name, status:'PASS'}); }
function mode(value) { query('[data-mode]').find(n => n.dataset.mode === value).fire('click'); }
function view(name) { query('.nav [data-route]').find(n => n.dataset.route === name).fire('click'); }
function visible() { return ['smart','tasks','history','reports','profiles','settings'].filter(v => !get('page-' + v).hidden); }
assert(source.includes('<html lang="zh-CN">'));
assert(source.includes('href="benchagent-v3-1.css"') && source.includes('src="benchagent-v3-1.js"'));
assert(!/\b(?:fetch|XMLHttpRequest|WebSocket|setTimeout|setInterval)\s*\(/.test(script));
assert(!/<form\b|\bhttps?:\/\/|api[-_]key/i.test(source + script + css));
pass('本地资源、中文标签与零网络零执行入口');
for (const match of source.matchAll(/aria-controls="([^"]+)"/g)) assert(ids.has(match[1]));
pass('元素标识唯一与标签目标存在');
new vm.Script(script).runInNewContext({document, window, location, history});
assert.deepEqual(visible(), ['smart']);
assert(get('mode-note').textContent.includes('合成'));
assert.equal(query('.nav [data-route]').length, 6);
pass('智能测试默认视图与合成来源标识');
for (const name of ['smart','tasks','history','reports','profiles','settings']) {
  view(name); assert.deepEqual(visible(), [name]);
}
pass('六项侧栏导航');
view('smart');
const n = Number(get('overview-frame').textContent);
assert.equal(n, 100);
assert.equal(get('time-line').attrs.points.split(' ').length, n);
assert.equal(get('newton-line').attrs.points.split(' ').length, n);
assert.equal(get('pcg-line').attrs.points.split(' ').length, n);
assert.equal(get('key-5-frame').textContent, String(n));
pass('三条示例曲线与关键帧覆盖目标长度');
const newtonTotal = Number(get('metric-newton-total').textContent);
const pcgTotal = Number(get('metric-pcg-total').textContent);
assert.equal(get('metric-newton-mean').textContent, (newtonTotal/n).toFixed(2));
assert.equal(get('metric-pcg-mean').textContent, (pcgTotal/n).toFixed(2));
const seconds = parseFloat(get('overview-total').textContent);
const mean = Number(get('metric-mean').textContent);
assert(Math.abs(seconds*1000/n - mean) <= .011);
assert.equal(get('overview-mean').textContent, mean.toFixed(2) + ' 毫秒');
assert.equal(get('report-total').textContent, get('overview-total').textContent + '（合成）');
pass('均值、总量和报告展示的分母与数值一致');
mode('empty');
assert.equal(get('metric-mean').textContent, '—');
assert.equal(get('overview-state').textContent, '未执行');
assert.equal(get('time-line').attrs.points, '');
assert.equal(get('newton-line').attrs.points, '');
assert.equal(get('pcg-line').attrs.points, '');
assert.equal(get('demo-chat').hidden, true);
assert.equal(get('key-5-time').textContent, '—');
assert(query('[data-stage]').every(n => !n.classList.contains('demo')));
pass('未执行状态不补零、不保留合成曲线或完成态');
get('edit-plan').fire('click'); assert.equal(get('plan-editor').hidden, false);
get('plan-frames').value = '50'; get('apply-plan').fire('click');
assert.equal(get('plan-frame-count').textContent, '50');
assert.equal(get('overview-frame').textContent, '50');
assert.equal(get('plan-editor').hidden, true);
assert.equal(get('overview-state').textContent, '未执行');
mode('demo');
assert.equal(get('time-line').attrs.points.split(' ').length, 50);
assert.equal(get('key-5-frame').textContent, '50');
pass('修改计划仅预览并按新帧数联动');
get('prompt').value = '对默认场景运行20帧，分析耗时和迭代';
get('send-prompt').fire('click');
assert.equal(get('plan-frame-count').textContent, '20');
assert.equal(get('prompt').value, '');
assert(get('prompt-status').textContent.includes('未启动任务'));
assert.equal(get('overview-state').textContent, '未执行');
pass('中文固定请求生成本地规则计划');
const previous = get('request-bubble').textContent;
for (const prompt of ['默认场景运行19帧', '默认场景运行20帧并修改PCG容差', '其他场景运行20帧', '默认场景执行cmd.exe运行100帧']) {
  get('prompt').value = prompt; get('send-prompt').fire('click');
  assert.equal(get('request-bubble').textContent, previous);
  assert.equal(get('plan-frame-count').textContent, '20');
}
pass('拒绝非法帧数、场景和物理参数修改');
query('[data-case]').find(n => n.dataset.case === '100').fire('click');
assert.deepEqual(visible(), ['smart']);
assert.equal(get('overview-frame').textContent, '100');
assert.equal(get('overview-state').textContent, '未执行');
pass('基准任务入口只更新计划、不运行任务');
view('reports');
const tab = query('[data-report-tab]').find(n => n.dataset.reportTab === 'checks');
tab.fire('click');
assert.equal(get('report-checks-panel').hidden, false);
assert.equal(get('report-summary-panel').hidden, true);
assert.equal(tab.attrs['aria-selected'], 'true');
pass('报告标签切换和可访问状态');
location.hash = '#unknown'; window.handlers.hashchange();
assert.deepEqual(visible(), ['smart']);
pass('无效导航返回智能测试页');
assert(/disabled>▶ 开始执行（待接入）/.test(source));
assert(/disabled>↓ 下载报告（PDF）/.test(source));
assert(/模型待接入/.test(source));
assert(!/大模型已连接|RTX 4090|CUDA 12\.4/.test(source));
pass('真实执行、模型连接和导出均未伪装可用');
const report = {
  method:'HTML/CSS source inspection and mocked DOM execution; not browser rendering',
  passed:checks.length, failed:0, checks,
  source_mode:'synthetic-demo-and-empty-state',
  browser_visual_acceptance:'NOT_PERFORMED',
  new_simulations:0, model_calls:0,
};
fs.writeFileSync(path.join(dir, 'V3.1_原型逻辑检查.json'), JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({passed:report.passed,failed:0,browser_visual_acceptance:report.browser_visual_acceptance}));
