# scripts/migrate_configs.py

import json
import pyodbc
import os
import re
from pathlib import Path

# Добавим путь к папке app, чтобы импортировать db_utils
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db_utils import get_svc_conn # <-- Импортируем функцию подключения из нового модуля

def main():
    print("🔄 Начинаю миграцию конфигов из JSON в БД...")

    # Проверим, существуют ли файлы конфигов
    common_json_path = Path('configs/common.json')
    backup_json_path = Path('configs/backup.json')
    users_json_pattern = Path('configs/user_*.json')

    if not common_json_path.exists():
        print(f"❌ Файл не найден: {common_json_path}")
        return
    if not backup_json_path.exists():
        print(f"❌ Файл не найден: {backup_json_path}")
        return

    # --- 1. Загрузка JSON-конфигов ---
    print("📄 Загружаю конфигурационные файлы...")
    with open(common_json_path, 'r', encoding='utf-8') as f:
        common_conf = json.load(f)
    with open(backup_json_path, 'r', encoding='utf-8') as f:
        backup_conf = json.load(f)

    # --- 2. Подключение к БД ---
    print("🔌 Подключаюсь к служебной БД svc_sqlrestore...")
    try:
        conn = get_svc_conn() # <-- Используем централизованную функцию
        cursor = conn.cursor()
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        return

    # --- 3. Заполнение BackupSources (из backup.json) ---
    print("💾 Заполняю таблицу BackupSources...")
    for db_type in ['ERP', 'UPP', 'HRM']:
        source_db_key = f'sourceDB{db_type}'
        if source_db_key not in backup_conf:
            print(f"  ⚠️ Ключ {source_db_key} отсутствует в backup.json, пропускаю.")
            continue

        source_db = backup_conf[source_db_key]
        server_key = f'serverDB{db_type}'
        user_key = f'userDB{db_type}'
        passw_key = f'passwDB{db_type}'
        datafile_key = f'{db_type}Datafile'

        server = backup_conf.get(server_key, '')
        user = backup_conf.get(user_key, '')
        password = backup_conf.get(passw_key, '')
        datafile = backup_conf.get(datafile_key, '')

        # Проверим, существует ли уже такая запись
        cursor.execute("SELECT COUNT(*) FROM BackupSources WHERE source_db_name = ?", source_db)
        exists = cursor.fetchone()[0]
        if exists:
            print(f"  ℹ️ Источник {source_db} уже существует, обновляю.")
            cursor.execute("""
                UPDATE BackupSources
                SET source_server = ?, sql_login = ?, sql_password = ?, datafile_name = ?
                WHERE source_db_name = ?
            """, server, user, password, datafile, source_db)
        else:
            cursor.execute("""
                INSERT INTO BackupSources (
                    source_db_name, source_server, sql_login, sql_password, datafile_name
                ) VALUES (?, ?, ?, ?, ?)
            """, source_db, server, user, password, datafile)
            print(f"  ✅ Добавлен источник: {source_db}")

    # --- 4. Заполнение CommonDatabases (из backup.json и common.json) ---
    print("🌐 Заполняю таблицу CommonDatabases...")
    for db_type in ['ERP', 'UPP', 'HRM']:
        source_db_key = f'sourceDB{db_type}'
        target_db_key = f'{db_type}DBtest'
        header_key = f'{db_type}Header'
        storage_path_key = f'{db_type}StoragePath' # из common.json

        if source_db_key not in backup_conf or target_db_key not in backup_conf:
            print(f"  ⚠️ Ключи для {db_type} отсутствуют в backup.json, пропускаю.")
            continue

        source_db = backup_conf[source_db_key]
        target_db = backup_conf[target_db_key]
        header = backup_conf.get(header_key, f'Тестовая {db_type}')
        storage_path = common_conf.get(storage_path_key, '') # путь к хранилищу из common.json

        # Проверим, существует ли уже такая запись
        cursor.execute("SELECT COUNT(*) FROM CommonDatabases WHERE restore_target_db = ?", target_db)
        exists = cursor.fetchone()[0]
        if exists:
            print(f"  ℹ️ Общая БД {target_db} уже существует, обновляю.")
            cursor.execute("""
                UPDATE CommonDatabases
                SET source_db_name = ?, header = ?, storage_path = ?
                WHERE restore_target_db = ?
            """, source_db, header, storage_path, target_db)
        else:
            cursor.execute("""
                INSERT INTO CommonDatabases (
                    source_db_name, restore_target_db, backup_path_template, header, storage_path
                ) VALUES (?, ?, ?, ?, ?)
            """, source_db, target_db, backup_conf.get('backupPath', ''), header, storage_path)
            print(f"  ✅ Добавлена общая БД: {target_db}")

    # --- 5. Обработка всех user_*.json файлов ---
    print("👤 Обрабатываю пользовательские конфиги (user_*.json)...")
    configs_dir = Path('configs')
    user_config_files = list(configs_dir.glob('user_*.json'))

    if not user_config_files:
        print("  ⚠️ Файлы user_*.json не найдены.")
    else:
        for user_config_path in user_config_files:
            filename = user_config_path.name
            match = re.match(r'^user_(.+)\.json$', filename)
            if not match:
                continue
            windows_login = match.group(1).lower()
            print(f"  🔄 Обрабатываю конфиг: {filename} -> пользователь: {windows_login}")

            with open(user_config_path, 'r', encoding='utf-8') as f:
                try:
                    user_conf = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"    ❌ Ошибка парсинга JSON в {filename}: {e}")
                    continue

            # Проверим, существует ли пользователь в Users, если нет — добавим
            cursor.execute("SELECT id FROM Users WHERE windows_login = ?", windows_login)
            user_row = cursor.fetchone()
            if not user_row:
                # Можно попробовать извлечь имя из конфига, если оно есть
                full_name = user_conf.get('full_name', f'Пользователь {windows_login}')
                cursor.execute("INSERT INTO Users (windows_login, full_name) VALUES (?, ?)", windows_login, full_name)
                conn.commit() # Нужно закоммитить, чтобы получить ID
                cursor.execute("SELECT id FROM Users WHERE windows_login = ?", windows_login)
                user_id = cursor.fetchone()[0]
                print(f"    ✅ Добавлен пользователь: {windows_login}")
            else:
                user_id = user_row[0]
                print(f"    ℹ️ Пользователь {windows_login} уже существует.")

            # 6. Заполнение UserDatabases для этого пользователя
            for db_type in ['ERP', 'UPP', 'HRM']:
                target_db_key = f'{db_type}DB'
                source_db_key = f'sourceDB{db_type}' # из common.json

                if target_db_key not in user_conf:
                    print(f"      ⚠️ Ключ {target_db_key} отсутствует в {filename}, пропускаю.")
                    continue

                target_db_name = user_conf[target_db_key]
                # Источник берем из common.json (как в OScript-скриптах)
                source_db_name = common_conf.get(source_db_key, f'TST_{db_type}_YD') # дефолтное имя источника

                storage_user = user_conf.get('storageUser')
                storage_password = user_conf.get('storagePassw')
                header = user_conf.get(f'{db_type}Header')
                use_storage_str = user_conf.get(f'{db_type}_storage', 'Ложь')
                use_storage = 1 if use_storage_str == 'Истина' else 0
                storage_path_key = f'{db_type}StoragePath'
                storage_path = common_conf.get(storage_path_key, '') # путь к хранилищу из common.json

                # Проверим, существует ли уже такая БД для пользователя
                cursor.execute("SELECT id FROM UserDatabases WHERE user_id = ? AND restore_target_db = ?", user_id, target_db_name)
                existing_db = cursor.fetchone()
                if existing_db:
                    print(f"      ⚠️ БД {target_db_name} уже существует для пользователя {windows_login}, обновляю.")
                    cursor.execute("""
                        UPDATE UserDatabases
                        SET source_db_name = ?, storage_user = ?, storage_password = ?, header = ?, use_storage = ?, storage_path = ?
                        WHERE id = ?
                    """, source_db_name, storage_user, storage_password, header, use_storage, storage_path, existing_db[0])
                else:
                    cursor.execute("""
                        INSERT INTO UserDatabases (
                            user_id, source_db_name, restore_target_db, backup_path_template,
                            storage_user, storage_password, header, use_storage, storage_path
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    user_id,
                    source_db_name,
                    target_db_name,
                    common_conf.get('backupPath', ''), # путь к бэкапам из common.json
                    storage_user,
                    storage_password,
                    header,
                    use_storage,
                    storage_path
                    )
                    print(f"      ✅ Добавлена БД {target_db_name} для пользователя {windows_login}")

    # --- 7. Фиксация изменений ---
    conn.commit()
    conn.close()
    print("✅ Миграция конфигов завершена!")

if __name__ == '__main__':
    main()