'use strict';

const $ = id => document.getElementById(id);
const state = {
  runs: [], overall: null, workloads: [],
  selectedWorkload: null, comparison: null, selectedSlice: null,
  evidence: null, analyses: [], baselineRelation: '',
  workloadFilter: 'all', counterCategory: 'all', version: 0,
  history: null, trendRows: [], trendScope: 'suite', trendMode: 'absolute',
  trendMetric: null, trendRequest: 0,
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}
function number(value, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'N/A';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(Number(value));
}
function signed(value, digits = 3) {
  return value === null || value === undefined ? 'N/A' : (value > 0 ? '+' : '') + number(value, digits);
}
function tone(value, higherIsBetter = false) {
  if (value === null || value === undefined || value === 0) return 'neutral';
  return (higherIsBetter ? value < 0 : value > 0) ? 'bad' : 'good';
}
function label(run) {
  return run.short_sha + ' · ' + run.config + ' · ' + run.status;
}
function query(path, params) {
  return path + '?' + new URLSearchParams(params);
}
async function getJson(path, params) {
  const response = await fetch(params ? query(path, params) : path);
  if (!response.ok) throw new Error(path + ' HTTP ' + response.status);
  return response.json();
}
function notice(message, kind = '') {
  $('pair-note').className = 'notice' + (kind ? ' ' + kind : '');
  $('pair-note').textContent = message;
}
function empty(element, message) {
  $(element).innerHTML = '<div class="empty">' + esc(message) + '</div>';
}
function resetDetails() {
  state.comparison = null;
  state.selectedSlice = null;
  state.evidence = null;
  state.analyses = [];
  empty('workload-detail', '选择一个 workload 查看加权 CPI 和覆盖率。');
  empty('slice-table', '选择一个 workload 查看切片贡献。');
  empty('slice-detail', '选择一个切片查看 ROI 指标和原始日志。');
  $('counter-categories').innerHTML = '';
  empty('counter-table', '选择一个切片查看性能计数器。');
  empty('analysis-list', '选择一个 workload 查看已登记的阶段分析。');
}

function trendSpec() {
  if (state.trendScope === 'suite') {
    return { level: 'suite', object: 'SPEC2006', metric: 'score_per_ghz',
      label: 'SPEC2006/GHz', unit: '发布分数' };
  }
  if (state.trendScope === 'workload' && state.selectedWorkload) {
    return { level: 'workload', object: state.selectedWorkload, metric: 'equivalent_ipc',
      label: state.selectedWorkload + ' · 等效 IPC', unit: 'IPC' };
  }
  const slice = state.comparison && state.comparison.slices.find(
    row => row.slice_id === state.selectedSlice);
  if (!slice) return null;
  if (state.trendScope === 'slice') {
    return { level: 'slice', object: slice.slice, metric: 'ipc',
      label: slice.slice + ' · IPC', unit: 'IPC' };
  }
  const metric = state.evidence && state.evidence.metrics.find(
    row => row.metric_id === state.trendMetric);
  if (!metric) return null;
  return { level: 'counter', object: slice.slice, metric: metric.metric_id,
    label: metric.display_name, unit: metric.semantics ? metric.semantics.unit : metric.unit };
}

function updateTrendControls() {
  for (const button of $('trend-scopes').querySelectorAll('button')) {
    button.classList.toggle('active', button.dataset.trendScope === state.trendScope);
  }
  $('trend-mode').value = state.trendMode;
  $('trend-counter-wrap').hidden = state.trendScope !== 'counter';
  const metrics = state.evidence ? state.evidence.metrics : [];
  if (metrics.length && !metrics.some(row => row.metric_id === state.trendMetric)) {
    state.trendMetric = metrics[0].metric_id;
  }
  $('trend-counter').innerHTML = metrics.map(row =>
    '<option value="' + esc(row.metric_id) + '">' + esc(row.display_name) + '</option>'
  ).join('');
  if (state.trendMetric) $('trend-counter').value = state.trendMetric;
}

