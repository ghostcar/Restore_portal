# app/db_actions.py

import pyodbc
import datetime
import os
import re
from flask import current_app
from .config_loader import get_svc_conn, get_global_setting, get_db_config
from .onec_integration import run_1c_command_via_1cv8, run_1c_command_via_rac
from .logger_db import update_job_status, log_restore_job_status, log_1c_operation

# Глобальные переменные
running_tasks = set()  # <-- Добавлено
allow_dynamic_backup = get_global_setting('allow_dynamic_backup_creation') == '1'  # <-- Добавлено

def get_backup_path_for_db(source_db_name):  # <-- Добавлено
    """Получает путь к последнему бэкапу БД."""
    today = datetime.date.today()
    today_str = today.strftime("%d%m%Y")
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT backup_file_path FROM Backups WHERE source_db_name = ? AND backup_date = ?",
        source_db_name, today
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def validate_db_name(db_name):
    """Проверяет, что имя БД состоит только из допустимых символов."""
    if not re.match(r'^[A-Za-z0-9_]+$', db_name):
        raise ValueError(f"Недопустимое имя базы данных: {db_name}")
    return True

def restore_db_from_backup(target_db_name, backup_file_path, sql_login, sql_password):
    """Восстанавливает БД из .bak-файла."""
    validate_db_name(target_db_name)  # <-- Валидация

    conn_str = get_global_setting('sql_server_conn_str') or "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;Trusted_Connection=yes;"
    # Перезаписываем логин/пароль из параметров функции (это могут быть логины из БД)
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={get_global_setting('sql_server_address') or 'localhost'};UID={sql_login};PWD={sql_password};"
    conn = pyodbc.connect(conn_str)
    conn.autocommit = True
    cursor = conn.cursor()

    # Используем квадратные скобки для экранирования имени БД
    # validate_db_name уже гарантирует безопасность
    cursor.execute(f"ALTER DATABASE [{target_db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
    cursor.execute(f"""
        RESTORE DATABASE [{target_db_name}]
        FROM DISK = ?
        WITH REPLACE, RECOVERY, STATS = 5
    """, backup_file_path)
    cursor.execute(f"ALTER DATABASE [{target_db_name}] SET MULTI_USER")
    cursor.execute(f"ALTER AUTHORIZATION ON DATABASE::[{target_db_name}] TO [sa]")
    cursor.execute(f"ALTER DATABASE [{target_db_name}] SET RECOVERY SIMPLE")
    cursor.execute(f"USE [{target_db_name}]; DBCC SHRINKFILE (2, TRUNCATEONLY);")

    conn.close()

def set_parallelism(target_db_name, degree, sql_login, sql_password):
    """Устанавливает максимальную степень параллелизма для БД."""
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={get_global_setting('sql_server_address') or 'localhost'};UID={sql_login};PWD={sql_password};"
    conn = pyodbc.connect(conn_str)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(f"""
        USE [{target_db_name}];
        EXEC sp_configure 'show advanced options', 1;
        RECONFIGURE WITH OVERRIDE;
        EXEC sp_configure 'max degree of parallelism', {degree};
        RECONFIGURE WITH OVERRIDE;
    """)
    conn.close()

def perform_restore_job(job):
    """Выполняет задачу восстановления БД."""
    job_id = job['id']
    user = job['windows_user']
    target_db = job['target_db']
    db_config = get_db_config(target_db, user)
    if not db_config:
        update_job_status(job_id, 'failed', 'Нет доступа к БД')
        return

    source_db = db_config['source_db']
    backup_path = get_backup_path_for_db(source_db)
    if not backup_path:
        if not allow_dynamic_backup:
            update_job_status(job_id, 'failed', 'Бэкап отсутствует и создание запрещено')
            return
        print(f"⚠️ Нет бэкапа для {source_db}, создаю...")
        from scripts.backup_task import create_backup_from_source
        backup_path = create_backup_from_source(source_db)

    # SQL-логины для восстановления БД (не зависят от пользователя)
    sql_login = db_config['sql_login']
    sql_password = db_config['sql_password']
    try:
        # --- НОВОЕ: Блокировка сеансов через rac перед восстановлением ---
        try:
            run_1c_command_via_rac(target_db, "infobase update --sessions-deny=on")
            print(f"✅ Сеансы заблокированы для {target_db}")
        except Exception as e:
            print(f"⚠️ Не удалось заблокировать сеансы для {target_db}: {e}")

        restore_db_from_backup(target_db, backup_path, sql_login, sql_password)

        # --- НОВОЕ: Работа с 1С (с пользовательскими логинами) ---
        extension_name = db_config.get('extension_name', '')
        
        app_login = db_config.get('app_login')
        app_password = db_config.get('app_password')

        if extension_name:
            run_1c_command_via_1cv8(target_db, f"DisconnectFromStorage;{extension_name}", app_login, app_password)

        header = db_config.get('header', 'Без заголовка')
        today_str = datetime.date.today().strftime("%d.%m.%Y")
        final_header = f"{header} {today_str}"
        run_1c_command_via_1cv8(target_db, f"SetTitle;{final_header}", app_login, app_password)

        if db_config.get('use_storage'):
            # Используем пользовательские логины от хранилища
            storage_user = db_config.get('storage_user')
            storage_password = db_config.get('storage_password')
            storage_path = db_config.get('storage_path')
            if storage_user and storage_password and storage_path:
                run_1c_command_via_1cv8(target_db, f"ConnectToStorage;{storage_path};{storage_user};{storage_password};{extension_name}", app_login, app_password)
                run_1c_command_via_1cv8(target_db, f"UpdateFromStorage;{extension_name}", app_login, app_password)
                run_1c_command_via_1cv8(target_db, f"UpdateDBCfg;{extension_name}", app_login, app_password)

        set_parallelism(target_db, 0, sql_login, sql_password)
        set_parallelism(target_db, 1, sql_login, sql_password)

        # --- НОВОЕ: Разблокировка сеансов после восстановления ---
        try:
            run_1c_command_via_rac(target_db, "infobase update --sessions-deny=off")
            print(f"✅ Сеансы разблокированы для {target_db}")
        except Exception as e:
            print(f"⚠️ Не удалось разблокировать сеансы для {target_db}: {e}")

        update_job_status(job_id, 'completed')
        log_restore_job_status(user, target_db, "SUCCESS")
    except Exception as e:
        error_msg = str(e)
        update_job_status(job_id, 'failed', error_msg)
        log_restore_job_status(user, target_db, f"ERROR: {error_msg}")
    finally:
        running_tasks.discard(job_id)