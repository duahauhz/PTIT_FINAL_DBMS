'use strict';

// Danh sach buoc pipeline hien tren giao dien.
const PIPELINE_STEPS = [
  { key: 'validate_input',             name: 'Validate Input' },
  { key: 'execute_procedure',          name: 'Execute SQL / CALL' },
  { key: 'check_trigger_side_effects', name: 'Trigger Side-Effects' },
  { key: 'refresh_source_tables',      name: 'Read Tables' },
  { key: 'refresh_reporting_views',    name: 'Read Views' },
  { key: 'complete',                   name: 'Complete' },
];

// Bang lien quan theo tung kich ban.
const SCENARIO_RELEVANT_TABLES = {
  enroll: ['course_enrollments', 'student_streaks'],
  update_progress: ['course_enrollments', 'notification_users'],
  progress_comment: ['course_enrollments', 'comments', 'notification_users'],
  soft_delete_user: ['users'],
  soft_delete_course: ['general_courses'],
  view_reports: [],
  search_students: [],
  search_courses: [],
  reset: [],
};

// Khoa dinh danh de doi chieu before/after.
const ROW_KEY_FIELDS = {
  course_enrollments: ['enrollment_id'],
  student_streaks: ['student_id'],
  notification_users: ['notification_id'],
  comments: ['comment_id'],
  users: ['user_id'],
  general_courses: ['course_id'],
  vw_enrollments_by_day: ['enroll_day'],
  vw_top_courses: ['course_id'],
  vw_top_active_students: ['student_id'],
  vw_user_course_progress: ['student_id', 'course_id'],
};

let currentRunId = null;
let currentEventSrc = null;
let activeScenario = 'view_reports';

const $ = id => document.getElementById(id);
const el = {
  terminal: $('sql-terminal'),
  pipelineTrack: $('pipeline-track'),
  runId: $('run-id-display'),
  runStatus: $('run-status-display'),
  dbStatus: $('db-status-text'),
  dbChip: $('db-status-chip'),
  metricsSidebar: $('sidebar-metrics'),
  tablesDelta: $('tables-delta'),
  viewsDelta: $('views-delta'),
  ssKeyword: $('ss-keyword'),
  scKeyword: $('sc-keyword'),
  scCategory: $('sc-category'),
  scStatus: $('sc-status'),
  enrollStudent: $('enroll-student'),
  enrollCourse: $('enroll-course'),
  upStudent: $('up-student'),
  upCourse: $('up-course'),
  upProgress: $('up-progress'),
  pcStudent: $('pc-student'),
  pcCourse: $('pc-course'),
  pcLesson: $('pc-lesson'),
  pcProgress: $('pc-progress'),
  pcComment: $('pc-comment'),
  sduUser: $('sdu-user'),
  sdcCourse: $('sdc-course'),
};

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function normalizeCell(v) {
  if (v === null || v === undefined) return 'NULL';
  return String(v);
}

function valueEquals(a, b) {
  return normalizeCell(a) === normalizeCell(b);
}

function ts() {
  return new Date().toLocaleTimeString('vi-VN', { hour12: false });
}

function setRunStatus(text, cls = 'idle') {
  el.runStatus.textContent = text;
  el.runStatus.className = `run-badge badge-${cls}`;
}

function setDbStatus(ok) {
  el.dbStatus.textContent = ok ? 'PostgreSQL da ket noi' : 'Mat ket noi DB';
  const dot = el.dbChip.querySelector('.chip-dot');
  if (dot) dot.className = `chip-dot${ok ? '' : ' error'}`;
}

function classifyLine(line) {
  const t = line.trim();
  if (!t) return 'info';
  if (/^--/.test(t)) return 'sql-comment';
  if (/^(BEGIN|COMMIT|ROLLBACK)/i.test(t)) return 'sql-txn';
  if (/^(TRIGGER|FIRED|🔔)/i.test(t)) return 'sql-trigger';
  if (/^CALL\s/i.test(t)) return 'sql-function';
  if (/^(SELECT|FROM|WHERE|JOIN|INSERT|UPDATE|DELETE|WITH|VALUES|RETURNING)/i.test(t)) return 'sql-keyword';
  if (/uuid|::/.test(t)) return 'sql-string';
  return 'info';
}