function renderTrend(spec) {
  const rows = state.trendRows;
  const baseline = rows.find(row => row.run_id === $('base-run').value);
  const baseValue = baseline && baseline.value;
  if (state.trendMode === 'relative' && (!Number.isFinite(baseValue) || baseValue === 0)) {
    empty('trend-chart', '基线 A 没有有效值，无法计算相对变化。');
    $('trend-caption').textContent = '';
    return;
  }
  const values = rows.map(row => {
    if (!Number.isFinite(row.value)) return null;
    return state.trendMode === 'relative' ? (row.value / baseValue - 1) * 100 : row.value;
  });
  const valid = values.filter(Number.isFinite);
  if (!valid.length) {
    empty('trend-chart', '当前范围没有可用的趋势点。');
    $('trend-caption').textContent = '';
    return;
  }
  const W = 980, H = 300, pad = { l: 75, r: 27, t: 34, b: 60 };
  const width = W - pad.l - pad.r, height = H - pad.t - pad.b;
  let low = Math.min(...valid), high = Math.max(...valid);
  let spread = high - low;
  if (spread < 1e-9) spread = Math.max(Math.abs(high) * 0.04, 0.04);
  low -= spread * 0.2;
  high += spread * 0.2;
  const x = index => pad.l + index * width / Math.max(1, rows.length - 1);
  const y = value => pad.t + (high - value) / (high - low) * height;
  const fmt = value => number(value, state.trendMode === 'relative' ? 2 : 4) +
    (state.trendMode === 'relative' ? '%' : '');
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' +
    esc(spec.label) + ' 性能变化曲线">';
  for (let step = 0; step <= 4; step++) {
    const value = low + (high - low) * step / 4;
    svg += '<line x1="' + pad.l + '" y1="' + y(value) + '" x2="' +
      (W - pad.r) + '" y2="' + y(value) +
      '" stroke="#e5ecef"/><text x="' + (pad.l - 12) + '" y="' +
      (y(value) + 4) + '" text-anchor="end" fill="#74879a" font-size="11">' +
      esc(fmt(value)) + '</text>';
  }
  if (state.trendMode === 'relative' && low < 0 && high > 0) {
    svg += '<line x1="' + pad.l + '" y1="' + y(0) + '" x2="' +
      (W - pad.r) + '" y2="' + y(0) +
      '" stroke="#9db1bd" stroke-dasharray="4 4"/>';
  }
  for (const [runId, mark, color] of [
    [$('base-run').value, 'A', '#7d8fa1'],
    [$('target-run').value, 'B', '#087b78'],
  ]) {
    const index = rows.findIndex(row => row.run_id === runId);
    if (index < 0) continue;
    svg += '<line x1="' + x(index) + '" y1="' + pad.t + '" x2="' +
      x(index) + '" y2="' + (H - pad.b) + '" stroke="' + color +
      '" stroke-dasharray="5 5" opacity=".7"/><text x="' +
      x(index) + '" y="20" text-anchor="middle" fill="' + color +
      '" font-size="12" font-weight="700">' + mark + '</text>';
  }
  const segments = [];
  let segment = [], lastVersion = null;
  values.forEach((value, index) => {
    if (!Number.isFinite(value)) {
      if (segment.length) segments.push(segment);
      segment = [];
      lastVersion = null;
      return;
    }
    if (lastVersion !== null && rows[index].metric_version !== lastVersion) {
      segments.push(segment);
      segment = [];
    }
    segment.push(x(index) + ',' + y(value));
    lastVersion = rows[index].metric_version;
  });
  if (segment.length) segments.push(segment);
  for (const points of segments) {
    if (points.length > 1) {
      svg += '<polyline points="' + points.join(' ') +
        '" fill="none" stroke="#087b78" stroke-width="3" stroke-linejoin="round"/>';
    }
  }
  const labelStep = Math.max(1, Math.ceil(rows.length / 9));
  rows.forEach((row, index) => {
    if (index % labelStep === 0 || index === rows.length - 1 ||
        row.run_id === $('base-run').value || row.run_id === $('target-run').value) {
      svg += '<text x="' + x(index) + '" y="' + (H - 22) +
        '" text-anchor="middle" fill="#607789" font-size="11">' +
        esc(row.short_sha.slice(0, 7)) + '</text>';
    }
    if (!Number.isFinite(values[index])) return;
    svg += '<circle class="trend-point" data-trend-run="' + esc(row.run_id) +
      '" cx="' + x(index) + '" cy="' + y(values[index]) +
      '" r="' + (row.run_id === $('target-run').value ? 6 : 4.5) +
      '" fill="white" stroke="#087b78" stroke-width="2.5" tabindex="0" role="button">' +
      '<title>' + esc(row.short_sha + ' · ' + row.commit_time + ' · ' +
        number(row.value, 5)) + '</title></circle>';
  });
  $('trend-chart').innerHTML = svg + '</svg>';
  const basis = state.history.basis === 'first_parent_tested_commits'
    ? '目标提交第一父链上的已测试点' : '同实验条件的已发布观测';
  $('trend-caption').textContent = basis + ' · ' + valid.length + '/' +
    rows.length + ' 个有效点 · ' +
    (state.trendMode === 'relative' ? '相对基线 A 的原始数值变化' : spec.unit) +
    ' · 点击数据点切换目标 B';
}

