(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  let lastFocused = null;

  window.openModal = (id) => {
    const modal = document.getElementById(id);
    if (!modal) return;
    lastFocused = document.activeElement;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    const first = $('input:not([type="hidden"]), textarea, select, button', modal);
    if (first) setTimeout(() => first.focus(), 20);
  };
  window.closeModal = (id) => {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = '';
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
  };

  $$('.modal-backdrop').forEach(modal => {
    modal.addEventListener('click', (event) => { if (event.target === modal) window.closeModal(modal.id); });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      const open = $('.modal-backdrop:not([hidden])');
      if (open) window.closeModal(open.id);
    }
    if (event.key === 'Tab') {
      const modal = $('.modal-backdrop:not([hidden])');
      if (!modal) return;
      const focusable = $$('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])', modal).filter(el => el.offsetParent !== null);
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
    }
  });

  const taskSelect = $('#task-select');
  const handoffTeam = $('#handoff-team');
  const customTitleWrap = $('#custom-title-wrap');
  const customTitle = $('#handoff-custom-title');
  const draftButton = $('#draft-button');
  const draftState = $('#draft-state');
  const fields = { completed: $('#completed'), next_steps: $('#next-steps'), blockers: $('#blockers') };
  const readiness = {
    score: $('#readiness-score'), fill: $('#readiness-fill'),
    done: $('#ready-done'), next: $('#ready-next'), blockers: $('#ready-blockers'), links: $('#ready-links')
  };
  const updateReadiness = () => {
    if (!readiness.score) return;
    const checks = [
      [readiness.done, Boolean(fields.completed.value.trim())],
      [readiness.next, Boolean(fields.next_steps.value.trim())],
      [readiness.blockers, Boolean(fields.blockers.value.trim())],
      [readiness.links, Boolean($('#links')?.value.trim())]
    ];
    const complete = checks.filter(([, ok]) => ok).length;
    readiness.score.textContent = `${complete} / 4`;
    readiness.fill.style.width = `${complete * 25}%`;
    checks.forEach(([node, ok]) => {
      if (!node) return;
      node.classList.toggle('complete', ok);
      node.textContent = `${ok ? '✓' : '○'} ${node.id === 'ready-done' ? 'What got done' : node.id === 'ready-next' ? "What's next" : node.id === 'ready-blockers' ? 'Blockers clarified' : 'Useful links added'}`;
    });
  };
  Object.values(fields).forEach(field => field?.addEventListener('input', updateReadiness));
  $('#links')?.addEventListener('input', updateReadiness);
  updateReadiness();

  const syncTaskSelection = () => {
    const option = taskSelect && taskSelect.selectedOptions[0];
    const selected = Boolean(taskSelect && taskSelect.value);
    if (customTitleWrap) customTitleWrap.hidden = selected;
    if (customTitle) customTitle.required = !selected;
    if (!selected || !option) {
      if (draftState) draftState.textContent = 'Editable suggestions';
      updateReadiness();
      return;
    }
    if (handoffTeam && option.dataset.team) handoffTeam.value = option.dataset.team;
    // Selecting a task supplies context to the draft engine but never alters text fields.
    // This keeps user-authored content safe and makes the draft button's action explicit.
    if (draftState) draftState.textContent = `${option.dataset.project || 'Task'} selected · draft when ready`;
    updateReadiness();
  };
  if (taskSelect) taskSelect.addEventListener('change', syncTaskSelection);
  syncTaskSelection();

  if (draftButton) {
    draftButton.addEventListener('click', async () => {
      const option = taskSelect?.selectedOptions[0];
      const custom = customTitle?.value.trim() || '';
      const title = option?.value ? (option.dataset.title || option.textContent.trim()) : custom;
      if (!title) {
        if (draftState) { draftState.textContent = 'Choose a task or add a handoff title first'; draftState.className = 'draft-hint error'; }
        taskSelect?.focus();
        customTitleWrap.hidden = false;
        return;
      }
      const originalText = draftButton.innerHTML;
      draftButton.disabled = true;
      draftButton.innerHTML = '<span>◌</span> Drafting…';
      if (draftState) { draftState.textContent = 'Building your draft…'; draftState.className = 'draft-hint loading'; }
      const payload = {
        title,
        project: option?.dataset.project || 'General',
        owner: option?.dataset.owner || $('#from-person')?.value || 'You',
        status: option?.dataset.status || 'In progress',
        priority: option?.dataset.priority || 'Normal',
        description: option?.dataset.description || '',
        blockers: fields.blockers.value.trim(),
        completed: fields.completed.value.trim(),
        next_steps: fields.next_steps.value.trim()
      };
      try {
        const response = await fetch('/api/draft', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not create a draft.');
        // Never overwrite user-authored text. Fill empty inputs only.
        if (!fields.completed.value.trim()) fields.completed.value = data.completed || '';
        if (!fields.next_steps.value.trim()) fields.next_steps.value = data.next_steps || '';
        if (!fields.blockers.value.trim()) fields.blockers.value = data.blockers || '';
        updateReadiness();
        if (draftState) {
          draftState.textContent = data.note || (data.mode === 'ai' ? 'AI draft ready — review it' : 'Smart draft ready — edit it');
          draftState.className = 'draft-hint';
        }
      } catch (error) {
        if (draftState) { draftState.textContent = error.message || 'Draft unavailable'; draftState.className = 'draft-hint error'; }
      } finally {
        draftButton.disabled = false;
        draftButton.innerHTML = originalText;
      }
    });
  }

  const composerForm = $('#handoff-form');
  if (composerForm) composerForm.addEventListener('submit', (event) => {
    const title = taskSelect?.value || customTitle?.value.trim();
    if (!title) {
      event.preventDefault();
      customTitleWrap.hidden = false;
      customTitle?.focus();
      if (draftState) { draftState.textContent = 'Add a title or choose a related task'; draftState.className = 'draft-hint error'; }
      return;
    }
    if (!fields.completed.value.trim() || !fields.next_steps.value.trim()) {
      event.preventDefault();
      (!fields.completed.value.trim() ? fields.completed : fields.next_steps).focus();
    }
  });

  // Optional readable mode; preference stays on this device only.
  const readableToggle = $('#readable-toggle');
  const applyReadableMode = (enabled) => {
    document.documentElement.classList.toggle('readable-mode', enabled);
    if (readableToggle) readableToggle.setAttribute('aria-pressed', String(enabled));
  };
  let readableOn = false;
  try { readableOn = localStorage.getItem('handoff-readable-mode') === 'true'; } catch (_) {}
  applyReadableMode(readableOn);
  if (readableToggle) readableToggle.addEventListener('click', () => {
    readableOn = !document.documentElement.classList.contains('readable-mode');
    applyReadableMode(readableOn);
    try { localStorage.setItem('handoff-readable-mode', String(readableOn)); } catch (_) {}
  });

  // Set a human-readable weekday line from the viewer's local clock.
  const eyebrow = $('.eyebrow');
  if (eyebrow) {
    const day = new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(new Date()).toUpperCase();
    eyebrow.innerHTML = `<span class="eyebrow-line"></span> ${day.toLowerCase()}, YOUR WORK IS IN GOOD HANDS`;
  }
})();

