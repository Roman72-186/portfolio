(function () {
    'use strict';

    function node(tag, className, label) {
        var el = document.createElement(tag);
        if (className) el.className = className;
        if (label != null) el.textContent = label;
        return el;
    }

    function field(host, label, value, key, multiline) {
        var wrap = node('label', 'prg-field', label);
        var input = node(multiline ? 'textarea' : 'input');
        input.dataset.diagnosticField = key;
        input.value = value || '';
        if (multiline) input.rows = 3;
        else input.type = 'text';
        wrap.appendChild(input);
        host.appendChild(wrap);
        return input;
    }

    function editor(root) {
        var state = {questions: [], results: [], assignments: {}};
        var step = 'questions';
        var message = node('p', 'prg-message');
        message.setAttribute('aria-live', 'polite');

        function combinationKey(values) { return JSON.stringify(values); }
        function combinations() {
            var rows = [[]];
            state.questions.forEach(function (question) {
                var next = [];
                rows.forEach(function (row) {
                    question.options.forEach(function (option) { next.push(row.concat(option.value)); });
                });
                rows = next;
            });
            return state.questions.length ? rows : [];
        }
        function readQuestions() {
            root.querySelectorAll('[data-diagnostic-question]').forEach(function (card, index) {
                var question = state.questions[index];
                question.text = card.querySelector('[data-diagnostic-field="question"]').value;
                card.querySelectorAll('[data-diagnostic-option]').forEach(function (row, oi) {
                    question.options[oi].text = row.querySelector('[data-diagnostic-field="option"]').value;
                    question.options[oi].value = row.querySelector('[data-diagnostic-field="value"]').value;
                });
            });
        }
        function readResults() {
            root.querySelectorAll('[data-diagnostic-result]').forEach(function (card, index) {
                state.results[index].title = card.querySelector('[data-diagnostic-field="title"]').value;
                state.results[index].text = card.querySelector('[data-diagnostic-field="text"]').value;
            });
            root.querySelectorAll('[data-diagnostic-combination]').forEach(function (select) {
                state.assignments[select.dataset.diagnosticCombination] = select.value;
            });
        }
        function read() { if (step === 'questions') readQuestions(); else readResults(); }
        function clear() { root.replaceChildren(); message.textContent = ''; }
        function button(host, label, handler, style) {
            var btn = node('button', style || 'btn-outline', label);
            btn.type = 'button';
            btn.addEventListener('click', handler);
            host.appendChild(btn);
            return btn;
        }
        function renderQuestions() {
            clear();
            root.appendChild(node('h3', null, 'Вопросы диагностики'));
            state.questions.forEach(function (question, index) {
                var card = node('section', 'prg-diagnostic-card');
                card.dataset.diagnosticQuestion = String(index);
                card.appendChild(node('h4', null, 'Вопрос ' + (index + 1)));
                field(card, 'Текст вопроса', question.text, 'question', true);
                question.options.forEach(function (option, oi) {
                    var row = node('div', 'prg-diagnostic-option');
                    row.dataset.diagnosticOption = String(oi);
                    field(row, 'Вариант ответа', option.text, 'option', true);
                    field(row, 'Значение', option.value, 'value', false);
                    if (oi > 0) button(row, '↑', function () {
                        readQuestions();
                        var tmp = question.options[oi - 1];
                        question.options[oi - 1] = question.options[oi];
                        question.options[oi] = tmp;
                        renderQuestions();
                    });
                    if (oi < question.options.length - 1) button(row, '↓', function () {
                        readQuestions();
                        var tmp = question.options[oi + 1];
                        question.options[oi + 1] = question.options[oi];
                        question.options[oi] = tmp;
                        renderQuestions();
                    });
                    button(row, 'Убрать', function () {
                        readQuestions();
                        question.options.splice(oi, 1);
                        renderQuestions();
                    });
                    card.appendChild(row);
                });
                button(card, '+ Добавить вариант', function () {
                    readQuestions();
                    question.options.push({text: '', value: String(question.options.length + 1)});
                    renderQuestions();
                });
                if (index > 0) button(card, 'Вопрос вверх', function () {
                    readQuestions();
                    var tmp = state.questions[index - 1];
                    state.questions[index - 1] = state.questions[index];
                    state.questions[index] = tmp;
                    state.assignments = {};
                    renderQuestions();
                });
                if (index < state.questions.length - 1) button(card, 'Вопрос вниз', function () {
                    readQuestions();
                    var tmp = state.questions[index + 1];
                    state.questions[index + 1] = state.questions[index];
                    state.questions[index] = tmp;
                    state.assignments = {};
                    renderQuestions();
                });
                button(card, 'Убрать вопрос', function () {
                    readQuestions();
                    state.questions.splice(index, 1);
                    state.assignments = {};
                    renderQuestions();
                });
                root.appendChild(card);
            });
            button(root, '+ Добавить вопрос', function () {
                readQuestions();
                state.questions.push({text: '', options: [{text: '', value: '1'}, {text: '', value: '2'}]});
                renderQuestions();
            });
            button(root, 'Далее: результаты', function () {
                readQuestions();
                if (!state.questions.length || state.questions.some(function (q) {
                    return !q.text.trim() || q.options.length < 2 || q.options.some(function (o) { return !o.text.trim() || !o.value.trim(); }) ||
                        new Set(q.options.map(function (o) { return o.value.trim(); })).size !== q.options.length;
                })) {
                    message.textContent = 'Заполните вопросы, варианты и разные значения внутри каждого вопроса.';
                    return;
                }
                if (combinations().length > 4096) {
                    message.textContent = 'Слишком много сочетаний. Сократите число вопросов или вариантов.';
                    return;
                }
                step = 'results';
                renderResults();
            }, 'btn-blue');
            root.appendChild(message);
        }
        function renderResults() {
            clear();
            root.appendChild(node('h3', null, 'Результаты диагностики'));
            state.results.forEach(function (result, index) {
                var card = node('section', 'prg-diagnostic-card');
                card.dataset.diagnosticResult = String(index);
                card.appendChild(node('h4', null, 'Результат ' + (index + 1)));
                var titleInput = field(card, 'Название результата', result.title, 'title', false);
                titleInput.addEventListener('input', function () {
                    root.querySelectorAll('[data-diagnostic-combination] option[value="' + index + '"]').forEach(function (option) {
                        option.textContent = titleInput.value.trim() || 'Результат ' + (index + 1);
                    });
                });
                field(card, 'Текст для ученика', result.text, 'text', true);
                button(card, 'Убрать результат', function () {
                    readResults();
                    state.results.splice(index, 1);
                    state.assignments = {};
                    renderResults();
                });
                root.appendChild(card);
            });
            button(root, '+ Добавить результат', function () {
                readResults();
                state.results.push({title: '', text: ''});
                renderResults();
            });
            root.appendChild(node('h4', null, 'Какой результат соответствует сочетанию'));
            var stats = node('p', 'caption');
            function updateStats() {
                var all = combinations();
                var configured = all.filter(function (combination) {
                    var value = state.assignments[combinationKey(combination)];
                    return value != null && value !== '';
                }).length;
                stats.textContent = 'Всего возможных комбинаций: ' + all.length +
                    ' · Настроено: ' + configured + ' · Не настроено: ' + (all.length - configured);
            }
            root.appendChild(stats);
            var table = node('div', 'prg-diagnostic-combinations');
            combinations().forEach(function (combination) {
                var key = combinationKey(combination);
                var row = node('label', 'prg-diagnostic-match');
                row.appendChild(node('span', null, combination.join(' · ')));
                var select = node('select');
                select.dataset.diagnosticCombination = key;
                var empty = node('option', null, 'Выберите результат');
                empty.value = '';
                select.appendChild(empty);
                state.results.forEach(function (result, index) {
                    var option = node('option', null, result.title || 'Результат ' + (index + 1));
                    option.value = String(index);
                    select.appendChild(option);
                });
                select.value = state.assignments[key] == null ? '' : state.assignments[key];
                select.addEventListener('change', function () {
                    state.assignments[key] = select.value;
                    updateStats();
                });
                row.appendChild(select);
                table.appendChild(row);
            });
            updateStats();
            root.appendChild(table);
            button(root, 'Назад к вопросам', function () { readResults(); step = 'questions'; renderQuestions(); });
            root.appendChild(message);
        }
        function collect() {
            read();
            if (step !== 'results') throw new Error('Перейдите к результатам диагностики.');
            if (!state.results.length || state.results.some(function (r) { return !r.title.trim() || !r.text.trim(); })) {
                throw new Error('Заполните название и текст каждого результата.');
            }
            var results = state.results.map(function (r) {
                return {title: r.title.trim(), text: r.text.trim(), combinations: []};
            });
            combinations().forEach(function (combination) {
                var index = state.assignments[combinationKey(combination)];
                if (index == null || index === '' || !results[Number(index)]) throw new Error('Назначьте результат каждому сочетанию ответов.');
                results[Number(index)].combinations.push(combination);
            });
            if (results.some(function (r) { return !r.combinations.length; })) throw new Error('У каждого результата должно быть хотя бы одно сочетание.');
            return {questions: state.questions, results: results};
        }
        function load(config) {
            state.questions = config && config.questions ? JSON.parse(JSON.stringify(config.questions)) : [];
            state.results = config && config.results ? config.results.map(function (r) { return {title: r.title, text: r.text}; }) : [];
            state.assignments = {};
            if (config && config.results) config.results.forEach(function (result, index) {
                result.combinations.forEach(function (combination) { state.assignments[combinationKey(combination)] = String(index); });
            });
            step = 'questions';
            renderQuestions();
        }
        load(null);
        return {collect: collect, load: load, showError: function (text) { message.textContent = text; }};
    }
    window.DiagnosticBuilder = {create: editor};
})();
