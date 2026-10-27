# Формат входных файлов импорта (ПМ.02, п. 5.1.4)

## Общие правила

1. Все файлы загружаются «как есть» в схему `stage` (все поля `text`)
2. Каждая строка получает номер исходной строки (`src_line`) и имя файла (`src_file`)
3. Повторный запуск не создаёт дублей (идемпотентность)
4. Каждая строка попадает либо в целевую таблицу, либо в журнал отклонений

## Форматы файлов

### 1. CSV-файлы (кадры, справочники, CMDB)

**Кодировка:** UTF-8 с BOM (или Windows-1251 для легаси-файлов)

**Разделитель:** `;` (точка с запятой)

**Заголовок:** Первая строка содержит названия колонок

**Пример (hr.csv):**
```csv
tab_number;last_name;first_name;department;position;email;phone;mobile;snils;hire_date;dismissal_date
1215;Марков;Олег;IT;Инженер;o.markov@corp.local;+7(999)123-45-67;+79991234567;118-100-0098 98;2020-01-15;
1386;Скворцова;Надежда;HR;Менеджер;n.skvortsova@corp.local;+7(999)234-56-78;+79992345678;118-100-0099 99;2019-03-20;
```

**Аномалии, которые нужно обрабатывать:**
- Кодировка (Windows-1251 vs UTF-8)
- Формат дат (DD.MM.YYYY, YYYY-MM-DD, MM/DD/YYYY)
- Нулевые значения (пустые строки, NULL)
- Дубликаты (по табельному номеру, СНИЛС)
- Некорректные email/телефоны
- Циклические ссылки (подразделения)
- Ре-наймы (один СНИЛС — несколько периодов работы)

### 2. JSON-файлы (CMDB, конфигурация)

**Кодировка:** UTF-8

**Формат:** Массив объектов

**Пример (assets.json):**
```json
[
  {
    "inv_no": "INV-001",
    "serial_no": "SN-12345",
    "name": "Компьютер Dell",
    "category": "ПК",
    "location": "A101",
    "responsible": "Иванов И.И.",
    "is_active": true
  }
]
```

### 3. LDIF-файлы (LDAP)

**Кодировка:** UTF-8

**Формат:** LDIF (LDAP Data Interchange Format)

**Пример (directory.ldif):**
```ldif
dn: uid=ivanov,ou=people,dc=corp,dc=local
objectClass: inetOrgPerson
uid: ivanov
cn: Иванов Иван
sn: Иванов
givenName: Иван
mail: ivanov@corp.local
employeeNumber: 1234
```

### 4. EML-файлы (почта)

**Кодировка:** UTF-8 (MIME-encoded headers)

**Формат:** RFC 2822 (email message)

**Пример (001.eml):**
```
From: Иванов Иван <ivanov@corp.local>
To: support@corp.local
Subject: [#120991] Не работает принтер
Date: Mon, 20 Oct 2026 10:30:00 +0300
Message-ID: <abc123@corp.local>
Content-Type: text/plain; charset=utf-8

Добрый день! Не печатает принтер в кабинете 101.
```

### 5. CSV-файлы (справочники)

**Кодировка:** UTF-8 с BOM

**Разделитель:** `;`

**Пример (categories.csv):**
```csv
code;name;description;legacy_name
HW;Оборудование;Компьютеры, серверы, оргтехника;Hardware
SW;Программное обеспечение;ОС, приложения, драйверы;Software
NET;Сеть;Сетевое оборудование, кабели;Network
```

---

## Правила разбора входящего письма (п. 5.1.4)

1. **Автоответы** (заголовки `Auto-Submitted` / `X-Autoreply` или тема «Автоматический ответ») не создают ни заявок, ни комментариев
2. **Системные письма** (mailer-daemon, Return-Path: <>) пропускаются
3. **Письма с признаками фишинга** (Received-SPF: fail, поддельный Return-Path) отклоняются
4. **Опасные вложения** (исполняемые файлы) не прикрепляются к заявке, а изолируются в карантин
5. **Ответ с [#номер] в теме** добавляет комментарий к заявке с этим номером
6. **Остальные письма** от действующих сотрудников создают новую заявку (категория — по ключевым словам, приоритет — по признаку срочности)
7. **Повторное письмо** от того же автора с той же темой отклоняется как дубль
8. **Отправитель-уволенный** сотрудник не создаёт заявку на доступ (ИБ)

---

## Структура staging (схема stage)

Каждая staging-таблица содержит:
- `src_line` — номер исходной строки
- `src_file` — имя исходного файла
- `line_data` — данные строки (все поля `text`)
- `imported_at` — время загрузки
- `import_status` — статус (loaded, corrected, rejected)

**Пример:**
```sql
CREATE TABLE stage.import_log (
    log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_file varchar(255) NOT NULL,
    src_line integer NOT NULL,
    line_data text,
    imported_at timestamptz DEFAULT now(),
    import_status varchar(20) NOT NULL CHECK (import_status IN ('loaded', 'corrected', 'rejected')),
    reason text
);
```