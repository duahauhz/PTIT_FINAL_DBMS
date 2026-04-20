'use strict';

// ── Pipeline step definitions ──────────────────────────────
const PIPELINE_STEPS = [
  { key: 'validate_input',             name: 'Validate Input' },
  { key: 'execute_procedure',          name: 'Execute SQL / CALL' },
  { key: 'check_trigger_side_effects', name: 'Trigger Side-Effects' },
  { key: 'refresh_source_tables',      name: 'Read Tables' },
  { key: 'refresh_reporting_views',    name: 'Read Views' },
  { key: 'complete',                   name: 'Complete' },
];

// Bảng liên quan đến từng kịch bản (để lọc Before/After)
const SCENARIO_RELEVANT_TABLES = {
  enroll:           ['course_enrollments', 'student_streaks'],
  update_progress:  ['course_enrollments', 'notification_users'],
  progress_comment: ['course_enrollments', 'comments', 'notification_users'],
  soft_delete_user: ['course_enrollments', 'student_streaks'],
  soft_delete_course: ['course_enrollments', 'student_streaks'],
  view_reports:     [],   // Chỉ views
  search_students:  [],
  search_courses:   [],
};

// ── State ──────────────────────────────────────────────────
let currentRunId    = null;
let currentEventSrc = null;
let activeScenario  = 'view_reports';

// ── Element cache ──────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = {
  terminal:       $('sql-terminal'),
  pipelineTrack:  $('pipeline-track'),
  runId:          $('run-id-display'),
  runStatus:      $('run-status-display'),
  dbStatus:       $('db-status-text'),
  dbChip:         $('db-status-chip'),
  metricsSidebar: $('sidebar-metrics'),
  tablesBefore:   $('tables-before'),
  tablesAfter:    $('tables-after'),
  viewsBefore:    $('views-before'),
  viewsAfter:     $('views-after'),
  ssKeyword:    $('ss-keyword'),
  scKeyword:    $('sc-keyword'), scCategory: $('sc-category'), scStatus: $('sc-status'),
  enrollStudent: $('enroll-student'), enrollCourse: $('enroll-course'),
  upStudent: $('up-student'), upCourse: $('up-course'), upProgress: $('up-progress'),
  pcStudent: $('pc-student'), pcCourse: $('pc-course'), pcLesson: $('pc-lesson'),
  pcProgress: $('pc-progress'), pcComment: $('pc-comment'),
  sduUser:   $('sdu-user'),
  sdcCourse: $('sdc-course'),
};

// ── Helpers ────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function ts() {
  return new Date().toLocaleTimeString('vi-VN', { hour12: false });
}

function setRunStatus(text, cls='idle') {
  el.runStatus.textContent = text;
  el.runStatus.className = `run-badge badge-${cls}`;
}

function setDbStatus(ok) {
  el.dbStatus.textContent = ok ? 'PostgreSQL đã kết nối' : 'Mất kết nối DB';
  const dot = el.dbChip.querySelector('.chip-dot');
  if (dot) dot.className = 'chip-dot' + (ok ? '' : ' error');
}

// ── SQL Terminal ───────────────────────────────────────────
function classifyLine(line) {
  const t = line.trim();
  if (!t) return 'info';
  if (/^--/.test(t))                     return 'sql-comment';
  if (/^(BEGIN|COMMIT|ROLLBACK)/i.test(t)) return 'sql-txn';
  if (/^(TRIGGER|FIRED|🔔)/i.test(t))   return 'sql-trigger';
  if (/^CALL\s/i.test(t))               return 'sql-function';
  if (/^(SELECT|FROM|WHERE|JOIN|INSERT|UPDATE|DELETE|WITH|VALUES|RETURNING)/i.test(t)) return 'sql-keyword';
  if (/uuid|::/.test(t))                return 'sql-string';
  return 'info';
}

function termWrite(text, cls='info') {
  const line = document.createElement('span');
  line.className = `terminal-line ${cls} new-line`;
  line.textContent = text;
  el.terminal.appendChild(line);
  el.terminal.scrollTop = el.terminal.scrollHeight;
  line.addEventListener('animationend', () => line.classList.remove('new-line'), { once: true });
}

function termSeparator(char = '─', color = 'separator') {
  termWrite(char.repeat(72), color);
}