function termWrite(text, cls = 'info') {
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

function termClear() {
  el.terminal.innerHTML = '';
}

function buildPipeline() {
  el.pipelineTrack.innerHTML = '';
  PIPELINE_STEPS.forEach((step, idx) => {
    const node = document.createElement('div');
    node.className = 'step-node';

    const card = document.createElement('div');
    card.className = 'step-card';
    card.id = `step-${step.key}`;
    card.innerHTML = `
      <div class="step-label">Buoc ${idx + 1}</div>
      <div class="step-name">${esc(step.name)}</div>
      <div class="step-status-dot pending">○ Cho</div>
      <div class="step-duration"></div>
    `;
    node.appendChild(card);

    if (idx < PIPELINE_STEPS.length - 1) {
      const arrow = document.createElement('div');
      arrow.className = 'step-arrow';
      arrow.id = `arrow-${step.key}`;
      node.appendChild(arrow);
    }

    el.pipelineTrack.appendChild(node);
  });
}

function resetPipeline() {
  el.pipelineTrack.querySelectorAll('.step-card').forEach(card => {
    card.className = 'step-card';
    const dot = card.querySelector('.step-status-dot');
    const dur = card.querySelector('.step-duration');
    const stepName = card.querySelector('.step-name');
    dot.className = 'step-status-dot pending';
    dot.textContent = '○ Cho';
    dur.textContent = '';
    const stepDef = PIPELINE_STEPS.find(s => `step-${s.key}` === card.id);
    if (stepDef) stepName.textContent = stepDef.name;
  });

  el.pipelineTrack.querySelectorAll('.step-arrow').forEach(a => {
    a.className = 'step-arrow';
  });
}

function updateStep(stepKey, status, durationMs, stepName) {
  const card = $(`step-${stepKey}`);
  if (!card) return;

  card.classList.remove('running', 'success', 'error');
  card.classList.add(status);

  const dot = card.querySelector('.step-status-dot');
  dot.className = `step-status-dot ${status}`;
  const labels = {
    running: '◉ Running...',
    success: '✓ Done',
    error: '✕ Error',
    pending: '○ Cho',
  };
  dot.textContent = labels[status] || status;

  if (durationMs != null) {
    card.querySelector('.step-duration').textContent = `${durationMs} ms`;
  }
  if (stepName) {
    card.querySelector('.step-name').textContent = stepName;
  }

  const arrow = $(`arrow-${stepKey}`);
  if (arrow && status !== 'pending') arrow.className = 'step-arrow active';
}

function renderSidebarMetrics(tables, views) {
  const all = { ...(tables || {}), ...(views || {}) };
  el.metricsSidebar.innerHTML = Object.entries(all)
    .map(([k, v]) => `
      <div class="metric-chip">
        <span class="metric-chip-name">${esc(k.replace('vw_', '').replace(/_/g, ' '))}</span>
        <span class="metric-chip-val">${Array.isArray(v) ? v.length : '—'}</span>
      </div>
    `)
    .join('');
}

function hint(objKey, field) {
  return objKey.includes('after') || field === 'is_deleted' || field === 'updated_at';
}

function termRenderResult(details) {
  if (!details || Object.keys(details).length === 0) return;
  for (const [k, v] of Object.entries(details)) {
    if (v === null || v === undefined) continue;
    if (typeof v === 'object' && !Array.isArray(v)) {
      termWrite(`  -> ${k}:`, 'sql-string');
      for (const [field, val] of Object.entries(v)) {
        termWrite(`     ${String(field).padEnd(22)} = ${val}`, hint(k, field) ? 'sql-ok' : 'sql-string');
      }
      continue;
    }
    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') {
      const cls = k.includes('after') || k === 'visible_in_fn_search_students' || k === 'visible_in_fn_search_courses_advanced'
        ? 'sql-ok'
        : 'sql-string';
      termWrite(`  -> ${k}: ${v}`, cls);
    }
  }
}

