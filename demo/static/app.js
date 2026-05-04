'use strict';

// Danh sach buoc pipeline hien tren giao dien.
const PIPELINE_STEPS = [
  { key: 'validate_input',             name: 'Kiểm tra đầu vào' },
  { key: 'execute_procedure',          name: 'Chạy SQL / CALL' },
  { key: 'check_trigger_side_effects', name: 'Kiểm tra trigger' },
  { key: 'refresh_source_tables',      name: 'Đọc bảng nguồn' },
  { key: 'refresh_reporting_views',    name: 'Đọc view báo cáo' },
  { key: 'complete',                   name: 'Hoàn tất' },
];

// Bang lien quan theo tung kich ban.
const SCENARIO_RELEVANT_TABLES = {
  enroll: ['course_enrollments', 'student_streaks'],
  update_progress: ['course_enrollments', 'notification_users'],
  progress_comment: ['course_enrollments', 'comments', 'notification_users'],
  transfer_to_admin: ['wallets', 'transaction_logs', 'transaction_action_logs'],
  soft_delete_user: ['users'],
  soft_delete_course: ['general_courses'],
  trigger_updated_at: ['users'],
  trigger_init_streak: ['users', 'students', 'student_streaks'],
  trigger_publish_guard: ['general_courses', 'general_course_modules'],
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
  students: ['user_id'],
  general_course_modules: ['module_id'],
  wallets: ['user_id'],
  transaction_logs: ['transaction_id'],
  transaction_action_logs: ['action_log_id'],
  vw_student_progress_report: ['email', 'course_title', 'enrolled_at'],
  vw_course_analytics: ['course_id'],
  vw_top_learners_leaderboard: ['full_name', 'avatar_url'],
};

let currentRunId = null;
let currentEventSrc = null;
let activeScenario = 'view_reports';
let demoSuggestions = {};

const SCENARIO_LABELS = {
  view_reports: 'View báo cáo',
  trigger_updated_at: 'Trigger cập nhật thời gian',
  trigger_init_streak: 'Trigger tạo streak',
  trigger_publish_guard: 'Trigger chặn publish',
  search_students: 'Tìm học viên',
  search_courses: 'Tìm khóa học',
  enroll: 'Đăng ký học viên',
  update_progress: 'Cập nhật tiến độ',
  progress_comment: 'Transaction tiến độ + bình luận',
  transfer_to_admin: 'Chuyển tiền về ví ADMIN',
  soft_delete_user: 'Xóa mềm user',
  soft_delete_course: 'Xóa mềm khóa học',
  reset: 'Khôi phục dữ liệu demo',
};

const $ = id => document.getElementById(id);
const el = {
  terminal: $('sql-terminal'),
  pipelineBar: $('global-pipeline-bar'),
  pipelineTrack: $('pipeline-track'),
  viewPipelineTrack: $('view-pipeline-track'),
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
  tuUser: $('tu-user'),
  tuUsername: $('tu-username'),
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
  txFromUser: $('tx-from-user'),
  txAdminUser: $('tx-admin-user'),
  txAmount: $('tx-amount'),
  sduUser: $('sdu-user'),
  sdcCourse: $('sdc-course'),
};

