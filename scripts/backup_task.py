import datetime
import os
import pyodbc
from pathlib import Path
import sys
import json
# Добавим путь к папке app, чтобы импортировать db_utils
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from app.email_sender import notify_admin_on_backup_failure
from app.config_loader import get_global_setting

from app.db_utils import get_svc_conn, get_sql_server_conn # <-- Новый импорт
from app.config_loader import get_global_setting # <-- Импорт для настроек из БД

def daily_backup():
    # ... (ваша логика бэкапа)
    try:
        # ... (ваш код)
        pass
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Ошибка бэкапа: {error_msg}")
        notify_admin_on_backup_failure(error_msg)  # <-- Новое
        raise e
    
def get_svc_conn():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=svc_sqlrestore;"
        "Trusted_Connection=yes;"
    )

def create_backup_from_source(source_db_name):
    # ... (чтение из BackupSources через get_svc_conn)
    conn = get_svc_conn() # <-- Используем функцию из db_utils
    cursor = conn.cursor()
    cursor.execute("""
        SELECT source_server, sql_login, sql_password, datafile_name
        FROM BackupSources WHERE source_db_name = ?
    """, source_db_name)
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Источник для {source_db_name} не найден")
    source_server, sql_login, sql_password, datafile_name = row
    conn.close()

    # Папка бэкапа
    backup_dir = os.path.join(get_global_setting('backup_base_path') or r'D:\SQLBackups', source_db_name)
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    today_str = datetime.date.today().strftime("%d%m%Y")
    backup_file = os.path.join(backup_dir, f"{source_db_name}{today_str}.bak")

    # Подключение к prod-серверу через основной SQL Server (или через source_server, если он отличается)
    # В данном случае, source_db_name - это БД на source_server
    # Если source_server != основному серверу, то нужно использовать source_server
    # Для простоты, предположим, что source_server == основному серверу, и используем настройки из config
    # Если source_server != основному, нужно передавать его в функцию или читать из BackupSources
    # prod_conn = pyodbc.connect(
    #     f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    #     f"SERVER={source_server};"  # <-- Если source_server != основному
    #     f"DATABASE={source_db_name};"
    #     f"UID={sql_login};PWD={sql_password}"
    # )
    # Используем основной SQL Server из config/app_config.json
    sql_server_address = get_global_setting('sql_server_address') or 'localhost'
    prod_conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_server_address};DATABASE={source_db_name};UID={sql_login};PWD={sql_password}"
    prod_conn = pyodbc.connect(prod_conn_str) # <-- Используем основной сервер

    cursor = prod_conn.cursor()
    cursor.execute(f"BACKUP DATABASE [{source_db_name}] TO DISK = ? WITH COPY_ONLY, COMPRESSION, STATS = 10", backup_file)
    prod_conn.commit()
    prod_conn.close()

    # Записываем в служебную БД через get_svc_conn
    conn = get_svc_conn() # <-- Используем функцию из db_utils
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO Backups (source_db_name, backup_file_path, backup_date) VALUES (?, ?, ?)",
        source_db_name, backup_file, datetime.date.today()
    )
    conn.commit()
    conn.close()

    return backup_file

def daily_backup():
    today = datetime.date.today()
    today_str = today.strftime("%d%m%Y")

    conn = get_svc_conn()
    cursor = conn.cursor()

    # Получаем все уникальные source_db_name
    cursor.execute("""
        SELECT DISTINCT source_db_name FROM (
            SELECT source_db_name FROM UserDatabases
            UNION
            SELECT source_db_name FROM CommonDatabases
        ) AS all_dbs
    """)
    source_dbs = [row[0] for row in cursor.fetchall()]
    conn.close()

    for db_name in source_dbs:
        print(f"🔄 Бэкап {db_name}...")
        try:
            create_backup_from_source(db_name)
            print(f"✅ {db_name} — бэкап создан")
        except Exception as e:
            print(f"❌ {db_name} — ошибка: {e}")

if __name__ == '__main__':
    daily_backup()