function termClear() { el.terminal.innerHTML = ''; }

// ── Pipeline rendering ──────────────────────────────────────
function buildPipeline() {
  el.pipelineTrack.innerHTML = '';
  PIPELINE_STEPS.forEach((step, i) => {
    const node = document.createElement('div');
    node.className = 'step-node';

    const card = document.createElement('div');
    card.className = 'step-card';
    card.id = `step-${step.key}`;
    card.innerHTML = `
      <div class="step-label">Bước ${i + 1}</div>
      <div class="step-name">${esc(step.name)}</div>
      <div class="step-status-dot pending">○ Chờ</div>
      <div class="step-duration"></div>
    `;
    node.appendChild(card);

    if (i < PIPELINE_STEPS.length - 1) {
      const arrow = document.createElement('div');
      arrow.className = 'step-arrow';
      arrow.id = `arrow-${step.key}`;
      node.appendChild(arrow);
    }
    el.pipelineTrack.appendChild(node);
  });
}

function resetPipeline() {
  el.pipelineTrack.querySelectorAll('.step-card').forEach(c => {
    c.className = 'step-card';
    c.querySelector('.step-status-dot').className = 'step-status-dot pending';
    c.querySelector('.step-status-dot').textContent = '○ Chờ';
    c.querySelector('.step-duration').textContent = '';
    // reset step name
    const stepDef = PIPELINE_STEPS.find(s => `step-${s.key}` === c.id);
    if (stepDef) c.querySelector('.step-name').textContent = stepDef.name;
  });
  el.pipelineTrack.querySelectorAll('.step-arrow').forEach(a => a.className = 'step-arrow');
}

function updateStep(stepKey, status, durationMs, stepName) {
  const card = $(`step-${stepKey}`);
  if (!card) return;
  card.classList.remove('running', 'success', 'error');
  card.classList.add(status);
  const dot = card.querySelector('.step-status-dot');
  dot.className = `step-status-dot ${status}`;
  const labels = { running: '◉ Running...', success: '✓ Done', error: '✕ Error', pending: '○ Chờ' };
  dot.textContent = labels[status] || status;
  if (durationMs != null) card.querySelector('.step-duration').textContent = `${durationMs} ms`;
  if (stepName) card.querySelector('.step-name').textContent = stepName;

  const arrow = $(`arrow-${stepKey}`);
  if (arrow && status !== 'pending') arrow.className = 'step-arrow active';
}

// ── Sidebar metrics ────────────────────────────────────────
function renderSidebarMetrics(tables, views) {
  const all = { ...(tables || {}), ...(views || {}) };
  el.metricsSidebar.innerHTML = Object.entries(all)
    .map(([k, v]) => `
      <div class="metric-chip">
        <span class="metric-chip-name">${esc(k.replace('vw_','').replace(/_/g,' '))}</span>
        <span class="metric-chip-val">${Array.isArray(v) ? v.length : '—'}</span>
      </div>`)
    .join('');
}

// ── Render inline result table in terminal ─────────────────
function termRenderResult(details, stepKey) {
  if (!details || Object.keys(details).length === 0) return;

  // Render before→after diffs nếu có
  const diffKeys = ['streak_before', 'streak_after', 'notification_before', 'notification_after',
                    'user_before', 'user_after_soft_delete', 'course_before', 'course_after_soft_delete',
                    'enrollment', 'updated_enrollment', 'inserted_comment', 'latest_notification'];

  // Hiển thị mỗi key đáng chú ý
  for (const [k, v] of Object.entries(details)) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'object' && !Array.isArray(v)) {
      // Object: hiển thị dạng bảng nhỏ
      termWrite(`  → ${k}:`, 'sql-string');
      for (const [field, val] of Object.entries(v)) {
        const highlight = (k.includes('after') || k.includes('after')) ? 'sql-ok' : 'sql-string';
        termWrite(`     ${String(field).padEnd(22)} = ${val}`, hint(k, field) ? 'sql-ok' : 'sql-string');
      }
    } else if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') {
      const cls = k.includes('after') || k === 'visible_in_fn_search_students' || k === 'visible_in_fn_search_courses_advanced' ? 'sql-ok' : 'sql-string';
      termWrite(`  → ${k}: ${v}`, cls);
    }
  }
}

