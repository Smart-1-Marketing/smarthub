(function () {
  'use strict';
  if (window.self !== window.top || window.S1HubInboxLoaded || document.getElementById('hub-account')) return;
  window.S1HubInboxLoaded = true;
  const css = document.createElement('link');
  css.rel = 'stylesheet'; css.href = '/assets/help-center.css?v=account-menu-1'; document.head.appendChild(css);
  let data, control, panel, seen = {}, states = {}, busy = false;
  const terminal = s => ['complete', 'completed', 'succeeded', 'failed'].includes(s);
  const read = key => { try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) { return {}; } };
  const save = () => { try { localStorage.setItem('hub-inbox:' + data.user.key, JSON.stringify(seen)); } catch (_) {} };
  function paint() {
    const items = data.items;
    let count = 0;
    panel.querySelector('.hub-inbox-list').replaceChildren();
    items.forEach(item => {
      const status = states[item.id] || item.status;
      if (seen[item.id] !== status) count++;
      const a = document.createElement('a'); a.href = item.url;
      const strong = document.createElement('strong'); strong.textContent = item.title + ' · ' + status;
      const detail = document.createElement('span'); detail.textContent = item.detail;
      const time = document.createElement('small'); time.textContent = new Date(item.time).toLocaleString();
      a.append(strong, detail, time); panel.querySelector('.hub-inbox-list').appendChild(a);
    });
    if (!items.length) panel.querySelector('.hub-inbox-list').textContent = 'No recent processing notifications.';
    control.querySelector('[data-count]').textContent = count ? String(count) : '';
    control.querySelector('[data-bell]').setAttribute('aria-label', 'Notifications, ' + count + ' unread');
  }
  function mount() {
    control = document.createElement('div'); control.id = 'hub-account';
    control.innerHTML = '<a href="/help">Help</a><button type="button" data-bell aria-expanded="false" aria-controls="hub-inbox-panel"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg> <span>Notifications</span> <b data-count></b></button><span class="hub-person"><b class="hub-initials"></b><span data-name></span></span><section id="hub-inbox-panel" hidden><div class="hub-inbox-head"><strong>Processing notifications</strong><button type="button" data-read>Mark all read</button></div><p data-inbox-error role="status"></p><div class="hub-inbox-list"></div></section>';
    const person = control.querySelector('.hub-person');
    const accountButton = document.createElement('button');
    accountButton.type = 'button'; accountButton.className = 'hub-person';
    accountButton.setAttribute('aria-expanded', 'false');
    accountButton.setAttribute('aria-controls', 'hub-account-menu');
    accountButton.setAttribute('aria-label', 'Account options for ' + data.user.name);
    accountButton.append(...person.childNodes); person.replaceWith(accountButton);
    const accountMenu = document.createElement('div');
    accountMenu.id = 'hub-account-menu'; accountMenu.hidden = true;
    accountMenu.innerHTML = '<div class="hub-account-identity"><small>Signed in as</small><strong></strong></div><a href="/logout">Log out</a>';
    accountMenu.querySelector('strong').textContent = data.user.name;
    control.appendChild(accountMenu);
    panel = control.querySelector('#hub-inbox-panel');
    control.querySelector('.hub-initials').textContent = data.user.initials;
    control.querySelector('[data-name]').textContent = data.user.name;
    control.querySelector('.hub-person').title = 'Signed in as ' + data.user.name;
    const bar = document.querySelector('.topbar');
    if (bar) { const old = bar.querySelector('.chip'); if (old) old.remove(); bar.appendChild(control); }
    else { control.classList.add('hub-account-floating'); document.body.prepend(control); }
    control.querySelector('[data-bell]').onclick = () => {
      accountMenu.hidden = true; accountButton.setAttribute('aria-expanded', 'false');
      panel.hidden = !panel.hidden;
      control.querySelector('[data-bell]').setAttribute('aria-expanded', String(!panel.hidden));
    };
    control.querySelector('[data-read]').onclick = () => { data.items.forEach(i => { seen[i.id] = states[i.id] || i.status; }); save(); paint(); };
    accountButton.onclick = () => {
      panel.hidden = true; control.querySelector('[data-bell]').setAttribute('aria-expanded', 'false');
      accountMenu.hidden = !accountMenu.hidden;
      accountButton.setAttribute('aria-expanded', String(!accountMenu.hidden));
    };
    function close() {
      panel.hidden = true; control.querySelector('[data-bell]').setAttribute('aria-expanded', 'false');
      accountMenu.hidden = true; accountButton.setAttribute('aria-expanded', 'false');
    }
    document.addEventListener('click', e => { if (!control.contains(e.target)) close(); });
    control.addEventListener('focusout', e => { if (e.relatedTarget && !control.contains(e.relatedTarget)) close(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && (!panel.hidden || !accountMenu.hidden)) {
        const trigger = !accountMenu.hidden ? accountButton : control.querySelector('[data-bell]');
        close(); trigger.focus();
      }
    });
  }
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch('/api/hub-inbox');
      if (!response.ok) throw new Error('Notifications could not be refreshed.');
      data = await response.json();
      if (!control) { seen = read('hub-inbox:' + data.user.key); mount(); }
      panel.querySelector('[data-inbox-error]').textContent = '';
      paint();
      for (const item of data.items) {
        if (!item.poll || terminal(states[item.id] || item.status)) continue;
        try {
          const r = await fetch(item.poll, {signal: AbortSignal.timeout(12000)});
          if (!r.ok) throw new Error();
          const d = await r.json(); const job = d.render_job || d.job || d;
          states[item.id] = job.status || item.status;
        } catch (_) { states[item.id] = 'status unavailable'; }
      }
      paint();
    } catch (e) { if (panel) panel.querySelector('[data-inbox-error]').textContent = e.message; }
    finally { busy = false; }
  }
  refresh(); setInterval(refresh, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
