/* Offline prototype only: no provider, executor, timers, downloads or network calls. */
(function () {
  'use strict';
  var frameCount = 100;
  var mode = 'demo';
  var views = ['smart', 'tasks', 'history', 'reports', 'profiles', 'settings'];
  var titles = {
    smart: '智能基准测试（模型接口预留）', tasks: '基准任务',
    history: '运行记录', reports: '测试报告', profiles: '配置管理', settings: '系统设置'
  };
  function el(id) { return document.getElementById(id); }
  function text(id, value) { el(id).textContent = value; }
  function number(value, digits) { return value.toFixed(digits); }
  function count(value) { return String(value); }
  function showView(name) {
    if (views.indexOf(name) < 0) { name = 'smart'; }
    views.forEach(function (view) { el('page-' + view).hidden = view !== name; });
    document.querySelectorAll('.nav [data-route]').forEach(function (button) {
      var active = button.dataset.route === name;
      button.classList.toggle('active', active);
      if (active) { button.setAttribute('aria-current', 'page'); }
      else { button.removeAttribute('aria-current'); }
    });
    text('page-title', titles[name]);
    document.title = titles[name] + ' · V3.1 离线原型';
    history.replaceState(null, '', '#' + name);
  }
  function createSyntheticFrames(n) {
    return Array.from({ length: n }, function (_, i) {
      return {
        frame: i + 1,
        time: Number((43 + 34 * Math.exp(-i / 3) + 2.6 * Math.sin(i * 1.7) + 1.5 * Math.cos(i / 2.1)).toFixed(2)),
        newton: 12 + (i % 9 === 0 ? 1 : 0) - (i % 17 === 0 ? 1 : 0),
        pcg: 84 + Math.round(4 * Math.sin(i * 0.8)) + (i % 7 === 0 ? 2 : 0)
      };
    });
  }
  function total(frames, field) {
    return frames.reduce(function (sum, frame) { return sum + frame[field]; }, 0);
  }
  function points(frames, field, ceiling) {
    return frames.map(function (frame, i) {
      return (31 + i / (frames.length - 1) * 198).toFixed(2) + ',' +
        (146 - frame[field] / ceiling * 128).toFixed(2);
    }).join(' ');
  }
  function render() {
    var demo = mode === 'demo';
    var frames = createSyntheticFrames(frameCount);
    var milliseconds = total(frames, 'time');
    var newton = total(frames, 'newton');
    var pcg = total(frames, 'pcg');
    // Aggregate fixture value only; it is not used to infer per-frame collisions.
    var collisionCounter = frameCount * 34 + 28;
    text('plan-frame-count', String(frameCount));
    el('plan-frames').value = String(frameCount);
    text('overview-frame', String(frameCount));
    text('report-frame', String(frameCount));
    text('plan-state', '未执行');
    text('mode-note', demo ? '当前显示：合成数据演示，不是实验结果' : '当前显示：未执行，无实验数据');
    text('dashboard-state', demo ? '合成示例 · 非真实任务' : '未执行');
    text('overview-state', demo ? '合成示例' : '未执行');
    text('overview-total', demo ? number(milliseconds / 1000, 3) + ' 秒' : '未采集');
    text('overview-mean', demo ? number(milliseconds / frameCount, 2) + ' 毫秒' : '未采集');
    text('metric-mean', demo ? number(milliseconds / frameCount, 2) : '—');
    text('metric-newton-mean', demo ? number(newton / frameCount, 2) : '—');
    text('metric-pcg-mean', demo ? number(pcg / frameCount, 2) : '—');
    text('metric-newton-total', demo ? count(newton) : '—');
    text('metric-pcg-total', demo ? count(pcg) : '—');
    text('metric-collision-total', demo ? count(collisionCounter) : '—');
    el('demo-chat').hidden = !demo;
    text('chat-total', '合成 ' + frameCount + ' 帧：物理步总耗时 ' + number(milliseconds / 1000, 3) + ' 秒。');
    text('chat-mean', '示例均帧耗时 ' + number(milliseconds / frameCount, 2) +
      ' 毫秒；PCG 平均 ' + number(pcg / frameCount, 2) + ' 次/帧。');
    document.querySelectorAll('[data-mode]').forEach(function (button) {
      var active = button.dataset.mode === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-stage]').forEach(function (stage) {
      stage.classList.toggle('demo', demo);
    });
    var timeCeiling = Math.ceil(Math.max.apply(null, frames.map(function (f) { return f.time; })) / 20) * 20;
    var iterationCeiling = Math.ceil(Math.max.apply(null, frames.map(function (f) { return Math.max(f.newton, f.pcg); })) / 20) * 20;
    document.querySelectorAll('[data-time-y]').forEach(function (tick) {
      tick.textContent = demo ? String(timeCeiling * Number(tick.dataset.timeY) / 4) : '';
    });
    document.querySelectorAll('[data-iteration-y]').forEach(function (tick) {
      tick.textContent = demo ? String(iterationCeiling * Number(tick.dataset.iterationY) / 4) : '';
    });
    text('time-last-frame', String(frameCount));
    text('iteration-last-frame', String(frameCount));
    el('time-line').setAttribute('points', demo ? points(frames, 'time', timeCeiling) : '');
    el('newton-line').setAttribute('points', demo ? points(frames, 'newton', iterationCeiling) : '');
    el('pcg-line').setAttribute('points', demo ? points(frames, 'pcg', iterationCeiling) : '');
    el('time-empty').hidden = demo;
    el('iteration-empty').hidden = demo;
    text('conclusion-title', demo ? '合成演示说明' : '等待真实任务证据');
    text('conclusion-one', demo ? '所有数值均为合成样例，不是实际性能成绩。' : '尚未执行任务，不预设通过或失败。');
    text('conclusion-two', demo ? '曲线、关键帧和均值来自同一份示例数据。' : '无有效数据时不绘制曲线、不用零补缺失值。');
    text('conclusion-three', '模型待接入，不据此评价数值正确性或性能优劣。');
    var indices = [0, Math.round(frameCount * .2) - 1, Math.round(frameCount * .4) - 1,
      Math.round(frameCount * .6) - 1, Math.round(frameCount * .8) - 1, frameCount - 1];
    indices.forEach(function (index, row) {
      var frame = frames[index];
      ['frame', 'time', 'newton', 'pcg'].forEach(function (field) {
        text('key-' + row + '-' + field, demo ? (field === 'time' ? number(frame.time, 2) : String(frame[field])) : '—');
      });
    });
    var log = [
      '[合成示例] 以下是日志布局文案，非进程输出。',
      '[合成示例] 目标：默认耦合场景 · ' + frameCount + ' 帧',
      '[合成示例] 第 1 帧：牛顿 ' + frames[0].newton + '，PCG ' + frames[0].pcg,
      '[合成示例] 第 ' + frameCount + ' 帧：牛顿 ' + frames[frameCount - 1].newton + '，PCG ' + frames[frameCount - 1].pcg,
      '[合成示例] 总物理步耗时：' + number(milliseconds / 1000, 3) + ' 秒',
      '[合成示例] 没有运行仿真，也没有连接模型。'
    ];
    text('log-summary', demo ? log.join('\n') : '未载入真实进程日志。\n原型不会启动任务或生成执行日志。');
    text('report-source', demo ? '合成数据展示 · 不是实际实验报告' : '未载入真实报告');
    text('report-notice', demo ? '合成演示仅用于确认布局，不产生真实性能结论。' : '尚未载入报告，不补判检查通过。');
    text('report-total', demo ? number(milliseconds / 1000, 3) + ' 秒（合成）' : '未采集');
    text('report-mean', demo ? number(milliseconds / frameCount, 2) + ' 毫秒（合成）' : '未采集');
  }
  function chooseFrame(n, request) {
    if ([20, 50, 100].indexOf(n) < 0) { return; }
    frameCount = n;
    mode = 'empty';
    text('request-bubble', request || '对默认耦合场景运行 ' + n + ' 帧，分析耗时与迭代次数。');
    text('prompt-status', '规则计划已更新；模型未接入，未启动任务。');
    render();
  }
  document.querySelectorAll('[data-route]').forEach(function (button) {
    button.addEventListener('click', function () { showView(button.dataset.route); });
  });
  document.querySelectorAll('[data-mode]').forEach(function (button) {
    button.addEventListener('click', function () { mode = button.dataset.mode; render(); });
  });
  el('edit-plan').addEventListener('click', function () { el('plan-editor').hidden = !el('plan-editor').hidden; });
  el('apply-plan').addEventListener('click', function () {
    chooseFrame(Number(el('plan-frames').value));
    el('plan-editor').hidden = true;
  });
  document.querySelectorAll('[data-case]').forEach(function (button) {
    button.addEventListener('click', function () {
      chooseFrame(Number(button.dataset.case));
      showView('smart');
    });
  });
  el('send-prompt').addEventListener('click', function () {
    var request = el('prompt').value.trim();
    if (!request) { text('prompt-status', '请先输入默认场景的任务需求。'); return; }
    if (/容差|摩擦|时间步|powershell|cmd\.exe|shell|切换场景|bunny|table|sphere7|stack10|hang|场景\s*[2-9]/i.test(request)) {
      text('prompt-status', '当前仅预览固定默认场景与帧数，不支持更改求解参数或场景。'); return;
    }
    if (!/默认|default/i.test(request)) {
      text('prompt-status', '请明确指定默认场景。'); return;
    }
    var requested = Array.from(request.matchAll(/(\d+)\s*帧/g));
    if (requested.length !== 1 || [20, 50, 100].indexOf(Number(requested[0][1])) < 0) {
      text('prompt-status', '请选择一个固定帧数：20、50 或 100 帧。'); return;
    }
    chooseFrame(Number(requested[0][1]), request);
    el('prompt').value = '';
  });
  document.querySelectorAll('[data-report-tab]').forEach(function (button) {
    button.addEventListener('click', function () {
      var selected = button.dataset.reportTab;
      document.querySelectorAll('[data-report-tab]').forEach(function (other) {
        other.classList.toggle('active', other === button);
        other.setAttribute('aria-selected', String(other === button));
      });
      document.querySelectorAll('[data-report-panel]').forEach(function (panel) {
        panel.hidden = panel.dataset.reportPanel !== selected;
      });
    });
  });
  window.addEventListener('hashchange', function () { showView(location.hash.slice(1)); });
  render();
  showView(location.hash.slice(1) || 'smart');
})();