function hint(objKey, field) {
  return objKey.includes('after') || field === 'is_deleted' || field === 'updated_at';
}

// ── Data table rendering ───────────────────────────────────
function renderDataset(container, dataObj, relevantTables) {
  container.innerHTML = '';
  const entries = Object.entries(dataObj || {});
  if (!entries.length) {
    container.innerHTML = '<div class="empty-note">Không có dữ liệu</div>';
    return;
  }
  // Lọc theo kịch bản nếu có danh sách liên quan
  const filtered = relevantTables && relevantTables.length > 0
    ? entries.filter(([name]) => relevantTables.some(r => name.includes(r.replace(' (is_deleted filter)', ''))))
    : entries;

  const toRender = filtered.length > 0 ? filtered : entries.slice(0, 2);

  for (const [name, rows] of toRender) {
    const block = document.createElement('div');
    block.className = 'dataset-block';
    const titleEl = document.createElement('div');
    titleEl.className = 'dataset-title';
    titleEl.innerHTML = `${esc(name)} <span class="dataset-count">${Array.isArray(rows) ? rows.length : 0} rows</span>`;
    block.appendChild(titleEl);

    if (!Array.isArray(rows) || !rows.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-note';
      empty.textContent = 'Không có bản ghi.';
      block.appendChild(empty);
    } else {
      const cols = Object.keys(rows[0]);
      const wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      wrap.innerHTML = `<table>
        <thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td title="${esc(r[c]??'')}">${esc(r[c]==null?'NULL':r[c])}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>`;
      block.appendChild(wrap);
    }
    container.appendChild(block);
  }
}

// ── Select/Dropdown builders ───────────────────────────────
function buildSelect(selectEl, rows, valueFn, labelFn, blank) {
  if (!selectEl) return;
  selectEl.innerHTML = '';
  if (blank) {
    const o = document.createElement('option');
    o.value = ''; o.textContent = '— Tất cả —';
    selectEl.appendChild(o);
  }
  for (const r of rows) {
    const o = document.createElement('option');
    o.value = valueFn(r);
    o.textContent = labelFn(r);
    selectEl.appendChild(o);
  }
}

// ── Payload collection ─────────────────────────────────────
function collectPayload(action) {
  switch (action) {
    case 'view_reports':    return {};
    case 'search_students': return { keyword: el.ssKeyword.value.trim() };
    case 'search_courses':  return { keyword: el.scKeyword.value.trim(), category_id: el.scCategory.value, status: el.scStatus.value };
    case 'enroll':          return { student_id: el.enrollStudent.value, course_id: el.enrollCourse.value };
    case 'update_progress': return { student_id: el.upStudent.value, course_id: el.upCourse.value, progress: el.upProgress.value };
    case 'progress_comment':return { student_id: el.pcStudent.value, course_id: el.pcCourse.value, lesson_id: el.pcLesson.value, progress: el.pcProgress.value, comment_text: el.pcComment.value };
    case 'soft_delete_user':   return { user_id: el.sduUser.value };
    case 'soft_delete_course': return { course_id: el.sdcCourse.value };
    default: return {};
  }
}

// ── SSE: Connect & Stream ─────────────────────────────────
function closeSSE() {
  if (currentEventSrc) { currentEventSrc.close(); currentEventSrc = null; }
}

