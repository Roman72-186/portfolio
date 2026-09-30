/* Экран «Ученики» (cabinet_students.html) — весь JS страницы.
   Вынесен из шаблона 29.09.2026 (шаг 10.3 плана «Ученики с телефона»): браузер
   кэширует файл, а не качает ~1900 строк с каждой страницей. Данные от сервера
   (CSRF_TOKEN, TARIFF_OPTIONS, CAN_SCORE, INITIAL_STUDENT_ID и др.) объявлены в
   шаблоне блоком до подключения этого файла — здесь Jinja нет и быть не должно.
   Подключается обычным <script>, не модулем: обработчики onclick="…" в разметке
   зовут функции отсюда как глобальные. Правишь файл — подними ?v= в шаблоне. */

// Однобуквенные бейджи шапки ученика (по макету 17082026/Скриншот-20260818-081501.png) —
// только для student-hero, полные названия предметов используются везде остальные.
const SUBJECT_BADGE_LABEL = {'Рисунок': 'Р', 'Композиция': 'К'};
const MONTH_NAMES    = MONTHS_LIST;
const MONTH_TO_NUM   = (function() {
    var m = {};
    MONTHS_LIST.forEach(function(name, idx) { m[String(name).toLowerCase()] = idx + 1; });
    return m;
})();
const NET_ERROR = 'Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.';

var _currentStudentId = null;
var _currentTab = 'portfolio';
var _tabCache = {};   // { studentId: { tabName: data } }
var _viewMode = 'profile'; // 'profile' or 'tab'
var _mockCalendarState = {};
var _portfolioDragWorkId = null;
// Раздел дашборда из URL (?tab=…) без выбранного ученика: при клике на ученика
// открывать сразу эту вкладку, а не профиль (используется навбаром «Статистика»).
var _navDefaultTab = (function() {
    try {
        var t = new URLSearchParams(location.search).get('tab');
        if (t === 'cycles') t = 'mock-exams';  // «Цикл пробника» слит с «Пробниками» 29.09.2026
        var valid = ['portfolio', 'tasks', 'mock-exams', 'statistics'];
        return (t && valid.indexOf(t) !== -1) ? t : null;
    } catch (e) { return null; }
})();

// ── История браузера ─────────────────────────────────────────────────────────
// Список → ученик → вкладка — отдельные записи истории, поэтому свайп «назад»
// на айфоне и системная «назад» на андроиде возвращают на шаг, а не уводят
// с экрана. Запись: s — ученик (null — список), v — 'profile' | 'tab', t — вкладка,
// d — сколько записей экран положил поверх той, с которой открылся,
// up — откуда пришли ('list' | 'profile'). Адрес — тот же ?student=&tab=,
// что сервер отдаёт после сохранения балла, так что перезагрузка не теряет место.
var _emptyMainHtml = document.getElementById('main-panel').innerHTML;
var _navState = null;       // запись, которую экран сейчас показывает
var _navRestoring = false;  // экран перерисовывается из истории — ничего в неё не пишем
var _navToList = false;     // «К списку учеников» отматывает историю до списка

function navUrl(st) {
    var u = new URL(location.href);
    ['student', 'tab', 'saved'].forEach(function(k) { u.searchParams.delete(k); });
    if (st.s) {
        u.searchParams.set('student', st.s);
        if (st.v === 'tab') u.searchParams.set('tab', st.t);
    } else if (_navDefaultTab) u.searchParams.set('tab', _navDefaultTab);
    return u.pathname + u.search;
}

function navCommit(push, up) {
    if (_navRestoring) return;
    var prev = _navState || history.state || {};
    var st = {s: _currentStudentId, v: _currentStudentId ? _viewMode : 'list', t: _currentTab,
              d: (prev.d || 0) + (push ? 1 : 0), up: push ? up : prev.up};
    history[push ? 'pushState' : 'replaceState'](st, '', navUrl(st));
    _navState = st;
}

// На телефоне список — часть страницы и прячется, пока открыт ученик. Место в нём
// запоминаем при открытии ученика и возвращаем здесь: через navShowList идут и кнопка
// «К списку учеников», и «назад» по истории. Сам браузер тут только мешает: запись
// списка он запоминает, когда список уже спрятан, и после «назад» вернул бы наверх.
var _listScrollY = null;
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

function navShowList() {
    var row = _currentStudentId && document.getElementById('srow-' + _currentStudentId);
    _currentStudentId = null;
    _viewMode = 'profile';
    document.querySelector('.split-wrap').classList.remove('student-selected');
    document.querySelectorAll('.student-row.active').forEach(function(r) { r.classList.remove('active'); });
    document.getElementById('tab-bar').style.display = 'none';
    document.getElementById('main-panel').innerHTML = _emptyMainHtml;
    if (_listScrollY !== null) window.scrollTo(0, _listScrollY);
    else if (row) row.scrollIntoView({block: 'center'});  // ученик открыт по ссылке ?student=, места в списке не было
    _listScrollY = null;
}

window.addEventListener('popstate', function(ev) {
    var st = ev.state;
    if (!st || !('d' in st)) return;  // запись не наша — например, переход по якорю
    if (_navToList) {
        _navToList = false;
        navShowList();
        _navState = null;
        navCommit(false);
        return;
    }
    if (!guardUnsavedScore()) {
        // Балл не сохранён — остаёмся: возвращаем в историю запись, с которой ушли
        history.pushState(_navState, '', navUrl(_navState));
        return;
    }
    _navState = st;
    _navRestoring = true;
    try {
        if (!st.s) navShowList();
        else if (st.v === 'tab') {
            if (st.s !== _currentStudentId) selectStudent(st.s, st.t);
            else if (_viewMode === 'tab') switchTab(st.t);
            else openTab(st.t);
        } else if (st.s !== _currentStudentId) selectStudent(st.s, 'profile');
        else showProfile();
    } finally { _navRestoring = false; }
});

// ── Student selection ────────────────────────────────────────────────────────

function mobileBackToList() {
    if (!guardUnsavedScore()) return;
    var d = (_navState && _navState.d) || 0;
    if (d > 0) { _navToList = true; history.go(-d); return; }
    navShowList();
    navCommit(false);
}

// forceTab: имя вкладки — открыть её сразу; 'profile' — профиль, даже если
// навбар задал вкладку по умолчанию (перерисовка профиля, «назад» к профилю).
function selectStudent(id, forceTab) {
    if (_currentStudentId && id !== _currentStudentId && !guardUnsavedScore()) return;
    var navPush = !_currentStudentId;  // из списка — новая запись истории, между учениками — замена
    if (navPush) _listScrollY = window.scrollY;
    _currentStudentId = id;
    if (!_tabCache[id]) _tabCache[id] = {};

    document.querySelector('.split-wrap').classList.add('student-selected');
    document.querySelectorAll('.student-row').forEach(function(r) { r.classList.remove('active'); });
    var row = document.getElementById('srow-' + id);
    if (row) { row.classList.add('active'); row.scrollIntoView({block: 'nearest'}); }

    // Навбар-раздел задал вкладку по умолчанию — открываем её сразу, минуя профиль
    if (forceTab === 'profile') forceTab = null;
    else if (!forceTab && _navDefaultTab) forceTab = _navDefaultTab;

    // If forceTab specified (e.g. after score redirect), go straight to tab
    if (forceTab) {
        _viewMode = 'tab';
        _currentTab = forceTab;
        navCommit(navPush, 'list');
        document.getElementById('tab-bar').style.display = 'flex';
        document.getElementById('main-panel').innerHTML =
            '<div id="student-hero-container"></div>' +
            '<div id="tab-content"><div class="tab-loading">Загружаем…</div></div>';
        switchTab(_currentTab);
        return;
    }

    // Default: show profile
    _viewMode = 'profile';
    navCommit(navPush, 'list');
    document.getElementById('tab-bar').style.display = 'none';
    document.getElementById('main-panel').innerHTML =
        '<div class="tab-loading">Загружаем…</div>';

    // Load profile (from cache or fetch)
    if (_tabCache[id].profile) {
        renderProfile(_tabCache[id].profile);
        return;
    }

    fetch('/cabinet/students/' + id + '/profile')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _tabCache[id].profile = data;
            renderProfile(data);
        })
        .catch(function() {
            document.getElementById('main-panel').innerHTML =
                '<div class="empty-state"><div class="empty-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div><div class="empty-text">Не удалось открыть ученика. Проверьте интернет и выберите его ещё раз.</div></div>';
        });
}

function openTab(tabName) {
    if (_viewMode === 'tab' && tabName !== _currentTab && !guardUnsavedScore()) return;
    var navPush = _viewMode === 'profile';  // из профиля — новая запись, между вкладками — замена
    _viewMode = 'tab';
    _currentTab = tabName;
    navCommit(navPush, 'profile');
    document.getElementById('tab-bar').style.display = 'flex';
    document.getElementById('main-panel').innerHTML =
        '<div id="student-hero-container"></div>' +
        '<div id="tab-content"><div class="tab-loading">Загружаем…</div></div>';
    switchTab(tabName);
}

function showProfile() {
    _viewMode = 'profile';
    document.getElementById('tab-bar').style.display = 'none';
    if (_tabCache[_currentStudentId] && _tabCache[_currentStudentId].profile) {
        renderProfile(_tabCache[_currentStudentId].profile);
    } else {
        selectStudent(_currentStudentId, 'profile');
    }
}

function backToProfile() {
    if (!_currentStudentId) return;
    if (!guardUnsavedScore()) return;
    // Вкладку открыли из профиля — «← К профилю» и есть шаг назад по истории
    if (_navState && _navState.v === 'tab' && _navState.up === 'profile') { history.back(); return; }
    showProfile();
    navCommit(false);
}

// ── Profile rendering ───────────────────────────────────────────────────────