async function loadTrend() {
  const request = ++state.trendRequest;
  updateTrendControls();
  const spec = trendSpec();
  if (!state.history || !spec) {
    empty('trend-chart', '选择 workload 或切片后可查看对应变化曲线。');
    $('trend-caption').textContent = '';
    return;
  }
  const version = state.version;
  $('trend-subtitle').textContent = spec.label + ' · 已测试提交趋势';
  empty('trend-chart', '正在读取趋势…');
  try {
    const rows = await getJson('/api/trend', {
      level: spec.level, object: spec.object, metric: spec.metric,
      comparison_key: state.history.comparison_key,
    });
    if (request !== state.trendRequest || version !== state.version) return;
    const byRun = new Map(rows.map(row => [row.run_id, row]));
    state.trendRows = state.history.runs.map(run => {
      const value = byRun.get(run.run_id);
      return { ...run,
        value: value && ['valid', 'available'].includes(value.status) &&
          Number.isFinite(value.value) ? value.value : null,
        metric_version: value ? value.metric_version : null,
      };
    });
    renderTrend(spec);
  } catch (error) {
    if (request !== state.trendRequest || version !== state.version) return;
    empty('trend-chart', '无法读取变化曲线：' + error.message);
    $('trend-caption').textContent = '';
  }
}

async function selectBaseline() {
  state.baselineRelation = '';
  const target = $('target-run').value;
  try {
    const result = await getJson('/api/baseline', { run: target });
    if (result.status === 'found') {
      $('base-run').value = result.baseline.run_id;
      state.baselineRelation = '最近第一父链已测试祖先';
      return;
    }
  } catch (_) {
    // A manual comparison remains available without a Git checkout.
  }
  const other = state.runs.find(run => run.run_id !== target);
  if (other) $('base-run').value = other.run_id;
  state.baselineRelation = '手动基线';
}

function renderOverall() {
  const overall = state.overall;
  if (!overall) {
    empty('score-cards', '选择两次运行查看整体结果。');
    $('overall-notes').textContent = '';
    $('run-meta').innerHTML = '';
    return;
  }
  const runA = state.runs.find(run => run.run_id === $('base-run').value);
  const runB = state.runs.find(run => run.run_id === $('target-run').value);
  $('run-meta').innerHTML = [runA, runB].map((run, index) =>
    '<div><span>' + (index ? '目标' : '基线') + ' · 完整 SHA / 报告目录</span><code>' +
    esc(run.commit_sha) + '</code><code>' + esc(run.source_uri) + '</code></div>'
  ).join('');
  const order = ['SPEC2006', 'SPECint2006', 'SPECfp2006'];
  const scores = [...overall.scores].sort(
    (a, b) => order.indexOf(a.name) - order.indexOf(b.name)
  );
  $('score-cards').innerHTML = scores.length ? scores.map(score => {
    const change = score.change_percent;
    return '<article class="score-card' + (score.name === 'SPEC2006' ? ' primary' : '') + '">' +
      '<div class="score-label">' + esc(score.name) + ' / GHz</div>' +
      '<div class="score-values">' + number(score.value_a) + ' <span>→</span> ' +
      number(score.value_b) + '</div>' +
      '<div class="delta ' + tone(change, true) + '">' +
      (change === null ? '条件不匹配，未计算变化' : signed(change) + '%') +
      '</div></article>';
  }).join('') : '<div class="empty">这组运行没有同公式的套件分数；可以继续查看 workload CPI。</div>';

  const provisional = state.workloads.some(row =>
    row.warnings.includes('comparison_key_is_provisional'));
  const notes = [];
  if (overall.status !== 'comparable') notes.push('运行条件不匹配：' + overall.reasons.join('、'));
  if (provisional) notes.push('历史收据为补建状态，实验条件尚未全部核实');
  notes.push('套件分数与 workload 加权 CPI 是不同聚合口径，不将 CPI 贡献解释为分数贡献');
  $('overall-notes').textContent = notes.join(' · ');
  const text = overall.status === 'comparable'
    ? state.baselineRelation + ' · ' + state.workloads.length + ' 个可比较 workload'
    : 'A/B 条件不匹配';
  $('header-status').textContent = text;
  notice(text + (provisional ? ' · 历史条件待核实' : ''),
         overall.status !== 'comparable' || provisional ? 'warning' : '');
}

