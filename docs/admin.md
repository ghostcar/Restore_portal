# Административная инструкция

## Установка

См. `deploy.ps1`.

## Управление пользователями

Добавление пользователя:
```sql
INSERT INTO svc_sqlrestore.dbo.Users (windows_login, full_name) VALUES ('ivanov', 'Иванов И.И.');

## Добавление пользовательской БД:
INSERT INTO svc_sqlrestore.dbo.UserDatabases (user_id, source_db_name, restore_target_db, backup_path_template, header)
VALUES (2, 'TST_ERP_YD', 'TST_ERP_IVANOV', 'D:\SQLBackups\ERP', 'ERP Иванова');

## Запуск ежедневного бэкапа
## Через Планировщик заданий Windows.


---

## 🚀 Запуск в VSCode

1. Откройте проект в VSCode.
2. Установите Python-интерпретатор (Ctrl+Shift+P → Python: Select Interpreter).
3. Установите зависимости: `pip install -r requirements.txt`.
4. Запустите: `python run.py`.
5. Перейдите в браузере на `http://localhost:5000`.

---

## ✅ Итог

Вы получили:

- ✅ Полный **Python-проект** с архитектурой на основе **служебной БД**.
- ✅ **Скрипт миграции** из ваших JSON-файлов.
- ✅ **Веб-интерфейс** с доменной авторизацией через IIS.
- ✅ **Скрипт ежедневного бэкапа**.
- ✅ **PowerShell-скрипт развёртывания**.
- ✅ **Документацию** (пользовательскую и административную).

---