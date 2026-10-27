# Соответствие атрибутов LDAP полям БД (ПМ.02, п. 5.1.4)

## Общие принципы

- Сопоставление с кадрами идёт по **табельному номеру** (`employeeNumber`), а не по ФИО
- Коллизии логинов разрешаются по правилу: `first_name[0].last_name` → `first_name[0].last_name2` и т.д.
- Заблокированный/уволенный пользователь получает `NOLOGIN`, активные сессии завершаются

## Таблица соответствия

| Атрибут LDAP | Поле БД | Таблица БД | Примечание |
|---|---|---|---|
| `uid` | `login_name` | `sec.users` | Логин пользователя |
| `cn` | `full_name` | `core.employees` | ФИО (last_name + first_name) |
| `sn` | `last_name` | `core.employees` | Фамилия |
| `givenName` | `first_name` | `core.employees` | Имя |
| `mail` | `email` | `core.employees` | Рабочая почта |
| `employeeNumber` | `tab_number` | `core.employees` | Табельный номер (ключ сопоставления!) |
| `departmentNumber` | `department_id` | `core.employees` | ID подразделения |
| `title` | `position` | `core.employees` | Должность |
| `employeeType` | `role` | `sec.user_roles` | Тип сотрудника (engineer, dispatcher и т.д.) |
| `telephoneNumber` | `phone` | `core.employees` | Рабочий телефон |
| `mobile` | `mobile_phone` | `core.employees` | Мобильный телефон |
| `snils` | `snils` | `core.employees` | СНИЛС (зашифрован pgcrypto) |

## Структура OU

```
dc=corp,dc=local
├── ou=people          # Сотрудники
│   ├── uid=ivanov     # uid = login
│   ├── uid=petrova
│   └── ...
├── ou=groups          # Группы (роли)
│   ├── cn=employees   # Все сотрудники
│   ├── cn=engineers   # Инженеры
│   ├── cn=dispatchers  # Диспетчеры
│   ├── cn=dept_heads  # Руководители
│   ├── cn=auditors    # Аудиторы
│   ├── cn=reporting   # Отчётность
│   ├── cn=app_service # Служебные учётки
│   ├── cn=migrators   # Миграторы
│   └── cn=dba         # Администраторы БД
└── ou=service         # Служебные учётки
    ├── cn=notifier    # Воркер уведомлений
    └── cn=mailintake  # Обработка почты
```

## Правило именования логинов

1. Базовый логин: `first_name[0].last_name` (например, `i.ivanov`)
2. При коллизии: добавлять цифру: `i.ivanov2`, `i.ivanov3`
3. Сопоставление с кадрами по `employeeNumber` (табельный номер), а не по ФИО
4. Уволенный сотрудник: `is_active = false`, `dismissal_date` установлена, права отозваны

## Регламент паролей

- **Длина:** не менее 8 символов
- **Сложность:** смешанный регистр, цифры, спецсимволы
- **Срок действия:** 90 дней
- **История:** последние 5 паролей не повторяются
- **Хранение:** SCRAM-SHA-256 в PostgreSQL, пароли LDAP — в каталоге
- **Служебные учётки:** пароли передаются через переменные окружения (`.env`), не хранятся в репозитории
- **Ротация:** каждые 90 дней, с уведомлением за 7 дней до истечения

## Директория dirsync

- **Модуль:** `dirsync/sync.py` — синхронизирует LDAP с БД
- **Правило именования:** `first_name[0].last_name` с проверкой коллизий
- **Роли:** создаются с `SCRAM-SHA-256`, `VALID UNTIL`, `CONNECTION LIMIT`
- **Блокировка:** при увольнении — `NOLOGIN`, завершение сессий, исключение из групп (в течение 5 минут)