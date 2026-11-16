document.querySelector('[data-review-sections]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-review-section]');
    const input = document.getElementById('feedback-text');
    if (!button || !input) return;
    const heading = `**${button.dataset.reviewSection}**`;
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
    const status = form.querySelector('[data-review-save-status]');
    const retry = form.querySelector('[data-review-save-retry]');
    const send = document.querySelector('[data-send-feedback]');
    const initial = JSON.parse(form.querySelector('[data-review-draft-state]').textContent);
    const storageKey = `curator-working-draft:${form.dataset.reviewDraftKey}`;
    let activeId = null;
    try { activeId = Number(sessionStorage.getItem(storageKey)); } catch (_) { /* Storage can be disabled. */ }
    const active = initial.drafts.find((draft) => draft.id === activeId);
    const otherDrafts = Number(send?.dataset.reviewDraftCount || 0) > (active ? 1 : 0);
    let id = active?.id ?? null;
    let revision = active?.revision ?? null;
    let cursor = initial.cursor;
    let savedText = active?.text ?? '';
    let savedImage = active?.image_id ?? null;
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
        if (send) send.disabled = busy || !(otherDrafts || id !== null || input.value.trim());
    }

    function currentImage() {
        return input.value.trim() && image?.value ? Number(image.value) : null;
    }

    function hasChanges() {
        return input.value.trim() !== savedText || currentImage() !== savedImage;
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
        id = data.id;
        revision = data.revision;
        cursor = data.cursor;
        savedText = payload.text;
        savedImage = payload.image_id;
        pendingRequest = null;
        remember();
        status.textContent = id === null ? 'Черновик очищен.' : 'Черновик сохранён.';
    }

    async function flush() {
        clearTimeout(timer);
        if (!inFlight) {
            inFlight = (async () => {
                while (pendingRequest || hasChanges()) {
                    pendingRequest ||= {text: input.value.trim(), image_id: currentImage(), message_id: id, revision, cursor};
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

    if (active) {
        input.value = active.text;
        if (image && savedImage !== null) {
            const value = String(savedImage);
            if (!Array.from(image.options).some((option) => option.value === value)) {
                image.add(new Option('Фото больше недоступно', value));
            }
            image.value = value;
        }
        window.RichTextField?.refresh(input);
        status.textContent = 'Черновик восстановлен.';
        const bubble = document.querySelector(`[data-own-review-draft="${active.id}"]`);
        if (bubble) bubble.hidden = true;
    }
    remember();
    updateSend();
    input.addEventListener('input', schedule);
    image?.addEventListener('change', schedule);
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
        setBusy: (value) => {
            busy = value;
            input.readOnly = value;
            if (image) image.disabled = value;
            if (input._rtEditable) input._rtEditable.contentEditable = value ? 'false' : 'true';
            form.querySelectorAll('[data-review-section]').forEach((button) => { button.disabled = value; });
            updateSend();
        },
        detach: () => {
            id = null;
            remember();
        },
    };
})();
