'use strict';

const $ = id => document.getElementById(id);
const DATA_ROOT = new URLSearchParams(location.search).get('data') || '../outputs/mainline-september';
const STABLE_THRESHOLD = 0.5;
const BASE_METRIC = 'rob_committed_instructions';
const PER_KINST = 1000;

const model = {
  manifest: null,
  commits: [],
  workloads: [],
  workloadRows: new Map(),
  slices: [],
  sliceIndex: new Map(),
  slicesByWorkload: new Map(),
  transitions: [],
  counterMetrics: [],
  counterMetricById: new Map(),
  counterSlices: [],
  counterSliceIndex: new Map(),
  counterValues: new Map(),
  counterObservationCount: 0,
  scoreValues: new Map()
};

let state = {
  level: 'suite',
  workload: '',
  slice: '',
  metric: 'score',
  base: 0,
  target: 0,
  mode: 'absolute',
  sort: 'impact',
  counterMetric: '',
  counterSlice: '',
  counterUnit: 'raw',
  relationMode: 'level',
  counterDisplay: 'combined',
  selectedCounterMetrics: []
};

const number = value => value === '' || value == null ? null : Number(value);
const finite = value => Number.isFinite(value);
const signed = (value, digits = 2) => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '—';
const pct = (value, digits = 2) => finite(value) ? `${signed(value, digits)}%` : '—';
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const shortDate = value => new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit'}).format(new Date(value));
const fullDate = value => new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(value));
const tone = value => !finite(value) || Math.abs(value) <= STABLE_THRESHOLD ? 'neutral' : value > 0 ? 'good' : 'bad';
const coreMetricOptions = '<option value="index">归一化指数</option><option value="ipc">IPC</option><option value="cpi">CPI</option>';
const counterKey = (slice, metric, commit) => `${slice}\u0000${metric}\u0000${commit}`;
const formatCounterValue = value => finite(value) ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2}).format(value) : '—';

function parseCSV(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(field); field = ''; }
    else if (char === '\n') { row.push(field.replace(/\r$/, '')); rows.push(row); row = []; field = ''; }
    else field += char;
  }
  if (field || row.length) { row.push(field.replace(/\r$/, '')); rows.push(row); }
  const headers = rows.shift() || [];
  return rows.filter(rowData => rowData.some(Boolean)).map(rowData => Object.fromEntries(headers.map((header, index) => [header, rowData[index] ?? ''])));
}

async function fetchText(path) {
  const response = await fetch(`${DATA_ROOT}/${path}`);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.text();
}

async function loadData() {
  const [manifestText, workloadText, sliceText, transitionText, counterText, scoreText] = await Promise.all([
    fetchText('manifest.json'),
    fetchText('workload-weighted-trend.csv'),
    fetchText('slice-ipc-wide.csv'),
    fetchText('commit-transition-summary.csv'),
    fetchText('perf-counters.json'),
    fetchText('scores.csv')
  ]);
  model.manifest = JSON.parse(manifestText);
  model.commits = [...model.manifest.runs].sort((a, b) => a.commit_order - b.commit_order);

  const workloadData = parseCSV(workloadText).map(row => ({
    ...row,
    commit_order: number(row.commit_order),
    weighted_ipc: number(row.weighted_ipc),
    weight_coverage_pct: number(row.weight_coverage_pct),
    slice_count: number(row.slice_count),
    expected_slice_count: number(row.expected_slice_count)
  }));
  for (const row of workloadData) {
    if (!model.workloadRows.has(row.workload)) model.workloadRows.set(row.workload, []);
    model.workloadRows.get(row.workload).push(row);
  }
  model.workloads = [...model.workloadRows.keys()].sort((a, b) => a.localeCompare(b));
  for (const rows of model.workloadRows.values()) rows.sort((a, b) => a.commit_order - b.commit_order);

  model.slices = parseCSV(sliceText).map(row => ({
    ...row,
    slice_order: number(row.slice_order),
    weight: number(row.weight),
    checkpoint: number(row.checkpoint)
  }));
  model.sliceIndex = new Map(model.slices.map(row => [row.slice, row]));
  for (const row of model.slices) {
    if (!model.slicesByWorkload.has(row.workload)) model.slicesByWorkload.set(row.workload, []);
    model.slicesByWorkload.get(row.workload).push(row);
  }
  for (const rows of model.slicesByWorkload.values()) rows.sort((a, b) => a.slice_order - b.slice_order);

  model.transitions = parseCSV(transitionText).map(row => ({
    ...row,
    comparable_slice_count: number(row.comparable_slice_count),
    median_change_pct: number(row.median_change_pct),
    mean_change_pct: number(row.mean_change_pct),
    improved_over_0_5pct: number(row.improved_over_0_5pct),
    stable_within_0_5pct: number(row.stable_within_0_5pct),
    regressed_over_0_5pct: number(row.regressed_over_0_5pct)
  }));

  const counterData = JSON.parse(counterText);
  model.counterMetrics = counterData.metrics;
  model.counterMetricById = new Map(model.counterMetrics.map(metric => [metric.metric_id, metric]));
  model.counterSlices = counterData.slices;
  model.counterSliceIndex = new Map(model.counterSlices.map(slice => [slice.slice, slice]));
  model.counterObservationCount = counterData.observation_count;
  for (const row of counterData.values) {
    row.value = number(row.value);
    row.dump_time = number(row.dump_time);
    model.counterValues.set(counterKey(row.slice, row.metric_id, row.short_commit), row);
  }

  for (const row of parseCSV(scoreText)) {
    if (row.level === 'suite') model.scoreValues.set(`${row.object_id}\u0000${row.short_commit}`, number(row.value));
  }

  state.workload = model.workloads.includes('mcf') ? 'mcf' : model.workloads[0];
  state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
  state.counterSlice = model.counterSlices.find(slice => slice.slice.includes('_6753_'))?.slice || model.counterSlices[0]?.slice || '';
  state.counterMetric = model.counterMetricById.has('branch_mispredictions') ? 'branch_mispredictions' : model.counterMetrics[0]?.metric_id || '';
  state.selectedCounterMetrics = [state.counterMetric];
  state.target = model.commits.length - 1;
}

function suiteScore(commitIndex) {
  return model.scoreValues.get(`SPEC2006\u0000${model.commits[commitIndex].short_commit}`) ?? null;
}

function workloadRow(workload, commitIndex) {
  return model.workloadRows.get(workload)?.find(row => row.short_commit === model.commits[commitIndex].short_commit) || null;
}

function workloadIPC(workload, commitIndex) {
  return workloadRow(workload, commitIndex)?.weighted_ipc ?? null;
}

function sliceIPC(slice, commitIndex) {
  const value = number(slice[`ipc_${model.commits[commitIndex].short_commit}`]);
  return finite(value) ? value : null;
}

function selectedCounterMetric() {
  if (!state.counterMetric && state.selectedCounterMetrics?.length) state.counterMetric = state.selectedCounterMetrics[0];
  return model.counterMetricById.get(state.counterMetric) || model.counterMetrics[0];
}

function selectedCounterMetricIds() {
  const available = new Set(model.counterMetrics.map(metric => metric.metric_id));
  const ids = (state.selectedCounterMetrics || []).filter(metricId => available.has(metricId));
  return ids;
}

function counterRow(slice, commitIndex, metricId = state.counterMetric) {
  return model.counterValues.get(counterKey(slice, metricId, model.commits[commitIndex].short_commit)) || null;
}

function counterRawValue(slice, commitIndex, metricId = state.counterMetric) {
  const row = counterRow(slice, commitIndex, metricId);
  return row?.availability === 'available' && finite(row.value) ? row.value : null;
}

function counterBaseValue(slice, commitIndex) {
  return counterRawValue(slice, commitIndex, BASE_METRIC);
}

// 计数器和 IPC summary 属于不同窗口，因此归一化只用同窗口的 committed instructions。
// 该窗口在所有切片/commit 上固定为 20M 指令，逐指令口径因此可跨切片比较。
function counterValue(slice, commitIndex, metricId = state.counterMetric) {
  const raw = counterRawValue(slice, commitIndex, metricId);
  if (!finite(raw)) return null;
  if (state.counterUnit !== 'per_kinst' || metricId === BASE_METRIC) return raw;
  const base = counterBaseValue(slice, commitIndex);
  return finite(base) && base > 0 ? raw / base * PER_KINST : null;
}

function counterUnitLabel(metric = selectedCounterMetric()) {
  if (state.counterUnit !== 'per_kinst' || metric.metric_id === BASE_METRIC) return metric.unit;
  return `${metric.unit}/KInst`;
}

function sliceLabel(slice) {
  const row = model.counterSliceIndex.get(slice);
  return row ? String(row.checkpoint) : slice;
}

function currentCounterWorkload() {
  return model.counterSliceIndex.get(state.counterSlice)?.workload || '';
}

function counterAnalysisSlices() {
  const workload = currentCounterWorkload();
  return workload ? model.counterSlices.filter(slice => slice.workload === workload) : model.counterSlices;
}