function renderProfile(data) {
    var s = data.student;
    // Разделы — сразу под шапкой (владелец 29.09.2026): на телефоне под анкетой до них было ~1100px
    var html = buildHero(s, s.avg_score_by_subject || null) + buildProfileActions(s);

    html += '<div class="profile-details"><div class="profile-grid">';
    html += buildStudyNow(s);

    // Fields
    // Контакты (телефон/телефон родителя/Telegram) видны только рангам >= 4 —
    // куратору бэкенд отдаёт can_see_contacts=false и сами поля пустыми, здесь
    // это осознанное «скрыто», не «не указано».
    if (s.can_see_contacts !== false) {
        html += profileField('Телефон', s.phone);
        html += profileField('Телефон родителя', s.parent_phone);
        html += profileField('Имя и отчество родителя', s.parent_name);
        html += profileField('Telegram', s.tg_username ? '@' + s.tg_username : null);
        html += profileField('Email', s.email);
        html += profileField('Дата рождения', s.birth_date);
        html += profileField('Город', s.city);
        html += profileField('Часовой пояс', s.timezone);
        html += profileField('Адрес СДЭК', s.sdek_address);
    }
    html += profileField('Тариф', s.tariff);
    html += profileField('Куратор', s.curator_name);
    html += profileField('Начало обучения', s.enrollment_year);
    html += profileField('Год поступления в вуз', s.university_year);
    if (s.past_tariffs) {
        html += profileField('Прошлые тарифы', s.past_tariffs, true);
    }
    // Строку показываем только тем, у кого срок задан: у большинства учеников
    // доступ бессрочный, и пустое «Доступ до» в каждой карточке было бы шумом.
    if (s.access_until) {
        html += profileField('Доступ до', s.access_until.replace('T', ' '));
    }
    if (s.about) {
        html += '<div class="profile-field full-width">'
            + '<div class="profile-field-label">О себе</div>'
            + '<div class="profile-about">' + (s.about_html || esc(s.about)) + '</div></div>';
    }

    // Status badges
    html += '<div class="profile-field full-width">'
        + '<div class="profile-field-label">Статус</div>'
        + '<div class="profile-status-badges">'
        + '<span class="profile-badge ' + (s.profile_completed ? 'ok' : 'no') + '">'
        + (s.profile_completed ? '<svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg> Анкета заполнена' : '<svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Анкета не заполнена') + '</span>'
        + '</div></div>';

    html += '</div></div>';

    // Edit button (admin/superadmin only)
    if (CAN_SCORE) {
        html += '<button class="profile-edit-btn" onclick="startEditProfile()"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg> Редактировать анкету</button>';
    }

    var mainPanel = document.getElementById('main-panel');
    mainPanel.innerHTML = html;
    window.RichTextField.enhanceAll(mainPanel);
}

function buildProfileActions(s) {
    return '<div class="profile-actions">'
        + '<button class="profile-action-btn" onclick="openTab(\'portfolio\')">'
        +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>'
        +   '<div class="profile-action-label">Портфолио</div>'
        +   '<div class="profile-action-count">' + workCountLabel(s.portfolio_count || 0) + '</div>'
        + '</button>'
        + '<button class="profile-action-btn" onclick="openTab(\'tasks\')">'
        +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg></div>'
        +   '<div class="profile-action-label">Задания</div>'
        +   '<div class="profile-action-count">ответы и сдачи</div>'
        + '</button>'
        + '<button class="profile-action-btn" onclick="openTab(\'mock-exams\')">'
        +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/></svg></div>'
        +   '<div class="profile-action-label">Пробники</div>'
        +   '<div class="profile-action-count">' + workCountLabel(s.mock_exam_count || 0) + '</div>'
        + '</button>'
        + '<button class="profile-action-btn" onclick="openTab(\'statistics\')">'
        +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3v18h18"/><polyline points="19 9 13 15 9 11 5 15"/></svg></div>'
        +   '<div class="profile-action-label">Статистика</div>'
        +   '<div class="profile-action-count">динамика баллов</div>'
        + '</button>'
        + (IS_ARCHIVE_VIEW ? (
            // Для архивного ученика «Архив» — единая страница со всеми фото
            // (До/После + импорт из старого чат-бота), поэтому кнопка видна
            // всегда, а не только когда есть исторические фото бота.
            '<a class="profile-action-btn" href="/cabinet/students/' + s.id + '/legacy-portfolio" target="_blank" rel="noopener">'
            +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg></div>'
            +   '<div class="profile-action-label">Архив</div>'
            +   '<div class="profile-action-count">' + ((s.portfolio_count || 0) + (s.legacy_photo_count || 0)) + ' фото</div>'
            + '</a>'
          ) : (s.legacy_photo_count ? (
            '<a class="profile-action-btn" href="/cabinet/students/' + s.id + '/legacy-portfolio" target="_blank" rel="noopener">'
            +   '<div class="profile-action-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg></div>'
            +   '<div class="profile-action-label">Архив</div>'
            +   '<div class="profile-action-count">' + s.legacy_photo_count + ' фото</div>'
            + '</a>'
          ) : ''))
        + '</div>';
}

// «Учёба сейчас» (владелец 29.09.2026): что ждёт преподавателя по ленте ученика.
// Проверка остаётся на одном экране (правило 12) — здесь счётчик и переход туда.
// Экраны проверки и точки А открывают только действующих учеников, поэтому
// в архиве переходов нет, остаются цифры.
function buildStudyNow(s) {
    var sn = s.study_now;
    if (!sn) return '';
    var html = '';
    if (sn.unreviewed) {
        html += '<span class="profile-badge no">Не проверено: ' + sn.unreviewed + '</span>';
        if (!IS_ARCHIVE_VIEW) {
            html += '<a class="btn-outline" href="/cabinet/staff/students-review/' + s.id
                + (sn.review_week ? '?week=' + esc(sn.review_week) : '') + '">Проверить</a>';
        }
    } else {
        html += '<span class="profile-badge ok">Всё проверено</span>';
    }
    var pa = sn.point_a;
    if (pa) {
        if (!pa.has_plates) {
            html += '<span class="profile-badge">Точка А: работ пока нет</span>';
        } else {
            var paText = pa.is_done
                ? 'Точка А: ' + pa.average + ' / 100'
                : 'Точка А: оценено не всё' + (pa.average != null ? ', пока ' + pa.average : '');
            html += '<span class="profile-badge ' + (pa.is_done ? 'ok' : 'no') + '">' + esc(paText) + '</span>';
            if (!IS_ARCHIVE_VIEW) {
                html += '<a class="btn-outline" href="/cabinet/staff/point-a/' + s.id + '">Открыть точку А</a>';
            }
        }
    }
    return '<div class="profile-field full-width">'
        + '<div class="profile-field-label">Учёба сейчас</div>'
        + '<div class="profile-status-badges">' + html + '</div></div>';
}

// ── Задания из ленты ─────────────────────────────────────────────────────────
// Только чтение: что ученик ответил и сдал в заданиях. Проверяют на экране
// «Проверка по ученику» (правило 12) и в диалогах сдач — туда ведут ссылки.
function buildTasks(data) {
    var items = data.items || [];
    var sid = data.student.id;
    var html = '<div class="section-title">Задания из ленты</div>';
    if (!items.length) {
        return html + '<div class="no-works">В заданиях ученик пока ничего не сдал и не ответил</div>';
    }
    items.forEach(function(it, idx) {
        var badge = it.needs_revision ? '<span class="profile-badge no">На доработке</span>'
            : (it.is_reviewed ? '<span class="profile-badge ok">Проверено</span>'
                              : '<span class="profile-badge no">Не проверено</span>');
        var meta = [it.subject, it.date_label ? formatMockDate(it.date_label) : '']
            .filter(Boolean).join(' · ');
        html += '<div class="profile-details">'
            + '<div class="profile-status-badges">'
            +   '<span class="profile-field-value">' + esc(it.title) + '</span>' + badge
            + '</div>'
            + (meta ? '<div class="profile-field-label">' + esc(meta) + '</div>' : '');
        if (it.question) html += '<div class="profile-field-value">' + esc(it.question) + '</div>';
        if (it.chosen && it.chosen.length) html += '<div class="profile-about">' + esc(it.chosen.join(', ')) + '</div>';
        if (it.text) html += '<div class="profile-about">' + esc(it.text) + '</div>';
        if (it.images && it.images.length) {
            html += '<div class="month-grid" data-gallery="task-' + sid + '-' + idx + '">';
            it.images.forEach(function(url) {
                html += '<button type="button" class="photo-zoom-button" onclick="openGallery(this.firstElementChild)" aria-label="Открыть фото">'
                    + '<img src="' + esc(url) + '" alt="' + esc(it.title) + '" loading="lazy"></button>';
            });
            html += '</div>';
        }
        if (it.review_comment) {
            html += '<div class="profile-field-label">Комментарий преподавателя</div>'
                + '<div class="profile-about">' + esc(it.review_comment) + '</div>';
        }
        if (!IS_ARCHIVE_VIEW) {
            var url = it.review_url
                || ('/cabinet/staff/students-review/' + sid + (it.date_label ? '?week=' + esc(it.date_label) : ''));
            html += '<a class="btn-outline" href="' + esc(url) + '">Открыть</a>';
        }
        html += '</div>';
    });
    return html;
}

function profileField(label, value, fullWidth) {
    var cls = 'profile-field' + (fullWidth ? ' full-width' : '');
    var valCls = value != null && value !== '' ? 'profile-field-value' : 'profile-field-value empty';
    var display = value != null && value !== '' ? esc(String(value)) : 'Не указано';
    return '<div class="' + cls + '">'
        + '<div class="profile-field-label">' + esc(label) + '</div>'
        + '<div class="' + valCls + '">' + display + '</div></div>';
}

// ── Tab switching ────────────────────────────────────────────────────────────

function switchTab(tabName) {
    if (_currentTab && tabName !== _currentTab && !guardUnsavedScore()) return;
    _currentTab = tabName;
    if (_viewMode === 'tab' && _currentStudentId) navCommit(false);

    ['portfolio', 'tasks', 'mock-exams', 'statistics'].forEach(function(t) {
        var btn = document.getElementById('tab-' + t);
        if (btn) btn.classList.toggle('active', t === tabName);
    });

    if (!_currentStudentId) return;

    // Serve from cache if available
    if (_tabCache[_currentStudentId][tabName]) {
        renderTab(tabName, _tabCache[_currentStudentId][tabName]);
        return;
    }

    // Show loading indicator in tab-content only
    var tc = document.getElementById('tab-content');
    if (tc) tc.innerHTML = '<div class="tab-loading">Загружаем…</div>';

    fetch('/cabinet/students/' + _currentStudentId + '/' + tabName)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _tabCache[_currentStudentId][tabName] = data;
            renderTab(tabName, data);
        })
        .catch(function() {
            var tc = document.getElementById('tab-content');
            if (tc) tc.innerHTML = '<div class="empty-state"><div class="empty-icon"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div><div class="empty-text">Не удалось открыть вкладку. Проверьте интернет и нажмите на неё ещё раз.</div></div>';
        });
}

