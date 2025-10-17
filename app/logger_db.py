import pyodbc
from flask import request
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

def log_auth(user, action, details=None):
    ip = request.environ.get('REMOTE_ADDR')
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO AuthLog (windows_user, ip_address, action, details)
        VALUES (?, ?, ?, ?)
    """, user, ip, action, details)
    conn.commit()
    conn.close()

def log_user_action(user, action_type, target_db=None, details=None):
    ip = request.environ.get('REMOTE_ADDR')
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO UserActionsLog (windows_user, ip_address, action_type, target_db, details)
        VALUES (?, ?, ?, ?, ?)
    """, user, ip, action_type, target_db, details)
    conn.commit()
    conn.close()

def log_1c_operation(job_id, target_db, operation, status, log_text=None, error_message=None):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO OneCOperationLog (job_id, target_db, operation, status, log_text, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
    """, job_id, target_db, operation, status, log_text, error_message)
    conn.commit()
    conn.close()