function ipcAt(slice, commitIndex) {
  const row = model.sliceIndex.get(slice);
  return row ? sliceIPC(row, commitIndex) : null;
}

function counterChangePct(slice, fromIndex, toIndex, metricId = state.counterMetric) {
  const a = counterValue(slice, fromIndex, metricId), b = counterValue(slice, toIndex, metricId);
  return finite(a) && a !== 0 && finite(b) ? (b / a - 1) * 100 : null;
}

function ipcChangePct(slice, fromIndex, toIndex) {
  return change(ipcAt(slice, fromIndex), ipcAt(slice, toIndex));
}

function rankAverage(values) {
  const order = values.map((value, index) => [value, index]).sort((a, b) => a[0] - b[0]);
  const ranks = new Array(values.length);
  let cursor = 0;
  while (cursor < order.length) {
    let end = cursor;
    while (end + 1 < order.length && order[end + 1][0] === order[cursor][0]) end++;
    const average = (cursor + end) / 2 + 1;
    for (let index = cursor; index <= end; index++) ranks[order[index][1]] = average;
    cursor = end + 1;
  }
  return ranks;
}

function pearson(xs, ys) {
  const n = xs.length;
  if (n < 2) return null;
  const meanX = xs.reduce((sum, value) => sum + value, 0) / n;
  const meanY = ys.reduce((sum, value) => sum + value, 0) / n;
  let cov = 0, varX = 0, varY = 0;
  for (let index = 0; index < n; index++) {
    const dx = xs[index] - meanX, dy = ys[index] - meanY;
    cov += dx * dy; varX += dx * dx; varY += dy * dy;
  }
  const denominator = Math.sqrt(varX * varY);
  return denominator > 0 ? cov / denominator : null;
}

function spearman(pairs) {
  const usable = pairs.filter(pair => finite(pair.x) && finite(pair.y));
  if (usable.length < 4) return {rho:null, n:usable.length};
  const rho = pearson(rankAverage(usable.map(pair => pair.x)), rankAverage(usable.map(pair => pair.y)));
  return {rho, n:usable.length};
}

function correlationStrength(rho) {
  const magnitude = Math.abs(rho);
  if (!finite(rho)) return '样本不足';
  if (magnitude >= 0.6) return '强';
  if (magnitude >= 0.4) return '中等';
  if (magnitude >= 0.2) return '弱';
  return '几乎无';
}

function relationPairs(mode, metricId = state.counterMetric) {
  const slices = counterAnalysisSlices();
  if (mode === 'panel') {
    const pairs = [];
    for (let index = 0; index < model.commits.length - 1; index++) {
      for (const slice of slices) {
        pairs.push({
          slice: slice.slice, weight: slice.weight,
          x: counterChangePct(slice.slice, index, index + 1, metricId),
          y: ipcChangePct(slice.slice, index, index + 1)
        });
      }
    }
    return pairs;
  }
  if (mode === 'delta') {
    return slices.map(slice => ({
      slice: slice.slice, weight: slice.weight,
      x: counterChangePct(slice.slice, state.base, state.target, metricId),
      y: ipcChangePct(slice.slice, state.base, state.target)
    }));
  }
  return slices.map(slice => ({
    slice: slice.slice, weight: slice.weight,
    x: counterValue(slice.slice, state.target, metricId),
    y: ipcAt(slice.slice, state.target)
  }));
}

function relationAxisLabels(mode, metric = selectedCounterMetric()) {
  if (mode === 'level') return {x:`${metric.display_name} · ${counterUnitLabel(metric)}`, y:'切片 IPC', xPct:false, yPct:false};
  return {x:`${metric.display_name} · 相对变化`, y:'IPC 相对变化', xPct:true, yPct:true};
}

function relationCaption(mode) {
  const workload = currentCounterWorkload();
  if (mode === 'level') return `每个点是一个 ${workload} 切片在 ${model.commits[state.target].short_commit} 的绝对水平；回答“哪些计数器解释了切片之间的 IPC 差异”。`;
  if (mode === 'delta') return `每个点是一个 ${workload} 切片从 ${model.commits[state.base].short_commit} 到 ${model.commits[state.target].short_commit} 的相对变化；回答“哪些计数器跟着 IPC 一起动”。`;
  return `每个点是“相邻 commit × ${workload} 切片”的变化对，汇总全部区间；样本更多但混合了不同 commit 的改动。`;
}

function performanceChange(metric, a, b) {
  if (!finite(a) || !finite(b)) return null;
  return metric.direction === 'lower_is_better' ? change(b, a) : change(a, b);
}

function change(a, b) {
  return finite(a) && finite(b) && a !== 0 ? (b / a - 1) * 100 : null;
}

function suiteRatio(fromIndex, toIndex) {
  const ratios = model.workloads.map(workload => {
    const fromRow = workloadRow(workload, fromIndex), toRow = workloadRow(workload, toIndex);
    if (fromRow?.status !== 'complete' || toRow?.status !== 'complete') return null;
    const a = fromRow.weighted_ipc, b = toRow.weighted_ipc;
    return finite(a) && finite(b) && a > 0 && b > 0 ? b / a : null;
  }).filter(finite);
  return ratios.length ? Math.exp(ratios.reduce((sum, ratio) => sum + Math.log(ratio), 0) / ratios.length) : null;
}

function absoluteValues() {
  const first = 0;
  if (state.level === 'suite') {
    if (state.metric === 'score') return model.commits.map((_, index) => suiteScore(index));
    return model.commits.map((_, index) => suiteRatio(first, index) * 100);
  }
  if (state.level === 'workload') {
    const values = model.commits.map((_, index) => workloadIPC(state.workload, index));
    if (state.metric === 'ipc') return values;
    if (state.metric === 'cpi') return values.map(value => finite(value) && value !== 0 ? 1 / value : null);
    return values.map(value => finite(value) && finite(values[first]) ? value / values[first] * 100 : null);
  }
  if (state.level === 'counter') {
    return model.commits.map((_, index) => counterValue(state.counterSlice, index));
  }
  const slice = model.slices.find(item => item.slice === state.slice);
  const values = model.commits.map((_, index) => sliceIPC(slice, index));
  if (state.metric === 'ipc') return values;
  if (state.metric === 'cpi') return values.map(value => finite(value) && value !== 0 ? 1 / value : null);
  return values.map(value => finite(value) && finite(values[first]) ? value / values[first] * 100 : null);
}

function displayValues(values) {
  if (state.mode === 'absolute') return values;
  const baseline = values[state.base];
  return values.map(value => {
    if (!finite(value) || !finite(baseline) || baseline === 0) return null;
    if (state.level === 'counter') return performanceChange(selectedCounterMetric(), baseline, value);
    return state.metric === 'cpi' ? (baseline / value - 1) * 100 : (value / baseline - 1) * 100;
  });
}

function currentName() {
  if (state.level === 'suite') return state.metric === 'score' ? 'SPEC2006/GHz' : `SPEC06 · ${model.workloads.length} workloads`;
  if (state.level === 'workload') return state.workload;
  if (state.level === 'counter') return state.counterSlice;
  return state.slice;
}

function metricLabel() {
  if (state.level === 'counter') {
    const metric = selectedCounterMetric();
    return state.mode === 'relative' ? `${metric.display_name} · 性能变化` : `${metric.display_name} · ${counterUnitLabel(metric)}`;
  }
  if (state.mode === 'relative') return '相对 IPC 变化';
  return state.metric === 'score' ? '官方分数' : state.metric === 'ipc' ? 'IPC' : state.metric === 'cpi' ? 'CPI' : '归一化指数';
}