function renderWorkloads() {
  const summary = state.workloadSummary;
  if (!summary) {
    $('workload-summary').innerHTML = '';
    empty('workload-table', '尚无 workload 对比。');
    return;
  }
  const improved = state.workloads.filter(row =>
    row.cpi_degradation_percent !== null && row.cpi_degradation_percent < -0.5).length;
  $('workload-summary').innerHTML =
    '<div class="summary-chip"><strong>' + summary.workload_count + '</strong><span>可比较 workload</span></div>' +
    '<div class="summary-chip"><strong class="delta bad">' + summary.anomalous_workload_count +
    '</strong><span>CPI 退化超过 0.5%</span></div>' +
    '<div class="summary-chip"><strong class="delta good">' + improved +
    '</strong><span>CPI 改善超过 0.5%</span></div>' +
    '<div class="summary-chip"><strong>' + summary.incomparable_workload_count +
    '</strong><span>不可比 workload</span></div>';

  const search = $('workload-search').value.trim().toLowerCase();
  const rows = state.workloads.filter(row => {
    if (!row.workload.toLowerCase().includes(search)) return false;
    const change = row.cpi_degradation_percent;
    return state.workloadFilter === 'all'
      || (state.workloadFilter === 'regressed' && change > 0)
      || (state.workloadFilter === 'improved' && change < 0);
  });
  $('workload-table').innerHTML = rows.length ?
    '<table><thead><tr><th>Workload</th><th>切片集</th><th>CPI A</th><th>CPI B</th>' +
    '<th>ΔCPI</th><th>CPI 变化</th><th>切片数</th></tr></thead><tbody>' +
    rows.map(row => {
      const change = row.cpi_degradation_percent;
      const full = row.comparison_mode === 'full_slice_set';
      return '<tr class="' + (state.selectedWorkload === row.workload ? 'selected' : '') + '">' +
        '<td><button type="button" class="row-button" data-workload="' + esc(row.workload) + '">' +
        esc(row.workload) + '</button></td>' +
        '<td><span class="status-tag ' + (full ? 'good' : 'warn') + '">' +
        (full ? '完整' : '部分诊断') + '</span></td>' +
        '<td>' + number(row.weighted_cpi_a, 5) + '</td>' +
        '<td>' + number(row.weighted_cpi_b, 5) + '</td>' +
        '<td>' + signed(row.weighted_cpi_delta, 5) + '</td>' +
        '<td class="delta ' + tone(change) + '">' + signed(change) + '%</td>' +
        '<td>' + row.slice_count + '</td></tr>';
    }).join('') + '</tbody></table>'
    : '<div class="empty">当前筛选下没有 workload。</div>';
  for (const button of $('workload-filters').querySelectorAll('button')) {
    button.classList.toggle('active', button.dataset.filter === state.workloadFilter);
  }
}