function connectSSE(runId, scenario) {
  closeSSE();
  const relevant = SCENARIO_RELEVANT_TABLES[scenario] || [];

  const src = new EventSource(`/api/runs/${runId}/events`);
  currentEventSrc = src;

  src.addEventListener('run_started', e => {
    const d = JSON.parse(e.data);
    termSeparator();
    termWrite(`[${ts()}]  🚀 RUN STARTED  action=${d.data.action}`, 'sql-ok');
    termSeparator();
  });

  src.addEventListener('step_started', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'running', null, step.step_name);
    termWrite(``, 'info');
    termWrite(`[${ts()}] ▶ ${step.step_name}`, 'sql-string');
  });

  // ★ NEW: SQL log event — from real backend sql_lines
  src.addEventListener('sql_log', e => {
    const d = JSON.parse(e.data);
    const line = d.data.line;
    if (line === '') {
      termWrite('', 'info');
      return;
    }
    const cls = classifyLine(line);
    termWrite('   ' + line, cls);
  });

  // ★ NEW: Step result event — show real returned data
  src.addEventListener('step_result', e => {
    const d = JSON.parse(e.data);
    const details = d.data.details;
    if (details && Object.keys(details).length > 0) {
      termRenderResult(details, d.data.step_key);
    }
  });

  src.addEventListener('step_finished', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'success', step.duration_ms, step.step_name);
    termWrite(`   ✓ Done in ${step.duration_ms} ms`, 'sql-ok');
  });

  src.addEventListener('step_failed', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'error', step.duration_ms, step.step_name);
    termWrite(`   ✕ ERROR: ${esc(step.error)}  (${step.duration_ms} ms)`, 'sql-error');
  });

  src.addEventListener('run_finished', async e => {
    const d = JSON.parse(e.data);
    const ok = d.data.ok;
    termWrite('', 'info');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    termWrite(`[${ts()}]  ${ok ? '✅ COMMIT — Hoàn thành thành công' : '❌ ROLLBACK — Thất bại'}  |  ${esc(d.data.message)}`, ok ? 'sql-ok' : 'sql-error');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    closeSSE();
    setRunStatus(ok ? 'Success' : 'Failed', ok ? 'success' : 'failed');
    await loadResult(runId, relevant);
  });

  src.onerror = () => {
    termWrite(`[${ts()}] SSE disconnected.`, 'sql-error');
  };
}

// ── Load final result ──────────────────────────────────────
async function loadResult(runId, relevant) {
  try {
    const res = await fetch(`/api/runs/${runId}/result`);
    const data = await res.json();
    const relTables = relevant && relevant.length > 0 ? relevant : null;
    if (data.tables_before) renderDataset(el.tablesBefore, data.tables_before, relTables);
    if (data.tables_after)  renderDataset(el.tablesAfter,  data.tables_after,  relTables);
    if (data.views_before)  renderDataset(el.viewsBefore,  data.views_before,  null);
    if (data.views_after)   renderDataset(el.viewsAfter,   data.views_after,   null);
    renderSidebarMetrics(data.tables_after || data.tables_before, data.views_after || data.views_before);

    // Nếu có action_data (kết quả search), hiển thị trong terminal
    const ad = data.action_data || {};
    if (ad.search_students_rows && ad.search_students_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Kết quả fn_search_students: ${ad.search_students_rows.length} hàng ──`, 'sql-string');
      termRenderInlineTable(ad.search_students_rows.slice(0, 8));
    }
    if (ad.search_courses_rows && ad.search_courses_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Kết quả fn_search_courses_advanced: ${ad.search_courses_rows.length} hàng ──`, 'sql-string');
      termRenderInlineTable(ad.search_courses_rows.slice(0, 8));
    }
    if (ad.report_views) {
      termWrite('', 'info');
      termWrite('── Kết quả 4 Reporting Views ──', 'sql-string');
      for (const [viewName, rows] of Object.entries(ad.report_views)) {
        termWrite(`  ${viewName}: ${(rows || []).length} rows`, 'sql-comment');
      }
    }
  } catch (err) {
    termWrite(`Lỗi tải kết quả: ${err.message}`, 'sql-error');
  }
}

function termRenderInlineTable(rows) {
  if (!rows || !rows.length) return;
  const cols = Object.keys(rows[0]);
  // Header
  termWrite('   ' + cols.map(c => String(c).padEnd(18)).join(' | '), 'sql-comment');
  termWrite('   ' + cols.map(() => '─'.repeat(18)).join('-+-'), 'separator');
  rows.forEach(row => {
    const line = cols.map(c => String(row[c] ?? 'NULL').slice(0, 18).padEnd(18)).join(' | ');
    termWrite('   ' + line, 'sql-string');
  });
}

// ── Run scenario ───────────────────────────────────────────
async function runScenario(action, scenario) {
  try {
    setRunStatus('Starting...', 'running');
    resetPipeline();
    termClear();
    termWrite(`[${ts()}] Chuẩn bị: ${scenario}`, 'sql-comment');

    document.querySelectorAll('.btn-run').forEach(b => b.disabled = true);

    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, payload: collectPayload(action) })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);

    currentRunId = data.run_id;
    el.runId.textContent = currentRunId.slice(0, 8) + '...';
    setRunStatus('Running', 'running');
    connectSSE(currentRunId, scenario);

  } catch (err) {
    setRunStatus('Error', 'failed');
    termWrite(`[${ts()}] ✕ ${err.message}`, 'sql-error');
  } finally {
    document.querySelectorAll('.btn-run').forEach(b => b.disabled = false);
  }
}