function syncControls() {
  $('level').value = state.level;
  $('base').innerHTML = model.commits.map((commit, index) => `<option value="${index}">${commit.short_commit} · ${shortDate(commit.commit_time)}</option>`).join('');
  $('target').innerHTML = $('base').innerHTML;
  $('base').value = state.base;
  $('target').value = state.target;
  $('sort').value = state.sort;

  if (state.level === 'suite') {
    $('entity').innerHTML = `<option>SPEC06 · ${model.workloads.length} workloads</option>`;
    $('entity').disabled = true;
  } else if (state.level === 'workload') {
    $('entity').disabled = false;
    $('entity').innerHTML = model.workloads.map(workload => `<option value="${escapeHtml(workload)}">${escapeHtml(workload)}</option>`).join('');
    $('entity').value = state.workload;
  } else if (state.level === 'slice') {
    $('entity').disabled = false;
    $('entity').innerHTML = model.slices.map(slice => `<option value="${escapeHtml(slice.slice)}">${escapeHtml(slice.workload)} / ${escapeHtml(slice.checkpoint)}</option>`).join('');
    $('entity').value = state.slice;
  } else {
    $('entity').disabled = false;
    const groups = new Map();
    for (const slice of model.counterSlices) {
      if (!groups.has(slice.workload)) groups.set(slice.workload, []);
      groups.get(slice.workload).push(slice);
    }
    $('entity').innerHTML = [...groups].map(([workload, slices]) => `<optgroup label="${escapeHtml(workload)}">${slices.map(slice => `<option value="${escapeHtml(slice.slice)}">${escapeHtml(String(slice.checkpoint))} · ${escapeHtml(slice.slice)}</option>`).join('')}</optgroup>`).join('');
    $('entity').value = state.counterSlice;
  }

  if (state.level === 'counter') {
    const categories = new Map();
    for (const metric of model.counterMetrics) {
      if (!categories.has(metric.category)) categories.set(metric.category, []);
      categories.get(metric.category).push(metric);
    }
    $('metric').innerHTML = [...categories].map(([category, metrics]) => `<optgroup label="${escapeHtml(category)}">${metrics.map(metric => `<option value="${escapeHtml(metric.metric_id)}">${escapeHtml(metric.display_name)}</option>`).join('')}</optgroup>`).join('');
    $('metric').value = state.counterMetric;
    $('counterUnitLabel').hidden = false;
    $('counterUnit').value = state.counterUnit;
    renderCounterTrendControls();
  } else {
    $('metric').innerHTML = state.level === 'suite'
      ? '<option value="score">SPEC2006/GHz 分数</option><option value="index">IPC 归一化指数</option>'
      : coreMetricOptions;
    $('metric').value = state.metric;
    $('counterUnitLabel').hidden = true;
    $('counterTrendControls').hidden = true;
  }
  document.querySelectorAll('#relationMode [data-rel]').forEach(button => button.classList.toggle('selected', button.dataset.rel === state.relationMode));
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected', button.dataset.mode === state.mode));
}

function renderCounterTrendControls() {
  const ids = selectedCounterMetricIds();
  state.selectedCounterMetrics = ids;
  $('counterTrendControls').hidden = state.level !== 'counter';
  $('counterSelectionSummary').textContent = `${ids.length} / ${model.counterMetrics.length} 个计数器已选择`;
  document.querySelectorAll('#counterDisplayMode [data-display]').forEach(button => button.classList.toggle('selected', button.dataset.display === state.counterDisplay));
  const categories = new Map();
  for (const metric of model.counterMetrics) {
    if (!categories.has(metric.category)) categories.set(metric.category, []);
    categories.get(metric.category).push(metric);
  }
  $('counterMetricChecks').innerHTML = [...categories].map(([category, metrics]) => `<div class="counter-check-group"><strong>${escapeHtml(category)}</strong>${metrics.map(metric => {
    const checked = ids.includes(metric.metric_id) ? ' checked' : '';
    const focused = metric.metric_id === state.counterMetric ? ' focused' : '';
    return `<label class="counter-check${focused}" title="${escapeHtml(metric.display_name)}"><input type="checkbox" data-counter-check="${escapeHtml(metric.metric_id)}"${checked}><span>${escapeHtml(metric.display_name)}</span></label>`;
  }).join('')}</div>`).join('');
  $('counterMetricChecks').querySelectorAll('[data-counter-check]').forEach(input => {
    input.onchange = () => {
      const next = [...$('counterMetricChecks').querySelectorAll('[data-counter-check]:checked')].map(item => item.dataset.counterCheck);
      state.selectedCounterMetrics = next;
      if (next.length) state.counterMetric = next.includes(state.counterMetric) ? state.counterMetric : next[0];
      render();
    };
    input.parentElement.ondblclick = event => {
      event.preventDefault();
      state.counterMetric = input.dataset.counterCheck;
      render();
    };
  });
}