// ── Render dispatch ──────────────────────────────────────────────────────────

function renderTab(tabName, data) {
    var heroEl = document.getElementById('student-hero-container');
    var bySubj = data.student ? (data.student.avg_score_by_subject || null) : null;
    if (heroEl && data.student) heroEl.innerHTML = buildHero(data.student, bySubj);

    var tc = document.getElementById('tab-content');
    if (!tc) return;

    var backBtn = '<button class="back-to-profile" onclick="backToProfile()">← К профилю</button>';

    if (tabName === 'portfolio')  {
        tc.innerHTML = backBtn + buildPortfolio(data);
        if (window.CYCCAL) {
            CYCCAL.init({
                key: 'staff-portfolio-mock',
                rootId: 'portfolio-mock-cal',
                subjects: data.mock_subjects || [],
                worksBySubject: data.mock_works_by_subject || {},
                months: MONTHS_LIST,
                currentYear: new Date().getFullYear(),
                emptyText: 'Пробников пока нет',
                showFeedback: false,
                showStages: false
            });
        }
    }
    if (tabName === 'tasks')      { tc.innerHTML = backBtn + buildTasks(data);      }
    if (tabName === 'mock-exams') { tc.innerHTML = backBtn + buildMockExams(data);  }
    if (tabName === 'statistics') { tc.innerHTML = backBtn + buildStatistics(data); }
}

// ── Statistics: динамика баллов по пробникам (инлайн-SVG) ────────────────────
function buildStatistics(data) {
    var pts = data.points || [];
    var title = '<div class="section-title">Динамика баллов по пробникам</div>';

    var hasData = pts.some(function(p) { return p.drawing != null || p.composition != null; });
    if (!pts.length || !hasData) {
        return title + '<div class="no-works">График появится после первого пробника с баллом.</div>';
    }

    // Геометрия
    var W = 720, H = 320;
    var padL = 38, padR = 16, padT = 16, padB = 56;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var n = pts.length;
    var xFor = function(i) { return padL + (n === 1 ? plotW / 2 : plotW * i / (n - 1)); };
    var yFor = function(v) { return padT + plotH * (1 - v / 100); };

    function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

    // Сетка + ось Y (0..100 шаг 20)
    var grid = '';
    for (var g = 0; g <= 100; g += 20) {
        var gy = yFor(g);
        grid += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy + '" stroke="var(--line)" stroke-width="1"/>';
        grid += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" text-anchor="end" font-size="11" fill="var(--dim)">' + g + '</text>';
    }

    // Подписи месяцев по X (короткий формат: первые 3 буквы месяца)
    var xLabels = '';
    pts.forEach(function(p, i) {
        var short = String(p.label || '').split(' ')[0].slice(0, 3);
        xLabels += '<text x="' + xFor(i) + '" y="' + (H - padB + 18) + '" text-anchor="middle" font-size="10" fill="var(--dim)">' + esc(short) + '</text>';
    });

    // Сегменты: разрываем линию на соседних не-null точках (null = разрыв графика)
    function buildSegments(key) {
        var segs = [];
        var current = [];
        pts.forEach(function(p, i) {
            var v = p[key];
            if (v == null) {
                if (current.length) segs.push(current);
                current = [];
                return;
            }
            current.push({ x: xFor(i), y: yFor(v), label: p.label, value: v });
        });
        if (current.length) segs.push(current);
        return segs;
    }

    // Плавная кривая через точки сегмента (Catmull-Rom → кубический Безье),
    // как в линейных графиках биржевых котировок.
    function smoothPath(points) {
        var d = 'M' + points[0].x.toFixed(2) + ',' + points[0].y.toFixed(2);
        for (var i = 0; i < points.length - 1; i++) {
            var p0 = points[i === 0 ? 0 : i - 1];
            var p1 = points[i];
            var p2 = points[i + 1];
            var p3 = points[i + 2 < points.length ? i + 2 : i + 1];
            var c1x = p1.x + (p2.x - p0.x) / 6;
            var c1y = p1.y + (p2.y - p0.y) / 6;
            var c2x = p2.x - (p3.x - p1.x) / 6;
            var c2y = p2.y - (p3.y - p1.y) / 6;
            d += ' C' + c1x.toFixed(2) + ',' + c1y.toFixed(2) + ' ' + c2x.toFixed(2) + ',' + c2y.toFixed(2) + ' ' + p2.x.toFixed(2) + ',' + p2.y.toFixed(2);
        }
        return d;
    }

    var baseY = padT + plotH;

    function renderArea(segs, fillId) {
        var out = '';
        segs.forEach(function(seg) {
            if (seg.length < 2) return;
            var first = seg[0], last = seg[seg.length - 1];
            out += '<path d="' + smoothPath(seg) + ' L' + last.x.toFixed(2) + ',' + baseY + ' L' + first.x.toFixed(2) + ',' + baseY + ' Z" fill="url(#' + fillId + ')" stroke="none"/>';
        });
        return out;
    }

    function renderLine(segs, color) {
        var out = '';
        segs.forEach(function(seg) {
            if (seg.length < 2) return;
            out += '<path d="' + smoothPath(seg) + '" fill="none" stroke="' + color + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
        });
        return out;
    }

    function renderDots(segs, color) {
        var out = '';
        segs.forEach(function(seg) {
            seg.forEach(function(p) {
                out += '<circle cx="' + p.x.toFixed(2) + '" cy="' + p.y.toFixed(2) + '" r="3.5" fill="' + color + '"/>';
                out += '<title>' + esc(p.label) + ': ' + p.value + '</title>';
            });
        });
        return out;
    }

    var COLOR_DRAW = '#3B5BFF';   // Рисунок
    var COLOR_COMP = '#F59E0B';   // Композиция

    var drawSegs = buildSegments('drawing');
    var compSegs = buildSegments('composition');

    var defs = '<defs>' +
        '<linearGradient id="statGradDraw" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="' + COLOR_DRAW + '" stop-opacity="0.22"/>' +
            '<stop offset="100%" stop-color="' + COLOR_DRAW + '" stop-opacity="0"/>' +
        '</linearGradient>' +
        '<linearGradient id="statGradComp" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="' + COLOR_COMP + '" stop-opacity="0.22"/>' +
            '<stop offset="100%" stop-color="' + COLOR_COMP + '" stop-opacity="0"/>' +
        '</linearGradient>' +
        '</defs>';

    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Динамика баллов по пробникам">' +
        defs + grid + xLabels +
        renderArea(drawSegs, 'statGradDraw') + renderArea(compSegs, 'statGradComp') +
        renderLine(drawSegs, COLOR_DRAW) + renderLine(compSegs, COLOR_COMP) +
        renderDots(drawSegs, COLOR_DRAW) + renderDots(compSegs, COLOR_COMP) +
        '</svg>';

    var legend = '<div class="stat-legend">' +
        '<span class="stat-legend-item"><span class="stat-dot" style="background:' + COLOR_DRAW + '"></span>Рисунок</span>' +
        '<span class="stat-legend-item"><span class="stat-dot" style="background:' + COLOR_COMP + '"></span>Композиция</span>' +
        '</div>';

    return title +
        '<div class="stat-chart-card">' + legend +
        '<div class="stat-chart-wrap">' + svg + '</div>' +
        '<div class="stat-note">Средний балл за месяц по пробникам с баллом.</div>' +
        '</div>';
}

// ── Hero ─────────────────────────────────────────────────────────────────────

function cohortBadgeHtml(tag) {
    var letters = {may: 'М', june: 'И', july: 'И', august: 'А'};
    if (!tag || !letters[tag]) return '';
    return '<span class="cohort-badge cohort-' + esc(tag) + '">' + letters[tag] + '</span>';
}

function buildHero(s, bySubj) {
    var avatar = s.photo_url
        ? '<img src="' + esc(s.photo_url) + '" class="student-hero-avatar" alt="" width="56" height="56">'
        : '<div class="student-hero-avatar-ph"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2"/></svg></div>';
    avatar = '<span class="avatar-wrap">' + avatar + cohortBadgeHtml(s.cohort_tag) + '</span>';
    var scoreHtml = '';
    var hasSubjScores = bySubj && MOCK_SUBJECTS.some(function(subj) { return bySubj[subj] != null; });
    if (hasSubjScores) {
        scoreHtml = '<div class="student-hero-score subj-scores">';
        MOCK_SUBJECTS.forEach(function(subj) {
            var sc = bySubj[subj];
            if (sc != null) {
                scoreHtml += '<div class="subj-score-item">'
                    + '<div class="score-big">' + sc + '</div>'
                    + '<div class="score-label">' + esc(SUBJECT_BADGE_LABEL[subj] || subj) + '</div>'
                    + '</div>';
            }
        });
        scoreHtml += '</div>';
    }
    var uploadBtn = '';
    if (CAN_SCORE && _viewMode === 'tab') {
        uploadBtn = '<div class="hero-upload-wrap">'
            + '<button class="admin-upload-btn" onclick="openUploadModal()">+ Загрузить</button>'
            + '</div>';
    }
    var periodPills = '';
    if (s.course_periods) {
        s.course_periods.split(',').forEach(function(p) {
            var abbrev = p.trim().split(' ')[0];
            if (abbrev) {
                var pcls = abbrev === '10-14' ? 'pink' : (abbrev === '15-20' ? 'blue' : 'purple');
                periodPills += '<span class="student-hero-pill student-hero-pill--' + pcls + '">' + esc(abbrev) + '</span>';
            }
        });
    }
    var lessonsCls = String(s.lessons_count) === '6' ? 'pink' : (String(s.lessons_count) === '8' ? 'blue' : 'purple');
    return '<div class="student-hero">'
        + avatar
        + '<div class="student-hero-info">'
        +   '<h2 class="student-hero-name">' + esc(s.name) + '</h2>'
        +   '<div class="student-hero-pills">'
        +     (s.tariff && s.tariff !== '—' ? '<span class="student-hero-pill">' + esc(s.tariff) + '</span>' : '')
        +     periodPills
        +     (s.lessons_count ? '<span class="student-hero-pill student-hero-pill--' + lessonsCls + '">' + esc(s.lessons_count) + '</span>' : '')
        +     (s.has_case ? '<span class="student-hero-pill">КЕЙС</span>' : '')
        +     (s.study_mode === 'offline' ? '<span class="student-hero-pill">ОЧНО</span>' : '')
        +     (s.study_mode === 'online' ? '<span class="student-hero-pill">ОНЛАЙН</span>' : '')
        +     (s.study_duration ? '<span class="student-hero-pill">' + esc(s.study_duration) + '</span>' : '')
        +   '</div>'
        + '</div>'
        + scoreHtml
        + uploadBtn
        + '</div>';
}