// Live, deterministic context-gap checks. Results are advisory and explicitly not AI.
(() => {
  const form = document.querySelector('#handoff-form');
  const output = document.querySelector('#gap-live');
  const list = document.querySelector('#gap-list');
  if (!form || !output || !list) return;
  let timer = null;
  const runCheck = async () => {
    const payload = Object.fromEntries(['completed', 'next_steps', 'blockers', 'links', 'to_person'].map(key => {
      const el = form.elements.namedItem(key);
      return [key, el ? el.value : ''];
    }));
    try {
      const response = await fetch('/api/check', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error('check unavailable');
      const result = await response.json();
      list.replaceChildren();
      if (!result.gaps.length) {
        const item = document.createElement('li');
        item.textContent = 'No obvious gaps detected. Please still verify the facts.';
        list.append(item);
      } else {
        result.gaps.forEach(gap => {
          const item = document.createElement('li');
          const title = document.createElement('strong');
          const detail = document.createElement('span');
          title.textContent = gap.title;
          detail.textContent = ` — ${gap.detail}`;
          item.append(title, detail);
          list.append(item);
        });
      }
      output.dataset.count = String(result.count);
    } catch (_) {
      list.replaceChildren();
      const item = document.createElement('li');
      item.textContent = 'Context check temporarily unavailable; you can still review the briefing checklist.';
      list.append(item);
    }
  };
  form.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(runCheck, 250);
  });
  runCheck();
})();
