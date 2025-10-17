# app/db_utils.py

import json
import os
import pyodbc

def get_svc_conn_config():
    """Читает настройки подключения к svc_sqlrestore из config/app_config.json"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'app_config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    svc_db_conf = config['svc_db']
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={svc_db_conf['server']};DATABASE={svc_db_conf['database']};"
    if svc_db_conf.get('trusted_connection'):
        conn_str += "Trusted_Connection=no;"
    else:
        # Если используются UID/PWD
        conn_str += f"UID={svc_db_conf.get('uid', '')};PWD={svc_db_conf.get('pwd', '')};"
    return conn_str

def get_svc_conn():
    """Возвращает соединение с svc_sqlrestore, используя настройки из config/app_config.json"""
    conn_str = get_svc_conn_config()
    return pyodbc.connect(conn_str)

def get_sql_server_conn_config():
    """Читает настройки подключения к основному SQL Server из config/app_config.json"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'app_config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    sql_server_conf = config['sql_server']
    # Если uid/pwd не заданы, используем Trusted Connection
    if sql_server_conf.get('uid') and sql_server_conf.get('pwd'):
        conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_server_conf['address']};UID={sql_server_conf['uid']};PWD={sql_server_conf['pwd']};"
    else:
        conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_server_conf['address']};Trusted_Connection=no;"
    return conn_str

def get_sql_server_conn():
    """Возвращает соединение с основным SQL Server, используя настройки из config/app_config.json"""
    conn_str = get_sql_server_conn_config()
    return pyodbc.connect(conn_str)