// ── Portfolio tab ────────────────────────────────────────────────────────────

function buildPortfolio(data) {
    var html = '';

    var sid = data.student.id;

    // «До» — одна плоская сетка без месяцев (владелец 09.09.2026: «в До
    // добавляется не по месяцам»). `.month-grid` вне `.portfolio-month` видна
    // по умолчанию, поэтому ни inline-стиля, ни правки CSS не нужно.
    // Вместе с месяцами у «До» пропала кнопка «Удалить папку»; поштучное
    // удаление работы через крестик осталось.
    html += '<div class="section-title">До обучения</div>';
    if (data.before_flat && data.before_flat.length) {
        html += '<div class="month-grid" data-gallery="before-' + sid + '">';
        data.before_flat.forEach(function(w) {
            if (!w.s3_url) return;
            var img = zoomPhoto(w);
            html += wrapPortfolioPhoto(img, sid, w.id, 'before', w.source);
        });
        html += '</div>';
    } else {
        html += '<div class="no-works">Работы не загружены</div>';
    }

    // After works by month
    html += '<div class="section-title">В процессе обучения</div>';
    if (data.after_by_month && data.after_by_month.length) {
        data.after_by_month.forEach(function(g, idx) {
            html += buildPortfolioMonthBlock(sid, 'after', g, 'after-' + idx);
        });
    } else {
        html += '<div class="no-works">Работы не загружены</div>';
    }

    // Пробные экзамены (финалки закрытых циклов) — тот же дневной календарь (CYCCAL),
    // что и в Портфолио ученика. Инициализируется в renderTab после вставки в DOM.
    html += '<div class="section-title">Пробные экзамены</div>';
    html += '<div class="subj-stack" id="portfolio-mock-cal"></div>';

    return html;
}

function buildPortfolioMonthBlock(sid, workType, g, blockId) {
    var label = cap(g.month) + ' ' + g.year;
    var safeId = 'portfolio-' + sid + '-' + blockId + '-' + String(g.year) + '-' + String(g.month).replace(/[^a-zA-Zа-яА-Я0-9_-]/g, '');
    var renameBtn = IS_SUPERADMIN && workType === 'after'
        ? '<button type="button" class="portfolio-rename-btn" onclick="event.stopPropagation();renamePortfolioMonth(' + sid + ',\'' + workType + '\',\'' + esc(g.month) + '\',' + g.year + ')">Переименовать</button>'
        : '';
    // Удаление папки считаем по `work_total`, а не по `total`: месяц
    // показывает и снимки, сданные внутри заданий, а bulk-роут работ их не
    // трогает. Иначе подтверждение обещало бы удалить больше, чем удалит.
    // Месяц без единой настоящей работы кнопку не получает вовсе.
    var workTotal = (g.work_total === undefined) ? g.total : g.work_total;
    var delBtn = (CAN_SCORE && workType !== 'mock' && workTotal > 0)
        ? '<button type="button" class="folder-del-btn" onclick="event.stopPropagation();deleteFolderWorks(' + sid + ',\'' + workType + '\',\'' + esc(g.month) + '\',' + g.year + ',' + workTotal + ')"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Удалить</button>'
        : '';
    var dropAttrs = IS_SUPERADMIN && workType === 'after'
        ? ' ondragover="portfolioAllowDrop(event,this)" ondragenter="portfolioDragEnter(event,this)" ondragleave="portfolioDragLeave(event,this)" ondrop="portfolioDrop(event,this,' + sid + ',\'' + esc(g.month) + '\',' + g.year + ')"'
        : '';
    var html = '<div class="month-block portfolio-month" id="' + safeId + '"' + dropAttrs + '>';
    html += '<div class="month-header">'
        + '<button type="button" class="portfolio-toggle" aria-expanded="false" onclick="togglePortfolioMonth(\'' + safeId + '\')">'
        + '<span class="portfolio-chevron">▼</span>'
        + '<span class="month-label">' + esc(label) + '</span>'
        + '</button>'
        + '<div class="month-actions">'
        + '<span class="month-count">' + g.total + ' фото</span>'
        + renameBtn
        + delBtn
        + '</div></div>';
    html += '<div class="month-grid" style="display:none" data-gallery="' + workType + '-' + sid + '-' + esc(g.year) + '-' + esc(g.month) + '">';
    g.works.forEach(function(w) {
        if (w.s3_url) {
            var img = zoomPhoto(w);
            html += wrapPortfolioPhoto(img, sid, w.id, workType, w.source);
        }
    });
    html += '</div></div>';
    return html;
}

function togglePortfolioMonth(id) {
    var el = document.getElementById(id);
    if (!el) return;
    var isOpen = el.classList.toggle('open');
    var grid = el.querySelector('.month-grid');
    if (grid) grid.style.display = isOpen ? 'grid' : 'none';
    var btn = el.querySelector('.portfolio-toggle');
    if (btn) btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

function normalizePortfolioMonth(value) {
    var v = String(value || '').trim().toLowerCase();
    for (var i = 0; i < MONTHS_LIST.length; i++) {
        if (String(MONTHS_LIST[i]).toLowerCase() === v) return MONTHS_LIST[i];
    }
    return '';
}

function reloadPortfolioAfterManage(message) {
    if (_tabCache[_currentStudentId]) {
        delete _tabCache[_currentStudentId].portfolio;
        delete _tabCache[_currentStudentId].profile;
    }
    switchTab('portfolio');
    if (message) showToast(message);
}

function renamePortfolioMonth(sid, workType, fromMonth, fromYear) {
    if (!IS_SUPERADMIN) return;
    var monthInput = prompt('Новый месяц', fromMonth);
    if (monthInput == null) return;
    var toMonth = normalizePortfolioMonth(monthInput);
    if (!toMonth) {
        alert('Укажите месяц из списка: ' + MONTHS_LIST.join(', '));
        return;
    }
    var yearInput = prompt('Новый год', String(fromYear));
    if (yearInput == null) return;
    var toYear = parseInt(yearInput, 10);
    if (!toYear || toYear < 2000 || toYear > 2100) {
        alert('Год – четыре цифры, например ' + CURRENT_YEAR);
        return;
    }

    window.csrfFetch('/cabinet/students/' + sid + '/portfolio/month', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            work_type: workType,
            from_month: fromMonth,
            from_year: fromYear,
            to_month: toMonth,
            to_year: toYear
        }),
    })
    .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
    .then(function(result) {
        if (!result.ok || !result.data.ok) {
            alert(window.csrfMessage(result.data, 'Не удалось переименовать месяц'));
            return;
        }
        reloadPortfolioAfterManage('Месяц обновлён');
    })
    .catch(function() { alert(NET_ERROR); });
}

function portfolioDragStart(ev, workId) {
    if (!IS_SUPERADMIN) return;
    _portfolioDragWorkId = workId;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', String(workId));
}

function portfolioDragEnd() {
    _portfolioDragWorkId = null;
    document.querySelectorAll('.portfolio-month.is-drop-target').forEach(function(el) {
        el.classList.remove('is-drop-target');
    });
}

function portfolioAllowDrop(ev, el) {
    if (!IS_SUPERADMIN || !_portfolioDragWorkId) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
}

function portfolioDragEnter(ev, el) {
    if (!IS_SUPERADMIN || !_portfolioDragWorkId) return;
    ev.preventDefault();
    el.classList.add('is-drop-target');
}

function portfolioDragLeave(ev, el) {
    if (el.contains(ev.relatedTarget)) return;
    el.classList.remove('is-drop-target');
}

function portfolioDrop(ev, el, sid, toMonth, toYear) {
    if (!IS_SUPERADMIN) return;
    ev.preventDefault();
    el.classList.remove('is-drop-target');
    var workId = parseInt(ev.dataTransfer.getData('text/plain') || _portfolioDragWorkId, 10);
    if (!workId) return;

    window.csrfFetch('/cabinet/students/' + sid + '/portfolio/works/' + workId + '/move', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({to_month: toMonth, to_year: toYear}),
    })
    .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
    .then(function(result) {
        if (!result.ok || !result.data.ok) {
            alert(window.csrfMessage(result.data, 'Не удалось перенести работу'));
            return;
        }
        reloadPortfolioAfterManage('Работа перенесена');
    })
    .catch(function() { alert(NET_ERROR); })
    .finally(function() { portfolioDragEnd(); });
}

// ── Mock exams tab ───────────────────────────────────────────────────────────

function buildMockExams(data) {
    var html = '';
    html += '<div class="subjects-grid">';
    MOCK_SUBJECTS.forEach(function(subject, idx) {
        html += buildSubjectCard(subject, data, idx);
    });
    html += '</div>';
    html += buildLegacyArchive(data.legacy_by_month || []);
    return html;
}

function buildLegacyArchive(groups) {
    if (!groups.length) return '';
    var html = '<div class="legacy-archive">'
        + '<div class="section-label">'
        + 'Архив (импорт из старого чат-бота)</div>';
    groups.forEach(function(g, i) {
        html += '<div class="legacy-month">'
            + '<div class="legacy-month-title">'
            + esc(g.year + ' — ' + g.month) + ' <span class="legacy-month-count">' + g.total + ' фото</span></div>'
            + '<div class="legacy-grid">';
        g.photos.forEach(function(p) {
            if (!p.s3_url) return;
            html += '<button type="button" class="photo-zoom-button" onclick="openGallery(this.firstElementChild)" aria-label="Открыть фото">'
                + '<img src="' + esc(p.s3_url) + '" alt="' + esc(p.filename) + '" loading="lazy" '
                + 'class="legacy-photo"></button>';
        });
        html += '</div></div>';
    });
    html += '</div>';
    return html;
}

