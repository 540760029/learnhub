/* ===================== LearnHub 前端 ===================== */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const State = {
  token: localStorage.getItem('lh_token') || '',
  me: null,
  providers: [],
  currentQuiz: null,   // { quiz, answers, submitted, review }
};

/* ------------------------- 请求封装 ------------------------- */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (State.token) headers.Authorization = 'Bearer ' + State.token;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || `请求失败（${res.status}）`);
  return data;
}
const get = (p) => api(p);
const post = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b ?? {}) });
const patch = (p, b) => api(p, { method: 'PATCH', body: JSON.stringify(b ?? {}) });
const del = (p) => api(p, { method: 'DELETE' });

/* ------------------------- 提示 / 弹窗 ------------------------- */
let toastTimer;
function toast(msg, type = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast ' + type; }, 2600);
}
function openModal(html) { $('#modalBox').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); $('#modalBox').innerHTML = ''; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

const fmtTime = (s) => s ? new Date(s.replace(' ', 'T') + (s.includes('Z') ? '' : 'Z'))
  .toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

/* ========================= 认证 ========================= */
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === t));
  $$('.tabpane').forEach(p => p.classList.toggle('active', p.id === t.dataset.tab + 'Form'));
  $('#authMsg').textContent = '';
}));

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const msg = $('#authMsg');
  msg.className = 'msg'; msg.textContent = '登录中…';
  try {
    const r = await post('/api/auth/login', { email: f.get('email'), password: f.get('password') });
    State.token = r.token;
    localStorage.setItem('lh_token', r.token);
    await boot();
  } catch (err) { msg.className = 'msg err'; msg.textContent = err.message; }
});

$('#registerForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const msg = $('#authMsg');
  msg.className = 'msg'; msg.textContent = '注册中…';
  try {
    const r = await post('/api/auth/register', {
      name: f.get('name'), email: f.get('email'), password: f.get('password'),
      role: f.get('role'), school: f.get('school') || null,
      student_no: f.get('student_no') || null,
    });
    State.token = r.token;
    localStorage.setItem('lh_token', r.token);
    await boot();
  } catch (err) { msg.className = 'msg err'; msg.textContent = err.message; }
});

$('#logoutBtn').addEventListener('click', () => {
  State.token = ''; State.me = null;
  localStorage.removeItem('lh_token');
  $('#appView').classList.add('hidden');
  $('#authView').classList.remove('hidden');
  location.hash = '';
});

async function boot() {
  State.me = await get('/api/me');
  try { State.providers = (await get('/api/ai/providers')).providers; } catch { State.providers = []; }
  $('#authView').classList.add('hidden');
  $('#appView').classList.remove('hidden');
  $('#meName').textContent = State.me.name;
  $('#meRole').textContent = (State.me.role === 'teacher' ? '教师' : '学生') +
    (State.me.school ? ' · ' + State.me.school : '');
  $('#meAvatar').textContent = State.me.name.slice(0, 1);
  $$('.student-only').forEach(el => el.classList.toggle('hidden', State.me.role !== 'student'));
  $$('.admin-only').forEach(el => el.classList.toggle('hidden', !State.me.is_admin));
  if (!location.hash) location.hash = '#/dashboard';
  router();
}

/* ========================= 路由 ========================= */
window.addEventListener('hashchange', router);

function router() {
  if (!State.me) return;
  const hash = location.hash || '#/dashboard';
  const parts = hash.replace(/^#\//, '').split('/').filter(Boolean);
  const page = parts[0] || 'dashboard';
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === page));
  const box = $('#content');
  box.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  const routes = {
    dashboard: renderDashboard,
    course: () => renderCourse(Number(parts[1]), parts[2] || 'kp'),
    assignment: () => renderAssignment(Number(parts[1])),
    quiz: () => renderQuiz(Number(parts[1])),
    quizdetail: () => renderQuizOverview(Number(parts[1])),
    attempts: renderAttempts,
    attempt: () => renderAttempt(Number(parts[1])),
    analytics: () => renderAnalytics(Number(parts[1])),
    admin: () => renderAdmin(box, parts[1] || 'stats'),
    settings: renderSettings,
  };
  (routes[page] || renderDashboard)(box).catch(err => {
    box.innerHTML = `<div class="card"><h3>出错了</h3><p class="muted">${esc(err.message)}</p></div>`;
  });
}

/* ========================= 我的课程 ========================= */
async function renderDashboard(box) {
  const data = await get('/api/courses');
  const isTeacher = State.me.role === 'teacher';
  const list = isTeacher ? [...data.teaching, ...data.assisting] : data.joined;

  let html = `<div class="page-head spread">
      <div>
        <h1>${isTeacher ? '我教的课程' : '我加入的课程'}</h1>
        <p>${isTeacher ? '管理知识点、作业与试题，查看学生学习数据。' : '进入课程查看知识点、做作业与模拟练习。'}</p>
      </div>
      <div class="hstack">
        ${isTeacher
          ? `<button class="btn primary" id="newCourse">+ 新建课程</button>`
          : `<button class="btn primary" id="joinCourse">+ 加入课程</button>`}
        ${!isTeacher ? `<button class="btn" id="aiGenQuick">✨ AI 出题</button>` : ''}
      </div>
    </div>`;

  if (!list.length) {
    html += `<div class="card empty"><div class="big">${isTeacher ? '📚' : '🎒'}</div>
      <p>${isTeacher ? '还没有课程，点右上角创建第一门课。' : '还没有加入课程，向老师要一个邀请码吧。'}</p></div>`;
  } else {
    html += '<div class="grid g2">' + list.map(c => `
      <div class="card course-card" data-cid="${c.id}">
        <div class="hstack" style="gap:14px">
          <div class="course-emoji">${esc(c.cover_emoji || '📘')}</div>
          <div style="flex:1;min-width:0">
            <h3>${esc(c.title)}</h3>
            <div class="muted" style="font-size:13.5px">${esc(c.teacher)} 老师</div>
          </div>
          ${c.role !== 'student' && c.join_code
            ? `<span class="pill" title="学生用这个邀请码加入">邀请码 ${esc(c.join_code)}</span>` : ''}
        </div>
        <p class="muted" style="font-size:14px">${esc(c.description || '暂无简介')}</p>
        <div class="kv">
          <span>📖 知识点 ${c.kp_count}</span>
          <span>📝 作业 ${c.assignment_count}</span>
          <span>🧪 试题 ${c.quiz_count}</span>
          ${c.role !== 'student' ? `<span>👥 学生 ${c.student_count}</span>` : ''}
        </div>
      </div>`).join('') + '</div>';
  }
  box.innerHTML = html;

  $$('.course-card', box).forEach(el =>
    el.addEventListener('click', () => location.hash = `#/course/${el.dataset.cid}`));

  const nc = $('#newCourse', box);
  if (nc) nc.addEventListener('click', newCourseModal);
  const jc = $('#joinCourse', box);
  if (jc) jc.addEventListener('click', joinCourseModal);
  const aq = $('#aiGenQuick', box);
  if (aq) aq.addEventListener('click', () => aiGenModal(list));
}

