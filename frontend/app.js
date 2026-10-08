// Roamly — travel journal UI
const { api, esc, $, toast, dialog, confirmBox, bindForm, money, today, addDays, fmtDate, qs, options, route, download, loading } = Lib;
const root = $('#app');
let me = null, meta = null, tripTab = 'itinerary';
const filters = { q: '', when: '' };
Lib.setUnauthorized(() => { if (me) { me = null; renderAuth('Your session ended. Please sign in again.'); } });

const SUN = `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="22" r="9" fill="#c8902e"/><path d="M4 30h32" stroke="#3b2a1f" stroke-width="3" stroke-linecap="round"/><path d="M20 5v5M8 10l3 3M32 10l-3 3" stroke="#b9512c" stroke-width="3" stroke-linecap="round"/></svg>`;
const LANDSCAPE = `<svg viewBox="0 0 520 300" role="img" aria-label="Illustration of hills, a sun and a dotted flight path">
  <circle cx="390" cy="110" r="52" fill="#e9b56a"/><path d="M60 120 C160 40 260 60 330 90 S460 40 500 30" fill="none" stroke="#b9512c" stroke-width="3" stroke-dasharray="2 10" stroke-linecap="round"/>
  <path d="M488 22l18 6-14 12z" fill="#3b2a1f"/><path d="M0 220 Q90 150 190 205 T380 190 T520 170 V300 H0Z" fill="#d98b5f"/>
  <path d="M0 250 Q120 200 240 245 T520 225 V300 H0Z" fill="#b9512c"/><path d="M0 280 Q160 250 300 275 T520 265 V300 H0Z" fill="#5f6f33"/>
  <g fill="#3b2a1f"><rect x="80" y="232" width="6" height="22"/><circle cx="83" cy="226" r="12" fill="#5f6f33"/><rect x="440" y="220" width="6" height="24"/><circle cx="443" cy="212" r="14" fill="#5f6f33"/></g></svg>`;

function renderAuth(notice = '', mode = 'login') {
  const reg = mode === 'register';
  root.innerHTML = `<div class="auth"><section class="auth-art"><span class="brand" style="color:#fff3e8">${SUN} roamly</span><h1>Less planning.<br>More going.</h1>${LANDSCAPE}
    <p>Plan day-by-day itineraries and keep your estimated budget honest against what you actually spend. Roamly never books flights or hotels for you.</p></section>
    <section class="auth-form"><h2>${reg ? 'Start a travel journal' : 'Welcome back, traveller'}</h2><form id="auth-form" novalidate>
    <label>Email<input type="email" name="email" autocomplete="username" required></label>
    <label>Password<input type="password" name="password" minlength="10" required autocomplete="${reg ? 'new-password' : 'current-password'}"></label>
    <p class="form-error" role="alert">${esc(notice)}</p><button class="btn primary" type="submit">${reg ? 'Create account' : 'Sign in'}</button></form>
    <button class="btn ghost" data-auth="${reg ? 'login' : 'register'}">${reg ? 'I already have an account' : 'Create a new account'}</button>
    <p class="hint">${reg ? 'Use at least 10 characters. Your trips stay private to your account.' : 'Seeded demo (if enabled): demo@example.com · DemoPass123!'}</p></section></div>`;
  $('[data-auth]').onclick = e => renderAuth('', e.target.dataset.auth);
  bindForm($('#auth-form'), async data => { me = (await api(reg ? 'register' : 'login', 'POST', data)).user; meta = await api('meta'); location.hash = '#/'; render(); });
  $('#auth-form input').focus();
}