function buildSubjectCard(subject, data, subjectIndex) {
    var works    = (data.mock_works || {})[subject] || [];
    var lock     = (data.mock_locks || {})[subject] || {};
    var isLocked = lock.is_locked === true;
    var sid      = data.student.id;
    var isScored = works.some(function(w) { return w.score != null; });
    var grouped  = groupMockWorksByDate(works);
    var state    = getMockCalendarState(sid, subjectIndex, grouped);

    // Header: subject title + lock status
    var lockBadge = isLocked
        ? '<span class="lock-badge locked"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Повторная сдача закрыта</span>'
        : '<span class="lock-badge open"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg> Повторная сдача открыта</span>';

    var html = '<div class="subject-card' + (isScored ? ' is-scored' : '') + '">'
        + '<div class="subject-card-header">'
        +   '<span class="subject-title">' + esc(subject) + '</span>'
        +   lockBadge
        + '</div>'
        + '<div class="subject-card-body">';

    // Unlock button (allow retry) — only for admin/superadmin, only when locked
    if (CAN_SCORE && isLocked) {
        html += '<div class="unlock-wrap">'
            + '<form method="post" action="/cabinet/students/' + sid + '/mock-exams/unlock" class="unlock-form" onsubmit="return submitWithFreshToken(this)">'
            + '<input type="hidden" name="csrf_token" value="' + CSRF_TOKEN + '">'
            + '<input type="hidden" name="subject" value="' + esc(subject) + '">'
            + '<button type="submit" class="unlock-btn" title="Ученик сможет заново загрузить пробник по этому предмету"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Разрешить пересдачу</button>'
            + '</form></div>';
    }

    if (works.length) {
        html += '<div class="mock-calendar-layout">'
            + buildMockCalendarSide(sid, subjectIndex, grouped, state)
            + buildMockDayPanel(sid, subject, grouped, state)
            + '</div>';
    } else {
        html += '<div class="no-works">Нет загруженных работ</div>';
    }

    html += '</div></div>';
    return html;
}

function mockStateKey(sid, subjectIndex) {
    return sid + ':' + subjectIndex;
}

function getMockWorkDate(w) {
    if (w.work_date) return w.work_date;
    if (!w.created_at) return '';
    try { return new Date(w.created_at).toISOString().slice(0, 10); }
    catch (e) { return ''; }
}

function groupMockWorksByDate(works) {
    var grouped = {};
    works.forEach(function(w) {
        var dateStr = getMockWorkDate(w);
        if (!dateStr) return;
        if (!grouped[dateStr]) grouped[dateStr] = [];
        grouped[dateStr].push(w);
    });
    return grouped;
}

function findLatestMockDate(grouped, year, month) {
    return Object.keys(grouped).filter(function(dateStr) {
        var parts = dateStr.split('-');
        if (parseInt(parts[0], 10) !== year) return false;
        if (month != null && parseInt(parts[1], 10) !== month + 1) return false;
        return true;
    }).sort().reverse()[0] || '';
}

function getMockCalendarState(sid, subjectIndex, grouped) {
    var key = mockStateKey(sid, subjectIndex);
    var now = new Date();
    if (!_mockCalendarState[key]) {
        var latestThisYear = findLatestMockDate(grouped, CURRENT_YEAR, null);
        _mockCalendarState[key] = {
            year: CURRENT_YEAR,
            month: latestThisYear ? parseInt(latestThisYear.split('-')[1], 10) - 1 : now.getMonth(),
            selectedDate: latestThisYear || ''
        };
    }
    var state = _mockCalendarState[key];
    if (state.selectedDate && !grouped[state.selectedDate]) {
        state.selectedDate = findLatestMockDate(grouped, state.year, state.month) || findLatestMockDate(grouped, state.year, null);
        if (state.selectedDate) state.month = parseInt(state.selectedDate.split('-')[1], 10) - 1;
    }
    return state;
}

function rerenderMockTab() {
    if (_currentTab === 'mock-exams') {
        var data = _tabCache[_currentStudentId] && _tabCache[_currentStudentId]['mock-exams'];
        if (data) renderTab('mock-exams', data);
    }
}

function setMockCalendarYear(sid, subjectIndex, delta) {
    if (!guardUnsavedScore()) return;
    var key = mockStateKey(sid, subjectIndex);
    var state = _mockCalendarState[key] || { year: CURRENT_YEAR, month: new Date().getMonth(), selectedDate: '' };
    state.year += delta;
    state.selectedDate = '';
    _mockCalendarState[key] = state;
    rerenderMockTab();
}

function setMockCalendarMonth(sid, subjectIndex, month) {
    if (!guardUnsavedScore()) return;
    var key = mockStateKey(sid, subjectIndex);
    var state = _mockCalendarState[key] || { year: CURRENT_YEAR, month: month, selectedDate: '' };
    state.month = month;
    state.selectedDate = '';
    _mockCalendarState[key] = state;
    rerenderMockTab();
}

function selectMockDay(sid, subjectIndex, dateStr) {
    if (!guardUnsavedScore()) return;
    var parts = dateStr.split('-');
    _mockCalendarState[mockStateKey(sid, subjectIndex)] = {
        year: parseInt(parts[0], 10),
        month: parseInt(parts[1], 10) - 1,
        selectedDate: dateStr
    };
    rerenderMockTab();
}

// Телефон: работа стоит над календарём, а день выбирают под ней. После выбора дня
// подводим экран к работе — в «Пробниках» и в календаре «Портфолио»
// (partials/cycle_calendar_lib.html). Оба календаря перерисовываются целиком,
// поэтому какой из них нажат, запоминаем до перерисовки — в фазе перехвата.
document.getElementById('main-panel').addEventListener('click', function(ev) {
    var day = ev.target.closest('.mock-day.has-works, .cal-day.has-works');
    if (!day || !window.matchMedia('(max-width: 768px)').matches) return;
    var sel = '.mock-calendar-layout, .cal-layout';
    var idx = Array.prototype.indexOf.call(this.querySelectorAll(sel), day.closest(sel));
    var panel = this;
    setTimeout(function() {
        var layout = panel.querySelectorAll(sel)[idx];
        var card = layout && layout.querySelector('.mock-day-card, .cal-detail');
        var top = card ? card.getBoundingClientRect().top : 0;
        if (top < 0) window.scrollBy(0, top - 12);
    }, 0);
}, true);

function buildMockCalendarSide(sid, subjectIndex, grouped, state) {
    var html = '<div class="mock-calendar-side">';
    html += '<div class="mock-year-row">'
        + '<button type="button" class="mock-year-btn" aria-label="Предыдущий год" onclick="setMockCalendarYear(' + sid + ',' + subjectIndex + ',-1)">‹</button>'
        + '<div class="mock-year-label">' + state.year + '</div>'
        + '<button type="button" class="mock-year-btn" aria-label="Следующий год" onclick="setMockCalendarYear(' + sid + ',' + subjectIndex + ',1)">›</button>'
        + '</div>';

    html += '<div class="mock-month-list">';
    MONTHS_LIST.forEach(function(monthName, idx) {
        html += '<button type="button" class="mock-month-btn' + (idx === state.month ? ' active' : '') + '" onclick="setMockCalendarMonth(' + sid + ',' + subjectIndex + ',' + idx + ')">' + esc(cap(monthName).slice(0, 3)) + '</button>';
    });
    html += '</div>';

    html += '<div class="mock-calendar-grid">';
    ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].forEach(function(day) {
        html += '<div class="mock-weekday">' + day + '</div>';
    });
    var first = new Date(state.year, state.month, 1);
    var offset = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(state.year, state.month + 1, 0).getDate();
    for (var i = 0; i < offset; i++) html += '<div class="mock-day is-empty"></div>';
    for (var d = 1; d <= daysInMonth; d++) {
        var dateStr = state.year + '-' + String(state.month + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
        var hasWorks = !!grouped[dateStr];
        var cls = 'mock-day' + (hasWorks ? ' has-works' : '') + (state.selectedDate === dateStr ? ' is-selected' : '');
        var click = hasWorks ? ' onclick="selectMockDay(' + sid + ',' + subjectIndex + ',\'' + dateStr + '\')"' : '';
        html += '<button type="button" class="' + cls + '"' + click + '>' + d + '</button>';
    }
    html += '</div></div>';
    return html;
}

function formatMockDate(dateStr) {
    if (!dateStr) return '';
    var parts = dateStr.split('-');
    return parts[2] + '.' + parts[1] + '.' + parts[0];
}

function scoreBadgeClass(score) {
    if (score == null) return 'score-none';
    var s = parseFloat(score);
    if (s <= 30) return 'score-red';
    if (s <= 60) return 'score-orange';
    if (s <= 74) return 'score-lime';
    return 'score-green';
}

function buildMockDayPanel(sid, subject, grouped, state) {
    var selected = state.selectedDate && grouped[state.selectedDate] ? state.selectedDate : findLatestMockDate(grouped, state.year, state.month);
    var works = selected ? grouped[selected] || [] : [];
    var html = '<div class="mock-day-card">';
    if (!works.length) {
        return html + '<div class="mock-calendar-empty">В этом месяце пока нет загруженных работ</div></div>';
    }
    html += '<div class="mock-day-head">'
        + '<div class="mock-day-title">' + formatMockDate(selected) + '</div>'
        + '<div class="mock-day-count">' + workCountLabel(works.length) + '</div>'
        + '</div>';
    // Primary = оцененная работа (если есть), иначе первая
    var primary = works.find(function(w){ return w.score != null; }) || works[0];
    var rest = works.filter(function(w){ return w !== primary; });
    var gallery = 'mock-' + sid + '-' + esc(subject) + '-' + esc(selected);

    function photoWrap(w, hero) {
        if (!w.s3_url) return '';
        var bc = scoreBadgeClass(w.score);
        var bt = w.score != null ? Math.round(w.score) + '/100' : '—';
        var imgStyle = hero
            ? 'class="mock-photo-hero"'
            : 'class="mock-photo-thumb"';
        // Большой снимок дня — на всю ширину, ему нужно само фото.
        var img = zoomPhoto(hero ? {s3_url: w.s3_url, filename: w.filename} : w, imgStyle);
        var badge = '<span class="work-score-badge ' + bc + '">' + bt + '</span>';
        // Справа сверху стоит бейдж балла, поэтому крестик — в левом углу.
        var delBtn = CAN_SCORE
            ? '<button type="button" class="photo-del photo-del--left" onclick="event.stopPropagation();deleteWork(' + sid + ',' + w.id + ',this)" title="Удалить" aria-label="Удалить работу">&times;</button>'
            : '';
        return '<div class="photo-wrap photo-wrap--block">' + img + badge + delBtn + '</div>';
    }

    if (rest.length === 0) {
        // Одна работа
        html += '<div data-gallery="' + gallery + '" class="mock-single">';
        html += photoWrap(primary, true);
        html += '</div>';
    } else {
        // Несколько работ — hero + thumbnails (все в одном data-gallery)
        html += '<div data-gallery="' + gallery + '">';
        html += '<div class="mock-hero-layout">';
        html += '<div class="mock-hero-photo">' + photoWrap(primary, true) + '</div>';
        html += '<div class="mock-thumbnails">';
        rest.forEach(function(w) { html += photoWrap(w, false); });
        html += '</div>';
        html += '</div>'; // .mock-hero-layout
        html += '</div>'; // data-gallery wrapper
    }

    // Комментарий куратора — только по primary
    if (primary.comment_html) {
        html += '<div class="mock-comment">' + primary.comment_html + '</div>';
    }
    if (CAN_SCORE) {
        html += buildScoreFormOrEditButton(sid, primary.id, 'mock-exams', primary.score, primary.comment);
    }
    html += buildFeedbackButton(primary);

    html += '</div>';
    return html;
}

