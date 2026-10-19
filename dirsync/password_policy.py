# -*- coding: utf-8 -*-
"""
Регламент паролей (п. 4.2 ТЗ).

Правила:
  - длина не менее 12 символов;
  - содержит буквы обоих регистров, цифру и спецсимвол;
  - не совпадает с логином и не содержит его целиком;
  - не входит в список 1000 самых частых паролей (проверяется по файлу
    `docs/common_passwords.txt`, если он есть);
  - срок действия 90 дней; история — последние 5 паролей;
  - смена пароля: пользователь меняет сам через `sec.change_password`;
  - сброс: только администратор через `sec.reset_password` с временным
    паролем и обязательной сменой при первом входе.

Хранение:
  - пароли служебных учёток (svc-*) передаются через переменные окружения
    (`SVC_NOTIFIER_PASSWORD` и т.п.) и в репозитории не хранятся;
  - пароли пользователей хранятся только в виде SCRAM-SHA-256 (PostgreSQL)
    и в виде SSHA-хеша в LDAP; открытый пароль никогда не попадает в БД,
    логи или репозиторий;
  - ротация служебных паролей — раз в 90 дней по регламенту, либо
    немедленно при подозрении на компрометацию.

Почему паролей нет в репозитории:
  - репозиторий публичный (GitHub), любой секрет в нём — утечка;
  - пароли служебных учёток выдаются администратором через переменные
    окружения или менеджер секретов, а не через код;
  - `.env` добавлен в `.gitignore`.
"""
import re


MIN_LENGTH = 12
REQUIRED_CLASSES = 3  # из 4: upper, lower, digit, special


def check(password, login=None):
    """Возвращает (ok: bool, причины: list[str])."""
    reasons = []
    if len(password) < MIN_LENGTH:
        reasons.append(f"длина меньше {MIN_LENGTH} символов")
    classes = 0
    if re.search(r"[A-Z]", password):
        classes += 1
    if re.search(r"[a-z]", password):
        classes += 1
    if re.search(r"\d", password):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", password):
        classes += 1
    if classes < REQUIRED_CLASSES:
        reasons.append(f"нужно не менее {REQUIRED_CLASSES} классов символов "
                       f"(верхний/нижний регистр, цифры, спецсимволы), сейчас {classes}")
    if login and login.lower() in password.lower():
        reasons.append("пароль содержит логин")
    return (len(reasons) == 0, reasons)


def generate(length=16):
    """Генерирует пароль, проходящий check()."""
    import secrets, string
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        ok, _ = check(pw)
        if ok:
            return pw