function drawChart(id, values, {color = '#2f63db', format = value => value.toFixed(2)} = {}) {
  const valid = values.filter(finite);
  if (!valid.length) { $(id).innerHTML = '<div class="empty-state">当前筛选没有可用数据</div>'; return; }
  const W = 1120, H = 274, pad = {l:68, r:28, t:28, b:50}, plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  let low = Math.min(...valid), high = Math.max(...valid), spread = high - low;
  if (spread < 1e-8) spread = Math.max(Math.abs(high) * 0.04, 0.04);
  low -= spread * 0.2; high += spread * 0.2;
  const x = index => pad.l + index * plotWidth / Math.max(1, model.commits.length - 1);
  const y = value => pad.t + (high - value) / (high - low) * plotHeight;
  const segments = [];
  let segment = [];
  values.forEach((value, index) => {
    if (finite(value)) segment.push(`${x(index)},${y(value)}`);
    else if (segment.length) { segments.push(segment); segment = []; }
  });
  if (segment.length) segments.push(segment);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(currentName())} 性能趋势">`;
  for (let step = 0; step <= 4; step++) {
    const value = low + (high - low) * step / 4;
    svg += `<line x1="${pad.l}" y1="${y(value)}" x2="${W - pad.r}" y2="${y(value)}" stroke="#e9eef5"/><text x="${pad.l - 13}" y="${y(value) + 4}" text-anchor="end" fill="#8795a9" font-size="11">${escapeHtml(format(value))}</text>`;
  }
  if (state.mode === 'relative' && low < 0 && high > 0) svg += `<line x1="${pad.l}" y1="${y(0)}" x2="${W - pad.r}" y2="${y(0)}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;
  for (const [index, letter, lineColor] of [[state.base, 'A', '#7d8ba0'], [state.target, 'B', color]]) {
    svg += `<line x1="${x(index)}" y1="${pad.t}" x2="${x(index)}" y2="${H - pad.b}" stroke="${lineColor}" stroke-dasharray="4 5" opacity=".55"/><text x="${x(index)}" y="16" text-anchor="middle" fill="${lineColor}" font-size="11" font-weight="700">${letter}</text>`;
  }
  segments.forEach(points => { svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linejoin="round"/>`; });
  values.forEach((value, index) => {
    const commit = model.commits[index];
    svg += `<text x="${x(index)}" y="${H - 18}" text-anchor="middle" fill="${index === state.target ? color : '#7f8da1'}" font-size="11" font-weight="${index === state.target ? 700 : 500}">${commit.short_commit.slice(0, 7)}</text>`;
    if (finite(value)) svg += `<circle class="point" data-rev="${index}" cx="${x(index)}" cy="${y(value)}" r="${index === state.target ? 5.5 : 4}" fill="white" stroke="${color}" stroke-width="2.3" tabindex="0" role="button"><title>${commit.short_commit} · ${fullDate(commit.commit_time)} · ${format(value)}</title></circle>`;
  });
  svg += '</svg>';
  $(id).innerHTML = svg;
  $(id).querySelectorAll('.point').forEach(point => {
    const index = Number(point.dataset.rev);
    const select = () => { state.target = index; render(); };
    point.onclick = select;
    point.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } };
    point.onmouseenter = () => { $('hoverReadout').textContent = `${model.commits[index].short_commit}  ${format(values[index])}`; };
    point.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function drawMultiChart(id, series) {
  const usable = series.flatMap(item => item.values.filter(finite));
  if (!usable.length) { $(id).innerHTML = '<div class="empty-state">当前筛选没有可用数据</div>'; return; }
  const W = 1120, H = 274, pad = {l:68, r:28, t:28, b:50}, plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  const x = index => pad.l + index * plotWidth / Math.max(1, model.commits.length - 1);
  // IPC 的变化通常只有几个百分点，而计数器可能变化几十甚至几百个百分点。
  // 每个序列使用自己的纵向范围，保留真实值用于 tooltip，从视觉上避免小波动被压扁。
  const scales = series.map(item => {
    const values = item.values.filter(finite);
    let low = Math.min(0, ...values), high = Math.max(0, ...values), spread = high - low;
    if (spread < 1e-8) spread = Math.max(Math.abs(high) * 0.04, 0.04);
    return {low: low - spread * 0.2, high: high + spread * 0.2};
  });
  const y = (value, seriesIndex) => {
    const scale = scales[seriesIndex];
    return pad.t + (scale.high - value) / (scale.high - scale.low) * plotHeight;
  };
  const format = value => `${signed(value, 2)}%`;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(currentName())} IPC 与性能计数器趋势">`;
  for (let step = 0; step <= 4; step++) { const gridY = pad.t + plotHeight * step / 4; svg += `<line x1="${pad.l}" y1="${gridY}" x2="${W - pad.r}" y2="${gridY}" stroke="#e9eef5"/>`; }
  svg += `<text x="${pad.l - 13}" y="${pad.t + 4}" text-anchor="end" fill="#8795a9" font-size="10">高</text><text x="${pad.l - 13}" y="${H - pad.b + 4}" text-anchor="end" fill="#8795a9" font-size="10">低</text>`;
  for (const [index, letter, lineColor] of [[state.base, 'A', '#7d8ba0'], [state.target, 'B', '#2f63db']]) svg += `<line x1="${x(index)}" y1="${pad.t}" x2="${x(index)}" y2="${H - pad.b}" stroke="${lineColor}" stroke-dasharray="4 5" opacity=".55"/><text x="${x(index)}" y="16" text-anchor="middle" fill="${lineColor}" font-size="11" font-weight="700">${letter}</text>`;
  series.forEach((item, seriesIndex) => {
    const color = item.color;
    const segments = []; let segment = [];
    item.values.forEach((value, index) => { if (finite(value)) segment.push(`${x(index)},${y(value, seriesIndex)}`); else if (segment.length) { segments.push(segment); segment = []; } });
    if (segment.length) segments.push(segment);
    segments.forEach(points => { svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="${item.kind === 'ipc' ? '2.8' : '2.1'}" stroke-linejoin="round" ${item.kind === 'ipc' ? '' : 'stroke-dasharray="5 3"'}/>`; });
    item.values.forEach((value, index) => { if (finite(value)) svg += `<circle class="multi-point" data-rev="${index}" data-series="${seriesIndex}" cx="${x(index)}" cy="${y(value, seriesIndex)}" r="${index === state.target ? 4.8 : 3.5}" fill="white" stroke="${color}" stroke-width="2"><title>${escapeHtml(item.label)} · ${model.commits[index].short_commit} · ${format(value)}</title></circle>`; });
  });
  model.commits.forEach((commit, index) => { svg += `<text x="${x(index)}" y="${H - 18}" text-anchor="middle" fill="${index === state.target ? '#2f63db' : '#7f8da1'}" font-size="11" font-weight="${index === state.target ? 700 : 500}">${commit.short_commit.slice(0, 7)}</text>`; });
  svg += '</svg>'; $(id).innerHTML = svg;
  $(id).querySelectorAll('.multi-point').forEach(point => {
    const index = Number(point.dataset.rev), item = series[Number(point.dataset.series)];
    const select = () => { state.target = index; render(); };
    point.onclick = select;
    point.onmouseenter = () => { $('hoverReadout').textContent = `${item.label} · ${model.commits[index].short_commit}  ${format(item.values[index])}`; };
    point.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function comparisonData() {
  return model.workloads.map(workload => {
    const fromRow = workloadRow(workload, state.base), toRow = workloadRow(workload, state.target);
    const complete = fromRow?.status === 'complete' && toRow?.status === 'complete';
    const a = complete ? fromRow.weighted_ipc : null, b = complete ? toRow.weighted_ipc : null;
    return {workload, a, b, delta: change(a, b)};
  });
}

function renderKpis() {
  if (state.level === 'counter') {
    const metric = selectedCounterMetric();
    const a = counterValue(state.counterSlice, state.base), b = counterValue(state.counterSlice, state.target);
    const delta = performanceChange(metric, a, b);
    const unit = counterUnitLabel(metric);
    const available = model.commits.filter((_, index) => finite(counterValue(state.counterSlice, index))).length;
    const ranking = correlationRanking();
    const top = ranking[0];
    const current = ranking.find(row => row.metric.metric_id === state.counterMetric);
    const cards = [
      {label:'计数器 A', value:formatCounterValue(a), note:`${model.commits[state.base].short_commit} · ${unit}`, tone:'neutral'},
      {label:'计数器 B', value:formatCounterValue(b), note:`${model.commits[state.target].short_commit} · ${unit}`, tone:'neutral'},
      {label:'A/B 性能变化', value:pct(delta, 3), note:metric.direction === 'lower_is_better' ? '数值越低越好' : '数值越高越好', tone:tone(delta)},
      {label:'最强相关计数器', value:top ? signed(top.rho, 2) : '—', note:top ? `${top.metric.display_name} · ${top.metric.category}` : '样本不足', tone:'neutral'},
      {label:'当前相关性 ρ', value:current ? signed(current.rho, 2) : '—', note:current ? `${correlationStrength(current.rho)}相关 · ${metric.category}` : '样本不足', tone:'neutral'},
      {label:'趋势覆盖率', value:`${available}<small> / ${model.commits.length}</small>`, note:`${metric.category} · final PERF dump`, tone:available === model.commits.length ? 'good' : 'bad'}
    ];
    $('kpis').innerHTML = cards.map(card => `<article class="kpi"><div class="kpi-label">${card.label}</div><div class="kpi-value ${card.tone}">${card.value}</div><div class="kpi-note" title="${escapeHtml(card.note)}">${escapeHtml(card.note)}</div></article>`).join('');
    return;
  }
  const suiteDelta = change(1, suiteRatio(state.base, state.target));
  const scoreDelta = change(suiteScore(state.base), suiteScore(state.target));
  const comparisons = comparisonData().filter(item => finite(item.delta));
  const improved = comparisons.filter(item => item.delta > STABLE_THRESHOLD).length;
  const regressed = comparisons.filter(item => item.delta < -STABLE_THRESHOLD).length;
  const stable = comparisons.length - improved - regressed;
  const worstWorkload = comparisons.filter(item => item.delta < -STABLE_THRESHOLD).reduce((worst, item) => !worst || item.delta < worst.delta ? item : worst, null);
  let worstSlice = null;
  for (const slice of model.slices) {
    const delta = change(sliceIPC(slice, state.base), sliceIPC(slice, state.target));
    if (finite(delta) && (!worstSlice || delta < worstSlice.delta)) worstSlice = {slice: slice.slice, workload: slice.workload, delta};
  }
  const cards = [
    {label:'SPEC2006/GHz 变化', value:pct(scoreDelta, 3), note:`${formatCounterValue(suiteScore(state.base))} → ${formatCounterValue(suiteScore(state.target))}`, tone:tone(scoreDelta)},
    {label:'套件 IPC 指数变化', value:pct(suiteDelta, 3), note:`提升 ${improved} · 稳定 ${stable} · 退化 ${regressed}`, tone:tone(suiteDelta)},
    {label:'最大 Workload 退化', value:worstWorkload ? pct(worstWorkload.delta) : '0 项', note:worstWorkload?.workload || '无超过 -0.5% 的退化', tone:worstWorkload ? 'bad' : 'neutral'},
    {label:'最大切片退化', value:worstSlice ? pct(worstSlice.delta) : '—', note:worstSlice ? `${worstSlice.workload} / ${worstSlice.slice.split('_').at(-2)}` : '无可比数据', tone:'bad'}
  ];
  $('kpis').innerHTML = cards.map(card => `<article class="kpi"><div class="kpi-label">${card.label}</div><div class="kpi-value ${card.tone}">${card.value}</div><div class="kpi-note" title="${escapeHtml(card.note)}">${escapeHtml(card.note)}</div></article>`).join('');
}

function heatStyle(value) {
  if (!finite(value)) return 'color:#7d8999;background:#f1f3f6';
  const alpha = Math.min(0.45, 0.08 + Math.abs(value) / 16);
  return value > 0 ? `color:#096e61;background:rgba(22,145,123,${alpha})` : value < 0 ? `color:#a43e36;background:rgba(213,87,73,${alpha})` : 'color:#66758a;background:#f0f3f7';
}

function renderHeatmap() {
  const header = model.commits.map((commit, index) => `<th title="${escapeHtml(commit.subject)}">${commit.short_commit.slice(0, 7)}${index === state.base ? '<b>A</b>' : index === state.target ? '<b>B</b>' : ''}</th>`).join('');
  const body = model.workloads.map(workload => {
    const baseRow = workloadRow(workload, state.base), base = baseRow?.weighted_ipc;
    const cells = model.commits.map((_, index) => {
      const row = workloadRow(workload, index), value = change(base, row?.weighted_ipc);
      const partial = row && (row.status !== 'complete' || baseRow?.status !== 'complete');
      const selected = workload === state.workload && index === state.target ? ' current' : '';
      return `<td><button type="button" class="heat-cell${selected}" data-workload="${escapeHtml(workload)}" data-rev="${index}" style="${heatStyle(value)}" title="${escapeHtml(workload)} · ${model.commits[index].short_commit}: ${pct(value)}${partial ? ' · 部分覆盖' : ''}">${finite(value) ? signed(value, 1) : '—'}${partial ? '*' : ''}</button></td>`;
    }).join('');
    return `<tr><th title="${escapeHtml(workload)}">${escapeHtml(workload)}</th>${cells}</tr>`;
  }).join('');
  $('heatTable').innerHTML = `<thead><tr><th>Workload</th>${header}</tr></thead><tbody>${body}</tbody>`;
  $('heatTable').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.workload = button.dataset.workload;
    state.target = Number(button.dataset.rev);
    state.level = 'workload';
    state.metric = 'ipc';
    state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
  });
}

function renderMovers() {
  const all = comparisonData().filter(item => finite(item.delta) && item.a > 0 && item.b > 0);
  const count = all.length || 1;
  const contributions = all.map(item => ({...item, contribution: Math.log(item.b / item.a) / count * 100}));
  const shown = [...contributions].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).slice(0, 12);
  const max = Math.max(0.01, ...shown.map(item => Math.abs(item.contribution)));
  $('bars').innerHTML = shown.map(item => `<button type="button" class="bar-row" data-workload="${escapeHtml(item.workload)}" title="查看 ${escapeHtml(item.workload)}"><span>${escapeHtml(item.workload)}</span><span class="bar-track"><i class="bar-fill ${item.contribution >= 0 ? 'positive' : 'negative'}" style="${item.contribution >= 0 ? 'left:50%' : 'right:50%'};width:${Math.abs(item.contribution) / max * 48}%"></i></span><strong class="${tone(item.delta)}">${pct(item.delta, 2)}</strong></button>`).join('');
  $('bars').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.workload = button.dataset.workload;
    state.level = 'workload';
    state.metric = 'ipc';
    state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
    document.querySelector('.trend-panel').scrollIntoView({behavior:'smooth', block:'start'});
  });
  const net = change(1, suiteRatio(state.base, state.target));
  $('net').textContent = pct(net, 3);
  $('net').className = tone(net);
}