function buildFeedbackButton(w) {
    if (!w || !w.cycle_id) return '';
    var role = (typeof USER_ROLE_RANK !== 'undefined') ? USER_ROLE_RANK : 0;
    if (role < 2) return '';
    var prefix = role >= 5 ? '/cabinet/superadmin/feedback/'
              : role >= 4 ? '/cabinet/admin/feedback/'
              : '/cabinet/curator/feedback/';
    var hasFb = !!w.has_feedback;
    var lbl = hasFb ? 'Открыть обратную связь' : 'Дать обратную связь';
    return '<a href="' + prefix + w.cycle_id + '#work-' + w.id + '" '
         + 'class="work-fb-link' + (hasFb ? ' work-fb-link--done' : '') + '">'
         + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
         + '<span>' + lbl + '</span>'
         + '</a>';
}

// ── Scoring form builder ─────────────────────────────────────────────────────

function scoreFormValue(root, selector) {
    var el = root ? root.querySelector(selector) : null;
    return el ? String(el.value || '').trim() : '';
}

function getDirtyScoreForm() {
    var forms = document.querySelectorAll('.score-form');
    for (var i = 0; i < forms.length; i++) {
        var root = forms[i];
        if (root.dataset.saving === '1') continue;
        var score = scoreFormValue(root, 'input[name="score"]');
        var comment = scoreFormValue(root, 'textarea[name="comment"]');
        var initialScore = root.dataset.initialScore || '';
        var initialComment = root.dataset.initialComment || '';
        if (score !== initialScore || comment !== initialComment) return root;
    }
    return null;
}

function guardUnsavedScore() {
    var dirty = getDirtyScoreForm();
    if (!dirty) return true;
    alert('Сначала сохраните балл, потом переходите к другой работе.');
    var input = dirty.querySelector('input[name="score"]');
    if (input) input.focus();
    return false;
}

window.addEventListener('beforeunload', function(ev) {
    if (!getDirtyScoreForm()) return;
    ev.preventDefault();
    ev.returnValue = '';
});

function buildScoreForm(sid, workId, tab, currentScore, currentComment) {
    var formId = 'score-form-' + tab + '-' + workId;
    var isScoringTab = (tab === 'mock-exams');
    var saveLabel = isScoringTab ? 'Сохранить балл' : 'Сохранить';
    var formLabel = isScoringTab ? 'Балл и комментарий' : (currentScore != null ? 'Изменить балл' : 'Поставить балл');
    var initialScore = currentScore != null ? String(Math.round(currentScore)) : '';
    var initialComment = currentComment ? esc(currentComment) : '';
    return '<div class="score-form" data-initial-score="' + esc(initialScore) + '" data-initial-comment="' + initialComment + '">'
        + '<div class="score-form-label">' + formLabel + '</div>'
        + '<form id="' + formId + '" method="post" action="/cabinet/students/' + sid + '/works/' + workId + '/score" onsubmit="return submitWithFreshToken(this)">'
        + '<input type="hidden" name="csrf_token" value="' + CSRF_TOKEN + '">'
        + '<input type="hidden" name="tab" value="' + esc(tab) + '">'
        + '<div class="score-form-row">'
        + '<input type="number" name="score" class="score-input" min="0" max="100" step="1"'
        + (currentScore != null ? ' value="' + Math.round(currentScore) + '"' : '')
        + ' placeholder="0–100" required>'
        + '<span class="score-of">/ 100</span>'
        + '</div>'
        + '<textarea data-rich-text name="comment" class="comment-input" placeholder="Комментарий (необязательно)" maxlength="500">'
        + (currentComment ? esc(currentComment) : '')
        + '</textarea>'
        + '<div class="score-form-actions">'
        + '<button type="submit" class="btn-blue btn-save"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> ' + saveLabel + '</button>'
        + (tab === 'mock-exams'
            ? (IS_SUPERADMIN && currentScore == null ? '<button type="button" class="score-revision-btn" onclick="submitMockRevision(' + sid + ',' + workId + ',\'' + formId + '\')"><svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> Вернуть на доработку</button>' : '')
            : '')
        + '</div>'
        + '</form>'
        + '</div>';
}

// Балл и разблокировка пересдачи уходят обычной формой с перезагрузкой
// страницы. Ключ в скрытом поле печатается при рендере и старится вместе с
// вкладкой, которую куратор держит открытой весь день, — перед отправкой
// подменяем его свежим (`static/js/csrf.js`). `form.submit()` событие submit
// не вызывает, поэтому повторного входа сюда нет; кнопка гаснет от двойного нажатия.
function submitWithFreshToken(form) {
    var btn = form.querySelector('[type="submit"]');
    var scoreForm = form.closest('.score-form');
    if (scoreForm) scoreForm.dataset.saving = '1';
    if (btn) {
        btn.disabled = true;
        if (scoreForm) btn.textContent = 'Сохраняем…';
    }
    var fresh = window.csrfFresh ? window.csrfFresh() : Promise.resolve('');
    fresh.catch(function() { return ''; }).then(function(token) {
        if (token) form.querySelector('[name="csrf_token"]').value = token;
        form.submit();
    });
    return false;
}

function buildScoreFormOrEditButton(sid, workId, tab, score, comment) {
    // Для пробников: если балл уже выставлен — показываем только «Изменить балл»,
    // форма раскрывается по клику. Для остальных вкладок поведение прежнее.
    if (tab === 'mock-exams' && score != null) {
        return '<div id="form-wrap-' + workId + '" class="edit-score-wrap">'
            + '<button type="button" class="btn-edit-score"'
            +   ' data-sid="' + sid + '" data-wid="' + workId + '" data-tab="' + esc(tab) + '"'
            +   ' data-score="' + Math.round(score) + '" data-comment="' + esc(comment || '') + '"'
            +   ' onclick="showMockScoreForm(this)">'
            +   '<svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true">'
            +   '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
            +   '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>'
            +   ' Изменить балл</button>'
            + '</div>';
    }
    return buildScoreForm(sid, workId, tab, score, comment);
}

function showMockScoreForm(btn) {
    var wrap = btn.closest('.edit-score-wrap');
    if (!wrap) return;
    var score = btn.dataset.score !== '' ? +btn.dataset.score : null;
    var parent = wrap.parentNode;
    wrap.outerHTML = buildScoreForm(+btn.dataset.sid, +btn.dataset.wid, btn.dataset.tab || 'mock-exams', score, btn.dataset.comment || '');
    window.RichTextField.enhanceAll(parent);
}

// ── Toggle edit form (scored works) ──────────────────────────────────────────

// Ответ мутации: JSON при успехе, иначе ошибка с причиной от сервера
// (`error` у роутов, `detail` у HTTPException — например «обнови страницу»).
function readJsonOrThrow(r) {
    return r.json().catch(function() { return {}; }).then(function(body) {
        if (r.ok) return body;
        var err = new Error('bad status');
        err.serverMessage = window.csrfMessage(body, '');
        throw err;
    });
}

function submitMockRevision(sid, workId, formId) {
    if (!confirm('Вернуть пробник на доработку? Ученик загрузит фото заново, балл не ставится.')) return;
    var fd = new FormData();
    var form = document.getElementById(formId);
    if (form) form.closest('.score-form').dataset.saving = '1';

    window.csrfFetch('/cabinet/students/' + sid + '/mock-exams/' + workId + '/revision', {
        method: 'POST',
        body: fd,
    }).then(readJsonOrThrow).then(function() {
        showToast('Пробник отправлен на доработку');
        if (_tabCache[sid]) {
            delete _tabCache[sid]['mock-exams'];
        }
        switchTab('mock-exams');
    }).catch(function(err) {
        if (form) form.closest('.score-form').dataset.saving = '0';
        alert(err.serverMessage || 'Не удалось вернуть на доработку');
    });
}

