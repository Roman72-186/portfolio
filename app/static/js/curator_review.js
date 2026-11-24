(function setupDraft() {
    const form = document.querySelector('[data-review-draft-url]');
    if (!form) return;
    const input = form.elements.text;
    const picker = form.querySelector('[data-review-draft-picker]');
    const newDraft = form.querySelector('[data-review-new]');
    const draftControls = form.querySelector('[data-review-draft-controls]');
    const tools = form.querySelector('.review-composer-tools');
    const files = form.querySelector('[data-demo-files]');
    const recorder = form.querySelector('[data-demo-recorder]');
    const fileFields = ['photo', 'video', 'audio'];
    const status = form.querySelector('[data-review-save-status]');
    const retry = form.querySelector('[data-review-save-retry]');
    const send = document.querySelector('[data-send-feedback]');
    const initial = JSON.parse(form.querySelector('[data-review-draft-state]').textContent);
    const storageKey = `curator-working-draft:${form.dataset.reviewDraftKey}`;
    let activeId = null;
    try { activeId = Number(sessionStorage.getItem(storageKey)); } catch (_) { /* Storage can be disabled. */ }
    const drafts = new Map(initial.drafts.map((draft) => [draft.id, draft]));
    let otherDrafts = Number(send?.dataset.reviewDraftCount || 0) > drafts.size;
    let id = null;
    let revision = null;
    let cursor = initial.cursor;
    let saved = {text: '', image_id: null, section: null, target_submission_id: null};
    let timer;
    let inFlight;
    let pendingRequest;
    let leaving = false;
    let busy = false;
    let hasFiles = false;

    function remember() {
        try {
            if (id === null) sessionStorage.removeItem(storageKey);
            else sessionStorage.setItem(storageKey, String(id));
        } catch (_) { /* Server still holds the draft. */ }
    }

    function updateSend() {
        if (send) send.disabled = busy || recorder?.hasAttribute('data-mrf-busy')
            || !(otherDrafts || drafts.size || input.value.trim() || hasFiles);
    }

    function draftPayload() {
        const text = input.value.trim();
        const draft = text ? drafts.get(id) : null;
        return {
            text,
            image_id: draft?.image_id ?? null,
            section: draft?.section ?? null,
            target_submission_id: draft?.target_submission_id ?? null,
        };
    }

    function refreshPicker() {
        const options = [new Option('Новое замечание', '')];
        for (const draft of drafts.values()) {
            const label = initial.sections?.[draft.section];
            const excerpt = draft.text.replace(/\s+/g, ' ').slice(0, 45);
            options.push(new Option([label, excerpt].filter(Boolean).join(' · '), draft.id));
            const bubble = document.querySelector(`[data-own-review-draft="${draft.id}"]`);
            if (bubble) bubble.hidden = false;
        }
        picker.replaceChildren(...options);
        picker.value = id ?? '';
        draftControls.hidden = drafts.size === 0;
    }

    function hasChanges() {
        const current = draftPayload();
        return Object.keys(saved).some((key) => current[key] !== saved[key]);
    }

    function openDraft(draft) {
        id = draft?.id ?? null;
        revision = draft?.revision ?? null;
        input.value = draft?.text ?? '';
        saved = draftPayload();
        window.RichTextField?.refresh(input);
        status.textContent = draft ? 'Черновик сохранён.' : 'Текст сохраняется как черновик.';
        status.classList.remove('is-error');
        retry.hidden = true;
        refreshPicker();
        remember();
        updateSend();
    }

    function saveError(error) {
        status.textContent = error.message || 'Не удалось сохранить. Текст остался в редакторе.';
        status.classList.add('is-error');
        retry.hidden = false;
    }

    async function saveOnce(payload) {
        retry.hidden = true;
        status.classList.remove('is-error');
        status.textContent = 'Сохраняем черновик…';
        let response;
        try {
            response = await window.csrfFetch(form.dataset.reviewDraftUrl, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
        } catch (_) {
            throw new Error('Не удалось сохранить. Текст остался в редакторе.');
        }
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
            const message = data.error || data.detail;
            throw new Error(typeof message === 'string' ? message : 'Не удалось сохранить черновик.');
        }
        if (data.id === null && id !== null) {
            drafts.delete(id);
            document.querySelector(`[data-own-review-draft="${id}"]`)?.remove();
        }
        id = data.id;
        revision = data.revision;
        cursor = data.cursor;
        const {text, image_id, section, target_submission_id} = payload;
        saved = {text, image_id, section, target_submission_id};
        if (id !== null) drafts.set(id, {...saved, id, revision});
        pendingRequest = null;
        refreshPicker();
        remember();
        updateSend();
        status.textContent = id === null ? 'Черновик очищен.' : 'Черновик сохранён.';
    }

    async function flush() {
        clearTimeout(timer);
        if (!inFlight) {
            inFlight = (async () => {
                while (pendingRequest || hasChanges()) {
                    pendingRequest ||= {...draftPayload(), message_id: id, revision, cursor};
                    await saveOnce(pendingRequest);
                }
            })().catch((error) => {
                saveError(error);
                throw error;
            }).finally(() => { inFlight = null; });
        }
        return inFlight;
    }

    function schedule() {
        clearTimeout(timer);
        updateSend();
        if (pendingRequest || hasChanges()) {
            status.textContent = 'Есть изменения.';
            status.classList.remove('is-error');
            timer = setTimeout(() => flush().catch(() => {}), 650);
        }
    }

    function setBusy(value) {
        busy = value;
        input.readOnly = value;
        [picker, newDraft].forEach((control) => { control.disabled = value; });
        tools.querySelectorAll('details').forEach((menu) => { menu.open = false; });
        tools.querySelectorAll('button:not([data-send-feedback])').forEach((button) => { button.disabled = value; });
        files.inert = value;
        if (input._rtEditable) input._rtEditable.contentEditable = value ? 'false' : 'true';
        updateSend();
    }

    async function switchDraft(nextId) {
        if (busy || leaving) return;
        if (hasFiles || recorder?.hasAttribute('data-mrf-busy')) {
            picker.value = id ?? '';
            status.textContent = 'Сначала откройте предпросмотр или уберите вложение.';
            return;
        }
        setBusy(true);
        try {
            await flush();
            openDraft(drafts.get(nextId));
        } catch (_) {
            picker.value = id ?? '';
        } finally {
            setBusy(false);
        }
    }

    openDraft(drafts.get(activeId));

    function showFiles() {
        const body = new FormData(form);
        files.replaceChildren();
        hasFiles = false;
        for (const kind of fileFields) {
            const file = body.get(kind);
            if (!file?.size) continue;
            hasFiles = true;
            // The recorder already renders a player and a remove button.
            const recorded = recorder?.querySelector('[data-mrf-preview]:not([hidden]) [data-mrf-player]');
            if ((kind === 'audio' && recorded?.querySelector('audio'))
                || (kind === 'video' && recorded?.querySelector('video'))) continue;
            const item = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = file.name;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn-outline';
            remove.textContent = 'Убрать';
            remove.setAttribute('aria-label', `Убрать вложение: ${file.name}`);
            remove.addEventListener('click', () => {
                form.elements[kind].value = '';
                showFiles();
            });
            item.append(name, remove);
            files.append(item);
        }
        files.hidden = !files.childElementCount;
        updateSend();
    }

    async function preparePreview() {
        await flush();
        const body = new FormData(form);
        if (!fileFields.some((kind) => body.get(kind)?.size)) return;
        body.set('demo_cursor', cursor);
        if (id !== null) {
            body.set('draft_id', id);
            body.set('draft_revision', revision);
        }
        let response;
        try {
            response = await window.csrfFetch(form.action, {method: 'POST', body});
        } catch (_) {
            throw new Error('Не удалось загрузить вложение. Проверьте соединение и снова откройте предпросмотр.');
        }
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) {
            throw new Error(window.csrfFailure(response.status, result, 'Не удалось загрузить вложение. Повторите попытку.'));
        }
        cursor = result.cursor;
        otherDrafts = true;
        drafts.delete(id);
        recorder?.querySelector('[data-mrf-preview]:not([hidden]) [data-mrf-remove]')?.click();
        for (const kind of fileFields) {
            if (form.elements[kind]) form.elements[kind].value = '';
        }
        openDraft(null);
        showFiles();
        return true;
    }

    tools.addEventListener('click', (event) => {
        const action = event.target.closest('[data-demo-file], [data-demo-record]');
        if (!action || busy || recorder?.hasAttribute('data-mrf-busy')) return;
        action.closest('details').open = false;
        if (action.dataset.demoRecord) {
            if (recorder?.querySelector('[data-mrf-preview]:not([hidden])')) {
                status.textContent = 'Сначала уберите текущую запись или откройте предпросмотр.';
                return;
            }
            recorder?.querySelector(`[data-mrf-start="${action.dataset.demoRecord}"]`)?.click();
        } else {
            form.elements[action.dataset.demoFile].click();
        }
    });
    tools.querySelectorAll('details').forEach((menu) => {
        menu.addEventListener('toggle', () => {
            if (!menu.open) return;
            tools.querySelectorAll('details[open]').forEach((other) => {
                if (other !== menu) other.open = false;
            });
        });
        menu.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape' || !menu.open) return;
            menu.open = false;
            menu.querySelector('summary').focus();
        });
    });
    document.addEventListener('click', (event) => {
        tools.querySelectorAll('details[open]').forEach((menu) => {
            if (!menu.contains(event.target)) menu.open = false;
        });
    });
    form.addEventListener('submit', (event) => event.preventDefault());
    form.addEventListener('change', (event) => {
        if (event.target.type === 'file') queueMicrotask(showFiles);
    });
    if (recorder) {
        new MutationObserver(showFiles).observe(recorder, {attributeFilter: ['data-mrf-busy']});
    }
    picker.addEventListener('change', () => switchDraft(Number(picker.value)));
    newDraft.addEventListener('click', () => switchDraft(null));
    input.addEventListener('input', schedule);
    retry.addEventListener('click', () => flush().catch(() => {}));
    form.addEventListener('focusout', (event) => {
        if (event.target === input || event.target === input._rtEditable) {
            flush().catch(() => {});
        }
    });

    document.addEventListener('click', (event) => {
        const link = event.target.closest('a[data-review-navigation]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (leaving || busy) return;
        if ((hasFiles || recorder?.hasAttribute('data-mrf-busy'))
            && !window.confirm('Уйти со страницы? Выбранные вложения ещё не сохранены.')) return;
        leaving = true;
        flush().then(() => { window.location.assign(link.href); }).catch(() => {
            leaving = false;
            status.scrollIntoView({block: 'nearest'});
            retry.focus();
        });
    });
    window.addEventListener('beforeunload', (event) => {
        if (!pendingRequest && !inFlight && !hasChanges() && (leaving || !hasFiles && !recorder?.hasAttribute('data-mrf-busy'))) return;
        event.preventDefault();
        event.returnValue = '';
    });

    window.curatorReviewDraft = {
        flush,
        preparePreview,
        setBusy,
        detach: () => {
            id = null;
            remember();
        },
    };
})();