function sliceRecords() {
  return (model.slicesByWorkload.get(state.workload) || []).map(slice => {
    const a = sliceIPC(slice, state.base), b = sliceIPC(slice, state.target);
    const delta = change(a, b);
    const impact = finite(a) && finite(b) && a > 0 && b > 0 ? slice.weight * (1 / b - 1 / a) : null;
    return {slice, a, b, delta, impact};
  });
}

function renderSlices() {
  if (state.level === 'counter') {
    renderCounterSlices();
    return;
  }
  const records = sliceRecords();
  records.sort((a, b) => {
    if (state.sort === 'weight') return b.slice.weight - a.slice.weight;
    if (state.sort === 'change') return (finite(b.delta) ? Math.abs(b.delta) : -1) - (finite(a.delta) ? Math.abs(a.delta) : -1);
    return (finite(b.impact) ? Math.abs(b.impact) : -1) - (finite(a.impact) ? Math.abs(a.impact) : -1);
  });
  $('sliceTitle').textContent = `${state.workload} · 切片 A/B 对比`;
  $('sliceRows').innerHTML = records.map(record => {
    const status = !finite(record.delta) ? '缺测' : record.delta > STABLE_THRESHOLD ? '提升' : record.delta < -STABLE_THRESHOLD ? '退化' : '稳定';
    return `<tr class="${record.slice.slice === state.slice ? 'selected-row' : ''}"><td><button type="button" class="slice-btn" data-slice="${escapeHtml(record.slice.slice)}"><strong>${escapeHtml(record.slice.checkpoint)}</strong><span>${escapeHtml(record.slice.slice)}</span></button></td><td>${(record.slice.weight * 100).toFixed(3)}%</td><td>${finite(record.a) ? record.a.toFixed(4) : '—'}</td><td>${finite(record.b) ? record.b.toFixed(4) : '—'}</td><td class="${tone(record.delta)}">${pct(record.delta)}</td><td class="${finite(record.impact) ? record.impact > 0 ? 'bad' : record.impact < 0 ? 'good' : 'neutral' : 'neutral'}">${signed(record.impact, 6)}</td><td><span class="status ${tone(record.delta)}">${status}</span></td></tr>`;
  }).join('');
  $('sliceRows').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.slice = button.dataset.slice;
    state.workload = model.slices.find(slice => slice.slice === state.slice).workload;
    state.level = 'slice';
    state.metric = 'ipc';
    render();
    document.querySelector('.trend-panel').scrollIntoView({behavior:'smooth', block:'start'});
  });
  const comparable = records.filter(record => finite(record.delta));
  const improved = comparable.filter(record => record.delta > STABLE_THRESHOLD).length;
  const regressed = comparable.filter(record => record.delta < -STABLE_THRESHOLD).length;
  const netImpact = comparable.reduce((sum, record) => sum + (record.impact || 0), 0);
  const weightCoverage = comparable.reduce((sum, record) => sum + record.slice.weight, 0) * 100;
  $('sliceSummary').innerHTML = `<span>可比切片 <b>${comparable.length} / ${records.length}</b></span><span>提升 <b class="good">${improved}</b></span><span>退化 <b class="bad">${regressed}</b></span><span>净加权 ΔCPI <b class="${netImpact > 0 ? 'bad' : netImpact < 0 ? 'good' : 'neutral'}">${signed(netImpact, 6)}</b></span><span>权重覆盖 <b>${weightCoverage.toFixed(3)}%</b></span>`;
}

function renderCounterSlices() {
  const metric = selectedCounterMetric();
  const workload = currentCounterWorkload();
  const records = counterAnalysisSlices().map(slice => {
    const a = counterValue(slice.slice, state.base), b = counterValue(slice.slice, state.target);
    return {slice, a, b, delta:performanceChange(metric, a, b)};
  }).sort((a, b) => (finite(b.delta) ? Math.abs(b.delta) : -1) - (finite(a.delta) ? Math.abs(a.delta) : -1));
  $('sliceTitle').textContent = `${workload} · ${metric.display_name} · 切片 A/B 对比`;
  $('valueAHead').textContent = `${counterUnitLabel(metric)} · A`;
  $('valueBHead').textContent = `${counterUnitLabel(metric)} · B`;
  $('changeHead').textContent = '性能变化';
  $('impactHead').textContent = '数值变化';
  $('sort').disabled = true;
  $('sliceRows').innerHTML = records.map(record => {
    const raw = finite(record.a) && finite(record.b) ? record.b - record.a : null;
    const status = !finite(record.delta) ? '缺测' : record.delta > STABLE_THRESHOLD ? '改善' : record.delta < -STABLE_THRESHOLD ? '退化' : '稳定';
    return `<tr class="${record.slice.slice === state.counterSlice ? 'selected-row' : ''}"><td><button type="button" class="slice-btn" data-counter-slice="${escapeHtml(record.slice.slice)}"><strong>${escapeHtml(record.slice.checkpoint)}</strong><span>${escapeHtml(record.slice.slice)}</span></button></td><td>${(record.slice.weight * 100).toFixed(3)}%</td><td>${formatCounterValue(record.a)}</td><td>${formatCounterValue(record.b)}</td><td class="${tone(record.delta)}">${pct(record.delta)}</td><td>${finite(raw) ? signed(raw, 0) : '—'}</td><td><span class="status ${tone(record.delta)}">${status}</span></td></tr>`;
  }).join('');
  $('sliceRows').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.counterSlice = button.dataset.counterSlice;
    render();
    document.querySelector('.trend-panel').scrollIntoView({behavior:'smooth', block:'start'});
  });
  const comparable = records.filter(record => finite(record.delta));
  const improved = comparable.filter(record => record.delta > STABLE_THRESHOLD).length;
  const regressed = comparable.filter(record => record.delta < -STABLE_THRESHOLD).length;
  $('sliceSummary').innerHTML = `<span>可比切片 <b>${comparable.length} / ${records.length}</b></span><span>改善 <b class="good">${improved}</b></span><span>退化 <b class="bad">${regressed}</b></span><span>方向 <b>${metric.direction === 'lower_is_better' ? '越低越好' : '越高越好'}</b></span><span>口径 <b>${counterUnitLabel(metric)}</b></span><span>窗口 <b>final PERF dump</b></span>`;
}

function median(values) {
  const usable = values.filter(finite).sort((a, b) => a - b);
  if (!usable.length) return null;
  const middle = Math.floor(usable.length / 2);
  return usable.length % 2 ? usable[middle] : (usable[middle - 1] + usable[middle]) / 2;
}

function leastSquares(points) {
  if (points.length < 3) return null;
  const meanX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  let covariance = 0, variance = 0;
  for (const point of points) {
    covariance += (point.x - meanX) * (point.y - meanY);
    variance += (point.x - meanX) ** 2;
  }
  if (variance <= 0) return null;
  const slope = covariance / variance;
  return {slope, intercept: meanY - slope * meanX};
}

function scatterAxisFormat(isPercent) {
  if (isPercent) return value => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
  return value => Math.abs(value) >= 1000 ? `${(value / 1000).toFixed(1)}k` : Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(2);
}