function pickKeyFields(name, beforeRows, afterRows) {
  const preset = ROW_KEY_FIELDS[name];
  if (preset && preset.length > 0) return preset;
  const sample = (afterRows && afterRows[0]) || (beforeRows && beforeRows[0]) || {};
  const cols = Object.keys(sample);
  const idCols = cols.filter(c => c.endsWith('_id'));
  if (idCols.length > 0) return [idCols[0]];
  if (cols.length > 0) return [cols[0]];
  return [];
}

function buildRowKey(row, keyFields, idx) {
  if (!row) return `__idx_${idx}`;
  if (keyFields.length === 0) return `__idx_${idx}`;
  const key = keyFields.map(k => normalizeCell(row[k])).join('||');
  return key || `__idx_${idx}`;
}

function diffRows(tableName, beforeRows, afterRows) {
  const rowsBefore = Array.isArray(beforeRows) ? beforeRows : [];
  const rowsAfter = Array.isArray(afterRows) ? afterRows : [];
  const keyFields = pickKeyFields(tableName, rowsBefore, rowsAfter);
  const allCols = Array.from(new Set([
    ...rowsBefore.flatMap(r => Object.keys(r || {})),
    ...rowsAfter.flatMap(r => Object.keys(r || {})),
  ]));

  const mapBefore = new Map();
  rowsBefore.forEach((row, idx) => {
    mapBefore.set(buildRowKey(row, keyFields, idx), row);
  });

  const mapAfter = new Map();
  rowsAfter.forEach((row, idx) => {
    mapAfter.set(buildRowKey(row, keyFields, idx), row);
  });

  const items = [];
  const stats = { added: 0, updated: 0, removed: 0, unchanged: 0 };

  rowsAfter.forEach((row, idx) => {
    const key = buildRowKey(row, keyFields, idx);
    const oldRow = mapBefore.get(key);

    if (!oldRow) {
      stats.added += 1;
      items.push({ status: 'added', row, changedFields: new Set(allCols) });
      return;
    }

    const changed = new Set();
    allCols.forEach(col => {
      if (!valueEquals(oldRow[col], row[col])) changed.add(col);
    });

    if (changed.size > 0) {
      stats.updated += 1;
      items.push({ status: 'updated', row, changedFields: changed });
    } else {
      stats.unchanged += 1;
      items.push({ status: 'unchanged', row, changedFields: new Set() });
    }
  });

  rowsBefore.forEach((row, idx) => {
    const key = buildRowKey(row, keyFields, idx);
    if (mapAfter.has(key)) return;
    stats.removed += 1;
    items.push({ status: 'removed', row, changedFields: new Set(allCols) });
  });

  return {
    keyFields,
    allCols,
    items,
    stats,
    hasChanges: stats.added > 0 || stats.updated > 0 || stats.removed > 0,
  };
}

function renderStatusBadges(stats) {
  return `
    <span class="delta-badge delta-added">+ ${stats.added}</span>
    <span class="delta-badge delta-updated">~ ${stats.updated}</span>
    <span class="delta-badge delta-removed">- ${stats.removed}</span>
    <span class="delta-badge delta-unchanged">= ${stats.unchanged}</span>
  `;
}