function renderSlices() {
  const result = state.comparison;
  if (!result || result.status !== 'comparable') {
    const reasons = result && result.reasons ? result.reasons.join('、') : '请选择 workload';
    empty('workload-detail', '无法比较：' + reasons);
    empty('slice-table', '没有可比较的切片。');
    return;
  }
  const partial = result.mode !== 'full_slice_set';
  const cpiA = partial ? result.diagnostic_subset_cpi_a : result.weighted_cpi_a;
  const cpiB = partial ? result.diagnostic_subset_cpi_b : result.weighted_cpi_b;
  $('workload-detail').innerHTML =
    '<div class="summary-chip"><strong>' + esc(result.object_id) +
    '</strong><span>当前 workload</span></div>' +
    '<div class="summary-chip"><strong>' + number(result.coverage_weight * 100, 2) +
    '%</strong><span>切片权重覆盖</span></div>' +
    '<div class="summary-chip"><strong>' + number(cpiA, 5) + ' → ' +
    number(cpiB, 5) + '</strong><span>' +
    (partial ? '选中子集 CPI' : '完整 workload 加权 CPI') + '</span></div>' +
    '<div class="summary-chip"><strong class="delta ' +
    tone(result.cpi_degradation_percent) + '">' +
    signed(result.cpi_degradation_percent) + '%</strong><span>CPI 变化</span></div>';
  const max = Math.max(...result.slices.map(row =>
    Math.abs(row.weighted_cpi_contribution)), 0);
  $('slice-table').innerHTML =
    '<table><thead><tr><th>切片 / checkpoint</th><th>权重</th><th>CPI A</th><th>CPI B</th>' +
    '<th>切片 CPI 变化</th><th>权重 × ΔCPI</th></tr></thead><tbody>' +
    result.slices.map(row => {
      const value = row.weighted_cpi_contribution;
      const width = max ? Math.max(2, Math.abs(value) / max * 100) : 0;
      return '<tr class="' + (state.selectedSlice === row.slice_id ? 'selected' : '') + '">' +
        '<td><button type="button" class="row-button" data-slice="' + esc(row.slice_id) + '">' +
        esc(row.slice) + '</button></td>' +
        '<td>' + number(row.weight, 7) + '</td>' +
        '<td>' + number(row.cpi_a, 5) + '</td><td>' + number(row.cpi_b, 5) + '</td>' +
        '<td class="delta ' + tone(row.cpi_degradation_percent) + '">' +
        signed(row.cpi_degradation_percent) + '%</td>' +
        '<td class="bar-cell delta ' + tone(value) + '">' + signed(value, 6) +
        '<span class="bar-track"><span class="bar-fill ' + (value < 0 ? 'good' : '') +
        '" style="width:' + width + '%"></span></span></td></tr>';
    }).join('') + '</tbody></table>';
  if (partial) {
    $('slice-table').insertAdjacentHTML('beforeend',
      '<p class="footnote">当前为部分覆盖的诊断子集，不能作为完整 workload CPI 结论。</p>');
  }
}

