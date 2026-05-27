/**
 * FileUploader — vanilla JS компонент загрузки файлов.
 * Адаптация file_save.tsx (React) под Jinja2 + vanilla.
 *
 *   const uploader = new FileUploader({
 *     container: '#upload-root',          // CSS selector или HTMLElement
 *     endpoint: '/upload/api',            // URL для POST multipart/form-data
 *     fieldName: 'photos',                // имя поля для файлов
 *     extraFields: () => ({ month: ... }),// функция или объект с доп.полями
 *     maxFiles: 10,
 *     maxSizeMb: 10,
 *     accept: 'image/*',
 *     csrfToken: '...',                   // значение X-CSRF-Token
 *     buttonLabel: 'Загрузить',
 *     onSuccess: (response) => {},
 *     onError: (err) => {},
 *   });
 */
(function (global) {
  'use strict';

  const DEFAULTS = {
    endpoint: '',
    fieldName: 'photos',
    extraFields: null,
    maxFiles: 10,
    maxSizeMb: 10,
    accept: 'image/*',
    csrfToken: '',
    buttonLabel: 'Загрузить',
    hintText: 'Перетащите файлы сюда или нажмите для выбора',
    subHint: 'Изображения до 10 МБ, до 10 файлов',
    onSuccess: null,
    onError: null,
  };

  function uid() {
    return 'f' + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function formatSize(bytes) {
    const kb = bytes / 1024;
    if (kb < 1024) return Math.round(kb) + ' КБ';
    return (kb / 1024).toFixed(1) + ' МБ';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  class FileUploader {
    constructor(options) {
      this.opts = Object.assign({}, DEFAULTS, options || {});
      const el = typeof this.opts.container === 'string'
        ? document.querySelector(this.opts.container)
        : this.opts.container;
      if (!el) throw new Error('FileUploader: container not found');
      this.root = el;
      this.files = [];        // [{id, file, previewUrl}]
      this.uploading = false;
      this._render();
      this._bind();
    }

    _render() {
      this.root.classList.add('fu-root');
      this.root.innerHTML = `
        <div class="fu-dropzone" data-role="dropzone" tabindex="0" role="button"
             aria-label="Зона загрузки файлов">
          <div class="fu-dz-icon" aria-hidden="true">📁</div>
          <div class="fu-dz-title">${escapeHtml(this.opts.hintText)}</div>
          <div class="fu-dz-sub">${escapeHtml(this.opts.subHint)}</div>
        </div>
        <input type="file" class="fu-input" data-role="input"
               accept="${escapeHtml(this.opts.accept)}" multiple hidden>
        <div class="fu-list-head" data-role="head" hidden>
          <span data-role="count">Выбрано: 0</span>
          <button type="button" class="fu-clear" data-role="clear">Очистить</button>
        </div>
        <div class="fu-grid" data-role="grid"></div>
        <div class="fu-progress" data-role="progress" hidden>
          <div class="fu-progress-bar" data-role="bar"></div>
        </div>
        <button type="button" class="fu-submit" data-role="submit" disabled>
          ${escapeHtml(this.opts.buttonLabel)}
        </button>
        <div class="fu-toast" data-role="toast" hidden></div>
      `;
      this.$ = (sel) => this.root.querySelector('[data-role="' + sel + '"]');
    }

    _bind() {
      const dz = this.$('dropzone');
      const input = this.$('input');
      const submit = this.$('submit');
      const clear = this.$('clear');

      dz.addEventListener('click', () => !this.uploading && input.click());
      dz.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && !this.uploading) {
          e.preventDefault(); input.click();
        }
      });
      ['dragenter', 'dragover'].forEach((ev) =>
        dz.addEventListener(ev, (e) => {
          e.preventDefault(); e.stopPropagation();
          if (!this.uploading) dz.classList.add('is-dragging');
        })
      );
      ['dragleave', 'dragend'].forEach((ev) =>
        dz.addEventListener(ev, (e) => {
          e.preventDefault(); e.stopPropagation();
          dz.classList.remove('is-dragging');
        })
      );
      dz.addEventListener('drop', (e) => {
        e.preventDefault(); e.stopPropagation();
        dz.classList.remove('is-dragging');
        if (this.uploading) return;
        if (e.dataTransfer && e.dataTransfer.files.length) {
          this._addFiles(e.dataTransfer.files);
        }
      });

      input.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length) this._addFiles(e.target.files);
        e.target.value = '';
      });

      submit.addEventListener('click', () => this.upload());
      clear.addEventListener('click', () => this.reset());
    }

    _addFiles(fileList) {
      const maxBytes = this.opts.maxSizeMb * 1024 * 1024;
      const skipped = [];
      const incoming = Array.from(fileList);
      for (const file of incoming) {
        if (this.files.length >= this.opts.maxFiles) {
          skipped.push(`${file.name}: превышен лимит ${this.opts.maxFiles}`);
          continue;
        }
        if (file.size > maxBytes) {
          skipped.push(`${file.name}: больше ${this.opts.maxSizeMb} МБ`);
          continue;
        }
        if (this.opts.accept === 'image/*' && !file.type.startsWith('image/')) {
          skipped.push(`${file.name}: не изображение`);
          continue;
        }
        if (this.files.some((f) => f.file.name === file.name && f.file.size === file.size)) {
          skipped.push(`${file.name}: дубликат`);
          continue;
        }
        const entry = { id: uid(), file: file, previewUrl: null };
        if (file.type.startsWith('image/')) {
          entry.previewUrl = URL.createObjectURL(file);
        }
        this.files.push(entry);
      }
      if (skipped.length) this._toast('Пропущено: ' + skipped.join('; '), 'info');
      this._renderList();
    }

    _renderList() {
      const grid = this.$('grid');
      const head = this.$('head');
      const count = this.$('count');
      const submit = this.$('submit');
      grid.innerHTML = '';
      if (!this.files.length) {
        head.hidden = true;
        submit.disabled = true;
        return;
      }
      head.hidden = false;
      count.textContent = 'Выбрано: ' + this.files.length;
      submit.disabled = this.uploading;
      for (const entry of this.files) {
        const card = document.createElement('div');
        card.className = 'fu-card';
        card.dataset.id = entry.id;
        const preview = entry.previewUrl
          ? `<img src="${entry.previewUrl}" alt="${escapeHtml(entry.file.name)}">`
          : `<div class="fu-card-icon">📄</div>`;
        card.innerHTML = `
          <div class="fu-card-preview">${preview}</div>
          <div class="fu-card-info">
            <div class="fu-card-name" title="${escapeHtml(entry.file.name)}">${escapeHtml(entry.file.name)}</div>
            <div class="fu-card-size">${formatSize(entry.file.size)}</div>
          </div>
          <button type="button" class="fu-card-remove" aria-label="Удалить файл">×</button>
        `;
        card.querySelector('.fu-card-remove').addEventListener('click', () => {
          if (this.uploading) return;
          this._removeFile(entry.id);
        });
        grid.appendChild(card);
      }
    }

    _removeFile(id) {
      const idx = this.files.findIndex((f) => f.id === id);
      if (idx === -1) return;
      if (this.files[idx].previewUrl) URL.revokeObjectURL(this.files[idx].previewUrl);
      this.files.splice(idx, 1);
      this._renderList();
    }

    reset() {
      for (const f of this.files) if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
      this.files = [];
      this._setProgress(0, false);
      this._renderList();
    }

    _setProgress(pct, visible) {
      const wrap = this.$('progress');
      const bar = this.$('bar');
      wrap.hidden = !visible;
      bar.style.transform = 'scaleX(' + (Math.max(0, Math.min(100, pct)) / 100) + ')';
    }

    _toast(message, type) {
      const el = this.$('toast');
      el.className = 'fu-toast fu-toast-' + (type || 'info');
      el.textContent = message;
      el.hidden = false;
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
    }

    _setUploading(flag) {
      this.uploading = flag;
      this.root.classList.toggle('is-uploading', flag);
      this.$('submit').disabled = flag || !this.files.length;
      this.$('clear').disabled = flag;
    }

    upload() {
      if (this.uploading || !this.files.length) return;
      const fd = new FormData();
      for (const entry of this.files) fd.append(this.opts.fieldName, entry.file, entry.file.name);
      const extras = typeof this.opts.extraFields === 'function'
        ? this.opts.extraFields() : (this.opts.extraFields || {});
      for (const [k, v] of Object.entries(extras)) {
        if (v === null || v === undefined) continue;
        fd.append(k, v);
      }

      const xhr = new XMLHttpRequest();
      xhr.open('POST', this.opts.endpoint);
      if (this.opts.csrfToken) xhr.setRequestHeader('X-CSRF-Token', this.opts.csrfToken);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) this._setProgress((e.loaded / e.total) * 100, true);
      };
      xhr.onerror = () => {
        this._setUploading(false);
        this._setProgress(0, false);
        this._toast('Ошибка сети', 'error');
        if (typeof this.opts.onError === 'function') this.opts.onError(new Error('network'));
      };
      xhr.onload = () => {
        this._setUploading(false);
        this._setProgress(100, true);
        setTimeout(() => this._setProgress(0, false), 600);
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* no-op */ }
        if (xhr.status >= 200 && xhr.status < 300 && (!data || data.success !== false)) {
          this._toast(`Загружено: ${this.files.length}`, 'success');
          this.reset();
          if (typeof this.opts.onSuccess === 'function') this.opts.onSuccess(data || {});
        } else {
          const msg = (data && (data.error || data.detail)) || `Ошибка ${xhr.status}`;
          this._toast(msg, 'error');
          if (typeof this.opts.onError === 'function') this.opts.onError(data || { status: xhr.status });
        }
      };
      this._setUploading(true);
      this._setProgress(0, true);
      xhr.send(fd);
    }
  }

  global.FileUploader = FileUploader;
})(window);
