/*
 * Маска российского номера для полей `input[data-phone-mask]`.
 *
 * Зачем: ученики и родители вводили номер кто как — «8 999…», «+8 999…»,
 * «9991234567», — и один человек попадал в базу в нескольких видах. Поле само
 * подставляет «+7» и дорисовывает разделители, так что набрать можно только
 * десять значащих цифр. Сервер всё равно нормализует ввод повторно
 * (`app/services/contacts.py`), скрипт лишь убирает лишние попытки и подсказки
 * об ошибке после отправки формы.
 *
 * Чего маска не делает: иностранный номер она не отвергает, а перекраивает на
 * российский лад — «+375 29 123-45-67» станет «+7 (375) 291-23-45», и сервер
 * такую запись примет. Так и задумано: номера в анкете только российские,
 * а подмену человек видит прямо в поле. Серверная проверка ловит чужой формат
 * лишь тогда, когда скрипт не отработал.
 *
 * Подключён в анкете первого входа (`profile.html`) и в «Изменить контакты»
 * (`cabinet_personal_contacts.html`) — правила этих двух форм обязаны совпадать.
 */
(function () {
    var fields = document.querySelectorAll('input[data-phone-mask]');
    if (!fields.length) return;

    var PREFIX = '+7 ';

    function extractDigits(value) {
        var text = value || '';
        // Префикс рисует само поле, и его «7» — не набранная цифра. Без этой
        // отрезки семёрка префикса съедала бы правило про код страны ниже, и
        // привычный ввод «8 999…» превращался в «+7 (899) 9…».
        if (text.indexOf(PREFIX) === 0) {
            text = text.slice(PREFIX.length);
        }
        var digits = text.replace(/\D/g, '');
        // Ведущие «7» и «8» — код страны: человек набирает по привычке
        // «8 999…» или «+7 999…», значащие десять цифр начинаются дальше.
        if (digits.charAt(0) === '7' || digits.charAt(0) === '8') {
            digits = digits.slice(1);
        }
        return digits.slice(0, 10);
    }

    function format(digits) {
        if (!digits) return PREFIX;
        var out = PREFIX + '(' + digits.slice(0, 3);
        if (digits.length >= 3) out += ')';
        if (digits.length > 3) out += ' ' + digits.slice(3, 6);
        if (digits.length > 6) out += '-' + digits.slice(6, 8);
        if (digits.length > 8) out += '-' + digits.slice(8, 10);
        return out;
    }

    function setValidity(field, digits) {
        if (!digits.length) {
            // Enter внутри поля отправляет форму без blur, и одинокий «+7 »
            // до проверки required не доходит: три символа не проходят
            // pattern, и браузер показывает своё «Введите данные в требуемом
            // формате» вместо человеческой просьбы.
            field.setCustomValidity(field.value ? 'Введи номер телефона' : '');
        } else if (digits.length < 10) {
            field.setCustomValidity('Номер: +7 и 10 цифр');
        } else {
            field.setCustomValidity('');
        }
    }

    function apply(field, digits) {
        field.dataset.phoneDigits = digits;
        field.value = format(digits);
        setValidity(field, digits);
    }

    function setupField(field) {
        // Старые записи в базе лежат в прежнем свободном формате — приводим их
        // к общему виду сразу при открытии формы, чтобы ученик правил номер
        // ровно в том виде, в каком он и сохранится.
        if (field.value) {
            apply(field, extractDigits(field.value));
        } else {
            field.dataset.phoneDigits = '';
        }

        field.addEventListener('focus', function () {
            if (!field.value) apply(field, '');
        });

        field.addEventListener('input', function (e) {
            var digits = extractDigits(field.value);
            // Backspace на разделителе: цифр не убавилось, потому что маска
            // тут же дорисовала скобку или дефис обратно. Без этой ветки номер
            // невозможно стереть — курсор навсегда упирается в «)».
            if (e.inputType === 'deleteContentBackward' && digits === field.dataset.phoneDigits) {
                digits = digits.slice(0, -1);
            }
            apply(field, digits);
        });

        field.addEventListener('blur', function () {
            // Пустое поле оставляем пустым: иначе одинокий «+7» выглядит как
            // заполненный номер, прячет подсказку в placeholder и проходит
            // мимо проверки required.
            if (!field.dataset.phoneDigits) {
                field.value = '';
                field.setCustomValidity('');
            }
        });
    }

    for (var i = 0; i < fields.length; i++) {
        setupField(fields[i]);
    }
})();