function renderCounters() {
  const evidence = state.evidence;
  if (!evidence || evidence.status !== 'ok') {
    empty('slice-detail', '选择一个切片查看 ROI 指标和原始日志。');
    $('counter-categories').innerHTML = '';
    empty('counter-table', '没有计数器数据。');
    return;
  }
  const row = evidence.slice;
  const a = evidence.roi_a || {};
  const b = evidence.roi_b || {};
  const catalog = evidence.catalog_revision;
  const runA = state.runs.find(run => run.run_id === $('base-run').value);
  const runB = state.runs.find(run => run.run_id === $('target-run').value);
  const versionMatched = runA.commit_sha === catalog && runB.commit_sha === catalog;
  $('slice-detail').innerHTML =
    '<h3>' + esc(row.slice) + '</h3>' +
    '<div class="evidence-grid">' +
    '<div class="evidence-card"><span>ROI IPC A → B</span><strong>' +
    number(a.ipc_computed, 4) + ' → ' + number(b.ipc_computed, 4) + '</strong></div>' +
    '<div class="evidence-card"><span>ROI 周期 A → B</span><strong>' +
    number(a.cycles, 0) + ' → ' + number(b.cycles, 0) + '</strong></div>' +
    '<div class="evidence-card"><span>ROI 指令 A → B</span><strong>' +
    number(a.instructions, 0) + ' → ' + number(b.instructions, 0) + '</strong></div>' +
    '<div class="evidence-card"><span>权重 × ΔCPI</span><strong class="delta ' +
    tone(row.weighted_cpi_contribution) + '">' +
    signed(row.weighted_cpi_contribution, 6) + '</strong></div></div>' +
    '<div class="path-box"><div><span>A ROI 日志</span><code>' +
    esc(a.source_out_uri) + '</code></div><div><span>B ROI 日志</span><code>' +
    esc(b.source_out_uri) + '</code></div><div><span>A PERF 日志</span><code>' +
    esc(a.source_err_uri) + '</code></div><div><span>B PERF 日志</span><code>' +
    esc(b.source_err_uri) + '</code></div></div>' +
    '<p class="semantic-note">计数器解释来自 RTL 目录 ' + esc(catalog.slice(0, 10)) +
    '；当前 A/B 为 ' + esc(runA.short_sha) + ' / ' + esc(runB.short_sha) +
    (versionMatched ? '，版本相同。' : '，需按各自 RTL 版本复核定义。') + '</p>';

  const categories = [...new Set(evidence.metrics.map(metric => metric.category))];
  $('counter-categories').innerHTML =
    '<button type="button" data-category="all" class="' +
    (state.counterCategory === 'all' ? 'active' : '') + '">全部</button>' +
    categories.map(category => '<button type="button" data-category="' +
      esc(category) + '" class="' + (state.counterCategory === category ? 'active' : '') +
      '">' + esc(category) + '</button>').join('');

  const metrics = evidence.metrics.filter(metric =>
    state.counterCategory === 'all' || metric.category === state.counterCategory);
  $('counter-table').innerHTML = metrics.length ?
    '<table><thead><tr><th>计数器</th><th>类别</th><th>A</th><th>B</th>' +
    '<th>原始变化</th><th>单位 / 窗口</th><th>来源</th></tr></thead><tbody>' +
    metrics.map(metric => {
      const semantics = metric.semantics;
      const status = metric.paired ? '' :
        (metric.window_a && metric.window_b && metric.window_a !== metric.window_b
          ? '窗口不同' : '未配对');
      return '<tr><td class="metric-cell"><strong>' + esc(metric.display_name) +
        '</strong>' +
        (semantics ? '<div class="metric-meaning">' + esc(semantics.meaning) + '</div>'
          : '<div class="metric-meaning">目录中没有匹配解释</div>') +
        '<small>' + esc(metric.metric_id) + ' · ' +
        esc(metric.semantic_version) + '</small></td>' +
        '<td>' + esc(metric.category) + '</td>' +
        '<td>' + number(metric.value_a, 2) + '</td>' +
        '<td>' + number(metric.value_b, 2) + '</td>' +
        '<td>' + (metric.paired
          ? (metric.change_percent === null ? 'A=0，百分比不定义' :
            signed(metric.change_percent) + '%')
          : '<span class="status-tag warn">' + status + '</span>') + '</td>' +
        '<td>' + esc(semantics ? semantics.unit : metric.unit) + '<br><small>' +
        esc(metric.paired ? metric.window_a : (metric.window_a || 'N/A') +
          ' / ' + (metric.window_b || 'N/A')) + '</small></td>' +
        '<td><details class="source-details"><summary>口径与来源</summary>' +
        (semantics ? '<p><b>计数：</b>' + esc(semantics.counting) +
          '</p><p><b>生效：</b>' + esc(semantics.availability) +
          '</p><p><b>定义：</b><code>' + esc(semantics.definition_id) +
          '</code></p><p><b>目录位置：</b>' + esc(semantics.documentation) + '</p>' : '') +
        '<p><b>平台注册单位：</b>' + esc(metric.unit) + '</p>' +
        '<code>A ' + esc(metric.source_a) + '</code><code>B ' +
        esc(metric.source_b) + '</code></details></td></tr>';
    }).join('') + '</tbody></table>'
    : '<div class="empty">当前类别没有计数器。</div>';
}

