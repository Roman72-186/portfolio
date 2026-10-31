(function () {
  const cards = JSON.parse(document.getElementById('lab-cards-data').textContent);
  const byId = new Map(cards.map(card => [card.id, card]));
  const cardSelect = document.getElementById('lab-card');
  const sectionSelect = document.getElementById('lab-section');
  const status = document.getElementById('lab-save-status');
  const cardSearch = document.getElementById('lab-card-search');
  const studentSearch = document.getElementById('lab-student-search');
  const studentSelect = document.getElementById('lab-students');
  const everyone = document.getElementById('lab-everyone');
  const tagNames = new Map([...document.querySelectorAll('input[name="tag"]')]
    .map(input => [Number(input.value), input.parentElement.textContent.trim()]));
  const sections = new Set();
  function countWord(n, one, few, many) {
    const last = n % 10, lastTwo = n % 100;
    return `${n} ${last === 1 && lastTwo !== 11 ? one : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14) ? few : many}`;
  }
  for (const card of cards) {
    const parts = card.path.split(' → ');
    for (let i = 1; i < parts.length; i++) sections.add(parts.slice(0, i).join(' → '));
  }
  for (const path of [...sections].sort()) sectionSelect.add(new Option(path, path));

  function checked(name) {
    return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
  }
  function setChecked(name, values) {
    const chosen = new Set(values.map(String));
    document.querySelectorAll(`input[name="${name}"]`).forEach(input => { input.checked = chosen.has(input.value); });
  }
  function loadCard() {
    const card = byId.get(cardSelect.value);
    if (!card) return;
    const audience = card.audience;
    everyone.checked = audience.everyone;
    setChecked('tariff', audience.tariffs);
    setChecked('level', audience.levels);
    setChecked('tag', audience.tag_ids);
    const users = new Set(audience.user_ids.map(String));
    [...studentSelect.options].forEach(option => { option.selected = users.has(option.value); });
    syncEveryone();
    document.getElementById('lab-selected-summary').textContent =
      audience.everyone ? 'Открыта всем ученикам' :
      audience.tariffs.length + audience.levels.length + audience.tag_ids.length + audience.user_ids.length
        ? `Открыта: ${[...audience.tariffs, ...audience.levels.map(v => `уровень ${v}`),
            ...audience.tag_ids.map(v => tagNames.get(v) || `трек ${v}`),
            ...(audience.user_ids.length ? [countWord(audience.user_ids.length, 'ученик', 'ученика', 'учеников')] : [])].join(', ')}`
        : 'Для учеников закрыта';
    status.textContent = '';
  }
  function syncEveryone() {
    if (everyone.checked) {
      for (const name of ['tariff', 'level', 'tag']) setChecked(name, []);
      [...studentSelect.options].forEach(option => { option.selected = false; });
    }
    document.querySelectorAll('input[name="tariff"], input[name="level"], input[name="tag"]')
      .forEach(input => { input.disabled = everyone.checked; });
    studentSelect.disabled = everyone.checked;
    studentSearch.disabled = everyone.checked;
  }
  everyone.addEventListener('change', syncEveryone);
  cardSelect.addEventListener('change', () => { sectionSelect.value = ''; loadCard(); });
  sectionSelect.addEventListener('change', () => {
    const path = sectionSelect.value;
    if (!path) return loadCard();
    const count = cards.filter(card => card.path.startsWith(path + ' → ')).length;
    document.getElementById('lab-selected-summary').textContent = `${countWord(count, 'фигура', 'фигуры', 'фигур')} в разделе. Сохранение задаст им одинаковый доступ.`;
    status.textContent = '';
  });
  cardSearch.addEventListener('input', () => {
    const term = cardSearch.value.trim().toLocaleLowerCase('ru');
    [...cardSelect.options].forEach(option => { option.hidden = !option.text.toLocaleLowerCase('ru').includes(term); });
  });
  studentSearch.addEventListener('input', () => {
    const term = studentSearch.value.trim().toLocaleLowerCase('ru');
    [...studentSelect.options].forEach(option => { option.hidden = !option.text.toLocaleLowerCase('ru').includes(term); });
  });
  document.getElementById('lab-save').addEventListener('click', async function () {
    const prefix = sectionSelect.value;
    const ids = prefix ? cards.filter(card => card.path.startsWith(prefix + ' → ')).map(card => card.id)
      : cardSelect.value ? [cardSelect.value] : [];
    if (!ids.length) { status.textContent = 'Выбери фигуру или раздел.'; return; }
    const payload = { card_ids: ids, everyone: everyone.checked, tariffs: checked('tariff'), levels: checked('level').map(Number),
      tag_ids: checked('tag').map(Number), user_ids: [...studentSelect.selectedOptions].map(option => Number(option.value)) };
    this.disabled = true;
    status.textContent = 'Сохраняем…';
    try {
      const response = await window.csrfFetch('/cabinet/3dlab/figures', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(`Ошибка ${response.status}. Обнови страницу и попробуй снова.`);
      for (const id of ids) byId.get(id).audience = { everyone: payload.everyone, tariffs: payload.tariffs, levels: payload.levels,
        tag_ids: payload.tag_ids, user_ids: payload.user_ids };
      if (!prefix) loadCard();
      status.textContent = `Сохранено: ${countWord(ids.length, 'фигура', 'фигуры', 'фигур')}.`;
    } catch (error) { status.textContent = error.message || 'Не удалось сохранить. Попробуй снова.'; }
    finally { this.disabled = false; }
  });
  if (cardSelect.options.length) { cardSelect.selectedIndex = 0; loadCard(); }
})();