function shell(page) {
  root.innerHTML = `<a class="skip" href="#main">Skip to content</a><header class="top"><a class="brand" href="#/">${SUN}roamly</a>
    <nav class="tabs" aria-label="Main"><a href="#/" ${page !== 'new' ? 'aria-current="page"' : ''}>My trips</a><a href="#/" data-act="new-trip">+ Plan a trip</a></nav>
    <div class="user"><span class="email">${esc(me.email)}</span><button class="btn sm" data-act="logout">Sign out</button></div></header>
    ${me.is_demo ? '<div class="demo"><p>Demo journal — these trips, activities and expenses are fictional sample data.</p></div>' : ''}<main id="main" tabindex="-1">${loading('Unpacking')}</main>`;
  return $('#main');
}

async function render() {
  if (!me) return renderAuth();
  const [page = '', id] = route();
  const main = shell(page);
  try {
    if (page === '') await viewTrips(main);
    else if (page === 'trips' && id) await viewTrip(main, +id);
    else main.innerHTML = '<div class="empty"><h2>Off the map</h2><p>This page does not exist.</p><a class="btn" href="#/">Back to my trips</a></div>';
  } catch (e) { if (e.status !== 401) main.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p><a class="btn" href="#/">Back to my trips</a></div>`; }
}

const nights = t => Math.round((new Date(t.end_date) - new Date(t.start_date)) / 864e5);
const range = t => `${fmtDate(t.start_date, { day: 'numeric', month: 'short' })} – ${fmtDate(t.end_date)}`;
const badge = s => `<span class="badge b-${esc(s.split(' ')[0])}">${esc(s)}</span>`;
function meter(t) {
  const cap = Math.max(t.budget || 0, t.estimated, t.spent, 1);
  return `<div class="meter"><div class="track" role="img" aria-label="Estimated ${money(t.estimated, t.currency)}, spent ${money(t.spent, t.currency)}${t.budget != null ? ', budget ' + money(t.budget, t.currency) : ''}">
    <i class="act" style="width:${t.spent / cap * 100}%"></i><i class="est" style="width:${Math.max(t.estimated - t.spent, 0) / cap * 100}%"></i></div>
    <span>Est. ${money(t.estimated, t.currency)} · Spent ${money(t.spent, t.currency)}${t.budget != null ? ` · Budget ${money(t.budget, t.currency)}` : ''}${t.budget != null && t.spent > t.budget ? ' · <span class="over">over budget</span>' : ''}</span></div>`;
}

async function viewTrips(main) {
  const d = await api('dashboard');
  const n = d.next;
  main.innerHTML = `<section class="hero"><div class="copy"><span class="phase">${n ? (n.phase === 'current' ? 'Happening now' : 'Next adventure') : 'Your travel journal'}</span>
    <h1>${n ? `${esc(n.destination)}<br><em>${n.phase === 'current' ? 'right now' : 'in ' + Math.max(Math.round((new Date(n.start_date) - new Date(today())) / 864e5), 0) + ' days'}</em>` : 'Where to <em>next?</em>'}</h1>
    <p>${n ? `${range(n)} · ${n.activity_count} planned stop${n.activity_count === 1 ? '' : 's'}. ${d.unbooked ? `${d.unbooked} upcoming activit${d.unbooked === 1 ? 'y is' : 'ies are'} still unbooked.` : 'Everything upcoming is booked or needs no booking.'}` : 'Plan your first trip: add destination, dates and a currency, then build each day.'}</p>
    ${n ? `<a class="btn primary" href="#/trips/${n.id}">Open itinerary</a>` : '<button class="btn primary" data-act="new-trip">Plan a trip</button>'}
    ${d.totals.length ? `<div class="sums">${d.totals.map(t => `<div class="sum">${t.trips} trip${t.trips === 1 ? '' : 's'} in ${t.currency}<b>${money(t.spent, t.currency)} spent</b>est. ${money(t.estimated)}${t.budget ? ' · budget ' + money(t.budget) : ''}</div>`).join('')}</div>` : ''}</div>${LANDSCAPE}</section>
    <div class="chips" role="group" aria-label="Filter trips">${[['', 'All trips'], ['upcoming', 'Upcoming'], ['current', 'Happening now'], ['past', 'Past']].map(([v, l]) => `<button class="chip" data-when="${v}" aria-pressed="${filters.when === v}">${l}</button>`).join('')}
    <label class="sr" for="trip-q">Search trips</label><input id="trip-q" type="search" data-filter="q" value="${esc(filters.q)}" placeholder="Search destination or name">
    <button class="btn sm" data-act="export">Export CSV</button></div><div id="results"></div>`;
  main.refresh = async () => {
    const list = await api('trips?' + qs(filters));
    $('#results').innerHTML = list.length ? `<div class="cards">${list.map(t => `<a class="postcard" href="#/trips/${t.id}"><span class="stamp" aria-hidden="true"><span>${esc(t.currency)}<small>${nights(t) + 1} DAYS</small></span></span>
      <span class="phase ${t.phase}">${t.phase === 'current' ? 'Happening now' : t.phase}</span><h2>${esc(t.destination)}</h2>
      <span class="where">${esc(t.name)}${t.country ? ' · ' + esc(t.country) : ''}<br>${range(t)}</span>${meter(t)}</a>`).join('')}</div>`
      : `<div class="empty"><h2>${filters.q || filters.when ? 'No trips match' : 'No trips yet'}</h2><p>${filters.q || filters.when ? 'Try a different filter.' : 'Every journey starts with a destination and some dates.'}</p><button class="btn primary" data-act="new-trip">Plan a trip</button></div>`;
  };
  await main.refresh();
}

async function viewTrip(main, id) {
  const t = await api('trips/' + id);
  main.trip = t;
  const days = Array.from({ length: nights(t) + 1 }, (_, i) => addDays(t.start_date, i));
  main.innerHTML = `<div class="trip-head"><a href="#/" class="hint">← All trips</a><span class="phase ${t.phase}">${t.phase === 'current' ? 'Happening now' : t.phase} · ${esc(t.name)}</span><h1>${esc(t.destination)}</h1>
    <div class="facts"><span><b>${range(t)}</b> · ${nights(t) + 1} days</span>${t.country ? `<span>${esc(t.country)}</span>` : ''}<span>Currency <b>${esc(t.currency)}</b></span>${t.timezone ? `<span>Local time: <b>${esc(t.timezone)}</b></span>` : ''}</div>
    <p class="explain">All times are <b>local to ${esc(t.destination)}</b> exactly as entered — Roamly does not convert time zones. All amounts are in <b>${esc(t.currency)}</b>; convert anything paid in another currency before recording it.</p>
    <div class="acts"><button class="btn" data-act="edit-trip">Edit trip</button><button class="btn" data-act="export-trip">Export itinerary CSV</button><button class="btn danger" data-act="delete-trip">Delete trip</button></div></div>
    <div class="subnav" role="tablist">${[['itinerary', 'Day by day'], ['budget', 'Budget & expenses'], ['notes', 'Notes']].map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${tripTab === k}">${l}</button>`).join('')}</div>
    <section id="tab" role="tabpanel"></section>`;
  const tab = $('#tab');
  if (tripTab === 'itinerary') {
    tab.innerHTML = days.map((day, i) => {
      const acts = t.activities.filter(a => a.day === day), est = acts.reduce((s, a) => s + a.est_cost, 0);
      return `<section class="day" aria-label="Day ${i + 1}"><div class="day-label"><div class="n">Day ${i + 1}</div><div class="d">${fmtDate(day, { weekday: 'long', day: 'numeric', month: 'short' })}</div><div class="c">${acts.length ? `est. ${money(est, t.currency)}` : 'Free day'}</div></div>
        <ol class="timeline">${acts.map(a => `<li class="stop"><div><div class="time">${a.start_time ? esc(a.start_time) + (a.end_time ? '–' + esc(a.end_time) : '') : 'Any time'} <span class="hint">· ${esc(a.category)}</span></div><h3>${esc(a.title)}</h3>${a.location ? `<div class="loc">⌖ ${esc(a.location)}</div>` : ''}${a.notes ? `<div class="notes">${esc(a.notes)}</div>` : ''}</div>
          <div class="right">${badge(a.booking_status)}<span>${a.est_cost ? money(a.est_cost, t.currency) : 'Free'}</span>${a.booking_ref ? `<span class="hint">Ref ${esc(a.booking_ref)}</span>` : ''}</div>
          <div class="acts"><button class="btn sm" data-edit-act="${a.id}">Edit</button><button class="btn sm danger" data-del-act="${a.id}">Delete</button></div></li>`).join('')}
        <li><button class="add-stop" data-add-act="${day}">+ Add to day ${i + 1}</button></li></ol></section>`;
    }).join('');
  } else if (tripTab === 'budget') {
    const remaining = t.budget != null ? t.budget - t.spent : null, maxCat = Math.max(...t.spent_by_category.map(c => c.total), 1);
    tab.innerHTML = `<div class="tiles"><div class="tile"><span>Planned budget</span><b>${t.budget != null ? money(t.budget, t.currency) : '—'}</b><small>The cap you set for this trip</small></div>
      <div class="tile est"><span>Estimated</span><b>${money(t.estimated, t.currency)}</b><small>Sum of activity estimates</small></div>
      <div class="tile act"><span>Actual spent</span><b>${money(t.spent, t.currency)}</b><small>${t.expenses.length} recorded expense${t.expenses.length === 1 ? '' : 's'}</small></div>
      <div class="tile"><span>Left in budget</span><b class="${remaining < 0 ? 'meter over' : ''}">${remaining != null ? money(remaining, t.currency) : '—'}</b><small>Budget minus actual spending</small></div></div>
      <div class="split"><div class="panel"><h2>Spent by category</h2>${t.spent_by_category.map(c => `<div class="catbar"><span>${esc(c.category)}</span><b>${money(c.total, t.currency)}</b><span class="t"><i style="width:${c.total / maxCat * 100}%"></i></span></div>`).join('') || '<p class="hint">No expenses yet.</p>'}
        <h2 style="margin-top:18px">Estimates by booking</h2>${t.estimated_by_booking.map(b => `<div class="catbar"><span>${badge(b.booking_status)} ${b.n}</span><b>${money(b.total, t.currency)}</b></div>`).join('') || '<p class="hint">No activities yet.</p>'}</div>
      <div class="panel"><h2>Expenses <button class="btn sm primary" data-act="new-expense">+ Record expense</button></h2>${t.expenses.length ? `<table class="stack"><thead><tr><th>Date</th><th>Description</th><th>Category</th><th class="num">${esc(t.currency)}</th><th><span class="sr">Actions</span></th></tr></thead><tbody>
        ${t.expenses.map(e => `<tr><td>${fmtDate(e.spent_on, { day: 'numeric', month: 'short' })}</td><td>${esc(e.description)}${e.activity_title ? `<br><span class="hint">for ${esc(e.activity_title)}</span>` : ''}</td><td>${esc(e.category)}</td><td class="num">${money(e.amount)}</td>
          <td class="num"><button class="btn sm" data-edit-exp="${e.id}">Edit</button> <button class="btn sm danger" data-del-exp="${e.id}" aria-label="Delete ${esc(e.description)}">✕</button></td></tr>`).join('')}</tbody></table>`
        : '<div class="empty"><p>No expenses recorded. Add what you actually paid — it stays separate from estimates.</p></div>'}</div></div>`;
  } else {
    tab.innerHTML = `<div class="panel"><h2>Trip notes</h2><p class="notes">${esc(t.notes || 'No notes yet. Use “Edit trip” to add packing lists, addresses or reminders.')}</p></div>`;
  }
}

function tripForm(t = {}) {
  const d = dialog(`<h2>${t.id ? 'Edit trip' : 'Plan a new trip'}</h2><form novalidate><div class="grid2">
    <label class="full">Trip name<input name="name" required maxlength="120" value="${esc(t.name)}" placeholder="e.g. Spring in Lisbon"></label>
    <label>Destination<input name="destination" required maxlength="120" value="${esc(t.destination)}"></label><label>Country<input name="country" maxlength="80" value="${esc(t.country)}"></label>
    <label>Start date<input name="start_date" type="date" required value="${esc(t.start_date)}"></label><label>End date<input name="end_date" type="date" required value="${esc(t.end_date)}"></label>
    <label>Trip currency<select name="currency">${options(meta.currencies, t.currency || 'AED')}</select></label><label>Planned budget (optional)<input name="budget" type="number" min="0" step="0.01" value="${t.budget ?? ''}"></label>
    <label class="full">Destination time zone (label only)<input name="timezone" maxlength="60" value="${esc(t.timezone)}" placeholder="e.g. Europe/Lisbon (UTC+0)"></label>
    <label class="full">Notes<textarea name="notes" maxlength="5000">${esc(t.notes)}</textarea></label></div>
    <p class="hint">Every amount on this trip uses its currency. Activity times are local to the destination. The currency is locked once amounts are recorded.</p>
    <p class="form-error" role="alert"></p><div class="dlg-actions"><button type="button" class="btn" data-close>Cancel</button><button type="submit" class="btn primary">Save trip</button></div></form>`);
  bindForm($('form', d), async data => {
    const r = await api(t.id ? 'trips/' + t.id : 'trips', t.id ? 'PUT' : 'POST', data);
    d.close(); toast(t.id ? 'Trip updated' : 'Trip created'); tripTab = 'itinerary'; go('#/trips/' + (t.id || r.id));
  });
}

function activityForm(t, a = {}) {
  const d = dialog(`<h2>${a.id ? 'Edit activity' : 'Add an activity'}</h2><form novalidate><div class="grid2">
    <label class="full">What<input name="title" required maxlength="160" value="${esc(a.title)}"></label>
    <label>Day<input name="day" type="date" min="${t.start_date}" max="${t.end_date}" required value="${esc(a.day)}"></label><label>Category<select name="category">${options(meta.activity_categories, a.category || 'Sightseeing')}</select></label>
    <label>Start (local time)<input name="start_time" type="time" value="${esc(a.start_time)}"></label><label>End (local time)<input name="end_time" type="time" value="${esc(a.end_time)}"></label>
    <label class="full">Location<input name="location" maxlength="160" value="${esc(a.location)}"></label>
    <label>Estimated cost (${esc(t.currency)})<input name="est_cost" type="number" min="0" step="0.01" value="${a.est_cost ?? 0}"></label><label>Booking status<select name="booking_status">${options(meta.booking, a.booking_status || 'Planned')}</select></label>
    <label class="full">Booking reference (optional)<input name="booking_ref" maxlength="80" value="${esc(a.booking_ref)}"></label>
    <label class="full">Notes<textarea name="notes" maxlength="3000">${esc(a.notes)}</textarea></label></div>
    <p class="hint">Booking status is your own record — Roamly does not make reservations.</p>
    <p class="form-error" role="alert"></p><div class="dlg-actions"><button type="button" class="btn" data-close>Cancel</button><button type="submit" class="btn primary">Save activity</button></div></form>`);
  bindForm($('form', d), async data => {
    await api(a.id ? 'activities/' + a.id : `trips/${t.id}/activities`, a.id ? 'PUT' : 'POST', data);
    d.close(); toast('Itinerary updated'); render();
  });
}

function expenseForm(t, e = {}) {
  const d = dialog(`<h2>${e.id ? 'Edit expense' : 'Record an expense'}</h2><form novalidate><div class="grid2">
    <label class="full">Description<input name="description" required maxlength="160" value="${esc(e.description)}"></label>
    <label>Amount (${esc(t.currency)})<input name="amount" type="number" min="0.01" step="0.01" required value="${e.amount ?? ''}"></label><label>Date paid<input name="spent_on" type="date" required value="${esc(e.spent_on || today())}"></label>
    <label>Category<select name="category">${options(meta.expense_categories, e.category || 'Food & drink')}</select></label>
    <label>Linked activity<select name="activity_id"><option value="">None</option>${options(t.activities.map(a => [a.id, `${fmtDate(a.day, { day: 'numeric', month: 'short' })} · ${a.title}`]), e.activity_id)}</select></label>
    <label class="full">Notes<textarea name="notes" maxlength="2000">${esc(e.notes)}</textarea></label></div>
    <p class="form-error" role="alert"></p><div class="dlg-actions"><button type="button" class="btn" data-close>Cancel</button><button type="submit" class="btn primary">Save expense</button></div></form>`);
  bindForm($('form', d), async data => {
    await api(e.id ? 'expenses/' + e.id : `trips/${t.id}/expenses`, e.id ? 'PUT' : 'POST', data);
    d.close(); toast('Expense saved'); render();
  });
}

const go = h => { if (location.hash === h) render(); else location.hash = h; };
const trip = () => $('#main').trip;
async function del(msg, title, path, done) {
  if (!await confirmBox(msg, { title })) return;
  try { await api(path, 'DELETE'); toast('Deleted'); done(); } catch (e) { toast(e.message, 'err'); }
}
root.addEventListener('click', e => {
  const b = e.target.closest('button,[data-act]'); if (!b) return;
  const ds = b.dataset;
  if (ds.act) e.preventDefault();
  if (ds.auth) return;
  if (ds.act === 'logout') api('logout', 'POST').finally(() => { me = null; location.hash = ''; renderAuth('You have signed out.'); });
  else if (ds.act === 'new-trip') tripForm();
  else if (ds.act === 'edit-trip') tripForm(trip());
  else if (ds.act === 'delete-trip') { const t = trip(); del(`Delete “${t.name}” with its ${t.activities.length} activities and ${t.expenses.length} expenses? This cannot be undone.`, 'Delete trip', 'trips/' + t.id, () => go('#/')); }
  else if (ds.act === 'export') download('export/trips?' + qs(filters));
  else if (ds.act === 'export-trip') download(`export/trips/${trip().id}/itinerary`);
  else if (ds.act === 'new-expense') expenseForm(trip());
  else if (ds.tab) { tripTab = ds.tab; render(); }
  else if (ds.when !== undefined) { filters.when = ds.when; render(); }
  else if (ds.addAct) activityForm(trip(), { day: ds.addAct });
  else if (ds.editAct) activityForm(trip(), trip().activities.find(a => a.id === +ds.editAct));
  else if (ds.delAct) { const a = trip().activities.find(x => x.id === +ds.delAct); del(`Remove “${a.title}” from the itinerary? Linked expenses are kept but unlinked.`, 'Delete activity', 'activities/' + a.id, render); }
  else if (ds.editExp) expenseForm(trip(), trip().expenses.find(x => x.id === +ds.editExp));
  else if (ds.delExp) del('Delete this expense record?', 'Delete expense', 'expenses/' + ds.delExp, render);
});
let timer;
root.addEventListener('input', e => {
  if (e.target.dataset.filter !== 'q') return;
  filters.q = e.target.value; clearTimeout(timer);
  timer = setTimeout(() => { const m = $('#main'); m.refresh && m.refresh().catch(err => toast(err.message, 'err')); }, 200);
});
window.addEventListener('hashchange', () => { if (me) { if (route()[0] !== 'trips') tripTab = 'itinerary'; render(); } });

(async () => {
  try { me = (await api('session')).user; meta = await api('meta'); render(); }
  catch (e) { e.status === 401 ? renderAuth() : (root.innerHTML = `<div class="loading">${esc(e.message)}</div>`); }
})();