function fileUrl(analysis, name) {
  return query('/api/analysis-file', { analysis: analysis.analysis_id, name });
}
function renderAnalyses() {
  if (!state.analyses.length) {
    empty('analysis-list',
      '这组 A/B 的当前 workload 尚无已登记的 Top-down 或 Rolling 结果。可以先用切片贡献和同窗口计数器定位，再运行现有分析脚本。');
    return;
  }
  $('analysis-list').innerHTML = state.analyses.map(analysis => {
    const isTopdown = analysis.kind === 'topdown';
    const events = analysis.events || [];
    const table = events.length ?
      '<div class="analysis-events"><h4>加权原始事件 · 末次 PERF 窗口</h4>' +
      '<div class="table-wrap"><table><thead><tr><th>事件 / 语义</th><th>A</th><th>B</th>' +
      '<th>Δ</th></tr></thead><tbody>' +
      events.map(event => {
        const semantics = event.semantics;
        return '<tr><td class="metric-cell"><strong>' + esc(event.name) + '</strong>' +
          (semantics ? '<div class="metric-meaning">' + esc(semantics.meaning) +
            '；单位：' + esc(semantics.unit) + '</div>'
            : '<div class="metric-meaning">目录未覆盖该汇总项</div>') + '</td><td>' +
          number(event.value_a, 2) + '</td><td>' + number(event.value_b, 2) +
          '</td><td>' + signed(event.value_b - event.value_a, 2) + '</td></tr>';
      }).join('') + '</tbody></table></div>' : '';
    const csvs = analysis.csvs.map(name => '<a href="' +
      esc(fileUrl(analysis, name)) + '" download>' + esc(name) + '</a>').join('');
    const images = analysis.images.map(name =>
      '<figure><img loading="lazy" src="' + esc(fileUrl(analysis, name)) +
      '" alt="' + esc(name) + '"><figcaption>' + esc(name) +
      '</figcaption></figure>').join('');
    return '<article class="analysis-card"><div class="analysis-head"><div><h3>' +
      (isTopdown ? 'Top-down' : 'Rolling') + '</h3><p>' +
      esc(analysis.created_at) + '</p></div><span class="status-tag ' +
      (analysis.status === 'completed' ? 'good' : 'warn') + '">' +
      esc(analysis.status) + '</span></div>' +
      (isTopdown ? '<p class="footnote">原始事件可作 A/B 线索。语义参考 RTL 目录 ' +
        esc(analysis.catalog_revision.slice(0, 10)) +
        '，跨版本需复核；缺失指标可能影响派生分类，Intel Top-down 派生图已隐藏。</p>'
        : '<p class="footnote">跨二进制的 Rolling 曲线需要额外验证程序阶段对应关系。</p>') +
      table + (csvs ? '<div class="artifact-links">' + csvs + '</div>' : '') +
      (images ? '<div class="image-grid">' + images + '</div>' : '') +
      '<p class="footnote artifact-path">分析记录：' +
      esc(analysis.result_uri) + '</p></article>';
  }).join('');
}

async function loadSlice(sliceId, scroll = false) {
  if (!state.comparison || state.comparison.status !== 'comparable') return;
  state.selectedSlice = sliceId;
  state.counterCategory = 'all';
  renderSlices();
  const version = state.version;
  const workload = state.selectedWorkload;
  try {
    const evidence = await getJson('/api/slice', {
      a: $('base-run').value, b: $('target-run').value,
      workload, slice: sliceId,
    });
    if (version !== state.version || workload !== state.selectedWorkload ||
        sliceId !== state.selectedSlice) return;
    state.evidence = evidence;
    renderCounters();
    if (state.trendScope === 'slice' || state.trendScope === 'counter') loadTrend();
    if (scroll) $('counters').scrollIntoView({ behavior: 'smooth' });
  } catch (error) {
    empty('slice-detail', '无法读取切片证据：' + error.message);
    empty('counter-table', '没有计数器数据。');
  }
}

async function loadWorkload(workload, scroll = false) {
  state.selectedWorkload = workload;
  state.selectedSlice = null;
  state.evidence = null;
  renderWorkloads();
  const version = state.version;
  try {
    const params = { a: $('base-run').value, b: $('target-run').value, workload };
    const [comparison, analyses] = await Promise.all([
      getJson('/api/compare', params), getJson('/api/analyses', params),
    ]);
    if (version !== state.version || workload !== state.selectedWorkload) return;
    state.comparison = comparison;
    state.analyses = analyses;
    renderSlices();
    renderAnalyses();
    if (state.trendScope === 'workload') loadTrend();
    if (comparison.status === 'comparable' && comparison.slices.length) {
      const best = comparison.slices.find(row =>
        row.weighted_cpi_contribution > 0) || comparison.slices[0];
      await loadSlice(best.slice_id);
    } else {
      renderCounters();
      if (state.trendScope === 'slice' || state.trendScope === 'counter') loadTrend();
    }
    if (scroll) $('slices').scrollIntoView({ behavior: 'smooth' });
  } catch (error) {
    empty('workload-detail', '无法读取 workload：' + error.message);
    empty('slice-table', '没有切片数据。');
    empty('analysis-list', '没有阶段分析数据。');
  }
}

