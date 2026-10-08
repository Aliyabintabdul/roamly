// Shared browser helpers (identical copy in every project): API calls with CSRF,
// escaping, toasts, dialogs, form errors, loading states and hash routing.
const Lib = (() => {
  let csrf = '';
  let onUnauthorized = () => {};
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  async function api(path, method = 'GET', body) {
    const headers = {};
    if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = csrf; }
    let res;
    try { res = await fetch('/api/' + path, { method, headers, body: method === 'GET' ? undefined : JSON.stringify(body || {}), credentials: 'same-origin' }); }
    catch { throw Object.assign(new Error('Cannot reach the server. Check that it is running.'), { fields: {} }); }
    const data = await res.json().catch(() => ({ error: 'Unexpected response from server.' }));
    if (res.status === 401 && !['login', 'register', 'session'].includes(path)) onUnauthorized();
    if (!res.ok) throw Object.assign(new Error(data.error || 'Request failed.'), { status: res.status, code: data.code, fields: data.fields || {} });
    if (data && data.csrf) csrf = data.csrf;
    return data;
  }

  function toast(msg, kind = 'ok') {
    let t = $('#toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.setAttribute('role', 'status'); t.setAttribute('aria-live', 'polite'); document.body.append(t); }
    t.textContent = msg; t.className = 'show ' + kind;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => t.className = '', 3800);
  }

  function dialog(html, { className = '' } = {}) {
    const d = document.createElement('dialog');
    d.className = 'dlg ' + className; d.innerHTML = html;
    document.body.append(d);
    d.addEventListener('close', () => d.remove());
    d.addEventListener('click', e => { if (e.target.matches('[data-close]')) d.close(); });
    d.showModal();
    const first = d.querySelector('input:not([type=hidden]),select,textarea,button:not([data-close])');
    if (first) first.focus();
    return d;
  }

  function confirmBox(message, { title = 'Are you sure?', confirm = 'Delete', danger = true } = {}) {
    return new Promise(resolve => {
      const d = dialog(`<form method="dialog" class="confirm"><h2>${esc(title)}</h2><p>${esc(message)}</p>
        <div class="dlg-actions"><button value="no" class="btn ghost">Cancel</button><button value="yes" class="btn ${danger ? 'danger' : 'primary'}">${esc(confirm)}</button></div></form>`);
      d.addEventListener('close', () => resolve(d.returnValue === 'yes'));
      d.querySelector('[value=no]').focus();
    });
  }

  const formData = form => Object.fromEntries([...new FormData(form)].map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]));

  function showErrors(form, err) {
    $$('[aria-invalid]', form).forEach(el => el.removeAttribute('aria-invalid'));
    $$('.field-error', form).forEach(el => el.remove());
    const box = $('.form-error', form);
    if (box) box.textContent = err ? err.message : '';
    for (const [name, msg] of Object.entries((err && err.fields) || {})) {
      const input = form.elements[name];
      if (!input || !input.after) continue;
      input.setAttribute('aria-invalid', 'true');
      const s = document.createElement('small'); s.className = 'field-error'; s.textContent = msg; s.id = 'err-' + name;
      input.setAttribute('aria-describedby', s.id); input.after(s);
    }
    const bad = $('[aria-invalid]', form); if (bad) bad.focus();
  }

  // Submit a form through the API with loading state and field errors.
  function bindForm(form, submit) {
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const btn = form.querySelector('button[type=submit],button:not([type])');
      if (btn) { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = 'Saving…'; }
      try { showErrors(form, null); await submit(formData(form)); }
      catch (err) { showErrors(form, err); }
      finally { if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = btn.dataset.label; } }
    });
  }

  const money = (n, cur = '') => (cur ? cur + ' ' : '') + Number(n || 0).toLocaleString('en', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const today = () => new Date().toLocaleDateString('en-CA');
  const addDays = (iso, n) => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return d.toLocaleDateString('en-CA'); };
  const fmtDate = (iso, opts = { day: 'numeric', month: 'short', year: 'numeric' }) => iso ? new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toLocaleDateString('en-GB', opts) : '—';
  const fmtStamp = iso => iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const qs = obj => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== '' && v != null)).toString();
  const options = (list, sel) => list.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${esc(l)}</option>`; }).join('');
  const route = () => location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  function download(path) { const a = document.createElement('a'); a.href = '/api/' + path; a.download = ''; document.body.append(a); a.click(); a.remove(); }
  const loading = (label = 'Loading') => `<div class="loading" role="status"><span class="spinner" aria-hidden="true"></span>${esc(label)}…</div>`;

  // Skip links must not change the hash (the hash is the router), so focus main directly.
  document.addEventListener('click', e => { const a = e.target.closest('a.skip'); if (a) { e.preventDefault(); const m = $('#main'); if (m) m.focus(); } });

  return { api, esc, $, $$, toast, dialog, confirmBox, formData, showErrors, bindForm, money, today, addDays, fmtDate, fmtStamp, qs, options, route, download, loading,
    setUnauthorized: fn => onUnauthorized = fn, setCsrf: t => csrf = t };
})();