// ── Reset DB ───────────────────────────────────────────────
async function resetDb() {
  try {
    setRunStatus('Resetting...', 'running');
    termClear();
    termWrite(`[${ts()}]  ↺  Reset DB — khôi phục dữ liệu gốc...`, 'sql-txn');
    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reset', payload: {} })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
    currentRunId = data.run_id;
    el.runId.textContent = currentRunId.slice(0, 8) + '...';
    connectSSE(currentRunId, 'reset');
  } catch (err) {
    termWrite(`Reset lỗi: ${err.message}`, 'sql-error');
    setRunStatus('Error', 'failed');
  }
}

// ── Sidebar navigation ──────────────────────────────────────
function activateScenario(key) {
  activeScenario = key;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.scenario === key));
  document.querySelectorAll('.scenario-panel').forEach(p => p.classList.toggle('active', p.id === `panel-${key}`));
  // Scroll main về đầu
  const mc = document.querySelector('.main-content');
  if (mc) mc.scrollTop = 0;
}

// ── Init ───────────────────────────────────────────────────
async function init() {
  buildPipeline();
  try {
    const res = await fetch('/api/init');
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Init failed');
    setDbStatus(true);

    const lk = data.lookups || {};
    buildSelect(el.enrollStudent, lk.students||[], r=>r.user_id, r=>`${r.full_name} (${r.username})`);
    buildSelect(el.upStudent,     lk.students||[], r=>r.user_id, r=>`${r.full_name} (${r.username})`);
    buildSelect(el.pcStudent,     lk.students||[], r=>r.user_id, r=>`${r.full_name} (${r.username})`);
    buildSelect(el.enrollCourse,  lk.courses||[],  r=>r.course_id, r=>`${r.title} [${r.visibility_status}]`);
    buildSelect(el.upCourse,      lk.courses||[],  r=>r.course_id, r=>`${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcCourse,      lk.courses||[],  r=>r.course_id, r=>`${r.title} [${r.visibility_status}]`);
    buildSelect(el.sdcCourse,     lk.courses||[],  r=>r.course_id, r=>`${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcLesson,      lk.lessons||[],  r=>r.lesson_id, r=>`${r.course_title} → ${r.lesson_title}`);
    buildSelect(el.sduUser,       lk.users||[],    r=>r.user_id,   r=>`${r.full_name} [${r.role_name}]${r.is_deleted?' ✕deleted':''}`);
    buildSelect(el.scCategory,    lk.course_categories||[], r=>r.category_id, r=>r.name, true);

    renderDataset(el.tablesBefore, data.tables||{}, null);
    renderDataset(el.tablesAfter,  data.tables||{}, null);
    renderDataset(el.viewsBefore,  data.views||{},  null);
    renderDataset(el.viewsAfter,   data.views||{},  null);
    renderSidebarMetrics(data.tables, data.views);

    setRunStatus('Idle', 'idle');
    termWrite(`[${ts()}] ✓ Kết nối PostgreSQL thành công.`, 'sql-ok');
    termWrite(`[${ts()}] Chọn kịch bản trong sidebar → Nhấn ▶ Chạy để xem SQL thực thi theo thời gian thực.`, 'sql-comment');

  } catch (err) {
    setDbStatus(false);
    setRunStatus('Error', 'failed');
    termWrite(`[${ts()}] ✕ Không thể kết nối DB: ${err.message}`, 'sql-error');
  }
}

// ── Event bindings ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  init();

  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => activateScenario(btn.dataset.scenario));
  });

  document.addEventListener('click', e => {
    const btn = e.target.closest('.btn-run');
    if (!btn || btn.disabled) return;
    const action   = btn.dataset.action;
    const scenario = btn.dataset.scenario;
    if (action && scenario) runScenario(action, scenario);
  });

  $('btn-reset-global').addEventListener('click', resetDb);

  $('btn-clear-terminal').addEventListener('click', () => {
    termClear();
    termWrite(`[${ts()}] Terminal cleared.`, 'info');
  });
});
