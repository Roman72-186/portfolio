/*
 * Помощник международного номера для полей `input[data-phone-mask]`.
 *
 * Зачем: ученики и родители вводили номер кто как — «8 999…», «+8 999…»,
 * «9991234567», — и один человек попадал в базу в нескольких видах. Поле само
 * Для российского номера сохраняется привычный ввод через 8 или десять цифр.
 * Номер другой страны вводится с «+» и кодом страны. На blur запись приводится
 * к компактному международному виду; сервер повторяет нормализацию независимо
 * от JavaScript (`app/services/contacts.py`).
 *
 * Подключён в анкете первого входа (`profile.html`) и в «Изменить контакты»
 * (`cabinet_personal_contacts.html`) — правила этих двух форм обязаны совпадать.
 */
(function () {
    var fields = document.querySelectorAll('input[data-phone-mask]');
    if (!fields.length) return;

    function normalize(value) {
        var text = (value || '').trim();
        if (!text) return '';
        if (!/^[\d\s+().-]+$/.test(text)) return text;

        var digits = text.replace(/\D/g, '');
        if (text.indexOf('00') === 0 && digits.length >= 3) {
            return '+' + digits.slice(2);
        }
        if (text.charAt(0) === '+' && text.indexOf('+', 1) === -1) {
            return '+' + digits;
        }
        if (digits.length === 11 && (digits.charAt(0) === '7' || digits.charAt(0) === '8')) {
            return '+7' + digits.slice(1);
        }
        if (digits.length === 10) return '+7' + digits;
        return text;
    }

    function setValidity(field) {
        var value = normalize(field.value);
        if (!value) {
            field.setCustomValidity('');
        } else if (!/^\+[1-9]\d{7,14}$/.test(value)) {
            field.setCustomValidity('Укажи код страны и номер, например +7 999 123-45-67');
        } else {
            field.setCustomValidity('');
        }
    }

    function setupField(field) {
        field.addEventListener('input', function () {
            setValidity(field);
        });

        field.addEventListener('blur', function () {
            field.value = normalize(field.value);
            setValidity(field);
        });

        setValidity(field);
    }

    for (var i = 0; i < fields.length; i++) {
        setupField(fields[i]);
    }
})();
