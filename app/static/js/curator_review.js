document.querySelector('[data-review-sections]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-review-heading]');
    const input = document.getElementById('feedback-text');
    if (!button || !input) return;
    const heading = `**${button.dataset.reviewHeading}**`;
    if (input.value.split('\n').includes(heading)) return;
    input.value = `${input.value.trimEnd()}${input.value.trim() ? '\n\n' : ''}${heading}\n`;
    if (input._rtEditable && window.RichTextField?.refresh) {
        window.RichTextField.refresh(input);
        input._rtEditable.focus();
    } else {
        input.focus();
    }
    input.dispatchEvent(new Event('input', {bubbles: true}));
});

(function setupDraft() {
    const form = document.querySelector('[data-review-draft-url]');
    if (!form) return;
    const input = form.elements.text;
    const image = form.querySelector('[data-review-image]');
    const section = form.querySelector('[data-review-section]');
    const work = form.querySelector('[data-review-work]');
    const picker = form.querySelector('[data-review-draft-picker]');
    const newDraft = form.querySelector('[data-review-new]');
    const addMessage = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-review-save-status]');
    const retry = form.querySelector('[data-review-save-retry]');
    const send = document.querySelector('[data-send-feedback]');
    const initial = JSON.parse(form.querySelector('[data-review-draft-state]').textContent);
    const storageKey = `curator-working-draft:${form.dataset.reviewDraftKey}`;
    let activeId = null;
    try { activeId = Number(sessionStorage.getItem(storageKey)); } catch (_) { /* Storage can be disabled. */ }
    const drafts = new Map(initial.drafts.map((draft) => [draft.id, draft]));
    const otherDrafts = Number(send?.dataset.reviewDraftCount || 0) > drafts.size;
    const imageOptions = Array.from(image.options);
    let id = null;
    let revision = null;
    let cursor = initial.cursor;
    let saved = {text: '', image_id: null, section: null, target_submission_id: null};
    let timer;
    let inFlight;
    let pendingRequest;
    let leaving = false;
    let busy = false;

    function remember() {
        try {
            if (id === null) sessionStorage.removeItem(storageKey);
            else sessionStorage.setItem(storageKey, String(id));
        } catch (_) { /* Server still holds the draft. */ }
    }

    function updateSend() {
        if (send) send.disabled = busy || !(otherDrafts || drafts.size || input.value.trim());
    }

    function draftPayload() {
        const text = input.value.trim();
        return {
            text,
            image_id: text && image.value ? Number(image.value) : null,
            section: text && section.value ? section.value : null,
            target_submission_id: text && work.value ? Number(work.value) : null,
        };
    }

    function refreshPicker() {
        const options = [new Option('Новое замечание', '')];
        for (const draft of drafts.values()) {
            const label = Array.from(section.options).find((option) => option.value === draft.section)?.text;
            const excerpt = draft.text.replace(/\s+/g, ' ').slice(0, 45);
            options.push(new Option([label, excerpt].filter(Boolean).join(' · '), draft.id));
            const bubble = document.querySelector(`[data-own-review-draft="${draft.id}"]`);
            if (bubble) bubble.hidden = true;
        }
        picker.replaceChildren(...options);
        picker.value = id ?? '';
    }

    function hasChanges() {
        const current = draftPayload();
        return Object.keys(saved).some((key) => current[key] !== saved[key]);
    }

    function filterImages(selected = image.value) {
        const options = imageOptions.filter((option) => !option.value || !work.value || option.dataset.work === work.value);
        image.replaceChildren(...options);
        image.value = options.some((option) => option.value === selected) ? selected : '';
    }

    function openDraft(draft) {
        id = draft?.id ?? null;
        revision = draft?.revision ?? null;
        input.value = draft?.text ?? '';
        section.value = draft?.section ?? '';
        const target = String(draft?.target_submission_id ?? '');
        if (target && !Array.from(work.options).some((option) => option.value === target)) {
            work.add(new Option('Работа больше недоступна', target));
        }
        work.value = target;
        const photo = String(draft?.image_id ?? '');
        filterImages(photo);
        if (photo && !image.value) {
            image.add(new Option('Фото больше недоступно', photo));
            image.value = photo;
        }
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
        [image, work, section, picker, newDraft, addMessage].forEach((control) => { control.disabled = value; });
        if (input._rtEditable) input._rtEditable.contentEditable = value ? 'false' : 'true';
        updateSend();
    }

    async function switchDraft(nextId) {
        if (busy || leaving) return;
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
    picker.addEventListener('change', () => switchDraft(Number(picker.value)));
    newDraft.addEventListener('click', () => switchDraft(null));
    input.addEventListener('input', schedule);
    image.addEventListener('change', schedule);
    section.addEventListener('change', schedule);
    work.addEventListener('change', () => {
        filterImages();
        schedule();
    });
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
        leaving = true;
        flush().then(() => { window.location.assign(link.href); }).catch(() => {
            leaving = false;
            status.scrollIntoView({block: 'nearest'});
            retry.focus();
        });
    });
    window.addEventListener('beforeunload', (event) => {
        if (!pendingRequest && !inFlight && !hasChanges()) return;
        event.preventDefault();
        event.returnValue = '';
    });

    window.curatorReviewDraft = {
        flush,
        hasDraft: () => id !== null,
        setBusy,
        detach: () => {
            id = null;
            remember();
        },
    };
})();