function toggleEditForm(id, btn) {
    var el = document.getElementById(id);
    if (!el) return;
    var visible = el.style.display !== 'none';
    el.style.display = visible ? 'none' : 'block';
    btn.textContent = visible ? 'Изменить балл' : 'Скрыть';
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg) {
    var t = document.getElementById('save-toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function() { t.classList.remove('show'); }, 3000);
}

// ── Sidebar filters ─────────────────────────────────────────────────────────

function removeServerFilter(btn) {
    try {
        var u = new URL(location.href);
        u.searchParams.delete(btn.dataset.param);
        location.href = u.toString();
    } catch (e) {}
}

function copyNotSubmittedUsernames(btn) {
    var items = document.querySelectorAll('#not-submitted-list li[data-username]');
    var usernames = [];
    items.forEach(function(li) {
        var u = li.getAttribute('data-username');
        if (u) usernames.push('@' + u);
    });
    if (!usernames.length) return;
    navigator.clipboard.writeText(usernames.join('\n')).then(function() {
        var orig = btn.textContent;
        btn.textContent = 'Скопировано!';
        setTimeout(function() { btn.textContent = orig; }, 2000);
    });
}

var _activeTariff = '';

function normalizeStudentSearch(value) {
    var normalized = String(value || '');
    // NFKC сглаживает различия между визуально одинаковыми символами,
    // а схлопывание пробелов защищает поиск от автоподстановки мобильной клавиатуры.
    if (typeof normalized.normalize === 'function') normalized = normalized.normalize('NFKC');
    return normalized.toLowerCase().replace(/ё/g, 'е').trim().replace(/\s+/g, ' ');
}

function selectTariff(btn) {
    document.querySelectorAll('.tariff-pill').forEach(function(p) { p.classList.remove('active'); });
    btn.classList.add('active');
    _activeTariff = btn.dataset.tariff;
    filterSidebar();
}

function filterSidebar() {
    var q = normalizeStudentSearch(document.getElementById('student-search').value);
    var curator = (document.getElementById('filter-curator') || {}).value || '';
    var year = (document.getElementById('filter-year') || {}).value || '';
    var queryTokens = q.split(' ').map(function(token) {
        return token.replace(/^@/, '');
    }).filter(Boolean);

    var anyShown = false;
    document.querySelectorAll('.student-row').forEach(function(row) {
        var searchable = normalizeStudentSearch([
            row.querySelector('.student-row-name').textContent,
            row.dataset.tgUsername || ''
        ].join(' '));
        var matchQ = queryTokens.every(function(token) {
            return searchable.includes(token);
        });
        var matchT = !_activeTariff || row.dataset.tariff === _activeTariff;
        var matchC = !curator || row.dataset.curator === curator;
        var matchY = !year    || row.dataset.year === year;
        var shown = matchQ && matchT && matchC && matchY;
        row.style.display = shown ? '' : 'none';
        if (shown) anyShown = true;
    });
    var emptyNote = document.getElementById('student-list-empty');
    if (emptyNote) emptyNote.hidden = anyShown;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function cap(s) {
    if (!s) return '';
    return String(s).charAt(0).toUpperCase() + String(s).slice(1);
}
function pluralLabel(n, one, few, many) {
    n = Number(n) || 0;
    var mod10 = Math.abs(n) % 10;
    var mod100 = Math.abs(n) % 100;
    var word = (mod10 === 1 && mod100 !== 11)
        ? one
        : (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many);
    return n + ' ' + word;
}
function workCountLabel(n) { return pluralLabel(n, 'работа', 'работы', 'работ'); }

// ── Profile inline editing ──────────────────────────────────────────────────

function startEditProfile() {
    if (!_currentStudentId) return;
    var data = _tabCache[_currentStudentId] && _tabCache[_currentStudentId].profile;
    if (!data) return;
    var s = data.student;

    var html = buildHero(s, s.avg_score_by_subject || null);
    html += '<div class="profile-details"><div class="profile-grid">';
    html += editField('Имя', 'first_name', s.first_name || '');
    html += editField('Фамилия', 'last_name', s.last_name || '');
    html += editField('Телефон', 'phone', s.phone || '');
    html += editField('Телефон родителя', 'parent_phone', s.parent_phone || '');
    html += editField('Telegram', 'tg_username', s.tg_username || '');
    // Профиль отдаёт «—» вместо пустого тарифа, поэтому прочерк здесь значит
    // «без тарифа», а не значение для списка.
    var tariffValue = (s.tariff && s.tariff !== '—') ? s.tariff : '';
    var tariffOptions = TARIFF_OPTIONS.slice();
    if (tariffValue && tariffOptions.indexOf(tariffValue) === -1) {
        // Отработавший тариф ученика остаётся в его карточке: без своего пункта
        // список открылся бы на чужом значении, и сохранение карточки молча
        // перевело бы человека на другой тариф.
        tariffOptions.push(tariffValue);
    }
    html += '<div class="profile-field"><div class="profile-field-label">Тариф</div>'
        + '<select class="profile-edit-select" id="edit-tariff">'
        + '<option value="__NONE__"' + (!tariffValue ? ' selected' : '') + '>Новенький (без тарифа)</option>'
        + tariffOptions.map(function (t) {
            return '<option value="' + esc(t) + '"' + (tariffValue === t ? ' selected' : '')
                + '>' + esc(TARIFF_LABELS[t] || t) + '</option>';
        }).join('')
        + '</select></div>';
    html += editField('Начало обучения', 'enrollment_year', s.enrollment_year || '');
    html += editField('Год поступления в вуз', 'university_year', s.university_year || '');
    html += '<div class="profile-field"><div class="profile-field-label">Метка набора</div>'
        + '<select class="profile-edit-select" id="edit-cohort_tag">'
        + '<option value="">— нет —</option>'
        + Object.keys(COHORT_TAG_LABELS).map(function(key) {
            return '<option value="' + key + '"' + (s.cohort_tag === key ? ' selected' : '') + '>' + COHORT_TAG_LABELS[key] + '</option>';
        }).join('')
        + '</select></div>';
    // Срок доступа. Пусто — учится без ограничения, так живут все действующие
    // ученики; дата — в этот момент кабинет закроется и останется одна «Личная
    // информация». Очистить поле = вернуть доступ оплатившему.
    html += '<div class="profile-field"><div class="profile-field-label">Доступ до</div>'
        + '<input type="datetime-local" class="profile-edit-input" id="edit-access_until" value="' + esc(s.access_until || '') + '">'
        + '<div class="field-hint">Пусто — доступ без ограничения</div>'
        + '</div>';
    html += '<div class="profile-edit-actions form-actions">'
        + '<button class="profile-cancel-btn" onclick="cancelEditProfile()">Отмена</button>'
        + '<button class="btn-blue btn-save" onclick="saveProfile()">Сохранить</button>'
        + '</div>';
    html += '</div></div>';

    var editPanel = document.getElementById('main-panel');
    editPanel.innerHTML = html;
    window.RichTextField.enhanceAll(editPanel);
}

function editField(label, id, value) {
    return '<div class="profile-field">'
        + '<div class="profile-field-label">' + esc(label) + '</div>'
        + '<input class="profile-edit-input" id="edit-' + id + '" value="' + esc(String(value)) + '">'
        + '</div>';
}

function cancelEditProfile() {
    if (_tabCache[_currentStudentId] && _tabCache[_currentStudentId].profile) {
        renderProfile(_tabCache[_currentStudentId].profile);
    }
}

function saveProfile() {
    if (!_currentStudentId) return;
    var body = new FormData();
    body.append('first_name', document.getElementById('edit-first_name').value);
    body.append('last_name', document.getElementById('edit-last_name').value);
    body.append('phone', document.getElementById('edit-phone').value);
    body.append('parent_phone', document.getElementById('edit-parent_phone').value);
    body.append('tg_username', document.getElementById('edit-tg_username').value);
    body.append('tariff', document.getElementById('edit-tariff').value);
    body.append('enrollment_year', document.getElementById('edit-enrollment_year').value);
    body.append('university_year', document.getElementById('edit-university_year').value);
    body.append('cohort_tag', document.getElementById('edit-cohort_tag').value);
    body.append('access_until', document.getElementById('edit-access_until').value);

    window.csrfFetch('/cabinet/students/' + _currentStudentId + '/profile', { method: 'POST', body: body })
        .then(function(r) { return r.json().catch(function() { return {}; }).then(function(d) { return {ok: r.ok, data: d}; }); })
        .then(function(res) {
            if (res.ok && res.data.ok) {
                // Clear cache and reload
                delete _tabCache[_currentStudentId].profile;
                selectStudent(_currentStudentId, 'profile');
                showToast('Анкета сохранена');
            } else {
                var msg = (res.data.errors || []).join(', ') || window.csrfMessage(res.data, 'Не удалось сохранить анкету');
                alert(msg);
            }
        })
        .catch(function() { alert(NET_ERROR); });
}

// ── Delete work (photo) ─────────────────────────────────────────────────────

function deleteWork(studentId, workId, el) {
    if (!confirm('Удалить эту работу?')) return;
    window.csrfFetch('/cabinet/students/' + studentId + '/works/' + workId, {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
    })
    .then(function(r) { return r.json().catch(function() { return {}; }); })
    .then(function(data) {
        if (data.ok) {
            // Clear tab caches for this student
            if (_tabCache[studentId]) {
                delete _tabCache[studentId].portfolio;
                delete _tabCache[studentId]['mock-exams'];
                delete _tabCache[studentId].profile;
            }
            // В пробниках от удалённого фото зависят главная работа дня и форма
            // оценки — убрать одну картинку мало, вкладку перечитываем. В
            // портфолио убираем только фото, чтобы не схлопнуть открытые месяцы.
            if (_currentTab === 'mock-exams' && el.closest('.mock-day-card')) {
                switchTab('mock-exams');
            } else {
                var wrap = el.closest('.photo-wrap');
                if (wrap) wrap.remove();
            }
            showToast('Работа удалена');
        } else {
            alert(window.csrfMessage(data, 'Не удалось удалить'));
        }
    })
    .catch(function() { alert(NET_ERROR); });
}

function wrapPhoto(imgHtml, studentId, workId) {
    if (!CAN_SCORE) return imgHtml;
    return '<div class="photo-wrap">'
        + imgHtml
        + '<button type="button" class="photo-del" onclick="event.stopPropagation();deleteWork(' + studentId + ',' + workId + ',this)" title="Удалить" aria-label="Удалить работу">&times;</button>'
        + '</div>';
}

// ── Upload modal ────────────────────────────────────────────────────────────

// Квадратик грузит превью 320px (`thumb_url`), лайтбокс открывает само фото
// из `data-full`. Превью есть только у работ с 29.09.2026 — у старых в
// квадратике остаётся `s3_url` (шаг 5 плана students-phone).
function zoomPhoto(w, imgAttrs) {
    return '<button type="button" class="photo-zoom-button" onclick="openGallery(this.firstElementChild)" aria-label="Открыть фото">'
        + '<img src="' + esc(w.thumb_url || w.s3_url) + '" data-full="' + esc(w.s3_url) + '" alt="' + esc(w.filename) + '" loading="lazy"'
        + (imgAttrs ? ' ' + imgAttrs : '') + '></button>';
}

// `source` различает настоящую работу портфолио (`work`) и снимок, сданный
// внутри задания (`submission`, живёт в task_block_submissions). У второго
// `id` — это id картинки сдачи, и он не имеет смысла для роутов удаления и
// переноса работ: отдать его туда значит удалить или перенести чужой Work.
// Поэтому у не-work элементов управляющих кнопок нет вовсе.
function wrapPortfolioPhoto(imgHtml, studentId, workId, workType, source) {
    if (source && source !== 'work') return imgHtml;
    if (!(IS_SUPERADMIN && workType === 'after')) {
        return wrapPhoto(imgHtml, studentId, workId);
    }
    return '<div class="photo-wrap portfolio-draggable" draggable="true" ondragstart="portfolioDragStart(event,' + workId + ')" ondragend="portfolioDragEnd()">'
        + imgHtml
        + '<button type="button" class="photo-del" onclick="event.stopPropagation();deleteWork(' + studentId + ',' + workId + ',this)" title="Удалить" aria-label="Удалить работу">&times;</button>'
        + '</div>';
}

function openUploadModal() {
    if (!_currentStudentId) return;
    document.querySelector('#upload-form [name="csrf_token"]').value = CSRF_TOKEN;
    toggleSubjectField();
    document.getElementById('upload-modal').classList.add('open');
    document.body.style.overflow = 'hidden';
}

function closeUploadModal() {
    document.getElementById('upload-modal').classList.remove('open');
    document.body.style.overflow = '';
}

function toggleSubjectField() {
    var wt = document.getElementById('upload-work-type').value;
    var isMock = wt === 'mock_exam';
    var needsSubject = isMock;
    var subjectField = document.getElementById('upload-subject-field');
    var subjectInput = document.getElementById('upload-subject');
    var monthField = document.getElementById('upload-month-field');
    var yearField = document.getElementById('upload-year-field');
    var dateField = document.getElementById('upload-mock-date-field');
    var scoreField = document.getElementById('upload-score-field');
    var monthInput = document.getElementById('upload-month');
    var yearInput = document.getElementById('upload-year');
    var dateInput = document.getElementById('upload-mock-date');
    var scoreInput = document.getElementById('upload-score');
    var photoInput = document.getElementById('upload-photo-input');
    var photoLabel = document.getElementById('upload-photo-label');
    var hint = document.getElementById('upload-dz-hint');

    subjectField.style.display = needsSubject ? '' : 'none';
    subjectInput.required = needsSubject;
    monthField.style.display = isMock ? 'none' : '';
    yearField.style.display = isMock ? 'none' : '';
    dateField.style.display = isMock ? '' : 'none';
    scoreField.style.display = isMock ? '' : 'none';

    monthInput.disabled = isMock;
    yearInput.disabled = isMock;
    dateInput.disabled = !isMock;
    scoreInput.disabled = !isMock;
    dateInput.required = isMock;
    scoreInput.required = isMock;
    photoInput.multiple = true;
    photoLabel.textContent = isMock ? 'Работы пробника (до 10)' : 'Фотографии (до 10)';
    hint.textContent = isMock ? 'До 10 МБ · до 10 работ' : 'До 10 МБ · до 10 штук';

    if (isMock && !dateInput.value) {
        var today = new Date();
        var month = String(today.getMonth() + 1).padStart(2, '0');
        var day = String(today.getDate()).padStart(2, '0');
        dateInput.value = today.getFullYear() + '-' + month + '-' + day;
    }
}

// ── Upload DnD/preview state ────────────────────────────────────────────────
var _uploadFiles = [];
var UPLOAD_MAX_FILES = 20;
var UPLOAD_MAX_SIZE = 10 * 1024 * 1024;

function _uploadSyncInput() {
    var dt = new DataTransfer();
    _uploadFiles.forEach(function(f) { dt.items.add(f); });
    document.getElementById('upload-photo-input').files = dt.files;
}

function _uploadRenderPreviews() {
    var grid = document.getElementById('upload-preview-grid');
    var row = document.getElementById('upload-count-row');
    var label = document.getElementById('upload-count-label');
    var btn = document.getElementById('upload-submit-btn');
    grid.innerHTML = '';
    if (!_uploadFiles.length) {
        row.style.display = 'none';
        btn.disabled = true;
        return;
    }
    row.style.display = 'flex';
    label.textContent = 'Выбрано: ' + _uploadFiles.length + ' из ' + UPLOAD_MAX_FILES;
    btn.disabled = false;
    _uploadFiles.forEach(function(f, i) {
        var cell = document.createElement('div');
        cell.className = 'upload-preview-cell';
        var del = document.createElement('button');
        del.type = 'button'; del.className = 'upload-preview-del'; del.innerHTML = '<svg class="svg-icon" viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
        del.onclick = function() { _uploadFiles.splice(i, 1); _uploadSyncInput(); _uploadRenderPreviews(); };
        cell.appendChild(del);
        var img = document.createElement('img');
        img.src = URL.createObjectURL(f);
        cell.appendChild(img);
        grid.appendChild(cell);
    });
}

function _uploadAddFiles(list) {
    Array.from(list).forEach(function(f) {
        if (!f.type.startsWith('image/')) { alert(f.name + ' — не изображение'); return; }
        if (f.size > UPLOAD_MAX_SIZE) { alert(f.name + ' — больше 10 МБ'); return; }
        if (_uploadFiles.length >= UPLOAD_MAX_FILES) { alert('Максимум ' + UPLOAD_MAX_FILES + ' фото'); return; }
        _uploadFiles.push(f);
    });
    _uploadSyncInput();
    _uploadRenderPreviews();
}

(function initUploadDnD() {
    var dz = document.getElementById('upload-dropzone');
    var input = document.getElementById('upload-photo-input');
    var clearBtn = document.getElementById('upload-clear-btn');
    if (!dz || !input) return;
    input.addEventListener('change', function() { _uploadAddFiles(input.files); });
    dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function() { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function(e) {
        e.preventDefault(); dz.classList.remove('dragover');
        if (e.dataTransfer && e.dataTransfer.files.length) _uploadAddFiles(e.dataTransfer.files);
    });
    clearBtn.addEventListener('click', function() { _uploadFiles = []; input.value = ''; _uploadRenderPreviews(); });
})();

function _uploadResetAll() {
    _uploadFiles = [];
    document.getElementById('upload-photo-input').value = '';
    _uploadRenderPreviews();
    document.getElementById('upload-form').reset();
    toggleSubjectField();
}

function submitUpload(e) {
    e.preventDefault();
    if (!_currentStudentId) return false;
    if (!_uploadFiles.length) return false;
    var workType = document.getElementById('upload-work-type').value;
    if (workType === 'mock_exam') {
        var subjectVal = document.getElementById('upload-subject').value;
        if (!subjectVal) { alert('Укажите предмет: Рисунок или Композиция'); return false; }
    }
    if (workType === 'mock_exam') {
        var mockDate = document.getElementById('upload-mock-date').value;
        var score = document.getElementById('upload-score').value;
        if (!mockDate) { alert('Укажите дату пробника'); return false; }
        if (score === '' || isNaN(parseFloat(score))) { alert('Укажите балл за пробник'); return false; }
        var scoreNum = parseFloat(score);
        if (scoreNum < 0 || scoreNum > 100) { alert('Балл должен быть от 0 до 100'); return false; }
    }
    _uploadSyncInput();

    var form = document.getElementById('upload-form');
    var btn = document.getElementById('upload-submit-btn');
    var btnOriginal = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Загружаем 0%…';

    var fd = new FormData(form);

    // Свежий ключ перед отправкой (`static/js/csrf.js`): карточка ученика
    // открыта у куратора весь рабочий день, а ключ из разметки старился вместе
    // со страницей и отдавал «Ошибка 403» на готовой пачке фото.
    function startUpload(token) {
    fd.set('csrf_token', token);

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/cabinet/students/' + _currentStudentId + '/upload');
    xhr.setRequestHeader('X-CSRF-Token', token);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.setRequestHeader('Accept', 'application/json');

    xhr.upload.onprogress = function(ev) {
        if (!ev.lengthComputable) return;
        btn.textContent = 'Загружаем ' + Math.round((ev.loaded / ev.total) * 100) + '%…';
    };
    xhr.onerror = function() {
        btn.disabled = false; btn.textContent = btnOriginal;
        alert(NET_ERROR);
    };
    xhr.onload = function() {
        btn.disabled = false; btn.textContent = btnOriginal;
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
            closeUploadModal();
            if (_tabCache[_currentStudentId]) {
                delete _tabCache[_currentStudentId].portfolio;
                delete _tabCache[_currentStudentId]['mock-exams'];
                delete _tabCache[_currentStudentId].profile;
            }
            if (_viewMode === 'tab') { switchTab(_currentTab); } else { selectStudent(_currentStudentId, 'profile'); }
            var msg = data.success_count + ' фото загружено';
            if (data.fail_count > 0) msg += ', ' + data.fail_count + ' не удалось';
            showToast(msg);
            _uploadResetAll();
        } else {
            alert((data && (data.error || data.detail)) || ('Не удалось загрузить фото, код ' + xhr.status));
        }
    };
    xhr.send(fd);
    }

    var freshToken = window.csrfFresh ? window.csrfFresh() : Promise.resolve(CSRF_TOKEN);
    freshToken.then(function (token) {
        startUpload(token || CSRF_TOKEN);
    }).catch(function () {
        startUpload(CSRF_TOKEN);
    });
    return false;
}

// ── Bulk delete (folder) ────────────────────────────────────────────────────

function deleteFolderWorks(studentId, workType, month, year, count) {
    var label = cap(month) + ' ' + year;
    if (!confirm('Удалить все ' + workCountLabel(count) + ' за ' + label + '?')) return;

    window.csrfFetch('/cabinet/students/' + studentId + '/works/bulk', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({work_type: workType, month: month, year: year}),
    })
    .then(function(r) { return r.json().catch(function() { return {}; }); })
    .then(function(data) {
        if (data.ok) {
            // Clear caches and reload tab
            if (_tabCache[studentId]) {
                delete _tabCache[studentId].portfolio;
                delete _tabCache[studentId]['mock-exams'];
                delete _tabCache[studentId].profile;
            }
            switchTab(_currentTab);
            showToast('Удалено ' + workCountLabel(data.deleted_count));
        } else {
            alert(window.csrfMessage(data, 'Не удалось удалить'));
        }
    })
    .catch(function() { alert(NET_ERROR); });
}

// ── Auto-select on load (after score/redirect) ────────────────────────────────

_navRestoring = true;  // открытие страницы — не шаг истории, запись ставится заменой ниже
if (INITIAL_STUDENT_ID) {
    var _params = new URLSearchParams(location.search);
    var hasTab = _params.has('tab');
    if (hasTab) {
        // After POST redirect (score/unlock) — go straight to tab
        selectStudent(INITIAL_STUDENT_ID, INITIAL_TAB || 'portfolio');
    } else {
        // Normal click — show profile
        selectStudent(INITIAL_STUDENT_ID);
    }
    if (_params.get('saved') === '1') {
        setTimeout(function() { showToast('Балл сохранён'); }, 600);
    }
    _listScrollY = null;  // список ещё не листали — вернёмся к строке ученика
}
_navRestoring = false;
navCommit(false);  // заодно убирает ?saved=1, чтобы перезагрузка не повторяла тост