// 参考 thp_seed_cmp 的关键路径散点：x 轴是计数器，y 轴是 IPC，
// 用 Spearman ρ 判断这个计数器是否真的和性能同向变化。
function drawScatter(id, pairs, {xLabel, yLabel, xPct, yPct, rho, mode}) {
  const points = pairs.filter(pair => finite(pair.x) && finite(pair.y));
  if (!points.length) { $(id).innerHTML = '<div class="empty-state">当前视角没有可用数据</div>'; return; }
  const W = 880, H = 360, pad = {l:74, r:30, t:34, b:62};
  const plotWidth = W - pad.l - pad.r, plotHeight = H - pad.t - pad.b;
  const bounds = (values, isPercent) => {
    const floor = Math.min(...values);
    let low = floor, high = Math.max(...values), span = high - low;
    if (span < 1e-9) span = Math.max(Math.abs(high) * 0.08, isPercent ? 0.5 : 1e-6);
    low -= span * 0.14; high += span * 0.14;
    if (isPercent) { low = Math.min(low, 0); high = Math.max(high, 0); }
    else if (floor >= 0) low = Math.max(low, 0);
    return [low, high];
  };
  const [xLow, xHigh] = bounds(points.map(point => point.x), xPct);
  const [yLow, yHigh] = bounds(points.map(point => point.y), yPct);
  const X = value => pad.l + (value - xLow) / (xHigh - xLow) * plotWidth;
  const Y = value => pad.t + (yHigh - value) / (yHigh - yLow) * plotHeight;
  const xFormat = scatterAxisFormat(xPct), yFormat = scatterAxisFormat(yPct);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(xLabel)} 与 ${escapeHtml(yLabel)} 的关系">`;
  for (let step = 0; step <= 4; step++) {
    const value = yLow + (yHigh - yLow) * step / 4;
    svg += `<line x1="${pad.l}" y1="${Y(value)}" x2="${W - pad.r}" y2="${Y(value)}" stroke="#e9eef5"/>`;
    svg += `<text x="${pad.l - 12}" y="${Y(value) + 4}" text-anchor="end" fill="#8795a9" font-size="11">${escapeHtml(yFormat(value))}</text>`;
  }
  for (let step = 0; step <= 4; step++) {
    const value = xLow + (xHigh - xLow) * step / 4;
    svg += `<text x="${X(value)}" y="${H - pad.b + 20}" text-anchor="middle" fill="#8795a9" font-size="11">${escapeHtml(xFormat(value))}</text>`;
  }
  if (xPct && xLow < 0 && xHigh > 0) svg += `<line x1="${X(0)}" y1="${pad.t}" x2="${X(0)}" y2="${H - pad.b}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;
  if (yPct && yLow < 0 && yHigh > 0) svg += `<line x1="${pad.l}" y1="${Y(0)}" x2="${W - pad.r}" y2="${Y(0)}" stroke="#aeb9c8" stroke-dasharray="3 4"/>`;

  const fit = leastSquares(points);
  if (fit) {
    const y1 = fit.slope * xLow + fit.intercept, y2 = fit.slope * xHigh + fit.intercept;
    svg += `<line x1="${X(xLow)}" y1="${Y(y1)}" x2="${X(xHigh)}" y2="${Y(y2)}" stroke="#8fa4c4" stroke-width="1.8" stroke-dasharray="6 5" opacity=".85"/>`;
  }

  const weights = points.map(point => point.weight);
  const weightMax = Math.max(...weights), weightMin = Math.min(...weights);
  const weightSpan = weightMax - weightMin || 1;
  const radius = point => 5 + 9 * Math.sqrt((point.weight - weightMin) / weightSpan);
  const yMedian = median(points.map(point => point.y));
  const better = point => yPct ? point.y >= 0 : point.y >= yMedian;

  const labelled = new Map();
  const remember = point => { if (point && !labelled.has(point.slice)) labelled.set(point.slice, point); };
  for (const point of [...points].sort((a, b) => b.weight - a.weight).slice(0, 3)) remember(point);
  remember(points.reduce((best, point) => !best || point.x > best.x ? point : best, null));
  remember(points.reduce((best, point) => !best || point.x < best.x ? point : best, null));
  remember(points.reduce((best, point) => !best || point.y > best.y ? point : best, null));
  remember(points.reduce((best, point) => !best || point.y < best.y ? point : best, null));

  for (const point of points) {
    const cx = X(point.x), cy = Y(point.y), selected = point.slice === state.counterSlice;
    svg += `<circle class="point" data-slice="${escapeHtml(point.slice)}" cx="${cx}" cy="${cy}" r="${radius(point).toFixed(2)}" fill="${better(point) ? '#16917b' : '#d55749'}" fill-opacity="${selected ? '0.95' : '0.6'}" stroke="${selected ? '#14233a' : (better(point) ? '#0d6f5e' : '#a8402f')}" stroke-width="${selected ? '2.6' : '1.3'}" tabindex="0" role="button"><title>${escapeHtml(point.slice)} · ${escapeHtml(xLabel)} ${xFormat(point.x)} · IPC ${yFormat(point.y)} · 权重 ${(point.weight * 100).toFixed(2)}%</title></circle>`;
  }
  for (const point of labelled.values()) {
    const cx = X(point.x), cy = Y(point.y);
    const anchor = cx > W - pad.r - 96 ? 'end' : 'start';
    svg += `<text x="${cx + (anchor === 'end' ? -8 : 8)}" y="${cy - radius(point) - 4}" text-anchor="${anchor}" fill="#5d6e86" font-size="10" font-weight="600">${escapeHtml(sliceLabel(point.slice))}</text>`;
  }
  svg += `<text x="${W - pad.r}" y="16" text-anchor="end" fill="${finite(rho) && Math.abs(rho) >= 0.4 ? '#2f63db' : '#8795a9'}" font-size="12" font-weight="700">Spearman ρ = ${finite(rho) ? signed(rho, 3) : '—'}</text>`;
  svg += `<text x="${pad.l}" y="16" fill="#8795a9" font-size="10">${escapeHtml(mode === 'level' ? '灰色虚线为最小二乘拟合' : '过零虚线表示性能不变')}</text>`;
  svg += `<text x="${pad.l + plotWidth / 2}" y="${H - 10}" text-anchor="middle" fill="#6d7c91" font-size="11">${escapeHtml(xLabel)}</text>`;
  svg += `<text transform="translate(16,${pad.t + plotHeight / 2}) rotate(-90)" text-anchor="middle" fill="#6d7c91" font-size="11">${escapeHtml(yLabel)}</text>`;
  svg += '</svg>';
  $(id).innerHTML = svg;
  $(id).querySelectorAll('.point').forEach(node => {
    const slice = node.dataset.slice;
    const select = () => { state.counterSlice = slice; render(); };
    node.onclick = select;
    node.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } };
    node.onmouseenter = () => { $('hoverReadout').textContent = slice; };
    node.onmouseleave = () => { $('hoverReadout').textContent = ''; };
  });
}

function renderRelation() {
  const metric = selectedCounterMetric();
  const mode = state.relationMode;
  const pairs = relationPairs(mode);
  const {rho, n} = spearman(pairs);
  const labels = relationAxisLabels(mode, metric);
  drawScatter('relationScatter', pairs, {xLabel: labels.x, yLabel: labels.y, xPct: labels.xPct, yPct: labels.yPct, rho, mode});

  const chipClass = !finite(rho) || Math.abs(rho) < 0.2 ? 'muted' : rho > 0 ? 'pos' : 'neg';
  const direction = !finite(rho) ? '样本不足，无法判断方向'
    : Math.abs(rho) < 0.2 ? '方向不明确，这个计数器不单独预测 IPC'
    : rho > 0 ? '正相关：计数器越大，IPC 越高'
    : '负相关：计数器越大，IPC 越低';
  const expected = metric.direction === 'lower_is_better'
    ? '该指标越低越好，预期与 IPC 负相关'
    : '该指标越高越好，预期与 IPC 正相关';
  $('relationSub').textContent = relationCaption(mode);
  $('relationStats').innerHTML = `<span class="rho-chip ${chipClass}"><i></i>Spearman ρ ${finite(rho) ? signed(rho, 3) : '—'}</span>`
    + `<span>相关强度 <b>${correlationStrength(rho)}</b></span>`
    + `<span>有效样本 <b>${n}</b></span>`
    + `<span>方向 <b>${direction}</b></span>`
    + `<span>口径 <b>${escapeHtml(metric.display_name)} · ${escapeHtml(counterUnitLabel(metric))}</b></span>`
    + `<span class="relation-note">${expected}。计数器来自 final PERF dump 的固定 ~20M 指令窗口，IPC 来自切片 summary 窗口，两者窗口不同，这里的 ρ 只用于诊断相关性，不构成归因。</span>`;
}

function correlationRanking(mode = state.relationMode) {
  return model.counterMetrics.map(metric => {
    const {rho, n} = spearman(relationPairs(mode, metric.metric_id));
    return {metric, rho, n};
  }).filter(row => finite(row.rho) && row.metric.metric_id !== BASE_METRIC).sort((a, b) => Math.abs(b.rho) - Math.abs(a.rho));
}

function renderCorrelationBars() {
  const rows = correlationRanking();
  $('rankingCount').textContent = `${rows.length} / ${model.counterMetrics.length} 项`;
  $('correlationBars').innerHTML = rows.map((row, index) => {
    const selected = row.metric.metric_id === state.counterMetric;
    const positive = row.rho >= 0;
    const width = Math.min(50, Math.abs(row.rho) * 50);
    return `<button type="button" class="corr-row${selected ? ' selected' : ''}" data-metric="${escapeHtml(row.metric.metric_id)}" title="${escapeHtml(row.metric.display_name)} · ρ=${signed(row.rho, 3)} · n=${row.n}">`
      + `<span>${index + 1}. ${escapeHtml(row.metric.display_name)}<em>${escapeHtml(row.metric.category)}</em></span>`
      + `<span class="corr-track"><i class="corr-fill ${positive ? 'pos' : 'neg'}" style="${positive ? 'left:50%' : 'right:50%'};width:${width}%"></i></span>`
      + `<span class="corr-value ${positive ? 'pos' : 'neg'}">${signed(row.rho, 2)}</span>`
      + `<span class="corr-flag">${selected ? '当前 ✓' : ''}</span></button>`;
  }).join('');
  $('correlationBars').querySelectorAll('button').forEach(button => button.onclick = () => {
    state.counterMetric = button.dataset.metric;
    if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    render();
  });
}

function renderCounterMatrix() {
  const metric = selectedCounterMetric();
  const slices = [...counterAnalysisSlices()].sort((a, b) => {
    const deltaA = ipcChangePct(a.slice, state.base, state.target), deltaB = ipcChangePct(b.slice, state.base, state.target);
    return (finite(deltaB) ? deltaB : -Infinity) - (finite(deltaA) ? deltaA : -Infinity);
  });
  const head = `<tr><th class="corner">计数器</th>`
    + slices.map(slice => `<th class="matrix-slice-head" title="${escapeHtml(slice.slice)}"><b>${escapeHtml(String(slice.checkpoint))}</b>${pct(ipcChangePct(slice.slice, state.base, state.target), 2)}</th>`).join('')
    + `<th>中位 Δ%</th></tr>`;
  const categories = new Map();
  for (const item of model.counterMetrics) {
    if (!categories.has(item.category)) categories.set(item.category, []);
    categories.get(item.category).push(item);
  }
  let body = '';
  for (const [category, metrics] of categories) {
    body += `<tr class="cat-row"><th colspan="${slices.length + 2}">${escapeHtml(category)}</th></tr>`;
    for (const item of metrics) {
      const deltas = slices.map(slice => counterChangePct(slice.slice, state.base, state.target, item.metric_id));
      const cells = slices.map((slice, index) => {
        const delta = deltas[index];
        const directional = finite(delta) && item.direction === 'lower_is_better' ? -delta : delta;
        const current = slice.slice === state.counterSlice ? ' current' : '';
        return `<td><div class="matrix-cell${current}" style="${heatStyle(directional)}" title="${escapeHtml(slice.slice)} · ${escapeHtml(item.display_name)}: ${pct(delta, 2)}">${finite(delta) ? signed(delta, 0) : '—'}</div></td>`;
      }).join('');
      const middle = median(deltas);
      const middleDirectional = finite(middle) && item.direction === 'lower_is_better' ? -middle : middle;
      body += `<tr class="metric-row${item.metric_id === metric.metric_id ? ' selected' : ''}" data-metric="${escapeHtml(item.metric_id)}"><th class="metric-head"><button type="button" title="${escapeHtml(item.display_name)}">${escapeHtml(item.display_name)}</button></th>${cells}<td><span class="matrix-median" style="${heatStyle(middleDirectional)}">${finite(middle) ? signed(middle, 1) : '—'}</span></td></tr>`;
    }
  }
  $('counterMatrix').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  $('counterMatrix').querySelectorAll('tr.metric-row').forEach(row => row.onclick = () => {
    state.counterMetric = row.dataset.metric;
    if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    render();
  });
  $('matrixSub').textContent = `${currentCounterWorkload()} · ${model.commits[state.base].short_commit} → ${model.commits[state.target].short_commit} · ${state.counterUnit === 'per_kinst' ? '每千指令' : '原始计数'}口径 · 绿色代表该计数器朝有利方向变化`;
}

function renderTransitions() {
  $('transitions').innerHTML = model.transitions.map((item, index) => {
    const total = item.comparable_slice_count;
    const improved = item.improved_over_0_5pct / total * 100;
    const stable = item.stable_within_0_5pct / total * 100;
    const regressed = item.regressed_over_0_5pct / total * 100;
    return `<article class="transition-card"><div class="transition-head"><span><b>${index + 1}</b>${item.from_commit.slice(0, 7)} <i>→</i> ${item.to_commit.slice(0, 7)}</span><strong class="${tone(item.median_change_pct)}">${pct(item.median_change_pct, 3)}</strong></div><div class="stack" title="提升 ${item.improved_over_0_5pct} · 稳定 ${item.stable_within_0_5pct} · 退化 ${item.regressed_over_0_5pct}"><i class="up" style="width:${improved}%"></i><i class="steady" style="width:${stable}%"></i><i class="down" style="width:${regressed}%"></i></div><div class="transition-meta"><span class="good">↑ ${item.improved_over_0_5pct}</span><span>稳定 ${item.stable_within_0_5pct}</span><span class="bad">↓ ${item.regressed_over_0_5pct}</span><span>${total} 可比</span></div></article>`;
  }).join('');
}

function renderCommits() {
  const errorsByCommit = new Map();
  for (const error of model.manifest.errors) errorsByCommit.set(error.short_commit, (errorsByCommit.get(error.short_commit) || 0) + 1);
  $('missingBadge').textContent = `${model.manifest.errors.length} 条缺测`;
  $('commitRows').innerHTML = model.commits.map((commit, index) => {
    const errors = errorsByCommit.get(commit.short_commit) || 0;
    return `<tr class="${index === state.target ? 'target-row' : ''}"><td>${String(index + 1).padStart(2, '0')}</td><td><code>${commit.short_commit}</code>${index === state.base ? '<span class="ab-badge">A</span>' : ''}${index === state.target ? '<span class="ab-badge target">B</span>' : ''}</td><td>${fullDate(commit.commit_time)}</td><td title="${escapeHtml(commit.subject)}">${escapeHtml(commit.subject)}</td><td><span class="coverage-state ${errors ? 'partial' : ''}">${errors ? `${errors} 条缺测` : '完整'}</span></td></tr>`;
  }).join('');
}

function renderTrend() {
  if (state.level === 'counter' && state.counterDisplay === 'counters' && !selectedCounterMetricIds().length) {
    $('trendTitle').textContent = `${currentName()} · 性能计数器趋势`;
    $('trendSub').textContent = '请至少勾选一个性能计数器';
    $('valueMode').hidden = true;
    $('legend').innerHTML = '';
    $('trend').innerHTML = '<div class="empty-state">未选择性能计数器</div>';
    return;
  }
  if (state.level === 'counter' && state.counterDisplay === 'ipc') {
    const values = model.commits.map((_, index) => ipcAt(state.counterSlice, index));
    $('trendTitle').textContent = `${currentName()} · IPC趋势`;
    $('trendSub').textContent = '横轴为 commit · 固定切片 IPC · 可切换绝对值 / 相对基线';
    $('valueMode').hidden = false;
    const displayed = state.mode === 'relative' ? (() => { const base = values[state.base]; return values.map(value => finite(value) && finite(base) && base !== 0 ? (value / base - 1) * 100 : null); })() : values;
    $('legend').innerHTML = '<span class="legend-item"><i class="legend-line"></i>IPC</span>';
    drawChart('trend', displayed, {color:'#2f63db', format:state.mode === 'relative' ? value => `${signed(value, 2)}%` : value => value.toFixed(3)});
    return;
  }
  if (state.level === 'counter' && state.counterDisplay !== 'ipc' && (state.counterDisplay === 'combined' || selectedCounterMetricIds().length > 1)) {
    const ids = selectedCounterMetricIds();
    const ipcRaw = model.commits.map((_, index) => ipcAt(state.counterSlice, index));
    const ipcBase = ipcRaw[state.base];
    const series = [];
    if (state.counterDisplay === 'combined') series.push({label:'IPC', kind:'ipc', color:'#2f63db', values:ipcRaw.map(value => finite(value) && finite(ipcBase) && ipcBase !== 0 ? (value / ipcBase - 1) * 100 : null)});
    const palette = ['#d5732f','#239b8b','#8259c7','#d14973','#5b84d8','#8c9a3c','#b34e9b','#4d8baf'];
    ids.forEach((metricId, index) => {
      const metric = model.counterMetricById.get(metricId);
      const raw = model.commits.map((_, commitIndex) => counterValue(state.counterSlice, commitIndex, metricId));
      const base = raw[state.base];
      series.push({label:metric.display_name, kind:'counter', color:palette[index % palette.length], values:raw.map(value => finite(value) && finite(base) && base !== 0 ? (metric.direction === 'lower_is_better' ? (base / value - 1) * 100 : (value / base - 1) * 100) : null)});
    });
    $('trendTitle').textContent = `${currentName()} · ${state.counterDisplay === 'combined' ? 'IPC + 性能计数器' : '性能计数器'}趋势`;
    $('trendSub').textContent = `横轴为 commit · 相对基线 A · 正值表示 IPC 或指标朝有利方向变化 · 各序列独立纵向缩放 · ${state.counterUnit === 'per_kinst' ? '每千指令' : '原始计数'}`;
    $('valueMode').hidden = true;
    $('legend').innerHTML = series.map(item => `<span class="legend-item" style="--legend-color:${item.color}"><i class="legend-line"></i>${escapeHtml(item.label)}</span>`).join('');
    drawMultiChart('trend', series);
    return;
  }
  $('valueMode').hidden = false;
  const rawValues = absoluteValues();
  const values = displayValues(rawValues);
  const label = metricLabel();
  $('trendTitle').textContent = `${currentName()} · ${label}趋势`;
  $('trendSub').textContent = state.mode === 'relative' ? '相对基线 A，正值统一表示性能改善' : state.level === 'counter' ? `${selectedCounterMetric().category} · ${counterUnitLabel()} · ${selectedCounterMetric().direction === 'lower_is_better' ? '越低越好' : '越高越好'} · final PERF dump` : state.metric === 'score' ? '评分文件发布值；固定 gcc16、RVA23、novec 口径' : state.metric === 'index' ? '首个 commit = 100；跨 workload 仅聚合相对变化' : '固定切片集与实验配置，按 committer time 排列';
  $('legend').innerHTML = `<span class="legend-item"><i class="legend-line"></i>${escapeHtml(currentName())}</span>`;
  const format = state.mode === 'relative' ? value => `${signed(value, 2)}%` : state.level === 'counter' ? (state.counterUnit === 'per_kinst' ? value => value.toFixed(2) : formatCounterValue) : state.metric === 'index' ? value => value.toFixed(2) : value => value.toFixed(3);
  drawChart('trend', values, {format});
}

function render() {
  syncControls();
  const counterMode = state.level === 'counter';
  if (counterMode) {
    $('sliceCount').textContent = `${model.counterSlices.length} 个切片`;
    $('observationCount').textContent = `${model.counterObservationCount} / ${model.counterSlices.length * model.counterMetrics.length * model.commits.length}`;
    $('coverageText').textContent = `${(model.counterObservationCount / (model.counterSlices.length * model.counterMetrics.length * model.commits.length) * 100).toFixed(3)}% counter 覆盖`;
  } else {
    $('sliceCount').textContent = `${model.manifest.slice_count} 个`;
    $('observationCount').textContent = `${model.manifest.valid_observations} / ${model.manifest.expected_observations}`;
    $('coverageText').textContent = `${(model.manifest.valid_observations / model.manifest.expected_observations * 100).toFixed(3)}% 数据覆盖`;
  }
  $('workloads').hidden = counterMode;
  $('commits').hidden = counterMode;
  $('counterInsights').hidden = !counterMode;
  if (!counterMode) {
    $('valueAHead').textContent = 'IPC · A';
    $('valueBHead').textContent = 'IPC · B';
    $('changeHead').textContent = 'IPC 变化';
    $('impactHead').textContent = '加权 ΔCPI';
    $('sort').disabled = false;
  }
  renderKpis();
  renderTrend();
  if (counterMode) {
    renderRelation();
    renderCorrelationBars();
    renderCounterMatrix();
  }
  if (!counterMode) {
    renderHeatmap();
    renderMovers();
  }
  renderSlices();
  if (!counterMode) renderTransitions();
  renderCommits();
}

function bindEvents() {
  $('level').onchange = event => {
    state.level = event.target.value;
    if (state.level === 'suite') state.metric = 'score';
    if (state.level === 'slice' && !state.slice) state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    render();
  };
  $('entity').onchange = event => {
    if (state.level === 'workload') {
      state.workload = event.target.value;
      state.slice = model.slicesByWorkload.get(state.workload)[0].slice;
    } else if (state.level === 'slice') {
      state.slice = event.target.value;
      state.workload = model.slices.find(slice => slice.slice === state.slice).workload;
    } else if (state.level === 'counter') {
      state.counterSlice = event.target.value;
    }
    render();
  };
  $('metric').onchange = event => {
    if (state.level === 'counter') {
      state.counterMetric = event.target.value;
      if (!selectedCounterMetricIds().includes(state.counterMetric)) state.selectedCounterMetrics = [...selectedCounterMetricIds(), state.counterMetric];
    }
    else state.metric = event.target.value;
    render();
  };
  $('base').onchange = event => { state.base = Number(event.target.value); render(); };
  $('target').onchange = event => { state.target = Number(event.target.value); render(); };
  $('sort').onchange = event => { state.sort = event.target.value; renderSlices(); };
  $('counterUnit').onchange = event => { state.counterUnit = event.target.value; render(); };
  document.querySelectorAll('#counterDisplayMode [data-display]').forEach(button => button.onclick = () => {
    state.counterDisplay = button.dataset.display;
    render();
  });
  $('selectAllCounters').onclick = () => { state.selectedCounterMetrics = model.counterMetrics.map(metric => metric.metric_id); render(); };
  $('clearCounters').onclick = () => { state.selectedCounterMetrics = []; state.counterDisplay = 'ipc'; render(); };
  document.querySelectorAll('#relationMode [data-rel]').forEach(button => button.onclick = () => { state.relationMode = button.dataset.rel; render(); });
  document.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => { state.mode = button.dataset.mode; render(); });
  $('reset').onclick = () => {
    state = {...state, level:'suite', metric:'score', base:0, target:model.commits.length - 1, mode:'absolute', sort:'impact', counterDisplay:'combined', selectedCounterMetrics:[state.counterMetric]};
    render();
  };
  $('export').onclick = exportComparison;
  $('counterNav').onclick = event => {
    event.preventDefault();
    state.level = 'counter';
    state.mode = 'absolute';
    render();
    document.querySelector('.filters').scrollIntoView({behavior:'smooth', block:'start'});
  };
}

function csvCell(value) {
  const text = value == null ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportComparison() {
  if (state.level === 'counter') {
    exportCounterComparison();
    return;
  }
  const header = ['baseline','target','workload','slice','checkpoint','weight','ipc_a','ipc_b','ipc_change_pct','weighted_cpi_delta'];
  const rows = model.slices.map(slice => {
    const a = sliceIPC(slice, state.base), b = sliceIPC(slice, state.target);
    const delta = change(a, b);
    const impact = finite(a) && finite(b) ? slice.weight * (1 / b - 1 / a) : null;
    return [model.commits[state.base].short_commit, model.commits[state.target].short_commit, slice.workload, slice.slice, slice.checkpoint, slice.weight, a, b, delta, impact].map(csvCell).join(',');
  });
  const blob = new Blob(['\ufeff' + [header.join(','), ...rows].join('\n')], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url;
  link.download = `spec06-ipc-${model.commits[state.base].short_commit}-to-${model.commits[state.target].short_commit}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportCounterComparison() {
  const metric = selectedCounterMetric();
  const {rho} = spearman(relationPairs(state.relationMode));
  const header = ['baseline','target','metric_id','metric_name','unit','unit_mode','direction','relation_mode','spearman_rho','slice','checkpoint','weight','value_a','value_b','performance_change_pct','ipc_a','ipc_b','ipc_change_pct','availability_a','availability_b'];
  const rows = model.counterSlices.map(slice => {
    const rowA = counterRow(slice.slice, state.base), rowB = counterRow(slice.slice, state.target);
    const a = counterValue(slice.slice, state.base), b = counterValue(slice.slice, state.target);
    const ipcA = ipcAt(slice.slice, state.base), ipcB = ipcAt(slice.slice, state.target);
    return [model.commits[state.base].short_commit, model.commits[state.target].short_commit, metric.metric_id, metric.display_name, counterUnitLabel(metric), state.counterUnit, metric.direction, state.relationMode, rho, slice.slice, slice.checkpoint, slice.weight, a, b, performanceChange(metric, a, b), ipcA, ipcB, change(ipcA, ipcB), rowA?.availability, rowB?.availability].map(csvCell).join(',');
  });
  const blob = new Blob(['\ufeff' + [header.join(','), ...rows].join('\n')], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url;
  link.download = `perf-counter-${metric.metric_id}-${model.commits[state.base].short_commit}-to-${model.commits[state.target].short_commit}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function init() {
  try {
    await loadData();
    const firstDate = shortDate(model.commits[0].commit_time), lastDate = shortDate(model.commits.at(-1).commit_time);
    $('scopeStats').textContent = `${model.commits.length} 个主线点 · ${model.workloads.length} workloads · ${model.slices.length} IPC 切片`;
    $('scopeRange').textContent = `${firstDate} - ${lastDate} · kunminghu-v3`;
    $('transitionCount').textContent = `共 ${model.transitions.length} 个区间`;
    if (!model.counterMetrics.length) {
      $('counterNav').hidden = true;
      $('level').querySelector('option[value="counter"]').remove();
    }
    $('configName').textContent = model.commits[0].config;
    $('sliceCount').textContent = `${model.manifest.slice_count} 个`;
    $('observationCount').textContent = `${model.manifest.valid_observations} / ${model.manifest.expected_observations}`;
    $('coverageText').textContent = `${(model.manifest.valid_observations / model.manifest.expected_observations * 100).toFixed(3)}% 数据覆盖`;
    bindEvents();
    render();
    $('loading').hidden = true;
    $('dashboard').hidden = false;
  } catch (error) {
    console.error(error);
    $('loading').hidden = true;
    $('error').hidden = false;
    $('error').innerHTML = `<strong>数据加载失败</strong><span>${escapeHtml(error.message)}。请通过本地 HTTP 服务打开看板，并确认数据目录可访问。</span>`;
  }
}

init();