function renderSnapshotDataset(container, dataObj, relevantTables) {
  if (!container) return;
  container.innerHTML = '';

  const entries = Object.entries(dataObj || {});
  if (entries.length === 0) {
    container.innerHTML = '<div class="empty-note">Khong co du lieu.</div>';
    return;
  }

  const filtered = relevantTables && relevantTables.length > 0
    ? entries.filter(([name]) => relevantTables.includes(name))
    : entries;

  const toRender = filtered.length > 0 ? filtered : entries;

  for (const [name, rows] of toRender) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const block = document.createElement('div');
    block.className = 'dataset-block';
    block.innerHTML = `
      <div class="dataset-title">
        <span>${esc(name)}</span>
        <span class="dataset-count">${safeRows.length} rows</span>
      </div>
    `;

    if (safeRows.length === 0) {
      block.innerHTML += '<div class="empty-note">Khong co ban ghi.</div>';
      container.appendChild(block);
      continue;
    }

    const cols = Object.keys(safeRows[0]);
    const wrap = document.createElement('div');
    wrap.className = 'table-scroll';
    wrap.innerHTML = `
      <table>
        <thead>
          <tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr>
        </thead>
        <tbody>
          ${safeRows.map(r => `<tr>${cols.map(c => `<td title="${esc(normalizeCell(r[c]))}">${esc(normalizeCell(r[c]))}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    `;

    block.appendChild(wrap);
    container.appendChild(block);
  }
}

function renderDeltaDataset(container, beforeObj, afterObj, relevantTables, options = {}) {
  if (!container) return;
  container.innerHTML = '';

  const beforeData = beforeObj || {};
  const afterData = afterObj || {};
  const names = Array.from(new Set([...Object.keys(beforeData), ...Object.keys(afterData)]));
  const filteredNames = relevantTables && relevantTables.length > 0
    ? names.filter(name => relevantTables.includes(name))
    : names;

  const changedBlocks = [];
  for (const tableName of filteredNames) {
    const beforeRows = beforeData[tableName] || [];
    const afterRows = afterData[tableName] || [];
    const diff = diffRows(tableName, beforeRows, afterRows);
    if (diff.hasChanges) changedBlocks.push({ tableName, afterRows, diff });
  }

  if (changedBlocks.length === 0) {
    if (options.showSnapshotWhenNoChange) {
      renderSnapshotDataset(container, afterData, relevantTables);
      container.insertAdjacentHTML(
        'afterbegin',
        '<div class="delta-empty">Khong co thay doi. Dang hien thi snapshot hien tai.</div>'
      );
      return;
    }

    container.innerHTML = '<div class="delta-empty">Khong co thay doi du lieu sau lan chay nay.</div>';
    return;
  }

  for (const { tableName, afterRows, diff } of changedBlocks) {
    const block = document.createElement('div');
    block.className = 'dataset-block dataset-block-changed';

    const rowTotal = Array.isArray(afterRows) ? afterRows.length : 0;
    const titleEl = document.createElement('div');
    titleEl.className = 'dataset-title';
    titleEl.innerHTML = `
      <span>${esc(tableName)}</span>
      <span class="dataset-count">${rowTotal} rows</span>
      <span class="delta-badges">${renderStatusBadges(diff.stats)}</span>
    `;
    block.appendChild(titleEl);

    const changedRows = diff.items.filter(item => item.status !== 'unchanged');
    if (changedRows.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-note';
      empty.textContent = 'Khong co ban ghi thay doi.';
      block.appendChild(empty);
      container.appendChild(block);
      continue;
    }

    const cols = diff.allCols;
    const wrap = document.createElement('div');
    wrap.className = 'table-scroll';
    wrap.innerHTML = `
      <table>
        <thead>
          <tr>
            <th class="change-col">_change</th>
            ${cols.map(c => `<th>${esc(c)}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${changedRows.map(item => {
            const statusLabel = item.status === 'added' ? '+ added' : item.status === 'removed' ? '- removed' : '~ updated';
            const rowClass = `row-${item.status}`;
            const cells = cols.map(col => {
              const cls = item.changedFields.has(col) ? 'cell-updated' : '';
              return `<td class="${cls}" title="${esc(normalizeCell(item.row[col]))}">${esc(normalizeCell(item.row[col]))}</td>`;
            }).join('');
            return `<tr class="${rowClass}"><td class="change-col"><span class="change-tag ${item.status}">${statusLabel}</span></td>${cells}</tr>`;
          }).join('')}
        </tbody>
      </table>
    `;

    block.appendChild(wrap);
    container.appendChild(block);
  }
}

function buildSelect(selectEl, rows, valueFn, labelFn, blank) {
  if (!selectEl) return;
  selectEl.innerHTML = '';
  if (blank) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '— Tat ca —';
    selectEl.appendChild(o);
  }

  for (const row of rows) {
    const o = document.createElement('option');
    o.value = valueFn(row);
    o.textContent = labelFn(row);
    selectEl.appendChild(o);
  }
}

function collectPayload(action) {
  switch (action) {
    case 'view_reports':
      return {};
    case 'search_students':
      return { keyword: el.ssKeyword.value.trim() };
    case 'search_courses':
      return {
        keyword: el.scKeyword.value.trim(),
        category_id: el.scCategory.value,
        status: el.scStatus.value,
      };
    case 'enroll':
      return { student_id: el.enrollStudent.value, course_id: el.enrollCourse.value };
    case 'update_progress':
      return { student_id: el.upStudent.value, course_id: el.upCourse.value, progress: el.upProgress.value };
    case 'progress_comment':
      return {
        student_id: el.pcStudent.value,
        course_id: el.pcCourse.value,
        lesson_id: el.pcLesson.value,
        progress: el.pcProgress.value,
        comment_text: el.pcComment.value,
      };
    case 'soft_delete_user':
      return { user_id: el.sduUser.value };
    case 'soft_delete_course':
      return { course_id: el.sdcCourse.value };
    default:
      return {};
  }
}

function closeSSE() {
  if (!currentEventSrc) return;
  currentEventSrc.close();
  currentEventSrc = null;
}

function connectSSE(runId, scenario) {
  closeSSE();
  const relevantTables = SCENARIO_RELEVANT_TABLES[scenario] || [];

  const src = new EventSource(`/api/runs/${runId}/events`);
  currentEventSrc = src;

  src.addEventListener('run_started', e => {
    const d = JSON.parse(e.data);
    termSeparator();
    termWrite(`[${ts()}] RUN STARTED action=${d.data.action}`, 'sql-ok');
    termSeparator();
  });

  src.addEventListener('step_started', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'running', null, step.step_name);
    termWrite('', 'info');
    termWrite(`[${ts()}] > ${step.step_name}`, 'sql-string');
  });

  src.addEventListener('sql_log', e => {
    const d = JSON.parse(e.data);
    const line = d.data.line;
    if (line === '') {
      termWrite('', 'info');
      return;
    }
    termWrite(`   ${line}`, classifyLine(line));
  });

  src.addEventListener('step_result', e => {
    const d = JSON.parse(e.data);
    const details = d.data.details;
    if (details && Object.keys(details).length > 0) {
      termRenderResult(details);
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
    termWrite(`   ✕ ERROR: ${step.error} (${step.duration_ms} ms)`, 'sql-error');
  });

  src.addEventListener('run_finished', async e => {
    const d = JSON.parse(e.data);
    const ok = d.data.ok;
    termWrite('', 'info');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    termWrite(`[${ts()}] ${ok ? 'COMMIT' : 'ROLLBACK'} | ${d.data.message}`, ok ? 'sql-ok' : 'sql-error');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    closeSSE();
    setRunStatus(ok ? 'Success' : 'Failed', ok ? 'success' : 'failed');
    await loadResult(runId, relevantTables);
  });

  src.onerror = () => {
    termWrite(`[${ts()}] SSE disconnected.`, 'sql-error');
  };
}

function termRenderInlineTable(rows) {
  if (!rows || rows.length === 0) return;
  const cols = Object.keys(rows[0]);
  termWrite(`   ${cols.map(c => String(c).padEnd(18)).join(' | ')}`, 'sql-comment');
  termWrite(`   ${cols.map(() => '─'.repeat(18)).join('-+-')}`, 'separator');
  rows.forEach(row => {
    const line = cols.map(c => normalizeCell(row[c]).slice(0, 18).padEnd(18)).join(' | ');
    termWrite(`   ${line}`, 'sql-string');
  });
}

async function loadResult(runId, relevantTables) {
  try {
    const res = await fetch(`/api/runs/${runId}/result`);
    const data = await res.json();

    const rel = relevantTables && relevantTables.length > 0 ? relevantTables : null;
    renderDeltaDataset(el.tablesDelta, data.tables_before || {}, data.tables_after || {}, rel, { showSnapshotWhenNoChange: false });
    renderDeltaDataset(el.viewsDelta, data.views_before || {}, data.views_after || {}, null, { showSnapshotWhenNoChange: true });

    renderSidebarMetrics(data.tables_after || data.tables_before, data.views_after || data.views_before);

    const ad = data.action_data || {};
    if (ad.search_students_rows && ad.search_students_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Ket qua fn_search_students: ${ad.search_students_rows.length} hang ──`, 'sql-string');
      termRenderInlineTable(ad.search_students_rows.slice(0, 8));
    }

    if (ad.search_courses_rows && ad.search_courses_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Ket qua fn_search_courses_advanced: ${ad.search_courses_rows.length} hang ──`, 'sql-string');
      termRenderInlineTable(ad.search_courses_rows.slice(0, 8));
    }

    if (ad.report_views) {
      termWrite('', 'info');
      termWrite('── Ket qua 4 reporting views ──', 'sql-string');
      Object.entries(ad.report_views).forEach(([viewName, rows]) => {
        termWrite(`  ${viewName}: ${(rows || []).length} rows`, 'sql-comment');
      });
    }
  } catch (err) {
    termWrite(`Loi tai ket qua: ${err.message}`, 'sql-error');
  }
}

async function runScenario(action, scenario) {
  try {
    setRunStatus('Starting...', 'running');
    resetPipeline();
    termClear();
    termWrite(`[${ts()}] Chuan bi: ${scenario}`, 'sql-comment');

    document.querySelectorAll('.btn-run').forEach(btn => {
      btn.disabled = true;
    });

    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, payload: collectPayload(action) }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);

    currentRunId = data.run_id;
    el.runId.textContent = `${currentRunId.slice(0, 8)}...`;
    setRunStatus('Running', 'running');
    connectSSE(currentRunId, scenario);
  } catch (err) {
    setRunStatus('Error', 'failed');
    termWrite(`[${ts()}] ✕ ${err.message}`, 'sql-error');
  } finally {
    document.querySelectorAll('.btn-run').forEach(btn => {
      btn.disabled = false;
    });
  }
}

async function resetDb() {
  try {
    setRunStatus('Resetting...', 'running');
    termClear();
    termWrite(`[${ts()}] Reset DB - khoi phuc baseline...`, 'sql-txn');

    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reset', payload: {} }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);

    currentRunId = data.run_id;
    el.runId.textContent = `${currentRunId.slice(0, 8)}...`;
    connectSSE(currentRunId, 'reset');
  } catch (err) {
    setRunStatus('Error', 'failed');
    termWrite(`Reset loi: ${err.message}`, 'sql-error');
  }
}

function activateScenario(key) {
  activeScenario = key;
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.scenario === key);
  });
  document.querySelectorAll('.scenario-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `panel-${key}`);
  });
  const main = document.querySelector('.main-content');
  if (main) main.scrollTop = 0;
}

async function init() {
  buildPipeline();

  try {
    const res = await fetch('/api/init');
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Init failed');

    setDbStatus(true);

    const lk = data.lookups || {};
    buildSelect(el.enrollStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.upStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.pcStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.enrollCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.upCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.sdcCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcLesson, lk.lessons || [], r => r.lesson_id, r => `${r.course_title} -> ${r.lesson_title}`);
    buildSelect(el.sduUser, lk.users || [], r => r.user_id, r => `${r.full_name} [${r.role_name}]${r.is_deleted ? ' xoa_mem' : ''}`);
    buildSelect(el.scCategory, lk.course_categories || [], r => r.category_id, r => r.name, true);

    renderSnapshotDataset(el.tablesDelta, data.tables || {}, null);
    renderSnapshotDataset(el.viewsDelta, data.views || {}, null);
    renderSidebarMetrics(data.tables, data.views);

    setRunStatus('Idle', 'idle');
    termWrite(`[${ts()}] Ket noi PostgreSQL thanh cong.`, 'sql-ok');
    termWrite(`[${ts()}] Chon kich ban ben trai va bam Chay.`, 'sql-comment');
  } catch (err) {
    setDbStatus(false);
    setRunStatus('Error', 'failed');
    termWrite(`[${ts()}] Khong the ket noi DB: ${err.message}`, 'sql-error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  init();

  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => activateScenario(btn.dataset.scenario));
  });

  document.addEventListener('click', e => {
    const btn = e.target.closest('.btn-run');
    if (!btn || btn.disabled) return;
    const action = btn.dataset.action;
    const scenario = btn.dataset.scenario;
    if (action && scenario) runScenario(action, scenario);
  });

  const resetBtn = $('btn-reset-global');
  if (resetBtn) resetBtn.addEventListener('click', resetDb);

  const clearBtn = $('btn-clear-terminal');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      termClear();
      termWrite(`[${ts()}] Terminal cleared.`, 'info');
    });
  }
});