const VIEW_DETAIL_RUNS = {
  studentProgress: {
    endpoint: '/api/demo/views/student-progress',
    targetId: 'detail-student-progress',
    label: 'Student Progress Report',
    sqlLines: [
      'SELECT course_title, progress, learning_status',
      'FROM vw_student_progress_report',
      "WHERE email = 'minh.student@signlearn.local'",
      'ORDER BY progress DESC, course_title ASC;',
    ],
  },
  courseAnalytics: {
    endpoint: '/api/demo/views/course-analytics',
    targetId: 'detail-course-analytics',
    label: 'Course Analytics',
    sqlLines: [
      'SELECT course_title, teacher_name, total_students, avg_progress, avg_rating',
      'FROM vw_course_analytics',
      'ORDER BY total_students DESC, avg_progress DESC, course_title ASC;',
    ],
  },
  topLearners: {
    endpoint: '/api/demo/views/top-learners',
    targetId: 'detail-top-learners',
    label: 'Top Learners Leaderboard',
    sqlLines: [
      'SELECT full_name, current_streak, highest_streak, total_achievements',
      'FROM vw_top_learners_leaderboard',
      'ORDER BY current_streak DESC, total_achievements DESC, full_name ASC',
      'LIMIT 5;',
    ],
  },
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

function pause(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
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
  el.dbStatus.textContent = ok ? 'Đã kết nối PostgreSQL' : 'Database đang offline';
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

function buildPipeline(track = el.pipelineTrack, idPrefix = '') {
  if (!track) return;
  track.innerHTML = '';
  PIPELINE_STEPS.forEach((step, idx) => {
    const node = document.createElement('div');
    node.className = 'step-node';

    const card = document.createElement('div');
    card.className = 'step-card';
    card.id = `${idPrefix}step-${step.key}`;
    card.dataset.stepKey = step.key;
    card.innerHTML = `
      <span class="step-status-dot pending">○</span>
      <span class="step-name">${esc(step.name)}</span>
      <span class="step-label">Bước ${idx + 1}</span>
      <span class="step-duration"></span>
    `;
    node.appendChild(card);

    if (idx < PIPELINE_STEPS.length - 1) {
      const arrow = document.createElement('div');
      arrow.className = 'step-arrow';
      arrow.id = `${idPrefix}arrow-${step.key}`;
      node.appendChild(arrow);
    }

    track.appendChild(node);
  });
}

function buildScenarioPipeline(container, scenario) {
  const track = typeof container === 'string' ? $(container) : container;
  const prefix = `${scenario}-`;
  buildPipeline(track, prefix);
  return { track, prefix };
}

function scenarioPipeline(scenario) {
  if (scenario === 'view_reports') {
    return { track: el.viewPipelineTrack, prefix: 'view-' };
  }
  const track = $(`pipeline-track-${scenario}`);
  return { track: track || el.pipelineTrack, prefix: track ? `${scenario}-` : '' };
}

function resetPipeline(track = el.pipelineTrack, idPrefix = '') {
  if (!track) return;
  track.querySelectorAll('.step-card').forEach(card => {
    card.className = 'step-card';
    const dot = card.querySelector('.step-status-dot');
    const dur = card.querySelector('.step-duration');
    const stepName = card.querySelector('.step-name');
    dot.className = 'step-status-dot pending';
    dot.textContent = '○';
    dur.textContent = '';
    const key = card.dataset.stepKey || card.id.replace(`${idPrefix}step-`, '');
    const stepDef = PIPELINE_STEPS.find(s => s.key === key);
    if (stepDef) stepName.textContent = stepDef.name;
  });

  track.querySelectorAll('.step-arrow').forEach(a => {
    a.className = 'step-arrow';
  });
}

function translatePipelineName(stepKey, rawName) {
  const name = String(rawName || '').toLowerCase();
  if (name.includes('trigger')) return 'Kiểm tra trigger';
  if (name.includes('refresh source') || name.includes('source table') || name.includes('bảng nguồn')) return 'Đọc bảng nguồn';
  if (name.includes('refresh reporting') || name.includes('view') || name.includes('rowset')) return 'Đọc view báo cáo';
  if (name.includes('complete') || name.includes('finalize') || name.includes('hoàn tất')) return 'Hoàn tất';
  if (name.includes('execute') || name.includes('call') || name.includes('select') || name.includes('transaction') || name.includes('update')) return 'Chạy SQL / CALL';
  if (name.includes('validate') || name.includes('prepare') || name.includes('kiểm tra') || name.includes('chuẩn bị')) return 'Kiểm tra đầu vào';
  return PIPELINE_STEPS.find(step => step.key === stepKey)?.name || rawName || '';
}

function updateStep(stepKey, status, durationMs, stepName, track = el.pipelineTrack, idPrefix = '') {
  const card = document.getElementById(`${idPrefix}step-${stepKey}`);
  if (!card) return;

  card.classList.remove('running', 'success', 'error');
  card.classList.add(status);

  const dot = card.querySelector('.step-status-dot');
  dot.className = `step-status-dot ${status}`;
  const labels = {
    running: '◉',
    success: '✓',
    error: '✕',
    pending: '○',
  };
  dot.textContent = labels[status] || status;

  if (durationMs != null) {
    card.querySelector('.step-duration').textContent = `${durationMs}ms`;
  }
  if (stepName) {
    card.querySelector('.step-name').textContent = translatePipelineName(stepKey, stepName);
  }

  const arrow = document.getElementById(`${idPrefix}arrow-${stepKey}`);
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
    container.innerHTML = '<div class="empty-note">No data available.</div>';
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
        <span class="dataset-count">${safeRows.length} dòng</span>
      </div>
    `;

    if (safeRows.length === 0) {
      block.innerHTML += '<div class="empty-note">Không có dòng dữ liệu.</div>';
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
        '<div class="delta-empty">Không có thay đổi. Đang hiển thị snapshot hiện tại.</div>'
      );
      return;
    }

    container.innerHTML = '<div class="delta-empty">Không có thay đổi dữ liệu sau lần chạy này.</div>';
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
      <span class="dataset-count">${rowTotal} dòng</span>
      <span class="delta-badges">${renderStatusBadges(diff.stats)}</span>
    `;
    block.appendChild(titleEl);

    const changedRows = diff.items.filter(item => item.status !== 'unchanged');
    if (changedRows.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-note';
      empty.textContent = 'Không có dòng thay đổi.';
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
            const statusLabel = item.status === 'added' ? '+ thêm' : item.status === 'removed' ? '- xóa' : '~ cập nhật';
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

function ensureScenarioDemoAreas() {
  document.querySelectorAll('.scenario-panel').forEach(panel => {
    const scenario = panel.id.replace('panel-', '');
    if (scenario === 'view_reports') return;
    if (panel.querySelector('.scenario-demo-shell')) return;

    const shell = document.createElement('div');
    shell.className = 'scenario-demo-shell';
    shell.innerHTML = `
      <div class="inline-pipeline-bar scenario-local-pipeline">
        <div class="pipeline-bar-label">Pipeline demo</div>
        <div class="pipeline-track pipeline-track--horizontal" id="pipeline-track-${esc(scenario)}"></div>
      </div>
      <div class="scenario-result" id="scenario-result-${esc(scenario)}">
        <div class="demo-result-titleline"><span>Kết quả demo</span><strong>${esc(SCENARIO_LABELS[scenario] || scenario)}</strong></div>
        <div class="inline-empty">Chọn dữ liệu và bấm chạy để xem sự kiện nghiệp vụ.</div>
      </div>
    `;

    const oldDelta = panel.querySelector('.scenario-delta');
    if (oldDelta) {
      oldDelta.classList.add('technical-delta');
      oldDelta.before(shell);
    } else {
      panel.querySelector('.panel-header')?.appendChild(shell);
    }
    buildScenarioPipeline(shell.querySelector('.pipeline-track'), scenario);
  });
}

function demoResultNode(scenario) {
  return $(`scenario-result-${scenario}`);
}

function scenarioResultPending(scenario) {
  const node = demoResultNode(scenario);
  return !!node && node.innerText.includes('Đang chạy demo nghiệp vụ');
}

function scheduleResultLoad(runId, relevantTables, scenario) {
  loadResult(runId, relevantTables, scenario);
  [700, 1800, 4200].forEach(delayMs => {
    setTimeout(() => {
      if (scenarioResultPending(scenario)) {
        loadResult(runId, relevantTables, scenario);
      }
    }, delayMs);
  });
}

function compactRow(row, fields) {
  if (!row) return '<div class="inline-empty">Không có dữ liệu.</div>';
  const keys = fields && fields.length ? fields : Object.keys(row);
  return `
    <div class="demo-kv-grid">
      ${keys.map(key => `
        <div class="demo-kv-item">
          <span>${esc(key)}</span>
          <strong>${esc(normalizeCell(row[key]))}</strong>
        </div>
      `).join('')}
    </div>
  `;
}

function renderBeforeAfterCard(title, before, after, highlights = []) {
  return `
    <section class="demo-card-wide">
      <div class="demo-section-title">${esc(title)}</div>
      <div class="demo-before-after">
        <div>
          <h4>Trước</h4>
          ${compactRow(before)}
        </div>
        <div>
          <h4>Sau</h4>
          ${compactRow(after)}
          ${highlights.length ? `<div class="demo-highlight-note">${highlights.map(esc).join(' • ')}</div>` : ''}
        </div>
      </div>
    </section>
  `;
}

function renderEventTimeline(steps) {
  return `
    <div class="demo-event-timeline">
      ${steps.map((step, idx) => `
        <div class="demo-event-step ${step.state || 'done'}">
          <span>${idx + 1}</span>
          <div>
            <strong>${esc(step.title)}</strong>
            <p>${esc(step.detail || '')}</p>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderMiniTable(rows, columns) {
  const safeRows = Array.isArray(rows) ? rows : [];
  if (safeRows.length === 0) return '<div class="inline-empty">Không có dòng nào khớp điều kiện.</div>';
  const cols = columns && columns.length ? columns : Object.keys(safeRows[0] || {});
  return `
    <div class="view-result-table-wrap demo-table-wrap">
      <table class="inline-table view-result-table">
        <thead><tr>${cols.map(col => `<th>${esc(col)}</th>`).join('')}</tr></thead>
        <tbody>
          ${safeRows.map(row => `<tr>${cols.map(col => `<td>${esc(normalizeCell(row[col]))}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function firstChangedRow(beforeRows, afterRows, table, predicate) {
  const rows = afterRows?.[table] || [];
  if (predicate) return rows.find(predicate) || null;
  const diff = diffRows(table, beforeRows?.[table] || [], rows);
  const changed = diff.items.find(item => item.status !== 'unchanged');
  return changed ? changed.row : null;
}

function findRow(rows, predicate) {
  return (rows || []).find(predicate) || null;
}

function findByField(dataset, table, field, value) {
  if (!value) return null;
  return findRow(dataset?.[table], row => normalizeCell(row[field]) === normalizeCell(value));
}

function latestFromTable(dataset, table) {
  const rows = dataset?.[table] || [];
  return rows.length ? rows[0] : null;
}

function payloadValue(result, key, fallback = '') {
  const value = result?.payload?.[key];
  if (value === null || value === undefined || value === '') return fallback;
  return value;
}

function scenarioInputValue(inputEl, fallback = '') {
  if (!inputEl) return fallback;
  return inputEl.value || fallback;
}

function transferAmount(ad) {
  return ad.transfer_result?.tx_amount
    || ad.transfer_result?.amount
    || ad.transaction_log?.amount
    || 'NULL';
}

function renderScenarioResult(scenario, result) {
  const node = demoResultNode(scenario);
  if (!node) return;
  const ad = result.action_data || {};
  const before = result.tables_before || {};
  const after = result.tables_after || {};
  const title = SCENARIO_LABELS[scenario] || 'Kịch bản demo';
  let html = '';

  switch (scenario) {
    case 'trigger_updated_at': {
      const afterUser = ad.user_after_update_timestamp || firstChangedRow(before, after, 'users');
      const userId = afterUser?.user_id || payloadValue(result, 'user_id', scenarioInputValue(el.tuUser));
      const beforeUser = ad.user_before_update_timestamp || findByField(before, 'users', 'user_id', userId);
      html = `
        <div class="demo-result-header"><span class="view-result-status">TRIGGER</span><strong>${esc(title)}</strong><span>Frontend chỉ gửi username, không gửi updated_at</span></div>
        ${renderBeforeAfterCard('User đang chọn', beforeUser, afterUser, ['SQL backend không SET updated_at', 'Trigger trong DB tự đổi updated_at'])}
      `;
      break;
    }
    case 'search_students': {
      const rows = ad.search_students_rows || [];
      const keyword = payloadValue(result, 'keyword', scenarioInputValue(el.ssKeyword, 'Tất cả'));
      html = `
        <div class="demo-result-header"><span class="view-result-status">CHỈ ĐỌC</span><strong>Function fn_search_students</strong><span>Từ khóa: ${esc(keyword || 'Tất cả')}</span><span>${rows.length} kết quả</span></div>
        ${rows.length === 0 ? '<div class="inline-empty">Không có học viên khớp keyword này. Chọn chip gợi ý khác rồi chạy lại.</div>' : ''}
        ${renderMiniTable(rows, ['student_id', 'username', 'full_name', 'grade_level', 'school_name'])}
      `;
      break;
    }
    case 'search_courses': {
      const rows = ad.search_courses_rows || [];
      const keyword = payloadValue(result, 'keyword', scenarioInputValue(el.scKeyword, 'Tất cả'));
      const status = payloadValue(result, 'status', scenarioInputValue(el.scStatus, 'Tất cả'));
      html = `
        <div class="demo-result-header"><span class="view-result-status">CHỈ ĐỌC</span><strong>Function fn_search_courses_advanced</strong><span>Từ khóa: ${esc(keyword || 'Tất cả')}</span><span>Trạng thái: ${esc(status || 'Tất cả')}</span><span>${rows.length} kết quả</span></div>
        ${rows.length === 0 ? '<div class="inline-empty">Không có khóa học khớp bộ lọc này. Chọn chip gợi ý khác rồi chạy lại.</div>' : ''}
        ${renderMiniTable(rows, ['course_id', 'title', 'teacher_name', 'category_name', 'visibility_status', 'total_students', 'avg_progress'])}
      `;
      break;
    }
    case 'trigger_init_streak': {
      html = `
        ${renderEventTimeline([
          { title: 'Trước INSERT', detail: `User/student/streak demo được làm sạch. Streak tồn tại trước: ${normalizeCell(ad.streak_before_insert_student)}` },
          { title: 'INSERT INTO students', detail: 'Sự kiện AFTER INSERT trên bảng students xảy ra.' },
          { title: 'Trigger tạo student_streaks', detail: `current_streak = ${normalizeCell(ad.streak_after_insert_student?.current_streak)}` },
        ])}
        ${renderBeforeAfterCard('Dòng student_streaks do trigger tạo', null, ad.streak_after_insert_student, ['Không insert trực tiếp vào student_streaks từ frontend'])}
      `;
      break;
    }
    case 'trigger_publish_guard': {
      html = `
        ${renderEventTimeline([
          { title: 'Course DRAFT, chưa có module', detail: 'Chuẩn bị khóa học demo với module_count = 0.' },
          { title: 'Thử publish khóa rỗng', detail: ad.blocked_publish_error ? 'Trigger chặn và rollback UPDATE.' : 'Không thấy lỗi chặn publish.', state: ad.blocked_publish_error ? 'error' : 'done' },
          { title: 'Thêm module', detail: `module_count = ${normalizeCell(ad.module_count)}` },
          { title: 'Publish lại', detail: `Trạng thái sau cùng: ${normalizeCell(ad.course_after_publish?.visibility_status)}` },
        ])}
        ${renderBeforeAfterCard('Trạng thái khóa học', ad.course_after_block_attempt, ad.course_after_publish, ['Rule: không publish khóa học rỗng'])}
      `;
      break;
    }
    case 'enroll': {
      const enrollment = ad.enrollment || firstChangedRow(before, after, 'course_enrollments');
      const studentId = enrollment?.student_id || payloadValue(result, 'student_id', scenarioInputValue(el.enrollStudent));
      const streakBefore = ad.streak_before || findByField(before, 'student_streaks', 'student_id', studentId);
      const streakAfter = ad.streak_after || firstChangedRow(before, after, 'student_streaks', row => row.student_id === studentId) || findByField(after, 'student_streaks', 'student_id', studentId);
      html = `
        ${renderEventTimeline([
          { title: 'Chọn học viên và khóa học', detail: 'Kiểm tra cả hai bản ghi tồn tại trong DB.' },
          { title: 'CALL sp_enroll_student', detail: 'Procedure tạo enrollment nếu chưa có.' },
          { title: 'Trigger cập nhật streak', detail: 'student_streaks phản ánh hoạt động học viên.' },
        ])}
        ${renderBeforeAfterCard('Enrollment', null, enrollment, [enrollment ? 'Có enrollment trong course_enrollments' : 'Procedure không tạo bản ghi trùng nếu đã enroll'])}
        ${renderBeforeAfterCard('Streak học viên', streakBefore, streakAfter)}
      `;
      break;
    }
    case 'update_progress': {
      const enrollment = ad.updated_enrollment || firstChangedRow(before, after, 'course_enrollments');
      const notification = ad.latest_notification || firstChangedRow(before, after, 'notification_users');
      html = `
        <section class="demo-card-wide">
          <div class="demo-section-title">Tiến độ sau khi cập nhật</div>
          <div class="progress-visual"><span style="width:${Math.min(100, Number(enrollment?.progress || 0))}%"></span></div>
          ${compactRow(enrollment)}
        </section>
        ${notification && Number(enrollment?.progress || 0) >= 100 ? renderBeforeAfterCard('Notification do trigger tạo', null, notification, ['Progress đạt 100%']) : '<div class="inline-empty">Chưa tạo notification hoàn thành vì progress chưa đạt 100%.</div>'}
      `;
      break;
    }
    case 'progress_comment': {
      const comment = ad.inserted_comment || firstChangedRow(before, after, 'comments');
      const notification = ad.latest_notification || firstChangedRow(before, after, 'notification_users');
      html = `
        ${renderEventTimeline([
          { title: 'BEGIN', detail: 'Bắt đầu transaction nguyên tử.' },
          { title: 'CALL sp_update_course_progress', detail: 'Cập nhật tiến độ học.' },
          { title: 'INSERT comments', detail: 'Ghi bình luận trong cùng transaction.' },
          { title: 'Trigger notification', detail: notification ? 'Có notification mới/hiện tại khi đạt điều kiện hoàn thành.' : 'Không tạo notification nếu chưa đủ điều kiện.' },
          { title: 'COMMIT', detail: 'Hoàn tất nếu mọi bước thành công.' },
        ])}
        ${renderBeforeAfterCard('Bình luận mới', null, comment)}
        ${renderBeforeAfterCard('Notification liên quan', null, notification)}
      `;
      break;
    }
    case 'transfer_to_admin': {
      html = `
        ${renderEventTimeline([
          { title: 'Khóa ví nguồn và ví ADMIN', detail: 'Function dùng logic giao dịch để kiểm soát số dư.' },
          { title: 'Trừ ví nguồn', detail: `Giảm ${normalizeCell(transferAmount(ad))}` },
          { title: 'Cộng ví ADMIN', detail: `Trạng thái: ${normalizeCell(ad.transfer_result?.tx_status || ad.transaction_log?.status)}` },
          { title: 'Ghi log', detail: `Action logs: ${(ad.tx_action_logs || []).length}` },
        ])}
        ${renderBeforeAfterCard('Ví nguồn', ad.from_wallet_before, ad.from_wallet_after)}
        ${renderBeforeAfterCard('Ví ADMIN', ad.admin_wallet_before, ad.admin_wallet_after)}
        ${renderMiniTable(ad.tx_action_logs || [], ['action_type', 'message', 'created_at'])}
      `;
      break;
    }
    case 'soft_delete_user': {
      const afterUser = ad.user_after_soft_delete || firstChangedRow(before, after, 'users');
      const beforeUser = ad.user_before_soft_delete || findByField(before, 'users', 'user_id', afterUser?.user_id);
      html = `
        ${renderBeforeAfterCard('User sau khi xóa mềm', beforeUser, afterUser, ['is_deleted chuyển sang TRUE', 'updated_at do trigger đổi'])}
        <div class="demo-result-header"><span class="view-result-status">ẨN KHỎI SEARCH</span><span>fn_search_students không còn trả user đã xóa mềm.</span></div>
      `;
      break;
    }
    case 'soft_delete_course': {
      const afterCourse = ad.course_after_soft_delete || firstChangedRow(before, after, 'general_courses');
      const beforeCourse = ad.course_before_soft_delete || findByField(before, 'general_courses', 'course_id', afterCourse?.course_id);
      html = `
        ${renderBeforeAfterCard('Khóa học sau khi xóa mềm', beforeCourse, afterCourse, ['is_deleted chuyển sang TRUE', 'course bị ẩn logic'])}
        <div class="demo-result-header"><span class="view-result-status">ẨN KHỎI SEARCH</span><span>fn_search_courses_advanced không còn trả khóa học đã xóa mềm.</span></div>
      `;
      break;
    }
    default:
      html = '<div class="inline-empty">Kịch bản đã chạy xong. Xem thêm nhật ký SQL và bảng thay đổi kỹ thuật bên dưới.</div>';
  }

  node.innerHTML = `
    <div class="demo-result-titleline"><span>Kết quả demo</span><strong>${esc(title)}</strong></div>
    ${html}
  `;
}

function buildSelect(selectEl, rows, valueFn, labelFn, blank) {
  if (!selectEl) return;
  selectEl.innerHTML = '';
  if (blank) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '— Tất cả —';
    selectEl.appendChild(o);
  }

  for (const row of rows) {
    const o = document.createElement('option');
    o.value = valueFn(row);
    o.textContent = labelFn(row);
    selectEl.appendChild(o);
  }
}

function selectValueIfPresent(selectEl, value) {
  if (!selectEl || !value) return;
  const wanted = String(value);
  for (const option of selectEl.options) {
    if (option.value === wanted) {
      selectEl.value = wanted;
      return;
    }
  }
}

function attachSuggestionChips(inputEl, values) {
  if (!inputEl || !Array.isArray(values) || values.length === 0) return;
  let wrap = inputEl.parentElement.querySelector(`[data-suggestion-for="${inputEl.id}"]`);
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'suggestion-chips';
    wrap.dataset.suggestionFor = inputEl.id;
    inputEl.insertAdjacentElement('afterend', wrap);
  }

  const uniqueValues = [...new Set(values.map(v => String(v || '').trim()).filter(Boolean))].slice(0, 8);
  wrap.innerHTML = uniqueValues
    .map(value => `<button class="suggestion-chip" type="button" data-value="${esc(value)}">${esc(value)}</button>`)
    .join('');
  wrap.querySelectorAll('.suggestion-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      inputEl.value = btn.dataset.value || '';
      inputEl.focus();
    });
  });
}

function buildStatusSelectFromSuggestions(selectEl, statuses) {
  if (!selectEl || !Array.isArray(statuses) || statuses.length === 0) return;
  const current = selectEl.value;
  selectEl.innerHTML = '<option value="">Tất cả trạng thái</option>';
  [...new Set(statuses.filter(Boolean))].forEach(status => {
    const option = document.createElement('option');
    option.value = status;
    option.textContent = status;
    selectEl.appendChild(option);
  });
  selectValueIfPresent(selectEl, current);
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
    case 'trigger_updated_at':
      return {
        user_id: el.tuUser.value,
        // Frontend chi gui du lieu can sua, tuyet doi khong gui updated_at.
        new_username: el.tuUsername.value.trim(),
      };
    case 'trigger_init_streak':
      return {};
    case 'trigger_publish_guard':
      return {};
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
    case 'transfer_to_admin':
      return {
        from_user_id: el.txFromUser.value || null,
        amount: el.txAmount.value,
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
  const pipe = scenarioPipeline(scenario);

  const src = new EventSource(`/api/runs/${runId}/events`);
  currentEventSrc = src;

  src.addEventListener('run_started', e => {
    const d = JSON.parse(e.data);
    termSeparator();
    termWrite(`[${ts()}] BẮT ĐẦU DEMO action=${d.data.action}`, 'sql-ok');
    termSeparator();
  });

  src.addEventListener('step_started', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'running', null, step.step_name, pipe.track, pipe.prefix);
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
    updateStep(step.step_key, 'success', step.duration_ms, step.step_name, pipe.track, pipe.prefix);
    termWrite(`   ✓ Xong trong ${step.duration_ms} ms`, 'sql-ok');
  });

  src.addEventListener('step_failed', e => {
    const d = JSON.parse(e.data);
    const step = d.data;
    updateStep(step.step_key, 'error', step.duration_ms, step.step_name, pipe.track, pipe.prefix);
    termWrite(`   ✕ LỖI: ${step.error} (${step.duration_ms} ms)`, 'sql-error');
  });

  src.addEventListener('run_finished', async e => {
    const d = JSON.parse(e.data);
    const ok = d.data.ok;
    termWrite('', 'info');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    termWrite(`[${ts()}] ${ok ? 'COMMIT' : 'ROLLBACK'} | ${d.data.message}`, ok ? 'sql-ok' : 'sql-error');
    termSeparator(ok ? '═' : '✕', ok ? 'sql-ok' : 'sql-error');
    closeSSE();
    setRunStatus(ok ? 'Thành công' : 'Thất bại', ok ? 'success' : 'failed');
    scheduleResultLoad(runId, relevantTables, scenario);
    // Remove global delta open
  });

  src.onerror = () => {
    termWrite(`[${ts()}] Mất kết nối SSE.`, 'sql-error');
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

async function detailRequest(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
  return data;
}

function detailLoading(id) {
  const node = $(id);
  if (node) node.innerHTML = '<div class="inline-empty">Đang tải...</div>';
}

function detailError(id, message) {
  const node = $(id);
  if (node) node.innerHTML = `<div class="inline-empty inline-error">${esc(message)}</div>`;
}

function detailRenderTable(id, columns, rows) {
  const node = $(id);
  if (!node) return;
  if (!rows || rows.length === 0) {
    node.innerHTML = '<div class="inline-empty">Không có dòng dữ liệu nào.</div>';
    return;
  }
  node.innerHTML = `
    <table class="inline-table">
      <thead><tr>${columns.map(col => `<th>${esc(col)}</th>`).join('')}</tr></thead>
      <tbody>
        ${rows.map(row => `
          <tr>${columns.map(col => `<td>${esc(normalizeCell(row[col]))}</td>`).join('')}</tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function detailRenderViewResult(id, payload, label) {
  const node = $(id);
  if (!node) return;

  const columns = payload.columns || [];
  const rows = payload.rows || [];
  const rowLabel = 'dòng';
  const tableMarkup = rows.length === 0
    ? '<div class="inline-empty">Không có dòng dữ liệu nào.</div>'
    : `
      <table class="inline-table view-result-table">
        <thead><tr>${columns.map(col => `<th>${esc(col)}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map(row => `
            <tr>${columns.map(col => `<td>${esc(normalizeCell(row[col]))}</td>`).join('')}</tr>
          `).join('')}
        </tbody>
      </table>
    `;

  node.innerHTML = `
    <div class="view-result-shell">
      <div class="view-result-topline">
        <span class="view-result-status">ĐỌC VIEW OK</span>
        <span class="view-result-count">${rows.length} ${rowLabel}</span>
        <span class="view-result-count">${columns.length} cột</span>
      </div>
      <div class="view-result-title">${esc(label)}</div>
      <div class="view-result-object">${esc(payload.view || '')}</div>
      <div class="view-result-table-wrap">${tableMarkup}</div>
    </div>
  `;
}

function setViewButtonsDisabled(disabled) {
  document.querySelectorAll('.btn-view-run').forEach(btn => {
    btn.disabled = disabled;
  });
}

async function loadDetailView(meta) {
  closeSSE();
  const viewTrack = el.viewPipelineTrack || el.pipelineTrack;
  const viewPrefix = el.viewPipelineTrack ? 'view-' : '';
  resetPipeline(viewTrack, viewPrefix);
  termClear();
  setRunStatus('Đang chạy view', 'running');
  el.runId.textContent = 'view';
  detailLoading(meta.targetId);
  setViewButtonsDisabled(true);

  let activeStep = 'validate_input';
  const runStartedAt = performance.now();
  try {
    termWrite(`[${ts()}] VIEW RUN: ${meta.label}`, 'sql-ok');
    updateStep('validate_input', 'running', null, 'Chuẩn bị câu SELECT', viewTrack, viewPrefix);
    await pause(100);
    updateStep('validate_input', 'success', 100, 'Chuẩn bị câu SELECT', viewTrack, viewPrefix);

    activeStep = 'execute_procedure';
    updateStep('execute_procedure', 'running', null, 'Chạy SELECT', viewTrack, viewPrefix);
    meta.sqlLines.forEach(line => termWrite(`   ${line}`, classifyLine(line)));
    const selectStartedAt = performance.now();
    const data = await detailRequest(meta.endpoint);
    if (!data.success) throw new Error(data.error || 'Request thất bại');
    updateStep('execute_procedure', 'success', Math.round(performance.now() - selectStartedAt), 'Chạy SELECT', viewTrack, viewPrefix);

    activeStep = 'check_trigger_side_effects';
    updateStep('check_trigger_side_effects', 'running', null, 'Kiểm tra rowset', viewTrack, viewPrefix);
    await pause(90);
    updateStep('check_trigger_side_effects', 'success', 90, 'Kiểm tra rowset', viewTrack, viewPrefix);

    activeStep = 'refresh_source_tables';
    updateStep('refresh_source_tables', 'running', null, 'Ánh xạ cột kết quả', viewTrack, viewPrefix);
    await pause(70);
    updateStep('refresh_source_tables', 'success', 70, 'Ánh xạ cột kết quả', viewTrack, viewPrefix);

    activeStep = 'refresh_reporting_views';
    updateStep('refresh_reporting_views', 'running', null, 'Hiển thị kết quả', viewTrack, viewPrefix);
    detailRenderViewResult(meta.targetId, data.data, meta.label);
    updateStep('refresh_reporting_views', 'success', 30, 'Hiển thị kết quả', viewTrack, viewPrefix);

    activeStep = 'complete';
    updateStep('complete', 'success', Math.round(performance.now() - runStartedAt), 'Hoàn tất', viewTrack, viewPrefix);
    setRunStatus('Thành công', 'success');
    termWrite(`[${ts()}] ${data.data.view}: đã hiển thị ${data.data.rows.length} dòng.`, 'sql-ok');
  } catch (err) {
    updateStep(activeStep, 'error', null, activeStep === 'execute_procedure' ? 'Chạy SELECT' : 'Pipeline view', viewTrack, viewPrefix);
    detailError(meta.targetId, err.message);
    setRunStatus('Thất bại', 'failed');
    termWrite(`[${ts()}] ${err.message}`, 'sql-error');
  } finally {
    setViewButtonsDisabled(false);
  }
}

function initDetailDemos() {
  const emptyTargets = [
    ['detail-student-progress', 'Bấm “Chạy view này” để xem kết quả.'],
    ['detail-course-analytics', 'Bấm “Chạy view này” để xem kết quả.'],
    ['detail-top-learners', 'Bấm “Chạy view này” để xem kết quả.'],
  ];
  emptyTargets.forEach(([id, message]) => {
    const node = $(id);
    if (node) node.innerHTML = `<div class="inline-empty">${esc(message)}</div>`;
  });
  const actions = [
    ['btn-detail-student-progress', () => loadDetailView(VIEW_DETAIL_RUNS.studentProgress)],
    ['btn-detail-course-analytics', () => loadDetailView(VIEW_DETAIL_RUNS.courseAnalytics)],
    ['btn-detail-top-learners', () => loadDetailView(VIEW_DETAIL_RUNS.topLearners)],
  ];
  actions.forEach(([id, handler]) => {
    const node = $(id);
    if (node) node.addEventListener('click', handler);
  });

}

function activateViewPanel(key) {
  const order = ['studentProgress', 'courseAnalytics', 'topLearners'];
  const switcher = document.querySelector('.view-switcher');
  if (switcher) switcher.style.setProperty('--active-index', String(Math.max(0, order.indexOf(key))));
  document.querySelectorAll('.view-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.viewKey === key);
  });
  document.querySelectorAll('.view-detail-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.viewPanel === key);
  });
  resetPipeline(el.viewPipelineTrack, 'view-');
}

function initViewSwitcher() {
  document.querySelectorAll('.view-tab').forEach(btn => {
    btn.addEventListener('click', () => activateViewPanel(btn.dataset.viewKey));
  });
  activateViewPanel('studentProgress');
}

async function loadResult(runId, relevantTables, scenario) {
  try {
    const scenarioNode = demoResultNode(scenario);
    if (scenarioNode?.dataset.runId && scenarioNode.dataset.runId !== runId) return;
    const data = await fetchRunResult(runId);
    if (scenarioNode?.dataset.runId && scenarioNode.dataset.runId !== runId) return;
    renderScenarioResult(scenario, data);

    const rel = relevantTables && relevantTables.length > 0 ? relevantTables : null;
    
    let tableContainer = el.tablesDelta;
    let viewContainer = el.viewsDelta;
    
    // Inline rendering
    const shouldRenderTechnicalDelta = scenario && !['search_students', 'search_courses'].includes(scenario);
    if (shouldRenderTechnicalDelta) {
      const inlineContainer = document.getElementById(`delta-${scenario}`);
      if (inlineContainer) {
        inlineContainer.innerHTML = `
          <div class="detail-compare-grid delta-inline-grid">
            <div class="delta-col">
              <h4>Thay đổi bảng nguồn</h4>
              <div class="inline-tables-delta"></div>
            </div>
            <div class="delta-col">
              <h4>Snapshot view báo cáo</h4>
              <div class="inline-views-delta"></div>
            </div>
          </div>
        `;
        tableContainer = inlineContainer.querySelector('.inline-tables-delta');
        viewContainer = inlineContainer.querySelector('.inline-views-delta');
        inlineContainer.style.display = 'block';
      }
    }

    if (tableContainer) renderDeltaDataset(tableContainer, data.tables_before || {}, data.tables_after || {}, rel, { showSnapshotWhenNoChange: false });
    if (viewContainer) renderDeltaDataset(viewContainer, data.views_before || {}, data.views_after || {}, null, { showSnapshotWhenNoChange: true });

    renderSidebarMetrics(data.tables_after || data.tables_before, data.views_after || data.views_before);

    const ad = data.action_data || {};
    if (ad.search_students_rows && ad.search_students_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Kết quả fn_search_students: ${ad.search_students_rows.length} dòng ──`, 'sql-string');
      termRenderInlineTable(ad.search_students_rows.slice(0, 8));
    }

    if (ad.search_courses_rows && ad.search_courses_rows.length > 0) {
      termWrite('', 'info');
      termWrite(`── Kết quả fn_search_courses_advanced: ${ad.search_courses_rows.length} dòng ──`, 'sql-string');
      termRenderInlineTable(ad.search_courses_rows.slice(0, 8));
    }

    if (ad.report_views) {
      termWrite('', 'info');
      termWrite('── Kết quả 3 reporting views ──', 'sql-string');
      Object.entries(ad.report_views).forEach(([viewName, rows]) => {
        termWrite(`  ${viewName}: ${(rows || []).length} dòng`, 'sql-comment');
      });
    }

    if (ad.user_after_update_timestamp) {
      termWrite('', 'info');
      termWrite('── Kết quả: Trigger updated_at ──', 'sql-string');
      termRenderInlineTable([ad.user_after_update_timestamp]);
    }

    if (ad.streak_after_insert_student) {
      termWrite('', 'info');
      termWrite('── Kết quả: Trigger init streak ──', 'sql-string');
      termRenderInlineTable([ad.streak_after_insert_student]);
    }

    if (ad.course_after_publish) {
      termWrite('', 'info');
      termWrite('── Kết quả: Trigger publish guard ──', 'sql-string');
      termRenderInlineTable([ad.course_after_publish]);
      if (ad.blocked_publish_error) {
        termWrite(`  blocked_error: ${String(ad.blocked_publish_error).slice(0, 220)}`, 'sql-comment');
      }
    }

    if (ad.transfer_result) {
      termWrite('', 'info');
      termWrite('── Kết quả chuyển ví ──', 'sql-string');
      termRenderInlineTable([ad.transfer_result]);
      if (ad.transaction_log) {
        termWrite('  transaction_log:', 'sql-comment');
        termRenderInlineTable([ad.transaction_log]);
      }
      if (ad.from_wallet_after && ad.admin_wallet_after) {
        termWrite('  wallets after transfer:', 'sql-comment');
        termRenderInlineTable([ad.from_wallet_after, ad.admin_wallet_after]);
      }
      if (Array.isArray(ad.tx_action_logs) && ad.tx_action_logs.length > 0) {
        termWrite(`  action_logs: ${ad.tx_action_logs.length} dong`, 'sql-comment');
        termRenderInlineTable(ad.tx_action_logs.slice(0, 10));
      }
    }
  } catch (err) {
    termWrite(`Lỗi tải kết quả: ${err.message}`, 'sql-error');
    const node = demoResultNode(scenario);
    if (node) {
      node.innerHTML = `
        <div class="demo-result-titleline"><span>Kết quả demo</span><strong>${esc(SCENARIO_LABELS[scenario] || scenario)}</strong></div>
        <div class="inline-empty inline-error">Không tải được kết quả demo: ${esc(err.message)}</div>
      `;
    }
  }
}

async function fetchRunResult(runId) {
  let lastMessage = '';
  for (let attempt = 0; attempt < 12; attempt += 1) {
    let res;
    let data = {};
    try {
      res = await fetchJsonWithTimeout(`/api/runs/${runId}/result?_=${Date.now()}`, 1800);
      data = res.data;
    } catch (err) {
      lastMessage = err.message;
      await pause(160);
      continue;
    }
    if (res.status === 202 || data.status === 'running' || data.message === 'Run chưa hoàn tất.') {
      lastMessage = data.message || 'Run chưa hoàn tất.';
      await pause(180);
      continue;
    }
    if (!res.ok || data.ok === false) {
      throw new Error(data.message || `HTTP ${res.status}`);
    }
    return data;
  }
  throw new Error(lastMessage || 'Chưa tải được kết quả run.');
}

async function fetchJsonWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { cache: 'no-store', signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  } finally {
    clearTimeout(timer);
  }
}

async function runScenario(action, scenario) {
  try {
    setRunStatus('Đang chuẩn bị...', 'running');
    const pipe = scenarioPipeline(scenario);
    resetPipeline(pipe.track, pipe.prefix);
    const resultNode = demoResultNode(scenario);
    if (resultNode) {
      resultNode.innerHTML = '<div class="inline-empty">Đang chạy demo nghiệp vụ...</div>';
    }
    termClear();
    termWrite(`[${ts()}] Chuẩn bị: ${SCENARIO_LABELS[scenario] || scenario}`, 'sql-comment');

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
    const activeResultNode = demoResultNode(scenario);
    if (activeResultNode) activeResultNode.dataset.runId = currentRunId;
    setRunStatus('Đang chạy', 'running');
    connectSSE(currentRunId, scenario);
  } catch (err) {
    setRunStatus('Lỗi', 'failed');
    termWrite(`[${ts()}] ✕ ${err.message}`, 'sql-error');
  } finally {
    document.querySelectorAll('.btn-run').forEach(btn => {
      btn.disabled = false;
    });
  }
}

async function resetDb() {
  try {
    setRunStatus('Đang khôi phục...', 'running');
    termClear();
    termWrite(`[${ts()}] Reset DB - khôi phục baseline...`, 'sql-txn');

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
    setRunStatus('Lỗi', 'failed');
    termWrite(`Reset lỗi: ${err.message}`, 'sql-error');
  }
}

function activateScenario(key) {
  activeScenario = key;
  if (el.pipelineBar) {
    el.pipelineBar.classList.add('hidden');
  }
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
  buildPipeline(el.viewPipelineTrack, 'view-');
  if (el.pipelineBar) {
    el.pipelineBar.classList.add('hidden');
  }

  try {
    const res = await fetch('/api/init');
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Init failed');

    setDbStatus(true);
    demoSuggestions = data.demo_suggestions || {};

    const lk = data.lookups || {};
    buildSelect(el.tuUser, lk.users || [], r => r.user_id, r => `${r.full_name} [${r.role_name}]${r.is_deleted ? ' đã xóa mềm' : ''}`);
    selectValueIfPresent(el.tuUser, demoSuggestions.default_user?.user_id);
    buildSelect(el.enrollStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.upStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.pcStudent, lk.students || [], r => r.user_id, r => `${r.full_name} (${r.username})`);
    buildSelect(el.enrollCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.upCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.sdcCourse, lk.courses || [], r => r.course_id, r => `${r.title} [${r.visibility_status}]`);
    buildSelect(el.pcLesson, lk.lessons || [], r => r.lesson_id, r => `${r.course_title} -> ${r.lesson_title}`);
    buildSelect(el.sduUser, lk.users || [], r => r.user_id, r => `${r.full_name} [${r.role_name}]${r.is_deleted ? ' đã xóa mềm' : ''}`);
    buildSelect(el.scCategory, lk.course_categories || [], r => r.category_id, r => r.name, true);
    buildStatusSelectFromSuggestions(el.scStatus, demoSuggestions.course_statuses || []);
    buildSelect(
      el.txFromUser,
      lk.wallet_senders || [],
      r => r.user_id,
      r => `${r.full_name} [${r.role_name}] - ${r.status} - balance=${r.balance}`
    );
    buildSelect(
      el.txAdminUser,
      lk.admin_wallets || [],
      r => r.user_id,
      r => `${r.full_name} [ADMIN] - ${r.status} - balance=${r.balance}`
    );
    if (el.txAdminUser) el.txAdminUser.disabled = true;
    selectValueIfPresent(el.sduUser, demoSuggestions.default_soft_delete_user?.user_id);
    selectValueIfPresent(el.sdcCourse, demoSuggestions.default_soft_delete_course?.course_id);
    selectValueIfPresent(el.txFromUser, demoSuggestions.default_wallet_sender?.user_id);
    selectValueIfPresent(el.txAdminUser, demoSuggestions.default_admin_wallet?.user_id);
    const defaultEnrollment = (demoSuggestions.enrollment_pairs || [])[0] || {};
    selectValueIfPresent(el.enrollStudent, demoSuggestions.default_student?.user_id);
    selectValueIfPresent(el.enrollCourse, demoSuggestions.default_course?.course_id);
    selectValueIfPresent(el.upStudent, defaultEnrollment.student_id || demoSuggestions.default_student?.user_id);
    selectValueIfPresent(el.upCourse, defaultEnrollment.course_id || demoSuggestions.default_course?.course_id);
    selectValueIfPresent(el.pcStudent, defaultEnrollment.student_id || demoSuggestions.default_student?.user_id);
    selectValueIfPresent(el.pcCourse, defaultEnrollment.course_id || demoSuggestions.default_course?.course_id);
    attachSuggestionChips(el.ssKeyword, demoSuggestions.student_keywords || []);
    attachSuggestionChips(el.scKeyword, demoSuggestions.course_keywords || []);
    if (el.ssKeyword && (demoSuggestions.student_keywords || []).length) {
      el.ssKeyword.placeholder = `Ví dụ: ${demoSuggestions.student_keywords[0]}`;
    }
    if (el.scKeyword && (demoSuggestions.course_keywords || []).length) {
      el.scKeyword.placeholder = `Ví dụ: ${demoSuggestions.course_keywords[0]}`;
    }

    // Removed global snapshot rendering
    renderSidebarMetrics(data.tables, data.views);

    setRunStatus('Sẵn sàng', 'idle');
    termWrite(`[${ts()}] PostgreSQL đã sẵn sàng.`, 'sql-ok');
    termWrite(`[${ts()}] Chọn kịch bản và chạy demo.`, 'sql-comment');
  } catch (err) {
    setDbStatus(false);
    setRunStatus('Lỗi', 'failed');
    termWrite(`[${ts()}] Không thể kết nối DB: ${err.message}`, 'sql-error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  ensureScenarioDemoAreas();
  init();
  initDetailDemos();
  initViewSwitcher();

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
      termWrite(`[${ts()}] Đã xóa nhật ký SQL.`, 'info');
    });
  }
});