function newCourseModal() {
  openModal(`<h2>新建课程</h2>
    <p class="muted" style="font-size:14px;margin:8px 0 18px">创建后系统会生成邀请码，学生凭码加入。</p>
    <label>课程名称<input id="ncTitle" placeholder="如：多传感器信息融合滤波技术"></label>
    <label>课程简介<textarea id="ncDesc" placeholder="一句话说明这门课讲什么"></textarea></label>
    <label>封面图标<input id="ncEmoji" value="📘" maxlength="4"></label>
    <div class="hstack" style="justify-content:flex-end">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="ncOk">创建</button>
    </div>`);
  $('#ncOk').addEventListener('click', async () => {
    try {
      await post('/api/courses', {
        title: $('#ncTitle').value.trim(), description: $('#ncDesc').value.trim(),
        cover_emoji: $('#ncEmoji').value.trim() || '📘',
      });
      closeModal(); toast('课程已创建', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

function joinCourseModal() {
  openModal(`<h2>加入课程</h2>
    <p class="muted" style="font-size:14px;margin:8px 0 18px">输入老师给你的邀请码。</p>
    <label>邀请码<input id="jcCode" placeholder="如 DEMO01" style="text-transform:uppercase"></label>
    <div class="hstack" style="justify-content:flex-end">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="jcOk">加入</button>
    </div>`);
  $('#jcOk').addEventListener('click', async () => {
    try {
      const r = await post('/api/courses/join', { join_code: $('#jcCode').value.trim() });
      closeModal(); toast(r.message || '已加入', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ========================= 课程详情 ========================= */
async function renderCourse(cid, tab) {
  const [d, quota] = await Promise.all([
    get(`/api/courses/${cid}`),
    get(`/api/courses/${cid}/ai-quota`).catch(() => null),
  ]);
  const c = d.course, canManage = !!d.can_manage, isTeacher = canManage;
  const box = $('#content');

  const tabs = [
    ['kp', '📖 知识点'], ['assignment', '📝 作业'], ['quiz', '🧪 试题'],
    ...(isTeacher ? [['analytics', '📊 学情分析'], ['students', '👥 学生']] : []),
    ['ai', '✨ AI 出题'],
  ];
  if (!tabs.some(t => t[0] === tab)) tab = 'kp';

  box.innerHTML = `
    <div class="crumb"><a href="#/dashboard">我的课程</a> / ${esc(c.title)}</div>
    <div class="page-head spread">
      <div class="hstack" style="gap:14px">
        <div class="course-emoji">${esc(c.cover_emoji || '📘')}</div>
        <div>
          <h1>${esc(c.title)}</h1>
          <p>${esc(c.teacher)} 老师 · ${esc(c.description || '')}</p>
        </div>
      </div>
      <div class="hstack">
        ${isTeacher ? `<span class="pill">邀请码 ${esc(c.join_code)}</span>` : ''}
        ${quota ? `<span class="pill" title="AI 出题额度">${quota.unlimited
            ? `✨ AI 无限次${quota.reason === 'admin' ? '（管理员）' : quota.reason === 'own_key' ? '（自己的 Key）' : ''}`
            : `✨ AI 今日剩余 ${quota.remaining}/${quota.limit} 套`}</span>` : ''}
      </div>
    </div>
    <div class="ctabs">${tabs.map(([k, label]) =>
      `<button data-tab="${k}" class="${k === tab ? 'active' : ''}">${label}</button>`).join('')}</div>
    <div id="tabBody"><div class="loading"><span class="spin"></span></div></div>`;

  $$('.ctabs button', box).forEach(b =>
    b.addEventListener('click', () => location.hash = `#/course/${cid}/${b.dataset.tab}`));

  const body = $('#tabBody', box);
  const render = {
    kp: () => tabKp(body, cid, d, canManage),
    assignment: () => tabAssignments(body, cid, d, canManage),
    quiz: () => tabQuizzes(body, cid, d, canManage),
    students: () => tabStudents(body, cid),
    analytics: () => renderAnalytics(body, cid),
    ai: () => tabAi(body, cid, d, quota),
  }[tab];
  await render();
}

/* ---------- 知识点 ---------- */
async function tabKp(body, cid, d, canManage) {
  const kps = d.knowledge_points;
  const isStudent = State.me.role === 'student';
  const scopeTag = (k) => {
    if (k.scope === 'private') return `<span class="tag" style="color:#c4b5fd;border-color:#4c3b7a;background:#1d1733">仅自己可见</span>`;
    if (k.scope === 'teacher') return `<span class="tag" style="color:#fbbf24;border-color:#6b5518;background:#2a2210">仅教师可见</span>`;
    return `<span class="tag" style="color:#7dd3fc;border-color:#2c4a63;background:#0f2233">全班可见</span>`;
  };
  body.innerHTML = `
    <div class="spread" style="margin-bottom:16px">
      <div class="muted">共 ${kps.length} 个知识点${isStudent ? '（含你自己整理的笔记）' : ''}</div>
      <div class="hstack">
        ${isStudent ? `<button class="btn sm" id="addKp">✏️ 添加我的笔记</button>` : ''}
        ${canManage ? `<button class="btn sm" id="upKp">📄 上传资料自动整理</button>
                      <button class="btn primary sm" id="addKp">+ 发布知识点</button>` : ''}
      </div>
    </div>
    <div class="stack">${kps.length ? kps.map(k => `
      <div class="card kp" style="${k.scope !== 'course' ? 'border-left-color:#a78bfa' : ''}">
        <div class="spread">
          <div class="hstack" style="gap:10px">
            <h4>${esc(k.title)}</h4>${scopeTag(k)}
            ${k.source_file ? `<span class="pill">来自 ${esc(k.source_file)}</span>` : ''}
          </div>
          ${(canManage || k.mine) ? `<span class="hstack">
            <button class="btn sm" data-edit="${k.id}">编辑</button>
            <button class="btn sm danger" data-del="${k.id}">删除</button></span>` : ''}
        </div>
        <div class="body">${esc(k.content || '（暂无内容）')}</div>
      </div>`).join('') : '<div class="card empty"><div class="big">📖</div><p>还没有知识点</p></div>'}
    </div>`;

  const addBtn = $('#addKp', body);
  if (addBtn) addBtn.addEventListener('click', () => kpModal(cid, null, canManage));
  const upBtn = $('#upKp', body);
  if (upBtn) upBtn.addEventListener('click', () => uploadKpModal(cid));
  $$('[data-edit]', body).forEach(b => b.addEventListener('click', () =>
    kpModal(cid, kps.find(k => k.id === Number(b.dataset.edit)), canManage)));
  $$('[data-del]', body).forEach(b => b.addEventListener('click', async () => {
    if (!confirm('确定删除这个知识点？')) return;
    try { await del(`/api/knowledge/${b.dataset.del}`); toast('已删除', 'ok'); router(); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

function kpModal(cid, kp, canManage) {
  const isStudent = State.me.role === 'student';
  openModal(`<h2>${kp ? '编辑' : (isStudent ? '添加我的笔记' : '发布知识点')}</h2>
    ${isStudent ? `<p class="muted" style="font-size:13.5px;margin:8px 0 16px">
      🔒 你添加的知识点<b>只有你自己能看到</b>，老师可以看到以便了解你的学习情况。</p>` : ''}
    <label>标题<input id="kpTitle" value="${esc(kp?.title || '')}" placeholder="如：无迹卡尔曼滤波（UKF）"></label>
    <label>内容（支持换行，AI 出题会读这里的内容）
      <textarea id="kpContent" style="min-height:220px" placeholder="把这一节的核心概念、公式、要点写在这里…">${esc(kp?.content || '')}</textarea>
    </label>
    ${canManage ? `<label>可见范围
      <select id="kpScope">
        <option value="course" ${(kp?.scope || 'course') === 'course' ? 'selected' : ''}>全班可见（学生都能看到）</option>
        <option value="teacher" ${kp?.scope === 'teacher' ? 'selected' : ''}>仅教师可见（学生看不到）</option>
      </select></label>` : ''}
    <div class="hstack" style="justify-content:flex-end">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="kpOk">保存</button>
    </div>`);
  $('#kpOk').addEventListener('click', async () => {
    const payload = { title: $('#kpTitle').value.trim(), content: $('#kpContent').value };
    if (!payload.title) return toast('标题不能为空', 'err');
    if (canManage && $('#kpScope')) payload.scope = $('#kpScope').value;
    try {
      if (kp) await patch(`/api/knowledge/${kp.id}`, { ...payload, order_no: kp.order_no });
      else await post(`/api/courses/${cid}/knowledge`, payload);
      closeModal(); toast('已保存', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ---------- 上传资料 → AI 自动整理知识点 ---------- */
function uploadKpModal(cid) {
  openModal(`<h2>📄 上传资料，AI 自动整理知识点</h2>
    <p class="muted" style="font-size:13.5px;margin:8px 0 16px">
      支持 <b>.docx / .txt / .md / .csv</b>（≤8MB）。AI 会把资料切分成若干知识点，
      <b>你确认（可逐条编辑或删除）后才会入库</b>，并统一选择可见范围。</p>
    <label>选择文件<input type="file" id="upFile" accept=".docx,.txt,.md,.markdown,.csv,.json"></label>
    <label>入库后的可见范围
      <select id="upScope">
        <option value="course">全班可见（学生都能看到）</option>
        <option value="teacher">仅教师可见（学生看不到）</option>
      </select></label>
    <div class="hstack">
      <button class="btn primary" id="upOk">开始解析</button>
      <span id="upMsg" class="muted" style="font-size:13.5px"></span>
    </div>`);
  $('#upOk').addEventListener('click', async () => {
    const f = $('#upFile').files[0];
    const msg = $('#upMsg');
    if (!f) { msg.textContent = '请先选择文件'; return; }
    msg.innerHTML = '<span class="spin"></span> 正在解析并整理…';
    const fd = new FormData();
    fd.append('file', f);
    try {
      const res = await fetch(`/api/courses/${cid}/knowledge/extract`, {
        method: 'POST', headers: { Authorization: 'Bearer ' + State.token }, body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '解析失败');
      previewPoints(cid, data);
    } catch (e) { msg.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
  });
}

function previewPoints(cid, data) {
  const pts = data.points || [];
  openModal(`<h2>确认知识点（${pts.length} 条）</h2>
    <p class="muted" style="font-size:13.5px;margin:8px 0 14px">
      文件 <b>${esc(data.filename)}</b> · 解析 ${data.chars} 字 ·
      整理方式：<b>${data.provider === 'mock' ? '本地启发式切分（未配 API Key）' : 'AI 整理'}</b><br>
      可直接修改标题与内容，或取消勾选不要的条目。${data.notice ? '（' + esc(data.notice) + '）' : ''}</p>
    <label>入库后的可见范围
      <select id="pvScope">
        <option value="course">全班可见（学生都能看到）</option>
        <option value="teacher">仅教师可见（学生看不到）</option>
      </select></label>
    <div style="max-height:44vh;overflow:auto" id="pvList">
      ${pts.map((p, i) => `<div class="card" style="padding:14px;margin-bottom:10px">
        <label class="opt" style="margin:0 0 8px;border:none;padding:0;background:none">
          <input type="checkbox" class="pvPick" data-i="${i}" checked>
          <span style="font-size:13px;color:var(--dim)">收录这条</span></label>
        <input class="pvTitle" data-i="${i}" value="${esc(p.title)}" style="margin-bottom:8px">
        <textarea class="pvContent" data-i="${i}" style="min-height:90px">${esc(p.content || '')}</textarea>
      </div>`).join('')}
    </div>
    <div class="hstack" style="justify-content:flex-end;margin-top:12px">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="pvOk">确认入库</button>
    </div>`);

  $('#pvOk').addEventListener('click', async () => {
    const out = [];
    $$('.pvPick:checked').forEach(cb => {
      const i = cb.dataset.i;
      const title = $(`.pvTitle[data-i="${i}"]`).value.trim();
      if (title) out.push({ title, content: $(`.pvContent[data-i="${i}"]`).value });
    });
    if (!out.length) return toast('至少要保留一条', 'err');
    try {
      const r = await post(`/api/courses/${cid}/knowledge/commit`, {
        source_file: data.filename, scope: $('#pvScope').value, points: out,
      });
      closeModal(); toast(`已入库 ${r.created} 个知识点`, 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ---------- 作业 ---------- */
async function tabAssignments(body, cid, d, isTeacher) {
  const list = d.assignments;
  body.innerHTML = `
    <div class="spread" style="margin-bottom:16px">
      <div class="muted">共 ${list.length} 份作业</div>
      ${isTeacher ? `<button class="btn primary sm" id="addAsg">+ 发布作业</button>` : ''}
    </div>
    <div class="stack">${list.length ? list.map(a => `
      <div class="card spread">
        <div>
          <h3 style="font-size:16.5px">${esc(a.title)}</h3>
          <div class="kv" style="margin-top:6px">
            <span>满分 ${a.full_score}</span>
            <span>截止 ${a.due_at ? fmtTime(a.due_at) : '未设置'}</span>
            ${isTeacher ? `<span>已交 ${a.submission_count} 份</span>` : ''}
          </div>
        </div>
        <button class="btn sm" data-open="${a.id}">${isTeacher ? '查看提交' : '查看 / 提交'}</button>
      </div>`).join('') : '<div class="card empty"><div class="big">📝</div><p>暂无作业</p></div>'}
    </div>`;
  if (isTeacher) $('#addAsg', body).addEventListener('click', () => asgModal(cid));
  $$('[data-open]', body).forEach(b =>
    b.addEventListener('click', () => location.hash = `#/assignment/${b.dataset.open}`));
}

function asgModal(cid) {
  openModal(`<h2>发布作业</h2>
    <label>标题<input id="aTitle" placeholder="如：实验一：卡尔曼滤波方差对比"></label>
    <label>要求说明<textarea id="aContent" style="min-height:170px" placeholder="写清楚要做什么、提交什么"></textarea></label>
    <div class="row2">
      <label>满分<input id="aScore" type="number" value="100" min="1"></label>
      <label>截止时间（可留空）<input id="aDue" type="datetime-local"></label>
    </div>
    <div class="hstack" style="justify-content:flex-end">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="aOk">发布</button>
    </div>`);
  $('#aOk').addEventListener('click', async () => {
    const due = $('#aDue').value;
    try {
      await post(`/api/courses/${cid}/assignments`, {
        title: $('#aTitle').value.trim(), content: $('#aContent').value,
        full_score: Number($('#aScore').value) || 100,
        due_at: due ? new Date(due).toISOString().slice(0, 19) : null,
      });
      closeModal(); toast('作业已发布', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function renderAssignment(aid) {
  const a = await get(`/api/assignments/${aid}`);
  const isTeacher = State.me.role === 'teacher';
  const box = $('#content');
  const mine = a.my_submission;

  box.innerHTML = `
    <div class="crumb"><a href="#/course/${a.course_id}/assignment">${esc(a.course_title)}</a> / 作业</div>
    <div class="page-head">
      <h1>${esc(a.title)}</h1>
      <p>满分 ${a.full_score} · 截止 ${a.due_at ? fmtTime(a.due_at) : '未设置'}</p>
    </div>
    <div class="card"><h3 style="font-size:16px;margin-bottom:10px">作业要求</h3>
      <div style="white-space:pre-wrap;color:var(--dim);font-size:14.5px">${esc(a.content || '（无）')}</div></div>
    ${!isTeacher ? `
      <div class="card">
        <h3 style="font-size:16px;margin-bottom:12px">我的提交 ${mine ? '<span class="tag">已提交</span>' : ''}</h3>
        ${mine && mine.score !== null ? `<p style="margin-bottom:10px">得分：<b style="color:var(--cyan);font-size:20px">${mine.score}</b>
          ${mine.feedback ? `<span class="muted"> · 评语：${esc(mine.feedback)}</span>` : ''}</p>` : ''}
        <textarea id="subContent" style="min-height:190px" placeholder="粘贴你的答案、代码或说明…">${esc(mine?.content || '')}</textarea>
        <div class="hstack" style="justify-content:flex-end;margin-top:12px">
          <button class="btn primary" id="subOk">${mine ? '更新提交' : '提交作业'}</button>
        </div>
      </div>` : `
      <div class="card">
        <h3 style="font-size:16px;margin-bottom:12px">学生提交（${a.submissions.length}）</h3>
        ${a.submissions.length ? `<table><thead><tr><th>学生</th><th>提交时间</th><th>内容</th><th>得分</th><th></th></tr></thead>
          <tbody>${a.submissions.map(s => `<tr>
            <td>${esc(s.student)}</td>
            <td class="muted">${fmtTime(s.submitted_at)}</td>
            <td style="max-width:340px;white-space:pre-wrap;font-size:13.5px">${esc(s.content).slice(0, 400)}</td>
            <td>${s.score === null ? '<span class="muted">未评分</span>' : `<b>${s.score}</b>`}</td>
            <td><button class="btn sm" data-grade="${s.id}" data-max="${a.full_score}">评分</button></td>
          </tr>`).join('')}</tbody></table>`
          : '<div class="empty"><div class="big">📭</div><p>还没有学生提交</p></div>'}
      </div>`}`;

  const subOk = $('#subOk', box);
  if (subOk) subOk.addEventListener('click', async () => {
    try {
      await post(`/api/assignments/${aid}/submit`, { content: $('#subContent').value });
      toast('提交成功', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });

  $$('[data-grade]', box).forEach(b => b.addEventListener('click', () => {
    openModal(`<h2>评分</h2>
      <label>分数（0 ~ ${b.dataset.max}）<input id="gScore" type="number" min="0" max="${b.dataset.max}" step="0.5"></label>
      <label>评语<input id="gFb" placeholder="选填"></label>
      <div class="hstack" style="justify-content:flex-end">
        <button class="btn ghost" onclick="closeModal()">取消</button>
        <button class="btn primary" id="gOk">保存</button>
      </div>`);
    $('#gOk').addEventListener('click', async () => {
      try {
        await post(`/api/submissions/${b.dataset.grade}/grade`, {
          score: Number($('#gScore').value), feedback: $('#gFb').value.trim() || null });
        closeModal(); toast('已评分', 'ok'); router();
      } catch (e) { toast(e.message, 'err'); }
    });
  }));
}

/* ---------- 试题 ---------- */
async function tabQuizzes(body, cid, d, canManage) {
  const list = d.quiz_sets;
  const isTeacher = canManage;
  body.innerHTML = `
    <div class="spread" style="margin-bottom:16px">
      <div class="muted">共 ${list.length} 套试题${isTeacher ? '（含学生自测卷，便于答疑）' : ''}</div>
      <div class="hstack">
        <button class="btn sm" id="aiGo">✨ 让 AI 出一套题</button>
        ${isTeacher ? `<button class="btn primary sm" id="addQuiz">+ 手工组卷</button>` : ''}
      </div>
    </div>
    <div class="stack">${list.length ? list.map(q => {
      const scopeTag = q.scope === 'private'
        ? `<span class="tag" style="color:#c4b5fd;border-color:#4c3b7a;background:#1d1733">仅${q.mine ? '我' : '其本人'}可见</span>`
        : q.scope === 'teacher'
          ? `<span class="tag" style="color:#fbbf24;border-color:#6b5518;background:#2a2210">仅教师可见</span>`
          : `<span class="tag" style="color:#7dd3fc;border-color:#2c4a63;background:#0f2233">全班可见</span>`;
      const who = q.source === 'ai' ? (q.mine ? '我生成的' : 'AI 生成') : '教师出题';
      return `
      <div class="card spread">
        <div>
          <h3 style="font-size:16.5px">${esc(q.title)}
            <span class="tag ${q.source === 'ai' ? 'ai' : 'manual'}">${who}</span>${scopeTag}
          </h3>
          <div class="kv" style="margin-top:6px">
            <span>${q.question_count} 道题</span><span>${fmtTime(q.created_at)}</span>
            ${q.attempt_count ? `<span>已作答 ${q.attempt_count} 次</span>` : ''}
            ${q.best_score !== null && q.best_score !== undefined
              ? `<span>最高 ${q.best_score} 分</span>` : ''}
          </div>
        </div>
        <div class="hstack">
          <button class="btn sm ${q.attempt_count ? '' : 'primary'}" data-do="${q.id}">
            ${q.attempt_count ? '再做一次' : '开始作答'}</button>
          ${isTeacher ? `<button class="btn sm" data-detail="${q.id}">详情</button>` : ''}
          ${isTeacher ? `<button class="btn sm" data-scope="${q.id}" data-cur="${q.scope}">可见性</button>` : ''}
          ${(isTeacher || q.mine) ? `<button class="btn sm danger" data-delq="${q.id}">删除</button>` : ''}
        </div>
      </div>`; }).join('') : '<div class="card empty"><div class="big">🧪</div><p>还没有试题，可以让 AI 出一套</p></div>'}
    </div>`;
  $('#aiGo', body).addEventListener('click', () => aiGenModal([{ id: cid, title: d.course.title }]));
  if (isTeacher) $('#addQuiz', body).addEventListener('click', () => manualQuizModal(cid));
  $$('[data-do]', body).forEach(b =>
    b.addEventListener('click', () => location.hash = `#/quiz/${b.dataset.do}`));
  $$('[data-detail]', body).forEach(b =>
    b.addEventListener('click', () => location.hash = `#/quizdetail/${b.dataset.detail}`));
  $$('[data-scope]', body).forEach(b => b.addEventListener('click', () => {
    openModal(`<h2>设置可见性</h2>
      <p class="muted" style="font-size:13.5px;margin:8px 0 16px">决定这套题谁能看到、谁能作答。</p>
      <label>可见范围
        <select id="scSel">
          <option value="course" ${b.dataset.cur === 'course' ? 'selected' : ''}>全班可见（所有学生都能看到并作答）</option>
          <option value="teacher" ${b.dataset.cur === 'teacher' ? 'selected' : ''}>仅教师可见（学生看不到）</option>
        </select></label>
      <div class="hstack" style="justify-content:flex-end">
        <button class="btn ghost" onclick="closeModal()">取消</button>
        <button class="btn primary" id="scOk">保存</button>
      </div>`);
    $('#scOk').addEventListener('click', async () => {
      try {
        await patch(`/api/quizzes/${b.dataset.scope}/scope`, { scope: $('#scSel').value });
        closeModal(); toast('已更新', 'ok'); router();
      } catch (e) { toast(e.message, 'err'); }
    });
  }));
  $$('[data-delq]', body).forEach(b => b.addEventListener('click', async () => {
    if (!confirm('确定删除这套题？')) return;
    try { await del(`/api/quizzes/${b.dataset.delq}`); toast('已删除', 'ok'); router(); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

/* ---------- 教师：某套题的详情（答案解析 + 作答情况） ---------- */
async function renderQuizOverview(qid) {
  const d = await get(`/api/quizzes/${qid}/overview`);
  const box = $('#content');
  const s = d.stats;
  box.innerHTML = `
    <div class="crumb"><a href="#/course/${d.quiz.course_id}/quiz">返回试题列表</a> / 试题详情</div>
    <div class="page-head">
      <h1>${esc(d.quiz.title)}</h1>
      <p>${d.quiz.question_count} 道题 · ${d.scope === 'course' ? '全班可见'
        : d.scope === 'teacher' ? '仅教师可见' : '仅创建者可见'}</p>
    </div>

    <div class="grid g4">
      <div class="card stat-card"><div class="num">${s.attempt_count}</div><div class="lbl">作答人次</div></div>
      <div class="card stat-card"><div class="num">${s.student_count}</div><div class="lbl">作答人数</div></div>
      <div class="card stat-card"><div class="num">${s.avg_accuracy === null ? '—' : s.avg_accuracy + '%'}</div><div class="lbl">平均正确率</div></div>
      <div class="card stat-card"><div class="num">${s.highest === null ? '—' : s.highest + '%'}</div><div class="lbl">最高正确率</div></div>
    </div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:14px">📈 每题正确率（定位全班都错的题）</h3>
      ${d.questions_stat.length ? `<table>
        <thead><tr><th style="width:58%">题目</th><th>正确率</th><th>正确/作答</th></tr></thead>
        <tbody>${d.questions_stat.map((q, i) => `<tr>
          <td>${i + 1}. ${esc(q.stem).slice(0, 80)}</td>
          <td>${q.accuracy === null ? '<span class="muted">无人作答</span>' :
            `<div class="bar ${q.accuracy < 60 ? 'warn' : ''}"><i style="width:${Math.min(100, q.accuracy)}%"></i></div>
             <span class="muted" style="font-size:12.5px">${q.accuracy}%</span>`}</td>
          <td class="muted">${q.correct} / ${q.total}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty" style="padding:24px"><p>暂无作答</p></div>'}
    </div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:14px">👥 学生作答情况</h3>
      ${d.attempts.length ? `<table>
        <thead><tr><th>学生</th><th>学号</th><th>得分</th><th>正确率</th><th>错题号</th><th>提交时间</th><th></th></tr></thead>
        <tbody>${d.attempts.map(a => `<tr>
          <td>${esc(a.student)}</td><td class="muted">${esc(a.student_no || '—')}</td>
          <td><b>${a.score}</b> / ${a.total}</td>
          <td><div class="bar ${a.accuracy < 60 ? 'warn' : ''}"><i style="width:${Math.min(100, a.accuracy)}%"></i></div>
            <span class="muted" style="font-size:12.5px">${a.accuracy}%</span></td>
          <td class="muted">${a.wrong.length ? a.wrong.map(id =>
            d.quiz.questions.findIndex(q => q.id === id) + 1).join(', ') : '全对'}</td>
          <td class="muted">${fmtTime(a.submitted_at)}</td>
          <td><button class="btn sm" onclick="location.hash='#/attempt/${a.id}'">看答卷</button></td>
        </tr>`).join('')}</tbody></table>`
        : '<div class="card empty" style="padding:24px"><p>还没有学生作答</p></div>'}
    </div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:14px">📝 题目与答案解析</h3>
      ${d.quiz.questions.map((q, i) => `
        <div style="padding:14px 0;border-bottom:1px solid var(--brd)">
          <div class="hstack" style="margin-bottom:8px">
            <span class="tag">第 ${i + 1} 题</span>
            <span class="tag">${{ single: '单选', multi: '多选', judge: '判断', short: '简答' }[q.qtype] || q.qtype}</span>
            <span class="pill">难度 ${q.difficulty}</span>
          </div>
          <div style="font-weight:650;margin-bottom:8px">${esc(q.stem)}</div>
          ${(q.options || []).map(o => `<div class="muted" style="font-size:14px;padding:2px 0">${esc(o)}</div>`).join('')}
          <div class="explain" style="margin-top:10px">
            <b>答案：</b>${esc(q.answer)}<br><b>解析：</b>${esc(q.analysis || '（无）')}
          </div>
        </div>`).join('')}
    </div>`;
}

function manualQuizModal(cid) {
  openModal(`<h2>手工组卷</h2>
    <p class="muted" style="font-size:13.5px;margin:8px 0 16px">
      每题用 <code>单选|多选|判断</code> 指定类型，选项用换行分隔，答案填字母。</p>
    <label>试卷标题<input id="qTitle" placeholder="如：第 2 章 小测"></label>
    <div id="qList">${manualQBlock(0)}</div>
    <div class="hstack">
      <button class="btn sm" id="qAdd">+ 再加一题</button>
      <div style="flex:1"></div>
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="qOk">创建试卷</button>
    </div>`);
  let n = 1;
  $('#qAdd').addEventListener('click', () => {
    $('#qList').insertAdjacentHTML('beforeend', manualQBlock(n++));
  });
  $('#qOk').addEventListener('click', async () => {
    const questions = [];
    $$('.qblk').forEach(b => {
      const stem = $('.qb-stem', b).value.trim();
      if (!stem) return;
      const type = $('.qb-type', b).value;
      const opts = $('.qb-opts', b).value.split('\n').map(s => s.trim()).filter(Boolean);
      questions.push({
        qtype: type, stem,
        options: type === 'judge' ? ['对', '错'] : opts,
        answer: $('.qb-ans', b).value.trim(),
        analysis: $('.qb-ana', b).value.trim(),
        difficulty: Number($('.qb-diff', b).value) || 3,
      });
    });
    if (!questions.length) return toast('至少填一道题', 'err');
    try {
      await post(`/api/courses/${cid}/quizzes`, { title: $('#qTitle').value.trim() || '小测', questions });
      closeModal(); toast('试卷已创建', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

function manualQBlock(i) {
  return `<div class="qblk card" style="margin-bottom:12px">
    <div class="row2">
      <label>题型<select class="qb-type"><option value="single">单选</option><option value="multi">多选</option><option value="judge">判断</option></select></label>
      <label>难度<select class="qb-diff"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
    </div>
    <label>题干<input class="qb-stem" placeholder="第 ${i + 1} 题题干"></label>
    <label>选项（每行一个，判断题可留空）<textarea class="qb-opts" style="min-height:70px" placeholder="A. 选项一&#10;B. 选项二"></textarea></label>
    <div class="row2">
      <label>答案<input class="qb-ans" placeholder="A / AB / 对"></label>
      <label>解析<input class="qb-ana" placeholder="为什么选它"></label>
    </div>
  </div>`;
}

/* ---------- AI 出题 ---------- */
async function tabAi(body, cid, d, quota) {
  const canManage = !!d.can_manage;
  const weak = State.me.role === 'student'
    ? (await get(`/api/courses/${cid}/weak-points`).catch(() => ({ weak_points: [] }))).weak_points
    : [];
  body.innerHTML = `
    <div class="grid g2">
      <div class="card">
        <h3 style="font-size:17px">✨ AI 智能出题</h3>
        <p class="muted" style="font-size:14px;margin:8px 0 18px">
          选择你想强化的知识点，AI 会围绕它们自动出一套试题；做完立刻看解析。</p>
        ${quota ? `<div class="card" style="background:#101a2c;padding:14px;margin-bottom:16px">
          <div class="kv">
            <span>当前 Key：<b>${quota.key_source === 'own' ? '你自己的' :
              quota.key_source === 'admin' ? '平台默认（管理员）' :
              quota.key_source === 'teacher' ? '课程教师的' : '平台默认'}</b></span>
            <span>额度：<b>${quota.unlimited
              ? (quota.reason === 'admin' ? '无限次（管理员）' : '无限次')
              : `${quota.remaining} / ${quota.limit} 套（今日）`}</b></span>
          </div>
          ${quota.will_fallback_mock ? `<div class="muted" style="font-size:13px;margin-top:8px">
            ⚠️ 平台未配置 API Key，将生成<b>离线模拟题</b>用于演示流程。
            想用真实 AI，请在「设置」里填入你自己的 Key（填了就不限次数）。</div>` : ''}
        </div>` : ''}
        <label>出题范围（不选则整门课）
          <div class="stack" style="margin-top:8px;max-height:210px;overflow:auto">
            ${d.knowledge_points.map(k => `<label class="opt" style="margin:0">
              <input type="checkbox" class="kpPick" value="${k.id}"><span>${esc(k.title)}</span></label>`).join('')
              || '<span class="muted">这门课还没有知识点</span>'}
          </div>
        </label>
        <div class="row2">
          <label>题量<select id="aiCount">${[3, 5, 8, 10, 15].map(n => `<option ${n === 5 ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
          <label>题型<select id="aiType">
            <option value="single">单选</option><option value="multi">多选</option>
            <option value="judge">判断</option><option value="mixed">混合</option></select></label>
        </div>
        <label>难度<select id="aiDiff">
          <option value="easy">偏基础</option><option value="medium" selected>中等</option>
          <option value="hard">偏难</option><option value="mixed">有梯度</option></select></label>
        <label>补充要求（选填）<input id="aiExtra" placeholder="如：多考公式推导与适用条件"></label>
        ${canManage ? `<label>生成后可见范围
          <select id="aiScope">
            <option value="course">全班可见（所有学生都能看到并作答）</option>
            <option value="teacher">仅教师可见（学生看不到）</option>
            <option value="private">仅自己可见</option>
          </select></label>`
        : `<div class="hint" style="font-size:13px;color:var(--dim2);margin-bottom:12px">
            🔒 你生成的试题<b>只有你自己能看到</b>，用于自我强化练习。</div>`}
        <button class="btn primary block" id="aiOk">生成试题</button>
        <div id="aiMsg" class="msg"></div>
      </div>

      <div class="card">
        <h3 style="font-size:17px">🎯 我的薄弱知识点</h3>
        <p class="muted" style="font-size:14px;margin:8px 0 16px">
          根据你以往的错题自动统计，点条目可快速勾选为出题范围。</p>
        ${weak.length ? `<table><thead><tr><th>知识点</th><th>错误率</th><th>错/总</th></tr></thead><tbody>
          ${weak.map(w => `<tr data-kp="${w.kp_id}" style="cursor:pointer">
            <td>${esc(w.title)}</td>
            <td><div class="bar ${w.wrong_rate >= 50 ? 'warn' : ''}"><i style="width:${Math.min(100, w.wrong_rate)}%"></i></div>
              <span class="muted" style="font-size:12.5px">${w.wrong_rate}%</span></td>
            <td class="muted">${w.wrong} / ${w.total}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty" style="padding:30px"><div class="big">🌟</div><p>还没有练习记录，先做一套题吧</p></div>'}
      </div>
    </div>`;

  $$('[data-kp]', body).forEach(tr => tr.addEventListener('click', () => {
    const cb = $(`.kpPick[value="${tr.dataset.kp}"]`, body);
    if (cb) { cb.checked = true; toast('已勾选：' + cb.nextElementSibling.textContent); }
  }));

  $('#aiOk', body).addEventListener('click', async () => {
    const ids = $$('.kpPick:checked', body).map(x => Number(x.value));
    const msg = $('#aiMsg', body);
    msg.className = 'msg'; msg.innerHTML = '<span class="spin"></span> AI 正在出题，请稍候…';
    try {
      const r = await post('/api/ai/generate', {
        course_id: cid, kp_ids: ids, count: Number($('#aiCount', body).value),
        qtype: $('#aiType', body).value, difficulty: $('#aiDiff', body).value,
        extra: $('#aiExtra', body).value.trim(),
        scope: $('#aiScope', body) ? $('#aiScope', body).value : 'auto',
      });
      toast(r.notice || `已生成 ${r.count} 道题`, r.notice ? '' : 'ok');
      location.hash = `#/quiz/${r.quiz_id}`;
    } catch (e) {
      msg.className = 'msg err';
      msg.textContent = e.message;
    }
  });
}

function aiGenModal(courses) {
  if (!courses.length) return toast('请先加入或创建课程', 'err');
  openModal(`<h2>✨ AI 出题</h2>
    <p class="muted" style="font-size:14px;margin:8px 0 16px">选择课程后进入出题页面，可指定知识点范围。</p>
    <label>课程<select id="agCourse">${courses.map(c =>
      `<option value="${c.id}">${esc(c.title)}</option>`).join('')}</select></label>
    <div class="hstack" style="justify-content:flex-end">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="agOk">进入出题</button>
    </div>`);
  $('#agOk').addEventListener('click', () => {
    closeModal();
    location.hash = `#/course/${$('#agCourse').value}/ai`;
  });
}

/* ---------- 答题页 ---------- */
async function renderQuiz(qid) {
  const quiz = await get(`/api/quizzes/${qid}`);
  State.currentQuiz = { quiz, answers: {}, submitted: false, review: null };
  drawQuiz();
}

function drawQuiz() {
  const st = State.currentQuiz;
  if (!st) return;
  const quiz = st.quiz, box = $('#content');
  const total = quiz.questions.length;

  box.innerHTML = `
    <div class="crumb"><a href="#/course/${quiz.course_id}/quiz">返回课程</a> / 作答</div>
    <div class="page-head spread">
      <div><h1>${esc(quiz.title)}</h1>
        <p>共 ${total} 道题 · ${quiz.source === 'ai' ? 'AI 生成' : '教师出题'}${st.submitted ? ' · 已完成' : ''}</p>
      </div>
      ${st.submitted ? `<div class="card stat-card" style="padding:12px 20px">
        <div class="num" style="color:var(--cyan)">${st.score} / ${st.total}</div>
        <div class="lbl">正确率 ${st.accuracy}%</div></div>` : ''}
    </div>
    ${st.submitted ? summaryHtml(st.summary) : renderQNav()}
    <div id="qArea">${quiz.questions.map((q, i) => questionHtml(q, i, st)).join('')}</div>
    <div class="quizbar">
      ${st.submitted
        ? `<button class="btn" onclick="location.hash='#/course/${quiz.course_id}/quiz'">返回课程</button>
           <button class="btn primary" id="retakeQuiz">🔄 重新作答</button>
           <button class="btn" onclick="location.hash='#/course/${quiz.course_id}/ai'">✨ 再练一套</button>
           ${st.summary && st.summary.unanswered_count
             ? `<button class="btn" id="jumpFirstUn">⬜ 跳到第 1 道未作答</button>` : ''}`
        : `<button class="btn primary" id="submitQuiz">提交并查看解析</button>
           <span class="muted" id="progress">已作答 0 / ${total}</span>`}
    </div>`;

  if (!st.submitted) {
    $$('.opt input', box).forEach(inp => inp.addEventListener('change', onPick));
    $('#submitQuiz', box).addEventListener('click', submitQuiz);
    bindJump(box);          // 顶部题号导航可点击跳题
    updateProgress();
  } else {
    bindJump(box);
    const first = $('#jumpFirstUn', box);
    if (first) first.addEventListener('click', () => {
      const firstUn = (st.summary.unanswered || [])[0];
      if (!firstUn) return;
      document.getElementById('q-' + firstUn.id)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    const rb = $('#retakeQuiz', box);
    if (rb) rb.addEventListener('click', () => {
      if (!confirm('重新作答会清空本次的选项（历史成绩仍保留在练习记录里），确定吗？')) return;
      State.currentQuiz = { quiz: st.quiz, answers: {}, submitted: false, review: null,
                            summary: null };
      drawQuiz();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }
}

/* ---------- 答案比对工具（保证"正确选项标绿"一定能匹配上） ---------- */
// 判断题的答案可能是 对/错/正确/错误/T/F
function judgeValue(s) {
  const t = String(s ?? '').trim().toLowerCase();
  if (['对', '√', '正确', '是', 'true', 't', 'yes', 'y', 'right', 'correct', '1'].includes(t)) return '对';
  if (['错', '×', '错误', '否', 'false', 'f', 'no', 'n', 'wrong', 'incorrect', '0'].includes(t)) return '错';
  if (t.includes('对') || t.includes('正确')) return '对';
  if (t.includes('错') || t.includes('错误')) return '错';
  return String(s ?? '').trim();
}

// 选项在本地的"字母"：优先取选项文本自带的 A./B./C.，否则按位置给 A/B/C/D
function optionLetter(raw, index) {
  const m = /^\s*([A-Za-z])\s*[.、)．]/.exec(String(raw));
  return m ? m[1].toUpperCase() : String.fromCharCode(65 + index);
}

// 正确答案集合：单选/多选返回字母集合，判断返回 {对} 或 {错}
function correctSet(q) {
  const type = q.qtype === 'judge' ? 'judge' : (q.qtype === 'multi' ? 'multi' : 'single');
  if (type === 'judge') return new Set([judgeValue(q.answer)]);
  // 多选/单选从答案里抽字母；若没有任何字母，退化为按选项文本匹配
  const letters = String(q.answer || '').toUpperCase().match(/[A-Z]/g) || [];
  return new Set(letters);
}

// 学生所选集合
function pickedSet(q) {
  const given = String(q.given ?? '');
  if (q.qtype === 'judge') return new Set(given ? [judgeValue(given)] : []);
  const letters = given.toUpperCase().match(/[A-Z]/g) || [];
  return new Set(letters);
}

function optionState(q, raw, index) {
  const letter = optionLetter(raw, index);
  const correctS = correctSet(q);
  const pickedS = pickedSet(q);
  let isCorrect, isPicked;
  if (q.qtype === 'judge') {
    isCorrect = correctS.has(judgeValue(raw));
    isPicked = pickedS.has(judgeValue(raw));
  } else {
    isCorrect = correctS.has(letter);
    isPicked = pickedS.has(letter);
  }
  return { letter, isCorrect, isPicked };
}

function difficultyText(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 1) return '—';
  return n + ' / 5';
}

function questionHtml(q, i, st) {
  const given = st.answers[String(q.id)] || '';
  const rev = st.review ? st.review.find(r => r.id === q.id) : null;
  const showAns = st.submitted;
  const typeLabel = { single: '单选题', multi: '多选题', judge: '判断题', short: '简答题' }[q.qtype] || '题目';
  // 交卷后用于判定的数据源：优先用后端 review（含 given/answer），保证与判分一致
  const judgeQ = rev ? { ...q, ...rev } : q;
  const answered = showAns
    ? (rev ? rev.answered !== false && String(rev.given || '').trim() !== '' : !!String(given).trim())
    : !!String(given).trim();

  let opts = '';
  if (q.options && q.options.length) {
    opts = q.options.map((o, k) => {
      const raw = String(o);
      const { isCorrect, isPicked } = optionState(judgeQ, raw, k);
      const inputType = q.qtype === 'multi' ? 'checkbox' : 'radio';
      let cls = 'opt';
      if (showAns) {
        cls += ' locked';
        if (isCorrect) cls += ' right';          // 正确选项 → 绿框
        else if (isPicked) cls += ' wrong';      // 选错 → 红框
      } else if (isPicked) cls += ' sel';
      const val = q.qtype === 'judge' ? raw : optionLetter(raw, k);
      return `<label class="${cls}">
        <input type="${inputType}" name="q_${q.id}" value="${esc(val)}"
          data-qid="${q.id}" ${isPicked ? 'checked' : ''} ${showAns ? 'disabled' : ''}>
        <span>${esc(raw)}</span>
        ${showAns && isCorrect ? '<span class="mark ok">✓ 正确答案</span>' : ''}
        ${showAns && !isCorrect && isPicked ? '<span class="mark no">✗ 你的选择</span>' : ''}
      </label>`;
    }).join('');
  } else {
    opts = `<textarea data-qid="${q.id}" ${showAns ? 'disabled' : ''}
      placeholder="写下你的答案…">${esc(given)}</textarea>`;
  }

  let explain = '';
  if (showAns) {
    const head = !answered ? '<b style="color:var(--amber)">⬜ 未作答</b>'
      : (rev && rev.correct ? '<b style="color:var(--green)">✅ 回答正确</b>'
        : '<b style="color:var(--red)">❌ 回答错误</b>');
    explain = `<div class="explain">
      ${head}　正确答案：<b>${esc(q.answer || '（见解析）')}</b>　你的答案：${esc(given || '未作答')}
      <div style="margin-top:10px"><b>解析：</b>${esc(q.analysis || '（本题暂无解析）')}</div>
    </div>`;
  }

  return `<div class="q" id="q-${q.id}" data-qid="${q.id}">
    <div class="qhead">
      <span class="tag">第 ${i + 1} 题</span>
      <span class="tag">${typeLabel}</span>
      <span class="pill">难度 ${difficultyText(q.difficulty)}</span>
      ${showAns ? (!answered
        ? '<span class="tag" style="color:#fbbf24;border-color:#6b5518">未作答</span>'
        : (rev && rev.correct
          ? '<span class="tag" style="color:#34d399;border-color:#2f6d51">正确</span>'
          : '<span class="tag" style="color:#f87171;border-color:#6d2f2f">错误</span>')) : ''}
    </div>
    <div class="stem">${esc(q.stem)}</div>
    ${opts}${explain}
  </div>`;
}

/* ---------- 提交前的完成度检查 ---------- */
// 该题是否已作答（与后端 _answered 判定一致）
function localAnswered(q, answers) {
  const v = answers[String(q.id)];
  return v !== undefined && v !== null && String(v).trim() !== '';
}

// 计算当前作答进度：哪些做了、哪些没做
function attemptProgress() {
  const st = State.currentQuiz;
  const qs = st.quiz.questions;
  const done = [], todo = [];
  qs.forEach((q, i) => {
    (localAnswered(q, st.answers) ? done : todo).push({ id: q.id, no: i + 1, stem: q.stem });
  });
  return { total: qs.length, done, todo, complete: todo.length === 0 };
}

// 完成度面板（提交前 / 提交后共用同一套渲染）
function progressPanel(p) {
  const pct = p.total ? Math.round(p.done.length / p.total * 100) : 0;
  const chips = (arr, cls, prefix) => arr.length
    ? arr.map(x => `<button class="qjump ${cls}" data-jump="${x.id}">${prefix}${x.no}</button>`).join('')
    : '<span class="muted" style="font-size:13px">无</span>';
  return `<div class="card summary">
    <div class="spread" style="align-items:flex-start;gap:18px">
      <div style="flex:1;min-width:220px">
        <div class="kv" style="margin-bottom:10px">
          <span>共 <b>${p.total}</b> 道题</span>
          <span>已作答 <b style="color:var(--green)">${p.done.length}</b> 道</span>
          <span>未作答 <b style="color:${p.todo.length ? 'var(--amber)' : 'var(--dim)'}">${p.todo.length}</b> 道</span>
        </div>
        <div class="bar ${p.complete ? '' : 'warn'}"><i style="width:${pct}%"></i></div>
        <div class="muted" style="font-size:12.5px;margin-top:6px">完成度 ${pct}%</div>
      </div>
      <div style="flex:1.5;min-width:250px">
        <div style="margin-bottom:10px">
          <div class="muted" style="font-size:13px;margin-bottom:6px">
            ✅ 已作答（${p.done.length}）</div>
          ${chips(p.done, 'ok', '第')}
        </div>
        <div>
          <div class="muted" style="font-size:13px;margin-bottom:6px">
            ⬜ 未作答（${p.todo.length}）${p.todo.length ? '—— 点击题号可直接跳过去继续做：' : ''}</div>
          ${chips(p.todo, 'un', '第')}
        </div>
      </div>
    </div>
  </div>`;
}

// 顶部小结（交卷后展示正确/错误）
function summaryHtml(summary) {
  const s = summary || {};
  const total = s.total ?? 0;
  const ans = s.answered_count ?? 0;
  const un = s.unanswered_count ?? 0;
  const pct = total ? Math.round(ans / total * 100) : 0;
  const chips = (arr, cls, prefix) => arr.length
    ? arr.map(x => `<button class="qjump ${cls}" data-jump="${x.id}">${prefix}${x.no}</button>`).join('')
    : '<span class="muted" style="font-size:13px">无</span>';

  return `<div class="card summary" id="quizSummary">
    <div class="spread" style="align-items:flex-start;gap:18px">
      <div style="flex:1;min-width:240px">
        <h3 style="font-size:16.5px;margin-bottom:10px">📋 本次作答小结</h3>
        <div class="kv" style="margin-bottom:10px">
          <span>共 <b>${total}</b> 道题</span>
          <span>已作答 <b style="color:var(--green)">${ans}</b> 道</span>
          <span>未作答 <b style="color:${un ? 'var(--amber)' : 'var(--dim)'}">${un}</b> 道</span>
          <span>答对 <b style="color:var(--green)">${s.correct_count ?? '—'}</b> 道</span>
          <span>答错 <b style="color:var(--red)">${s.wrong_count ?? 0}</b> 道</span>
        </div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="muted" style="font-size:12.5px;margin-top:6px">完成度 ${pct}%</div>
      </div>
      <div style="flex:1.4;min-width:260px">
        <div style="margin-bottom:10px">
          <div class="muted" style="font-size:13px;margin-bottom:6px">
            ⬜ 未作答（${un}）—— 点击可直接跳到该题：</div>
          ${chips(s.unanswered || [], 'un', '第')}
        </div>
        <div>
          <div class="muted" style="font-size:13px;margin-bottom:6px">
            ❌ 答错（${s.wrong_count ?? 0}）—— 点击可直接跳到该题：</div>
          ${chips(s.wrong || [], 'wr', '第')}
        </div>
      </div>
    </div>
    ${un ? `<div class="hint" style="margin-top:14px;border-top:1px dashed var(--brd);padding-top:12px;
      font-size:13px;color:var(--dim2)">
      还有 ${un} 道题没有作答。上面黄色按钮点一下就能跳过去；
      想重新做一遍可以点下面的「🔄 重新作答」。</div>` : ''}
  </div>`;
}

function bindJump(box) {
  $$('[data-jump]', box).forEach(b => b.addEventListener('click', () => {
    const el = document.getElementById('q-' + b.dataset.jump);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1400);
  }));
}

/* ---------- 点击提交后的对话框 ---------- */
// 全部做完 → 二次确认；有没做完 → 阻止提交并让点击题号跳转
function preSubmitDialog() {
  const st = State.currentQuiz;
  const p = attemptProgress();

  if (p.complete) {
    openModal(`<h2>确认提交</h2>
      <p class="muted" style="font-size:14px;margin:10px 0 16px">
        ${p.total} 道题已全部作答，提交后即可查看每道题的答案解析。提交后仍可「重新作答」。</p>
      ${progressPanel(p)}
      <div class="hstack" style="justify-content:flex-end;margin-top:14px">
        <button class="btn ghost" onclick="closeModal()">再检查一下</button>
        <button class="btn primary" id="preOk">确认提交</button>
      </div>`);
    $('#preOk').addEventListener('click', () => { closeModal(); doSubmit(); });
    return;
  }

  // 没做完：不允许提交
  openModal(`<h2 style="color:var(--amber)">还有 ${p.todo.length} 道题没做完</h2>
    <p class="muted" style="font-size:14px;margin:10px 0 16px">
      需要把 ${p.total} 道题<b>全部作答</b>后才能提交。
      点击下面的<b>黄色题号</b>可以直接跳到那道题继续做。</p>
    ${progressPanel(p)}
    <div class="hstack" style="justify-content:flex-end;margin-top:14px">
      <button class="btn ghost" onclick="closeModal()">留在本页</button>
      <button class="btn primary" id="goFirst">跳到第 1 道未作答</button>
    </div>`);
  // 弹窗内的题号按钮：跳题并关闭弹窗
  $$('#modalBox [data-jump]').forEach(b => b.addEventListener('click', () => {
    const id = b.dataset.jump;
    closeModal();
    const el = document.getElementById('q-' + id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1400);
  }));
  $('#goFirst').addEventListener('click', () => {
    const first = p.todo[0];
    closeModal();
    const el = document.getElementById('q-' + first.id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1400);
    }
  });
}

function onPick(e) {
  const st = State.currentQuiz;
  const qid = e.target.dataset.qid;
  const q = st.quiz.questions.find(x => x.id === Number(qid));
  if (q.qtype === 'multi') {
    const picked = $$(`input[name="q_${qid}"]:checked`).map(x => x.value).sort().join('');
    st.answers[qid] = picked;
  } else {
    st.answers[qid] = e.target.value;
  }
  const wrap = e.target.closest('.q');
  $$('.opt', wrap).forEach(o => o.classList.toggle('sel', $('input', o).checked));
  el_markAnswered(qid, true);
  updateProgress();
}

/* 题号导航里的作答状态色 */
function el_markAnswered(qid, on) {
  const chip = document.querySelector(`#qnav [data-navq="${qid}"]`);
  if (chip) chip.classList.toggle('done', !!on);
}

/* 顶部题号导航：点题号即跳题，已作答的高亮 */
function renderQNav() {
  const st = State.currentQuiz;
  if (!st || st.submitted) return '';
  const qs = st.quiz.questions;
  return `<div class="qnav" id="qnav">
    <span class="muted" style="font-size:13px;margin-right:6px">题号导航：</span>
    ${qs.map((q, i) => `<button class="qchip ${localAnswered(q, st.answers) ? 'done' : ''}"
      data-navq="${q.id}" data-jump="${q.id}">${i + 1}</button>`).join('')}
  </div>`;
}

function updateProgress() {
  const st = State.currentQuiz;
  const p = attemptProgress();
  const el = $('#progress');
  if (el) {
    el.innerHTML = p.complete
      ? `<span style="color:var(--green)">✅ 已全部作答（${p.total} / ${p.total}）</span>`
      : `已作答 <b>${p.done.length}</b> / ${p.total}　
         <span style="color:var(--amber)">还差 ${p.todo.length} 题</span>`;
  }
  const btn = $('#submitQuiz');
  if (btn) btn.textContent = p.complete ? '提交并查看解析' : `提交（还差 ${p.todo.length} 题）`;
  // 题号导航状态
  st.quiz.questions.forEach(q => el_markAnswered(q.id, localAnswered(q, st.answers)));
}

async function doSubmit() {
  const st = State.currentQuiz;
  try {
    const r = await post(`/api/quizzes/${st.quiz.id}/submit`, { answers: st.answers });
    st.submitted = true; st.review = r.review; st.summary = r.summary;
    st.score = r.score; st.total = r.total; st.accuracy = r.accuracy;
    toast(`得分 ${r.score}/${r.total}，正确率 ${r.accuracy}%`, r.accuracy >= 60 ? 'ok' : 'err');
    drawQuiz();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (e) { toast(e.message, 'err'); }
}

function submitQuiz() {
  preSubmitDialog();
}

/* ---------- 学生/学情 ---------- */
async function tabStudents(body, cid) {
  const d = await get(`/api/courses/${cid}/students`);
  body.innerHTML = d.students.length ? `<div class="card"><table>
    <thead><tr><th>姓名</th><th>学号</th><th>邮箱</th><th>答题次数</th></tr></thead>
    <tbody>${d.students.map(s => `<tr>
      <td>${esc(s.name)}</td><td class="muted">${esc(s.student_no || '—')}</td>
      <td class="muted">${esc(s.email)}</td><td>${s.attempts}</td></tr>`).join('')}</tbody>
    </table></div>` : '<div class="card empty"><div class="big">👥</div><p>还没有学生加入，把邀请码发给他们</p></div>';
}

async function renderAnalytics(target, cidArg) {
  const cid = cidArg ?? (location.hash.split('/')[2]);
  const box = cidArg ? target : $('#content');
  const d = await get(`/api/courses/${cid}/analytics`);

  box.innerHTML = `
    <div class="grid g4">
      <div class="card stat-card"><div class="num">${d.course.student_count}</div><div class="lbl">学生数</div></div>
      <div class="card stat-card"><div class="num">${d.course.kp_count}</div><div class="lbl">知识点</div></div>
      <div class="card stat-card"><div class="num">${d.course.assignment_count}</div><div class="lbl">作业</div></div>
      <div class="card stat-card"><div class="num">${d.course.quiz_count}</div><div class="lbl">试题套数</div></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3 style="font-size:17px;margin-bottom:14px">🎯 班级薄弱知识点</h3>
      ${d.weak_knowledge_points.length ? `<table>
        <thead><tr><th>知识点</th><th>错误率</th><th>错误 / 作答</th></tr></thead><tbody>
        ${d.weak_knowledge_points.map(w => `<tr>
          <td>${esc(w.title)}</td>
          <td><div class="bar ${w.wrong_rate >= 50 ? 'warn' : ''}"><i style="width:${Math.min(100, w.wrong_rate)}%"></i></div>
            <span class="muted" style="font-size:12.5px">${w.wrong_rate}%</span></td>
          <td class="muted">${w.wrong} / ${w.total}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty" style="padding:26px"><p>还没有答题数据</p></div>'}
    </div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:14px">👥 学生学习情况</h3>
      ${d.students.length ? `<table>
        <thead><tr><th>姓名</th><th>学号</th><th>答题</th><th>平均正确率</th><th>作业提交</th><th>平均分</th></tr></thead>
        <tbody>${d.students.map(s => `<tr>
          <td>${esc(s.name)}</td><td class="muted">${esc(s.student_no || '—')}</td>
          <td>${s.quiz_attempts}</td>
          <td>${s.quiz_accuracy === null ? '<span class="muted">—</span>' :
            `<div class="bar ${s.quiz_accuracy < 60 ? 'warn' : ''}"><i style="width:${Math.min(100, s.quiz_accuracy)}%"></i></div>
             <span class="muted" style="font-size:12.5px">${s.quiz_accuracy}%</span>`}</td>
          <td>${s.submitted}</td>
          <td>${s.avg_score === null ? '<span class="muted">未评分</span>' : s.avg_score}</td>
        </tr>`).join('')}</tbody></table>`
        : '<div class="empty" style="padding:26px"><p>暂无学生</p></div>'}
    </div>`;
}

async function renderAttempts(box) {
  const d = await get('/api/my/attempts');
  box.innerHTML = `
    <div class="page-head"><h1>我的练习记录</h1><p>点进去可以重新看每道题的解析。</p></div>
    ${d.attempts.length ? `<div class="card"><table>
      <thead><tr><th>试卷</th><th>课程</th><th>得分</th><th>正确率</th><th>时间</th><th></th></tr></thead>
      <tbody>${d.attempts.map(a => `<tr>
        <td>${esc(a.quiz_title)}</td><td class="muted">${esc(a.course)}</td>
        <td><b>${a.score}</b> / ${a.total}</td>
        <td><div class="bar ${a.accuracy < 60 ? 'warn' : ''}"><i style="width:${Math.min(100, a.accuracy)}%"></i></div>
          <span class="muted" style="font-size:12.5px">${a.accuracy}%</span></td>
        <td class="muted">${fmtTime(a.submitted_at)}</td>
        <td><button class="btn sm" onclick="location.hash='#/attempt/${a.id}'">看解析</button></td>
      </tr>`).join('')}</tbody></table></div>`
      : '<div class="card empty"><div class="big">📊</div><p>还没有练习记录</p></div>'}`;
}

async function renderAttempt(aid) {
  const a = await get(`/api/attempts/${aid}`);
  const st = { submitted: true, review: a.review, answers: {}, score: a.score, total: a.total,
    accuracy: a.accuracy, summary: a.summary,
    quiz: { id: a.quiz_id || 0, title: a.quiz_title, course_id: a.course_id,
            source: 'manual', questions: a.review.map(r => ({ ...r })) } };
  a.review.forEach(r => { st.answers[String(r.id)] = r.given; });
  State.currentQuiz = st;
  const box = $('#content');
  box.innerHTML = `
    <div class="crumb"><a href="#/attempts">练习记录</a> / 解析</div>
    <div class="page-head spread">
      <div><h1>${esc(a.quiz_title)}</h1><p>${fmtTime(a.submitted_at)} 提交</p></div>
      <div class="card stat-card" style="padding:12px 20px">
        <div class="num" style="color:var(--cyan)">${a.score} / ${a.total}</div>
        <div class="lbl">正确率 ${a.accuracy}%</div></div>
    </div>
    ${summaryHtml(a.summary)}
    <div>${a.review.map((q, i) => questionHtml(q, i, st)).join('')}</div>
    <div class="quizbar">
      <button class="btn" onclick="location.hash='#/attempts'">返回列表</button>
      ${a.quiz_id ? `<button class="btn primary" onclick="location.hash='#/quiz/${a.quiz_id}'">
        🔄 再练一遍这套题</button>` : ''}
    </div>`;
  bindJump(box);
}

/* ---------- 设置 ---------- */
async function renderSettings(box) {
  const me = await get('/api/me');
  const provs = State.providers;
  box.innerHTML = `
    <div class="page-head"><h1>设置</h1><p>配置你自己的 AI API Key —— 填了就<b>不限出题次数</b>。</p></div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:8px">账号</h3>
      <div class="kv"><span>姓名：<b>${esc(me.name)}</b></span>
        <span>身份：<b>${me.role === 'teacher' ? '教师' : '学生'}${me.is_admin ? ' · 平台管理员' : ''}</b></span>
        <span>邮箱：${esc(me.email)}</span>
        ${me.school ? `<span>单位：${esc(me.school)}</span>` : ''}</div>
    </div>

    <div class="card">
      <h3 style="font-size:17px;margin-bottom:8px">出题额度</h3>
      <div class="kv" style="margin-bottom:16px">
        <span>状态：<b>${me.quota.unlimited
          ? (me.quota.reason === 'admin' ? '无限次（平台管理员）' : '无限次（使用自己的 Key）')
          : `每日 ${me.quota.limit} 套，今日已用 ${me.quota.used}，剩余 ${me.quota.remaining}`}</b></span>
        <span>当前 Key 来源：<b>${me.quota.has_own_key ? '你自己的'
          : (me.is_admin ? '平台默认（管理员无限）' : '教师 / 平台默认')}</b></span>
      </div>

      <h3 style="font-size:15.5px;margin:18px 0 10px">我的 AI 配置</h3>
      <label>服务商
        <select id="aiProv">${provs.map(p =>
          `<option value="${p.id}" ${p.id === me.ai.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}
        </select></label>
      <label>API Key
        <input id="aiKey" type="password" placeholder="${me.ai.has_key ? '已保存：' + esc(me.ai.key_masked) + '（留空则不改动）' : 'sk-... 留空则使用教师/平台额度'}">
      </label>
      <div class="row2">
        <label>Base URL（自定义服务商必填）<input id="aiBase" value="${esc(me.ai.base_url)}" placeholder="https://api.deepseek.com/v1"></label>
        <label>模型名<input id="aiModel" value="${esc(me.ai.model)}" placeholder="deepseek-chat"></label>
      </div>
      <div class="hstack" style="margin-top:6px">
        <button class="btn primary" id="saveAi">保存</button>
        <button class="btn danger" id="clearAi">清除我的 Key（回到每日限额）</button>
      </div>
      <div class="hint" style="border-top:1px dashed var(--brd);margin-top:18px;padding-top:14px;font-size:13px;color:var(--dim2);line-height:1.9">
        🔒 Key 使用对称加密后存库，接口只回显后 4 位，前端拿不到完整内容。<br>
        常见服务商地址：DeepSeek <code>https://api.deepseek.com/v1</code> ・
        OpenAI <code>https://api.openai.com/v1</code> ・
        通义 <code>https://dashscope.aliyuncs.com/compatible-mode/v1</code> ・
        智谱 <code>https://open.bigmodel.cn/api/paas/v4</code>
      </div>
    </div>`;

  $('#saveAi', box).addEventListener('click', async () => {
    const key = $('#aiKey', box).value.trim();
    try {
      await post('/api/me/ai-key', {
        provider: $('#aiProv', box).value,
        api_key: key,
        base_url: $('#aiBase', box).value.trim(),
        model: $('#aiModel', box).value.trim(),
      });
      toast(key ? '已保存，额度已解除限制' : '已保存', 'ok');
      router();
    } catch (e) { toast(e.message, 'err'); }
  });

  $('#clearAi', box).addEventListener('click', async () => {
    if (!confirm('确定清除自己的 Key？之后将回到每日限额。')) return;
    try {
      await post('/api/me/ai-key', {
        provider: $('#aiProv', box).value, api_key: '',
        base_url: $('#aiBase', box).value.trim(), model: $('#aiModel', box).value.trim(),
      });
      toast('已清除', 'ok'); router();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ========================= 管理员后台 ========================= */
async function renderAdmin(box, sub) {
  const tab = typeof sub === 'string' ? sub : 'stats';
  const tabs = [['stats', '📊 总览'], ['ai', '🔑 平台 AI Key'],
                ['users', '👥 用户'], ['courses', '📚 课程']];
  box.innerHTML = `
    <div class="page-head">
      <h1>🛡️ 平台管理</h1>
      <p>管理员可查看并管理全平台的数据。</p>
    </div>
    <div class="ctabs">${tabs.map(([k, l]) =>
      `<button data-atab="${k}" class="${k === tab ? 'active' : ''}">${l}</button>`).join('')}</div>
    <div id="adminBody"><div class="loading"><span class="spin"></span></div></div>`;
  $$('[data-atab]', box).forEach(b =>
    b.addEventListener('click', () => location.hash = `#/admin/${b.dataset.atab}`));
  const body = $('#adminBody', box);
  if (tab === 'users') return adminUsers(body);
  if (tab === 'courses') return adminCourses(body);
  if (tab === 'ai') return adminPlatformAi(body);
  return adminStats(body);
}

/* ---------- 管理员：配置平台默认 API Key ---------- */
async function adminPlatformAi(body) {
  const [cfg, provs] = await Promise.all([
    get('/api/admin/platform-ai'),
    get('/api/ai/providers').then(d => d.providers).catch(() => State.providers || []),
  ]);
  const srcText = cfg.source === 'db' ? '数据库（管理员上传）'
    : cfg.source === 'env' ? '环境变量兜底' : '未配置';

  body.innerHTML = `
    <div class="card">
      <div class="spread" style="margin-bottom:14px">
        <h3 style="font-size:17px">🔑 平台默认 AI API Key</h3>
        <div class="hstack">
          <span class="tag" style="color:${cfg.configured && cfg.enabled ? '#34d399' : '#fbbf24'};
            border-color:${cfg.configured && cfg.enabled ? '#2f6d51' : '#6b5518'}">
            ${cfg.configured ? (cfg.enabled ? '已配置 · 启用中' : '已配置 · 已停用') : '未配置'}</span>
        </div>
      </div>
      <p class="muted" style="font-size:14px;margin-bottom:18px">
        这里配置的 Key 是<b>平台默认 Key</b>，<b>对所有用户生效</b>：
        教师和学生用它出题时每日限 <b>${cfg.daily_limit}</b> 套，
        管理员使用它<b>不受次数限制</b>；用户也可以填自己的 Key 来解除限制。<br>
        当前来源：<b>${srcText}</b>${cfg.key_masked ? ' · Key：' + esc(cfg.key_masked) : ''}
        · 今日平台调用 <b>${cfg.usage_today}</b> 次
      </p>

      <div class="row2">
        <label>服务商
          <select id="pfProv">${provs.map(p =>
            `<option value="${p.id}" ${p.id === cfg.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}
          </select></label>
        <label>模型名（留空用默认）
          <input id="pfModel" value="${esc(cfg.model)}" placeholder="deepseek-chat"></label>
      </div>
      <label>API Key
        <input id="pfKey" type="password"
          placeholder="${cfg.configured ? '已配置：' + esc(cfg.key_masked) + '（留空则不改动）' : '粘贴平台 Key，如 sk-...'}">
      </label>
      <label>Base URL（自定义服务商必填，留空用服务商默认）
        <input id="pfBase" value="${esc(cfg.base_url)}" placeholder="https://api.deepseek.com/v1"></label>
      <div class="row2">
        <label>教师/学生每日额度（套）
          <input id="pfLimit" type="number" min="1" max="200" value="${cfg.daily_limit}"></label>
        <label>启用状态
          <select id="pfEnabled">
            <option value="1" ${cfg.enabled ? 'selected' : ''}>启用（所有用户可用）</option>
            <option value="0" ${cfg.enabled ? '' : 'selected'}>停用（不对外提供）</option>
          </select></label>
      </div>
      <div class="hstack" style="margin-top:6px">
        <button class="btn primary" id="pfSave">保存配置</button>
        <button class="btn" id="pfTest">🧪 测试连通性</button>
        <button class="btn danger" id="pfClear">清除平台 Key</button>
      </div>
      <div id="pfMsg" class="msg"></div>

      <div class="hint" style="border-top:1px dashed var(--brd);margin-top:18px;padding-top:14px;
        font-size:13px;color:var(--dim2);line-height:1.9">
        🔒 Key 使用对称加密后存库，接口只回显后 4 位，任何用户（含管理员）都无法通过接口取回明文。<br>
        常见服务商：DeepSeek <code>https://api.deepseek.com/v1</code> ·
        OpenAI <code>https://api.openai.com/v1</code> ·
        通义 <code>https://dashscope.aliyuncs.com/compatible-mode/v1</code> ·
        智谱 <code>https://open.bigmodel.cn/api/paas/v4</code> ·
        Kimi <code>https://api.moonshot.cn/v1</code>
        ${cfg.env_fallback_available ? '<br>ℹ️ 检测到环境变量 <code>LEARNHUB_PLATFORM_API_KEY</code>，未上传时会用它兜底。' : ''}
      </div>
    </div>`;

  const msg = $('#pfMsg', body);
  const payload = () => ({
    provider: $('#pfProv', body).value,
    api_key: $('#pfKey', body).value.trim(),
    base_url: $('#pfBase', body).value.trim(),
    model: $('#pfModel', body).value.trim(),
    enabled: $('#pfEnabled', body).value === '1',
    daily_limit: Number($('#pfLimit', body).value) || 3,
  });

  $('#pfSave', body).addEventListener('click', async () => {
    msg.className = 'msg'; msg.innerHTML = '<span class="spin"></span> 保存中…';
    try {
      const r = await post('/api/admin/platform-ai', payload());
      msg.className = 'msg ok';
      msg.textContent = r.configured
        ? `已保存：${r.key_masked}，所有用户现在可以使用平台 Key（每日 ${r.daily_limit} 套）`
        : '已保存，但尚未配置 Key（用户将得到离线模拟题）';
      toast('平台 AI 配置已保存', 'ok');
      setTimeout(() => adminPlatformAi(body), 900);
    } catch (e) { msg.className = 'msg err'; msg.textContent = e.message; }
  });

  $('#pfTest', body).addEventListener('click', async () => {
    msg.className = 'msg'; msg.innerHTML = '<span class="spin"></span> 正在调用 AI 服务测试…';
    try {
      const r = await post('/api/admin/platform-ai/test', {});
      msg.className = 'msg ok';
      msg.textContent = `✅ 连通成功：${r.provider} · ${r.model} · 模型回复「${r.reply}」`;
    } catch (e) { msg.className = 'msg err'; msg.textContent = '❌ ' + e.message; }
  });

  $('#pfClear', body).addEventListener('click', async () => {
    if (!confirm('清除平台 Key？清除后所有教师/学生将只能得到离线模拟题（可以填自己的 Key）。')) return;
    try {
      await post('/api/admin/platform-ai', { ...payload(), api_key: '', clear_key: true });
      toast('已清除平台 Key', 'ok');
      adminPlatformAi(body);
    } catch (e) { toast(e.message, 'err'); }
  });
}

async function adminStats(body) {
  const s = await get('/api/admin/stats');
  const cell = (n, l) => `<div class="card stat-card"><div class="num">${n}</div><div class="lbl">${l}</div></div>`;
  body.innerHTML = `
    <div class="grid g4">
      ${cell(s.users, '用户总数')}${cell(s.teachers, '教师')}
      ${cell(s.students, '学生')}${cell(s.admins, '管理员')}
    </div>
    <div class="grid g4" style="margin-top:16px">
      ${cell(s.courses, '课程')}${cell(s.knowledge_points, '知识点')}
      ${cell(s.quiz_sets, '试题套数')}${cell(s.questions, '题目总数')}
    </div>
    <div class="grid g4" style="margin-top:16px">
      ${cell(s.assignments, '作业')}${cell(s.submissions, '作业提交')}
      ${cell(s.attempts, '答题记录')}${cell(s.ai_generations_today, '今日 AI 调用')}
    </div>
    <div class="card" style="margin-top:16px">
      <h3 style="font-size:16px;margin-bottom:10px">平台 AI 配置</h3>
      <div class="kv">
        <span>平台默认 Key：<b>${s.platform_key_configured ? '已配置' : '未配置（走离线模拟题）'}</b></span>
        <span>教师/学生每日额度：<b>${s.daily_ai_limit} 套</b></span>
        <span>管理员：<b>不受次数限制</b></span>
      </div>
      <p class="muted" style="font-size:13.5px;margin-top:12px">
        平台 Key 由管理员在「🔑 平台 AI Key」页面上传，对所有用户生效。</p>
    </div>`;
}

async function adminUsers(body) {
  const d = await get('/api/admin/users');
  body.innerHTML = `
    <div class="spread" style="margin-bottom:14px">
      <input id="uSearch" placeholder="搜索姓名 / 邮箱 / 学号" style="max-width:320px">
      <div class="muted">共 ${d.users.length} 个用户</div>
    </div>
    <div class="card"><table>
      <thead><tr><th>ID</th><th>姓名</th><th>邮箱</th><th>角色</th><th>状态</th>
        <th>课程</th><th>答题</th><th>操作</th></tr></thead>
      <tbody>${d.users.map(u => `<tr>
        <td class="muted">${u.id}</td>
        <td>${esc(u.name)}${u.is_admin ? ' <span class="pill">管理员</span>' : ''}
          ${u.has_own_key ? ' <span class="pill" title="已配置自己的 API Key">🔑</span>' : ''}</td>
        <td class="muted">${esc(u.email)}</td>
        <td><span class="tag">${u.role === 'teacher' ? '教师' : '学生'}</span></td>
        <td>${u.is_active ? '<span style="color:var(--green)">正常</span>'
          : '<span style="color:var(--red)">已停用</span>'}</td>
        <td>${u.course_count}</td><td>${u.attempt_count}</td>
        <td><div class="hstack">
          <button class="btn sm" data-role="${u.id}" data-cur="${u.role}">改角色</button>
          <button class="btn sm" data-adm="${u.id}" data-cur="${u.is_admin ? 1 : 0}">
            ${u.is_admin ? '取消管理员' : '设为管理员'}</button>
          <button class="btn sm" data-act="${u.id}" data-cur="${u.is_active ? 1 : 0}">
            ${u.is_active ? '停用' : '启用'}</button>
          <button class="btn sm danger" data-delu="${u.id}" data-name="${esc(u.name)}">删除</button>
        </div></td></tr>`).join('')}</tbody>
    </table></div>`;

  $('#uSearch', body).addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    const q = e.target.value.trim();
    const r = await get('/api/admin/users' + (q ? '?q=' + encodeURIComponent(q) : ''));
    await adminUsers(body);
    if (q) { $('#uSearch', body).value = q; toast(`找到 ${r.users.length} 个用户`); }
  });

  const refresh = async () => { await adminUsers(body); };
  $$('[data-role]', body).forEach(b => b.addEventListener('click', async () => {
    const next = b.dataset.cur === 'teacher' ? 'student' : 'teacher';
    if (!confirm(`把该用户角色改为「${next === 'teacher' ? '教师' : '学生'}」？`)) return;
    try { await patch(`/api/admin/users/${b.dataset.role}`, { role: next }); toast('已修改', 'ok'); refresh(); }
    catch (e) { toast(e.message, 'err'); }
  }));
  $$('[data-adm]', body).forEach(b => b.addEventListener('click', async () => {
    const next = b.dataset.cur !== '1';
    if (!confirm(next ? '设为平台管理员？' : '取消管理员权限？')) return;
    try { await patch(`/api/admin/users/${b.dataset.adm}`, { is_admin: next }); toast('已修改', 'ok'); refresh(); }
    catch (e) { toast(e.message, 'err'); }
  }));
  $$('[data-act]', body).forEach(b => b.addEventListener('click', async () => {
    const next = b.dataset.cur !== '1';
    try { await patch(`/api/admin/users/${b.dataset.act}`, { is_active: next });
      toast(next ? '已启用' : '已停用', 'ok'); refresh(); }
    catch (e) { toast(e.message, 'err'); }
  }));
  $$('[data-delu]', body).forEach(b => b.addEventListener('click', async () => {
    if (!confirm(`删除用户「${b.dataset.name}」？其课程与答题记录会一并删除，不可恢复！`)) return;
    try { await del(`/api/admin/users/${b.dataset.delu}`); toast('已删除', 'ok'); refresh(); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

async function adminCourses(body) {
  const d = await get('/api/admin/courses');
  body.innerHTML = `
    <div class="spread" style="margin-bottom:14px">
      <div class="muted">共 ${d.courses.length} 门课程</div>
    </div>
    <div class="card"><table>
      <thead><tr><th>ID</th><th>课程</th><th>教师</th><th>邀请码</th>
        <th>学生</th><th>知识点</th><th>作业/提交</th><th>试题</th><th>操作</th></tr></thead>
      <tbody>${d.courses.map(c => `<tr>
        <td class="muted">${c.id}</td>
        <td>${esc(c.title)}</td>
        <td>${esc(c.teacher)}</td>
        <td><span class="pill">${esc(c.join_code)}</span></td>
        <td>${c.student_count}</td><td>${c.kp_count}</td>
        <td>${c.assignment_count} / ${c.submission_count}</td>
        <td>${c.quiz_count}</td>
        <td><div class="hstack">
          <button class="btn sm" onclick="location.hash='#/course/${c.id}'">进入</button>
          <button class="btn sm danger" data-delc="${c.id}" data-title="${esc(c.title)}">删除</button>
        </div></td></tr>`).join('')}</tbody>
    </table></div>`;
  $$('[data-delc]', body).forEach(b => b.addEventListener('click', async () => {
    if (!confirm(`删除课程「${b.dataset.title}」？其知识点、作业、试题、学生提交会一并删除，不可恢复！`)) return;
    try { await del(`/api/admin/courses/${b.dataset.delc}`); toast('已删除', 'ok');
      await adminCourses(body); }
    catch (e) { toast(e.message, 'err'); }
  }));
}

/* ========================= 启动 ========================= */
(async function start() {
  if (State.token) {
    try { await boot(); return; } catch { State.token = ''; localStorage.removeItem('lh_token'); }
  }
  $('#authView').classList.remove('hidden');
})();