async function loadPair() {
  const version = ++state.version;
  const a = $('base-run').value;
  const b = $('target-run').value;
  state.history = null;
  state.trendRows = [];
  empty('trend-chart', '正在读取趋势…');
  $('trend-caption').textContent = '';
  if (!a || !b || a === b) {
    notice('请选择不同的基线与目标运行。', 'warning');
    state.overall = null;
    state.workloads = [];
    state.workloadSummary = null;
    renderOverall();
    renderWorkloads();
    resetDetails();
    loadTrend();
    return;
  }
  notice('正在读取整体分数、workload 与切片…');
  const previous = state.selectedWorkload;
  try {
    const [overall, anomalies, history] = await Promise.all([
      getJson('/api/overall', { a, b }),
      getJson('/api/anomalies', { current: b, baseline: a }),
      getJson('/api/history', { run: b }),
    ]);
    if (version !== state.version) return;
    state.overall = overall;
    state.workloads = anomalies.workloads;
    state.workloadSummary = anomalies.summary;
    state.history = history;
    renderOverall();
    renderWorkloads();
    if (state.trendScope === 'suite') loadTrend();
    const selected = state.workloads.find(row => row.workload === previous) ||
      state.workloads.find(row => row.is_anomaly && row.comparison_mode === 'full_slice_set') ||
      state.workloads[0];
    if (selected) await loadWorkload(selected.workload);
    else resetDetails();
  } catch (error) {
    if (version !== state.version) return;
    notice('无法读取 A/B 数据：' + error.message, 'error');
    state.overall = null;
    state.workloads = [];
    state.workloadSummary = null;
    renderOverall();
    renderWorkloads();
    resetDetails();
    loadTrend();
  }
}

$('trend-scopes').addEventListener('click', event => {
  const button = event.target.closest('button[data-trend-scope]');
  if (!button) return;
  state.trendScope = button.dataset.trendScope;
  loadTrend();
});
$('trend-mode').addEventListener('change', event => {
  state.trendMode = event.target.value;
  loadTrend();
});
$('trend-counter').addEventListener('change', event => {
  state.trendMetric = event.target.value;
  loadTrend();
});
async function selectTrendPoint(event) {
  const point = event.target.closest('circle[data-trend-run]');
  if (!point) return;
  $('target-run').value = point.dataset.trendRun;
  await selectBaseline();
  await loadPair();
}
$('trend-chart').addEventListener('click', selectTrendPoint);
$('trend-chart').addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  selectTrendPoint(event);
});

$('target-run').addEventListener('change', async () => {
  await selectBaseline();
  await loadPair();
});
$('base-run').addEventListener('change', () => {
  state.baselineRelation = '手动基线';
  loadPair();
});
$('refresh').addEventListener('click', loadPair);
$('workload-search').addEventListener('input', renderWorkloads);
$('workload-filters').addEventListener('click', event => {
  const button = event.target.closest('button[data-filter]');
  if (!button) return;
  state.workloadFilter = button.dataset.filter;
  renderWorkloads();
});
$('workload-table').addEventListener('click', event => {
  const button = event.target.closest('button[data-workload]');
  if (button) loadWorkload(button.dataset.workload, true);
});
$('slice-table').addEventListener('click', event => {
  const button = event.target.closest('button[data-slice]');
  if (button) loadSlice(button.dataset.slice, true);
});
$('counter-categories').addEventListener('click', event => {
  const button = event.target.closest('button[data-category]');
  if (!button) return;
  state.counterCategory = button.dataset.category;
  renderCounters();
});

async function init() {
  state.runs = await getJson('/api/runs');
  if (!state.runs.length) {
    notice('数据库中没有运行数据。', 'warning');
    $('header-status').textContent = '无运行数据';
    return;
  }
  const options = state.runs.map(run =>
    '<option value="' + esc(run.run_id) + '">' + esc(label(run)) + '</option>').join('');
  $('target-run').innerHTML = options;
  $('base-run').innerHTML = options;
  await selectBaseline();
  await loadPair();
}
init().catch(error => notice('无法初始化看板：' + error.message, 'error'));
