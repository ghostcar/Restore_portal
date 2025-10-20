# app/onec_integration.py

import subprocess
import os
from flask import current_app
from .config_loader import get_global_setting

def get_cluster_info_for_db(db_name):
    """
    Получает информацию о кластере и базе данных из ClusterInfo по имени БД.
    Возвращает: dict с cluster_guid, infobase_guid или None.
    """
    from .config_loader import get_svc_conn  # <-- Локальный импорт, чтобы избежать циклической зависимости
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cluster_guid, infobase_guid FROM ClusterInfo WHERE name = ?
    """, db_name)
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"cluster_guid": row[0], "infobase_guid": row[1]}
    return None

def run_1c_command_via_1cv8(db_name, command, app_login=None, app_password=None):
    """
    Выполняет команду 1С через 1cv8.exe.
    command: строка, например: "SetTitle;Новый заголовок"
    app_login/app_password: если None — берутся из GlobalSettings
    """
    path_to_1cv8 = get_global_setting('path_to_1cv8')
    if not path_to_1cv8 or not os.path.exists(path_to_1cv8):
        raise FileNotFoundError(f"1cv8.exe не найден по пути из настроек: {path_to_1cv8}")

    server = get_global_setting('app_server')
    port = get_global_setting('app_port')
    
    if not app_login:
        app_login = get_global_setting('app_user')
    if not app_password:
        app_password = get_global_setting('app_password')

    cmd = [
        path_to_1cv8,
        "/S", f"{server}:{port}\\{db_name}",
        "/N", app_login,
        "/P", app_password,
        "/C", command
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка выполнения команды 1С: {result.stderr}")
    return result.stdout

def run_1c_command_via_rac(db_name, command):
    """
    Выполняет команду через rac, используя GUID из кластера.
    command: например "session list", "infobase update --sessions-deny=on"
    """
    path_to_rac = get_global_setting('path_to_rac')
    if not os.path.exists(path_to_rac):
        raise FileNotFoundError(f"rac.exe не найден по пути: {path_to_rac}")

    cluster_info = get_cluster_info_for_db(db_name)
    if not cluster_info:
        raise ValueError(f"Не найдены GUID для базы: {db_name}")

    cluster_guid = cluster_info['cluster_guid']
    infobase_guid = cluster_info['infobase_guid']

    user = get_global_setting('app_server_admin')  # из backup.json
    password = get_global_setting('app_server_admin_pwd')

    cmd = [
        path_to_rac,
        command,
        f"--cluster={cluster_guid}",
        f"--infobase={infobase_guid}",
        f"--cluster-user={user}",
        f"--cluster-pwd={password}"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode != 0:
        raise RuntimeError(f"Ошибка выполнения команды rac: {result.stderr}")
    return result.stdout