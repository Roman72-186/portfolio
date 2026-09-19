// Нативный WYSIWYG поверх textarea с markdown-подобной разметкой владельца
// (**жирный**, *курсив*, "- пункт", [текст](url)) — владелец 17.09.2026 хотел
// то же поведение, что в Word: выделил — Ctrl+B — сразу видно жирным, а не
// текстовые маркеры. Textarea остаётся ЕДИНСТВЕННЫМ источником истины значения
// (визуально скрыта) — вся существующая логика чтения формы (collectFormBlocks,
// FormData по name=) продолжает работать без изменений. Contenteditable рядом —
// только витрина, синхронизируется в обе стороны.
//
// markdownToHtml()/htmlToMarkdown() — JS-зеркало серверного format_rich_text()
// (app/tmpl.py:99-176). Правишь синтаксис — правь оба места, иначе то, что
// видно в редакторе, разойдётся с тем, что ученик увидит после сохранения.
(function () {
    'use strict';

    var LINK_RE = /\[([^\[\]]+)\]\(([^()\s]+)\)/g;
    var BOLD_RE = /\*\*([\s\S]+?)\*\*/g;
    var ITALIC_RE = /(?<!\*)\*(?!\*)([\s\S]+?)(?<!\*)\*(?!\*)/g;

    function isSafeLink(url) {
        var lower = (url || '').toLowerCase();
        return lower.indexOf('http://') === 0 || lower.indexOf('https://') === 0;
    }

    function escapeHtml(value) {
        var div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    // ---- markdown -> html, для показа в contenteditable (зеркало format_rich_text) ----
    function markdownToHtml(text) {
        if (!text) return '';
        var escaped = escapeHtml(text);
        var lines = escaped.split('\n');
        var outLines = [];
        var buf = [];

        function flush() {
            if (buf.length) {
                outLines.push('<ul>' + buf.map(function (item) {
                    return '<li>' + item + '</li>';
                }).join('') + '</ul>');
                buf = [];
            }
        }

        lines.forEach(function (line) {
            var stripped = line.replace(/^\s+/, '');
            if (stripped.indexOf('- ') === 0 || stripped.indexOf('• ') === 0) {
                buf.push(stripped.slice(2).trim());
            } else {
                flush();
                outLines.push(line);
            }
        });
        flush();
        var result = outLines.join('\n');

        result = result.replace(LINK_RE, function (m, label, url) {
            return isSafeLink(url)
                ? '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>'
                : m;
        });
        result = result.replace(BOLD_RE, '<strong>$1</strong>');
        result = result.replace(ITALIC_RE, '<em>$1</em>');

        var parts = result.split(/\n{2,}/).map(function (part) {
            return part.replace(/\n/g, '<br>');
        });
        return parts.join('<br><br>');
    }

    // ---- html (contenteditable) -> markdown, пишется в textarea.value ----
    // Форматов-комбинаций жирный+курсив на одном фрагменте текущий диалект не
    // умеет (сервер их не парсит) — при такой вложенности берём внешний тег,
    // внутренний теряется. Это уже существующее свойство format_rich_text, не
    // новая проблема: тройные звёздочки `***text***` там тоже не рендерятся.
    // Единственный элемент-потомок без другого текста рядом — нужен, чтобы
    // отличить "жирный, внутри которого только курсив целиком" (вложенность,
    // которую сериализатор схлопывает в один маркер) от "жирный с курсивом
    // как частью текста" (оставляем оба маркера как есть).
    function onlyChildTag(el) {
        if (el.childNodes.length !== 1) return null;
        var only = el.firstElementChild;
        if (!only) return null;
        return only.tagName.toLowerCase();
    }

    // Разбор одного узла инлайн-уровня (текст, <strong>/<b>, <em>/<i>, <a>,
    // <br>) — общий для walkInline (рекурсия внутрь тега) и для верхнего
    // уровня contenteditable, где текст и такие теги нередко лежат ПРЯМО в
    // корне без обёртки <div> (первая строка, пока Enter ни разу не нажат).
    function inlineNodeToMarkdown(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        var tag = node.tagName.toLowerCase();
        if (tag === 'br') return '\n';
        if (tag === 'strong' || tag === 'b') {
            var boldOnlyChild = onlyChildTag(node);
            if (boldOnlyChild === 'em' || boldOnlyChild === 'i') {
                return '**' + walkInline(node.firstElementChild) + '**';
            }
            var boldInner = walkInline(node);
            return boldInner ? '**' + boldInner + '**' : '';
        }
        if (tag === 'em' || tag === 'i') {
            var italicOnlyChild = onlyChildTag(node);
            if (italicOnlyChild === 'strong' || italicOnlyChild === 'b') {
                return '**' + walkInline(node.firstElementChild) + '**';
            }
            var italicInner = walkInline(node);
            return italicInner ? '*' + italicInner + '*' : '';
        }
        if (tag === 'a') {
            var href = node.getAttribute('href') || '';
            var text = walkInline(node);
            return isSafeLink(href) ? '[' + text + '](' + href + ')' : text;
        }
        return walkInline(node);
    }

    function walkInline(node) {
        var out = '';
        node.childNodes.forEach(function (child) {
            out += inlineNodeToMarkdown(child);
        });
        return out;
    }

    function blockLines(el, out) {
        var tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (tag === 'ul' || tag === 'ol') {
            Array.prototype.forEach.call(el.children, function (li) {
                out.push('- ' + walkInline(li).trim());
            });
            return;
        }
        walkInline(el).split('\n').forEach(function (line) {
            out.push(line);
        });
    }

    // Текст и инлайн-теги (strong/em/a), лежащие прямо в корне без <div>,
    // копятся в одну строку (current) — иначе "hello " + "<b>world</b>" без
    // обёртки распадались бы на две строки "hello" и "world" вместо одной
    // "hello **world**". Строка закрывается только настоящей границей блока:
    // <div>/<p>/<ul>/<ol> или <br>.
    function htmlToMarkdown(root) {
        var lines = [];
        var current = '';

        function flushCurrent() {
            // Пусто — значит перед этим блоком не было "голого" инлайн-контента
            // прямо в корне, пушить нечего: сам блок ниже добавит свою строку.
            if (current) {
                lines.push(current);
                current = '';
            }
        }

        root.childNodes.forEach(function (node) {
            if (node.nodeType === Node.TEXT_NODE) {
                current += node.nodeValue;
                return;
            }
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            var tag = node.tagName.toLowerCase();
            if (tag === 'div' || tag === 'p' || tag === 'ul' || tag === 'ol') {
                flushCurrent();
                blockLines(node, lines);
            } else if (tag === 'br') {
                // <br> — явный разрыв строки от пользователя, в отличие от
                // границы блока: пустой "current" здесь всё равно значащий
                // (пустая строка между двумя <br><br>).
                lines.push(current);
                current = '';
            } else {
                current += inlineNodeToMarkdown(node);
            }
        });
        if (current) lines.push(current);

        return lines.join('\n').replace(/\n{3,}/g, '\n\n');
    }

    function placeCaretAtEnd(el) {
        el.focus();
        var range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }

    function syncFromEditable(editable) {
        var textarea = editable._rtSource;
        if (!textarea) return;
        var value = htmlToMarkdown(editable);
        var max = textarea.getAttribute('maxlength');
        if (max && value.length > Number(max)) {
            value = value.slice(0, Number(max));
            textarea.value = value;
            editable.innerHTML = markdownToHtml(value);
            placeCaretAtEnd(editable);
        } else {
            textarea.value = value;
        }
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
        textarea.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function applyLink(editable) {
        var sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
            window.alert('Сначала выделите текст, который станет ссылкой.');
            return;
        }
        var url = window.prompt('Адрес ссылки (https://…)');
        if (!url) return;
        if (!/^https?:\/\//i.test(url)) {
            window.alert('Ссылка должна начинаться с http:// или https:// — иначе она не сохранится.');
            return;
        }
        editable.focus();
        document.execCommand('createLink', false, url);
        syncFromEditable(editable);
    }

    function enhance(textarea) {
        if (!textarea || textarea.dataset.rtEnhanced) return;
        textarea.dataset.rtEnhanced = '1';
        textarea.classList.add('rt-source-hidden');

        var wrap = document.createElement('div');
        wrap.className = 'rt-field';

        var toolbar = document.createElement('div');
        toolbar.className = 'rt-toolbar';
        toolbar.setAttribute('role', 'toolbar');
        toolbar.setAttribute('aria-label', 'Форматирование текста');
        toolbar.innerHTML =
            '<button type="button" class="rt-btn" data-rt-cmd="bold" title="Жирный (Ctrl+B)" aria-label="Жирный"><strong>Ж</strong></button>'
            + '<button type="button" class="rt-btn" data-rt-cmd="italic" title="Курсив (Ctrl+I)" aria-label="Курсив"><em>К</em></button>'
            + '<button type="button" class="rt-btn" data-rt-cmd="list" title="Список" aria-label="Список">&#9776;</button>'
            + '<button type="button" class="rt-btn" data-rt-cmd="link" title="Ссылка (Ctrl+K)" aria-label="Ссылка">&#128279;</button>';

        var editable = document.createElement('div');
        editable.className = 'rt-editable';
        editable.contentEditable = 'true';
        editable.setAttribute('role', 'textbox');
        // Однострочные поля (<input>, например короткая подпись цели трекера)
        // не должны переноситься по Enter — так же, как обычный <input>.
        var singleLine = textarea.tagName.toLowerCase() === 'input';
        if (!singleLine) editable.setAttribute('aria-multiline', 'true');
        var label = textarea.getAttribute('aria-label') || textarea.getAttribute('placeholder');
        if (label) editable.setAttribute('aria-label', label);
        if (textarea.placeholder) editable.setAttribute('data-placeholder', textarea.placeholder);

        textarea.parentNode.insertBefore(wrap, textarea);
        wrap.appendChild(toolbar);
        wrap.appendChild(editable);
        wrap.appendChild(textarea);

        textarea._rtEditable = editable;
        editable._rtSource = textarea;
        editable.innerHTML = markdownToHtml(textarea.value);

        if (singleLine) {
            editable.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') e.preventDefault();
            });
        }

        editable.addEventListener('input', function () {
            syncFromEditable(editable);
        });

        editable.addEventListener('click', function (event) {
            // Большинство редакторов лежат внутри <label>. После появления
            // toolbar первым labelable-контролом в таком label стала кнопка
            // «Ж»: обычный клик по тексту активировал её и уводил фокус из
            // contenteditable. Каретка уже установлена на mousedown, поэтому
            // отменяем только click-действие оборачивающего label.
            if (editable.closest('label')) event.preventDefault();
        });

        editable.addEventListener('keydown', function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            var key = e.key.toLowerCase();
            if (key === 'b') {
                e.preventDefault();
                document.execCommand('bold');
                syncFromEditable(editable);
            } else if (key === 'i') {
                e.preventDefault();
                document.execCommand('italic');
                syncFromEditable(editable);
            } else if (key === 'k') {
                e.preventDefault();
                applyLink(editable);
            }
        });

        editable.addEventListener('paste', function (e) {
            e.preventDefault();
            var text = (e.clipboardData || window.clipboardData).getData('text/plain');
            document.execCommand('insertText', false, text);
        });

        toolbar.addEventListener('mousedown', function (e) {
            var btn = e.target.closest('[data-rt-cmd]');
            if (!btn) return;
            // preventDefault держит выделение в contenteditable — иначе клик по
            // кнопке снимает selection до того, как успеет сработать execCommand.
            e.preventDefault();
            var cmd = btn.getAttribute('data-rt-cmd');
            editable.focus();
            if (cmd === 'link') {
                applyLink(editable);
                return;
            }
            if (cmd === 'bold') document.execCommand('bold');
            else if (cmd === 'italic') document.execCommand('italic');
            else if (cmd === 'list') document.execCommand('insertUnorderedList');
            syncFromEditable(editable);
        });
    }

    function enhanceAll(root) {
        (root || document).querySelectorAll('[data-rich-text]').forEach(enhance);
    }

    function refresh(textarea) {
        var editable = textarea && textarea._rtEditable;
        if (!editable) return;
        editable.innerHTML = markdownToHtml(textarea.value);
    }

    window.RichTextField = {
        enhance: enhance,
        enhanceAll: enhanceAll,
        refresh: refresh
    };

    document.addEventListener('DOMContentLoaded', function () {
        enhanceAll(document);
    });
})();
