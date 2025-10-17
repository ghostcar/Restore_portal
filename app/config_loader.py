import pyodbc
import json
import os

def get_svc_conn_config():
    """Читает настройки подключения к svc_sqlrestore из config/app_config.json"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'app_config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    svc_db_conf = config['svc_db']
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={svc_db_conf['server']};DATABASE={svc_db_conf['database']};"
    if svc_db_conf.get('trusted_connection'):
        conn_str += "Trusted_Connection=yes;"
    else:
        # Если используются UID/PWD
        conn_str += f"UID={svc_db_conf.get('uid', '')};PWD={svc_db_conf.get('pwd', '')};"
    return conn_str

def get_svc_conn():
    """Возвращает соединение с svc_sqlrestore, используя настройки из config/app_config.json"""
    conn_str = get_svc_conn_config()
    return pyodbc.connect(conn_str)
def is_user_admin(username):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM Users WHERE windows_login = ?", username)
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False

def get_global_setting(key):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM GlobalSettings WHERE setting_key = ?", key)
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_user_databases(windows_login):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ud.restore_target_db, ud.source_db_name, 
               ISNULL(ud.user_storage_login, ud.storage_user) as storage_user,
               ISNULL(ud.user_storage_password, ud.storage_password) as storage_password,
               ud.header, ud.use_storage, ud.storage_path,
               ISNULL(ud.user_app_login, (SELECT setting_value FROM GlobalSettings WHERE setting_key = 'app_user')) as app_login,
               ISNULL(ud.user_app_password, (SELECT setting_value FROM GlobalSettings WHERE setting_key = 'app_password')) as app_password,
               ud.infobase_guid
        FROM UserDatabases ud
        JOIN Users u ON ud.user_id = u.id
        WHERE u.windows_login = ?
    """, windows_login)
    result = []
    for row in cursor.fetchall():
        result.append({
            "target": row[0],
            "source": row[1],
            "storage_user": row[2],
            "storage_password": row[3],
            "header": row[4],
            "use_storage": row[5],
            "storage_path": row[6],
            "app_login": row[7],
            "app_password": row[8],
            "infobase_guid": row[9]  # Новое
        })
    conn.close()
    return result

def get_common_databases():
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT restore_target_db, source_db_name, header, infobase_guid
        FROM CommonDatabases
        WHERE is_admin_only = 0
    """)
    result = []
    for r in cursor.fetchall():
        result.append({
            "target": r[0],
            "source": r[1],
            "header": r[2],
            "infobase_guid": r[3]  # Новое
        })
    conn.close()
    return result

def get_db_config(target_db_name, windows_login):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ud.source_db_name, 
               ISNULL(ud.user_storage_login, ud.storage_user) as storage_user,
               ISNULL(ud.user_storage_password, ud.storage_password) as storage_password,
               ud.header, ud.use_storage, ud.backup_path_template, ud.storage_path,
               ISNULL(ud.user_app_login, (SELECT setting_value FROM GlobalSettings WHERE setting_key = 'app_user')) as app_login,
               ISNULL(ud.user_app_password, (SELECT setting_value FROM GlobalSettings WHERE setting_key = 'app_password')) as app_password,
               ud.infobase_guid
        FROM UserDatabases ud
        JOIN Users u ON ud.user_id = u.id
        WHERE u.windows_login = ? AND ud.restore_target_db = ?
    """, windows_login, target_db_name)
    row = cursor.fetchone()
    if row:
        conn.close()
        return {
            "source_db": row[0],
            "storage_user": row[1],
            "storage_password": row[2],
            "header": row[3],
            "use_storage": row[4],
            "backup_path": row[5],
            "storage_path": row[6],
            "app_login": row[7],
            "app_password": row[8],
            "infobase_guid": row[9],  # Новое
            "sql_login": "sa",
            "sql_password": "..."
        }

    cursor.execute("""
        SELECT source_db_name, header, backup_path_template, infobase_guid
        FROM CommonDatabases
        WHERE restore_target_db = ?
        AND is_admin_only = 0
    """, target_db_name)
    row = cursor.fetchone()
    if row:
        conn.close()
        return {
            "source_db": row[0],
            "header": row[1],
            "backup_path": row[2],
            "infobase_guid": row[3],  # Новое
            "use_storage": False,
            "app_login": get_global_setting('app_user'),
            "app_password": get_global_setting('app_password'),
            "sql_login": "sa",
            "sql_password": "..."
        }
    conn.close()
    return None