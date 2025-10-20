import os
from pathlib import Path
import pyodbc
import datetime
import threading
import time
from collections import deque
from flask import Blueprint, request, jsonify, render_template, flash,  redirect, url_for, Response, stream_with_context # добавим flash
from .auth import get_current_user
from .config_loader import get_user_databases, get_common_databases, get_db_config, get_svc_conn, is_user_admin, get_global_setting
from .onec_commands import run_1c_command_via_1cv8, run_1c_command_via_rac
from .email_sender import notify_user_on_restore_complete
from .logger_db import log_user_action, log_1c_operation
from .db_utils import get_svc_conn, get_sql_server_conn, get_sql_server_conn_config
from flask import current_app
import logging
import re
import json

try:
    from app.services import worker as services_worker
except Exception:
    services_worker = None
    

bp = Blueprint('db_ops', __name__)

# --- Очередь восстановления ---
restore_queue = deque()
running_tasks = set()
max_concurrent = int(get_global_setting('max_concurrent_restores') or 2)
allow_dynamic_backup = get_global_setting('allow_dynamic_backup_creation') == '1'

# Событие для остановки потока
shutdown_event = threading.Event()
running_tasks = set()

def validate_db_name(db_name):
    """Проверяет, что имя БД состоит только из допустимых символов."""
    if not re.match(r'^[A-Za-z0-9_]+$', db_name):
        raise ValueError(f"Недопустимое имя базы данных: {db_name}")
    return True

def get_quoted_name(db_name):
    """Возвращает безопасно экранированное имя БД через QUOTENAME."""
    validate_db_name(db_name)
    conn = get_sql_server_conn()  # Функция, которая возвращает подключение к SQL Server
    cursor = conn.cursor()
    cursor.execute("SELECT QUOTENAME(?)", db_name)
    quoted = cursor.fetchone()[0]
    conn.close()
    return quoted

def load_pending_jobs_from_db():
    """Загружает все pending задачи из БД при старте приложения."""
    recover_stuck_jobs()    # <-- Восстанавливаем зависшие задачи
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, windows_user, target_db
        FROM RestoreQueue
        WHERE status = 'pending'
        ORDER BY priority, created_at
    """)
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        job_id, windows_user, target_db = row
        current_app.logger.info(f"Задача {job_id} ({target_db}) загружена из БД")
        # Можно добавить в очередь или сразу запустить
        # Пока просто логируем

def atomic_claim_job(job_id):
    """Атомарно захватывает задачу из БД."""
    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE RestoreQueue
            SET status = 'running', started_at = GETDATE()
            WHERE id = ? AND status = 'pending'
        """, job_id)
        conn.commit()
        rows_affected = cursor.rowcount
        return rows_affected > 0
    except Exception as e:
        current_app.logger.error(f"Ошибка при захвате задачи {job_id}: {e}")
        return False
    finally:
        conn.close()

def worker_loop():
    """Основной цикл фонового потока."""
    current_app.logger.info("worker_loop: Поток запущен")
    while not shutdown_event.is_set():
        try:
            conn = get_svc_conn()
            cursor = conn.cursor()
            # Атомарно получаем одну задачу со статусом 'pending'
            cursor.execute("""
                UPDATE TOP(1) RestoreQueue
                SET status = 'running', started_at = GETDATE()
                OUTPUT INSERTED.id, INSERTED.windows_user, INSERTED.target_db
                WHERE status = 'pending'
                ORDER BY priority, created_at
            """)
            row = cursor.fetchone()
            conn.close()

            if row:
                job_id, windows_user, target_db = row
                current_app.logger.info(f"worker_loop: Захвачена задача {job_id} для {target_db}")

                # Запускаем обработку задачи в отдельном потоке
                thread = threading.Thread(
                    target=perform_restore_job,
                    args=({"id": job_id, "windows_user": windows_user, "target_db": target_db},),
                    daemon=True
                )
                thread.start()
                current_app.logger.info(f"worker_loop: Задача {job_id} передана в поток")
            else:
                # Нет задач, ждём
                current_app.logger.debug("worker_loop: Нет задач, жду 5 секунд...")
                shutdown_event.wait(timeout=5)
        except Exception as e:
            current_app.logger.error(f"worker_loop: Ошибка в основном цикле: {e}")
            # Ждём перед следующей попыткой
            shutdown_event.wait(timeout=5)
    current_app.logger.info("worker_loop: Поток остановлен")

def recover_stuck_jobs():
    """Переводит "зависшие" задачи (status = 'running' давно) в Error."""
    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE RestoreQueue
            SET status = 'failed', finished_at = GETDATE(), error_message = 'Задача зависла, процесс перезапущен'
            WHERE status = 'running' AND started_at < DATEADD(minute, -30, GETDATE())
        """)
        conn.commit()
        rows_affected = cursor.rowcount
        if rows_affected > 0:
            current_app.logger.info(f"Восстановлено {rows_affected} зависших задач")
    except Exception as e:
        current_app.logger.error(f"Ошибка при восстановлении зависших задач: {e}")
    finally:
        conn.close()
    
def update_job_status(job_id, status, error_message=None):
    """Обновляет статус задачи в RestoreQueue и записывает в RestoreJobs"""
    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        # Атомарное обновление статуса
        if status == 'running':
            cursor.execute("""
                UPDATE RestoreQueue
                SET status = ?, started_at = GETDATE()
                WHERE id = ?
            """, status, job_id)
        elif status in ('completed', 'failed'):
            cursor.execute("""
                UPDATE RestoreQueue
                SET status = ?, finished_at = GETDATE(), error_message = ?
                WHERE id = ?
            """, status, error_message, job_id)

        # Записываем в историю RestoreJobs
        cursor.execute("""
            INSERT INTO RestoreJobs (job_id, windows_user, target_db, status, error_message)
            SELECT id, windows_user, target_db, status, error_message
            FROM RestoreQueue
            WHERE id = ?
        """, job_id)

        conn.commit()
    except Exception as e:
        current_app.logger.error(f"Ошибка при обновлении статуса задачи {job_id}: {e}")
    finally:
        conn.close()
    
    
def perform_restore_job(job):
    # job = {'id': ..., 'windows_user': ..., 'target_db': ...}
    job_id = job['id']
    user = job['windows_user']
    target_db = job['target_db']
    db_config = get_db_config(target_db, user)
    if not db_config:
        update_job_status(job_id, 'failed', 'Нет доступа к БД')
        log_1c_operation(job_id, target_db, 'access_check', 'error', error_message='Нет доступа к БД')
        running_tasks.discard(job_id)
        return

    source_db = db_config['source_db']
    backup_path = get_backup_path_for_db(source_db)
    if not backup_path:
        if not allow_dynamic_backup:
            update_job_status(job_id, 'failed', 'Бэкап отсутствует и создание запрещено')
            log_1c_operation(job_id, target_db, 'backup_check', 'error', error_message='Бэкап отсутствует и создание запрещено')
            running_tasks.discard(job_id)
            return
        print(f"⚠️ Нет бэкапа для {source_db}, создаю...")
        log_1c_operation(job_id, target_db, 'backup_creation', 'started', log_text=f"Создание бэкапа {source_db}")
        from scripts.backup_task import create_backup_from_source
        backup_path = create_backup_from_source(source_db)
        log_1c_operation(job_id, target_db, 'backup_creation', 'success', log_text=f"Бэкап {backup_path} создан")

    # SQL-логины для восстановления БД (не зависят от пользователя)
    sql_login = db_config['sql_login']
    sql_password = db_config['sql_password']
    try:
        # --- НОВОЕ: Блокировка сеансов через rac перед восстановлением ---
        try:
            log_1c_operation(job_id, target_db, 'session_block', 'started', log_text="Блокировка сеансов через rac")
            run_1c_command_via_rac(target_db, "infobase update --sessions-deny=on")
            log_1c_operation(job_id, target_db, 'session_block', 'success', log_text="Сеансы заблокированы")
            print(f"✅ Сеансы заблокированы для {target_db}")
        except Exception as e:
            log_1c_operation(job_id, target_db, 'session_block', 'error', error_message=str(e))
            print(f"⚠️ Не удалось заблокировать сеансы для {target_db}: {e}")
            # Не фатально, можно продолжить

        log_1c_operation(job_id, target_db, 'restore_db', 'started', log_text="Начало восстановления БД из .bak")
        restore_db_from_backup(target_db, backup_path, sql_login, sql_password)
        log_1c_operation(job_id, target_db, 'restore_db', 'success', log_text="Восстановление БД завершено")

        # --- НОВОЕ: Работа с 1С (с пользовательскими логинами) ---
        extension_name = db_config.get('extension_name', '')
        
        app_login = db_config.get('app_login')
        app_password = db_config.get('app_password')

        if extension_name:
            log_1c_operation(job_id, target_db, 'disconnect_from_storage', 'started', log_text=f"Отключение от хранилища {extension_name}")
            run_1c_command_via_1cv8(target_db, f"DisconnectFromStorage;{extension_name}", app_login, app_password)
            log_1c_operation(job_id, target_db, 'disconnect_from_storage', 'success', log_text=f"Отключено от хранилища {extension_name}")

        header = db_config.get('header', 'Без заголовка')
        today_str = datetime.date.today().strftime("%d.%m.%Y")
        final_header = f"{header} {today_str}"
        log_1c_operation(job_id, target_db, 'set_title', 'started', log_text=f"Установка заголовка: {final_header}")
        run_1c_command_via_1cv8(target_db, f"SetTitle;{final_header}", app_login, app_password)
        log_1c_operation(job_id, target_db, 'set_title', 'success', log_text=f"Заголовок установлен: {final_header}")

        if db_config.get('use_storage'):
            # Используем пользовательские логины от хранилища
            storage_user = db_config.get('storage_user')
            storage_password = db_config.get('storage_password')
            storage_path = db_config.get('storage_path')
            if storage_user and storage_password and storage_path:
                log_1c_operation(job_id, target_db, 'connect_to_storage', 'started', log_text=f"Подключение к хранилищу {storage_path}")
                run_1c_command_via_1cv8(target_db, f"ConnectToStorage;{storage_path};{storage_user};{storage_password};{extension_name}", app_login, app_password)
                log_1c_operation(job_id, target_db, 'connect_to_storage', 'success', log_text=f"Подключено к хранилищу {storage_path}")

                log_1c_operation(job_id, target_db, 'update_from_storage', 'started', log_text=f"Обновление из хранилища {extension_name}")
                run_1c_command_via_1cv8(target_db, f"UpdateFromStorage;{extension_name}", app_login, app_password)
                log_1c_operation(job_id, target_db, 'update_from_storage', 'success', log_text=f"Обновление из хранилища завершено")

                log_1c_operation(job_id, target_db, 'update_cfg', 'started', log_text=f"Обновление конфигурации БД {extension_name}")
                run_1c_command_via_1cv8(target_db, f"UpdateDBCfg;{extension_name}", app_login, app_password)
                log_1c_operation(job_id, target_db, 'update_cfg', 'success', log_text=f"Конфигурация БД обновлена")

        set_parallelism(target_db, 0, sql_login, sql_password)
        log_1c_operation(job_id, target_db, 'set_parallelism', 'success', log_text="Параллелизм установлен в 0")
        set_parallelism(target_db, 1, sql_login, sql_password)
        log_1c_operation(job_id, target_db, 'set_parallelism', 'success', log_text="Параллелизм установлен в 1")

        # --- НОВОЕ: Разблокировка сеансов после восстановления ---
        try:
            log_1c_operation(job_id, target_db, 'session_unblock', 'started', log_text="Разблокировка сеансов через rac")
            run_1c_command_via_rac(target_db, "infobase update --sessions-deny=off")
            log_1c_operation(job_id, target_db, 'session_unblock', 'success', log_text="Сеансы разблокированы")
            print(f"✅ Сеансы разблокированы для {target_db}")
        except Exception as e:
            log_1c_operation(job_id, target_db, 'session_unblock', 'error', error_message=str(e))
            print(f"⚠️ Не удалось разблокировать сеансы для {target_db}: {e}")

        update_job_status(job_id, 'completed')

        # --- НОВОЕ: Отправка уведомления пользователю ---
        notify_user = db_config.get('notify_user', False)
        if notify_user:
            user_email = get_user_email(user)
            if user_email:
                notify_user_on_restore_complete(user_email, target_db, 'успешно завершено')

    except Exception as e:
        error_msg = str(e)
        update_job_status(job_id, 'failed', error_msg)
        log_1c_operation(job_id, target_db, 'restore_process', 'error', error_message=error_msg)

        # --- НОВОЕ: Отправка уведомления пользователю об ошибке ---
        notify_user = db_config.get('notify_user', False)
        if notify_user:
            user_email = get_user_email(user)
            if user_email:
                notify_user_on_restore_complete(user_email, target_db, 'ошибка', error_msg)

    finally:
        running_tasks.discard(job_id)
        
def perform_restore_job(job):
    # job = {'id': ..., 'windows_user': ..., 'target_db': ...}
    job_id = job['id']
    user = job['windows_user']
    target_db = job['target_db']
    db_config = get_db_config(target_db, user)
    if not db_config:
        update_job_status(job_id, 'failed', 'Нет доступа к БД')
        running_tasks.discard(job_id)
        return

    source_db = db_config['source_db']
    backup_path = get_backup_path_for_db(source_db)
    if not backup_path:
        if not allow_dynamic_backup:
            update_job_status(job_id, 'failed', 'Бэкап отсутствует и создание запрещено')
            running_tasks.discard(job_id)
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
        
        # --- НОВОЕ: Отправка уведомления пользователю ---
        notify_user = db_config.get('notify_user', False)
        if notify_user:
            user_email = get_user_email(user)  # новая функция
            if user_email:
                notify_user_on_restore_complete(user_email, target_db, 'успешно завершено')

    except Exception as e:
        error_msg = str(e)
        update_job_status(job_id, 'failed', error_msg)

        # --- НОВОЕ: Отправка уведомления пользователю об ошибке ---
        notify_user = db_config.get('notify_user', False)
        if notify_user:
            user_email = get_user_email(user)
            if user_email:
                notify_user_on_restore_complete(user_email, target_db, 'ошибка', error_msg)

    finally:
        running_tasks.discard(job_id)

def get_user_email(windows_login):
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM Users WHERE windows_login = ?", windows_login)
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

#def legacy_restore_worker():
#    """Фоновый поток, который обрабатывает очередь задач RestoreQueue."""
#    load_pending_jobs_from_db()  # <-- Загружаем pending задачи при старте
#    worker_loop()  # <-- Запускаем основной цикл
    
def get_backup_path_for_db(source_db_name):
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

def restore_db_from_backup(target_db_name, backup_file_path, sql_login, sql_password):
   # Подключаемся к master через основной SQL Server
    conn_str = get_sql_server_conn_config() # <-- Используем функцию из db_utils
    # Перезаписываем логин/пароль из параметров функции (это могут быть логины из БД)
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={get_global_setting('sql_server_address') or 'localhost'};UID={sql_login};PWD={sql_password};"
    conn = pyodbc.connect(conn_str)
    
    conn.autocommit = True
    cursor = conn.cursor()
    quoted_name = get_quoted_name(target_db_name)
    cursor.execute(f"ALTER DATABASE [{quoted_name} ] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
    cursor.execute(f"""
        RESTORE DATABASE [{quoted_name}]
        FROM DISK = ?
        WITH REPLACE, RECOVERY, STATS = 5
    """, backup_file_path)
    cursor.execute(f"ALTER DATABASE [{quoted_name}] SET MULTI_USER")
    cursor.execute(f"ALTER AUTHORIZATION ON DATABASE::[{quoted_name}] TO [sa]")
    cursor.execute(f"ALTER DATABASE [{quoted_name}] SET RECOVERY SIMPLE")
    cursor.execute(f"USE [{quoted_name}]; DBCC SHRINKFILE (2, TRUNCATEONLY);")

    conn.close()

def set_parallelism(target_db_name, degree, sql_login, sql_password):
    quoted_name = get_quoted_name(target_db_name)
     # Подключаемся к master через основной SQL Server
    conn_str = get_sql_server_conn_config() # <-- Используем функцию из db_utils
    # Перезаписываем логин/пароль из параметров функции (это могут быть логины из БД)
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={get_global_setting('sql_server_address') or 'localhost'};UID={sql_login};PWD={sql_password};"
    
    conn = pyodbc.connect(conn_str)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(f"""
        USE [{quoted_name}];
        EXEC sp_configure 'show advanced options', 1;
        RECONFIGURE WITH OVERRIDE;
        EXEC sp_configure 'max degree of parallelism', {degree};
        RECONFIGURE WITH OVERRIDE;
    """)
    conn.close()


@bp.route('/')
def index():
    user = get_current_user()
    is_admin = is_user_admin(user)
  
  # Получаем количество задач в очереди
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM RestoreQueue")
    total_queue_count = cursor.fetchone()[0]

    if is_admin:
        user_queue_count = total_queue_count
    else:
        cursor.execute("SELECT COUNT(*) FROM RestoreQueue WHERE windows_user = ?", user)
        user_queue_count = cursor.fetchone()[0]
          
    cursor.execute("SELECT COUNT(*) FROM RestoreQueue")
    if is_admin:
        cursor.execute("""
            SELECT COUNT(*) FROM RestoreQueue 
            WHERE status IN ('pending', 'running')
        """)
        active_queue_count = cursor.fetchone()[0]
        total_active_count = active_queue_count
    else:
        cursor.execute("""
            SELECT COUNT(*) FROM RestoreQueue 
            WHERE windows_user = ? AND status IN ('pending', 'running')
        """, user)
        user_active_count = cursor.fetchone()[0]
        
        # Общее количество активных задач (для отображения в ссылке)
        cursor.execute("""
            SELECT COUNT(*) FROM RestoreQueue 
            WHERE status IN ('pending', 'running')
        """)
        total_active_count = cursor.fetchone()[0]
        
        active_queue_count = user_active_count
        
    conn.close()
    
    # Получаем email пользователя
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM Users WHERE windows_login = ?", user)
    email_row = cursor.fetchone()
    user_email = email_row[0] if email_row else None

    # Получаем все БД для пользователя (и общие, если не админ_only)
    user_dbs = get_user_databases(user)
    common_dbs = get_common_databases()

    all_dbs = user_dbs + common_dbs

    # Получаем очередь
    cursor.execute("""
        SELECT rq.id, rq.windows_user, rq.target_db, rq.status, rq.created_at, rq.started_at, rq.finished_at
        FROM RestoreQueue rq
        WHERE rq.windows_user = ?
        ORDER BY rq.priority, rq.created_at
    """ if not is_admin else """
        SELECT rq.id, rq.windows_user, rq.target_db, rq.status, rq.created_at, rq.started_at, rq.finished_at
        FROM RestoreQueue rq
        ORDER BY rq.priority, rq.created_at
    """, (user,) if not is_admin else ())
    queue_data = cursor.fetchall()
    conn.close()

    # Получаем статусы последних задач для отображения на главной
    status_data = {}
    for db in all_dbs:
        conn = get_svc_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP 1 status, started_at, finished_at, error_message
            FROM RestoreJobs
            WHERE target_db = ?
            ORDER BY started_at DESC            
        """, db['target'])
        row = cursor.fetchone()
        if row:
            status_data[db['target']] = {
                "status": row[0],
                "started_at": row[1],
                "finished_at": row[2],
                "error_message": row[3]
            }
        else:
            status_data[db['target']] = {
                "status": "Нет данных",
                "started_at": None,
                "finished_at": None,
                "error_message": None
            }
        conn.close()

#    return render_template(
#        'dashboard.html',
#        databases=all_dbs,
#        queue=queue_data,
#        user=user,
#        user_email=user_email,
#        is_admin=is_admin,
#        status_data=status_data,
#        total_queue_count=total_queue_count,
#        user_queue_count=user_queue_count,
#        active_queue_count=active_queue_count,          # <-- Новое
#        total_active_count=total_active_count,             # <-- Новое
#        script_name=request.environ.get('SCRIPT_NAME', '')
#    )
 # НОВОЕ: Получаем последний лог для выполняющихся задач
    last_logs = {}
    for db in all_dbs:
        target_db = db['target']
        conn = get_svc_conn()
        cursor = conn.cursor()
        # Ищем последнюю RUNNING задачу
        cursor.execute("""
            SELECT TOP 1 rj.id
            FROM RestoreJobs rj
            WHERE rj.target_db = ? AND rj.status = 'running'
            ORDER BY rj.started_at DESC
        """, target_db)
        job_row = cursor.fetchone()
        if job_row:
            job_id = job_row[0]
            # Получаем последний лог по этой задаче
            cursor.execute("""
                SELECT TOP 1 message
                FROM RestoreTaskLogs
                WHERE job_id = ?
                ORDER BY timestamp DESC
            """, job_id)
            log_row = cursor.fetchone()
            if log_row:
                last_logs[target_db] = log_row[0]
            else:
                last_logs[target_db] = "Нет логов"
        else:
            last_logs[target_db] = ""
        conn.close()

    return render_template(
        'dashboard.html',
        databases=all_dbs,
        queue=queue_data,
        user=user,
        user_email=user_email,
        is_admin=is_admin,
        status_data=status_data,
        total_queue_count=total_queue_count,
        user_queue_count=user_queue_count,
        active_queue_count=active_queue_count,
        total_active_count=total_active_count,
        script_name=request.environ.get('SCRIPT_NAME', ''),
        last_logs=last_logs  # <-- Передаём последние логи
    )    
    
@bp.route('/profile', methods=['GET', 'POST'])
def profile():
    user = get_current_user()
    conn = get_svc_conn()
    cursor = conn.cursor()
    if request.method == 'POST':
        new_email = request.form.get('email')
        cursor.execute("UPDATE Users SET email = ? WHERE windows_login = ?", new_email, user)
        conn.commit()
        flash("Почта обновлена", "success")
        return redirect(url_for('db_ops.profile'))

    cursor.execute("SELECT email FROM Users WHERE windows_login = ?", user)
    email_row = cursor.fetchone()
    current_email = email_row[0] if email_row else None
    conn.close()
    return render_template('profile.html', user_email=current_email)

@bp.route('/restore', methods=['POST'])
def restore():
    user = get_current_user()
    target_db = request.form.get('database')

    db_config = get_db_config(target_db, user)
    if not db_config:
        return jsonify({"error": "Нет доступа к БД"}), 403
        log_user_action(user, 'restore_requested', target_db, 'Доступ запрещён')
        return jsonify({"error": "Нет доступа к БД"}), 403

    log_user_action(user, 'restore_requested', target_db, 'Задача добавлена в очередь')
    # Добавляем задачу в очередь
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO RestoreQueue (windows_user, target_db, status, priority)
        OUTPUT INSERTED.ID
        VALUES (?, ?, 'pending', 10)
    """, user, target_db)
    job_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()

    # Добавляем в очередь в памяти
    restore_queue.append({'id': job_id, 'windows_user': user, 'target_db': target_db})

    return jsonify({"status": "ok", "message": f"Задача восстановления для {target_db} добавлена в очередь", "job_id": job_id})

@bp.route('/queue/status')
def queue_status():
    user = get_current_user()
    is_admin = is_user_admin(user)

    conn = get_svc_conn()
    cursor = conn.cursor()
    if is_admin:
        cursor.execute("""
            SELECT rq.id, rq.windows_user, rq.target_db, rq.status, rq.priority,rq.created_at, rq.started_at, rq.finished_at, rq.error_message
            FROM RestoreQueue rq
            ORDER BY rq.priority, rq.created_at
        """)
    else:
        cursor.execute("""
            SELECT rq.id, rq.windows_user, rq.target_db, rq.status, rq.priority, rq.created_at, rq.started_at, rq.finished_at, rq.error_message
            FROM RestoreQueue rq
            WHERE rq.windows_user = ?
            ORDER BY rq.priority, rq.created_at
        """, user)
    rows = cursor.fetchall()

    # Преобразуем pyodbc.Row в словари
    queue_data = []
    for row in rows:
        queue_data.append({
            "id": row.id,
            "windows_user": row.windows_user,
            "target_db": row.target_db,
            "status": row.status,
            "priority": row.priority,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "error_message": row.error_message
        })
    conn.close()

    return jsonify({"queue": queue_data})

@bp.route('/settings/<target_db>', methods=['GET', 'POST'])
def user_db_settings(target_db):
    user = get_current_user()
    # Проверим, принадлежит ли БД пользователю
    db_config = get_db_config(target_db, user)
    if not db_config:
        return "Доступ запрещён", 403

    if request.method == 'POST':
        user_app_login = request.form.get('user_app_login')
        user_app_password = request.form.get('user_app_password')
        user_storage_login = request.form.get('user_storage_login')
        user_storage_password = request.form.get('user_storage_password')

        conn = get_svc_conn()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE UserDatabases
            SET user_app_login = ?, user_app_password = ?, user_storage_login = ?, user_storage_password = ?
            WHERE restore_target_db = ? AND user_id = (
                SELECT id FROM Users WHERE windows_login = ?
            )
        """, user_app_login, user_app_password, user_storage_login, user_storage_password, target_db, user)
        conn.commit()
        conn.close()
        flash("Настройки обновлены", "success")
        return redirect(url_for('db_ops.user_db_settings', target_db=target_db))

    # GET: покажем форму
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_app_login, user_app_password, user_storage_login, user_storage_password
        FROM UserDatabases
        WHERE restore_target_db = ? AND user_id = (
            SELECT id FROM Users WHERE windows_login = ?
        )
    """, target_db, user)
    row = cursor.fetchone()
    conn.close()

    settings = {
        "user_app_login": row[0] if row else None,
        "user_app_password": row[1] if row else None,
        "user_storage_login": row[2] if row else None,
        "user_storage_password": row[3] if row else None,
    }

    return render_template('user_settings.html', target_db=target_db, settings=settings)

# НОВОЕ: маршрут для просмотра логов по БД
@bp.route('/logs/<target_db>')
def user_logs(target_db):
    user = get_current_user()
    is_admin = is_user_admin(user)
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    if is_admin:
        # Админ видит все логи по БД
        cursor.execute("""
            SELECT windows_user, target_db, status, started_at, finished_at, error_message
            FROM RestoreJobs
            WHERE target_db = ?
            ORDER BY started_at DESC
        """, target_db)
    else:
        # Пользователь видит только свои логи по БД
        cursor.execute("""
            SELECT windows_user, target_db, status, started_at, finished_at, error_message
            FROM RestoreJobs
            WHERE target_db = ? AND windows_user = ?
            ORDER BY started_at DESC
        """, target_db, user)
    
    logs = cursor.fetchall()
    conn.close()
    return render_template('user_logs.html', logs=logs, target_db=target_db, is_admin=is_admin)

@bp.route('/logs/1c/<target_db>')
def user_1c_logs(target_db):
    user = get_current_user()
    is_admin = is_user_admin(user)
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    if is_admin:
        # Админ видит все логи по БД
        cursor.execute("""
            SELECT job_id, operation, status, log_text, error_message, timestamp
            FROM OneCOperationLog
            WHERE target_db = ?
            ORDER BY timestamp DESC
        """, target_db)
    else:
        # Пользователь видит только свои логи по БД (проверка через UserDatabases)
        cursor.execute("""
            SELECT ocl.job_id, ocl.operation, ocl.status, ocl.log_text, ocl.error_message, ocl.timestamp
            FROM OneCOperationLog ocl
            JOIN RestoreQueue rq ON ocl.job_id = rq.id
            WHERE ocl.target_db = ? AND rq.windows_user = ?
            ORDER BY ocl.timestamp DESC
        """, target_db, user)
    
    logs = cursor.fetchall()
    conn.close()
    return render_template('user_1c_logs.html', logs=logs, target_db=target_db, is_admin=is_admin)

@bp.route('/backups')
def user_backups():
    user = get_current_user()
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.id, ub.target_db_name, ub.backup_file_path, ub.created_at, ub.description
        FROM UserBackups ub
        JOIN Users u ON ub.user_id = u.id
        WHERE u.windows_login = ?
        ORDER BY ub.created_at DESC
    """, user)
    backups = cursor.fetchall()

    # Получим список БД, доступных пользователю, для возможности создания бэкапа
    user_dbs = get_user_databases(user)
    common_dbs = get_common_databases()
    all_dbs = user_dbs + common_dbs
    db_names = [db['target'] for db in all_dbs]

    conn.close()
    return render_template('user_backups.html', backups=backups, databases=db_names, user=user)

@bp.route('/backups/create', methods=['POST'])
def create_user_backup():
    user = get_current_user()
    target_db = request.form.get('database')

    # Проверим, принадлежит ли БД пользователю (или он админ)
    is_admin = is_user_admin(user)
    if not is_admin:
        db_config = get_db_config(target_db, user)
        if not db_config:
            return jsonify({"error": "Нет доступа к БД"}), 403

    # Получим глобальные настройки
    backup_base_path_setting = get_global_setting('backup_base_path') or r'D:\SQLBackups'
    backup_base_path = os.path.join(backup_base_path_setting, user) # <-- Папка по пользователю
    Path(backup_base_path).mkdir(parents=True, exist_ok=True)

    # Сформируем имя файла
    import datetime
    today_str = datetime.date.today().strftime("%d%m%Y")
    backup_filename = f"{target_db}_{today_str}.bak" # <-- Обновлённый формат
    backup_file_path = os.path.join(backup_base_path, backup_filename)

    # Получим настройки подключения к БД для бэкапа (из БД, через db_config)
    db_config = get_db_config(target_db, user)
    sql_login = db_config.get('sql_login', 'sa') # или получить из GlobalSettings
    sql_password = db_config.get('sql_password', '...')

    try:
        quoted_name = get_quoted_name(target_db)
        # Подключаемся к prod-серверу (или к серверу БД, если это не prod)
        # Используем настройки из config/app_config.json для основного сервера
        sql_server_address = get_global_setting('sql_server_address') or 'localhost'
        prod_conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_server_address};DATABASE={quoted_name};UID={sql_login};PWD={sql_password}"
        prod_conn = pyodbc.connect(prod_conn_str)

        # ... (остальной код бэкапа)
        prod_conn.close()

        # ... (запись в БД)
        conn = get_svc_conn() # <-- Используем функцию из db_utils
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO UserBackups (user_id, target_db_name, backup_file_path, description)
            VALUES ((SELECT id FROM Users WHERE windows_login = ?), ?, ?, ?)
        """, user, target_db, backup_file_path, f"Ручной бэкап {today_str}")
        conn.commit()
        conn.close()

        log_user_action(user, 'backup_created', target_db, f"Бэкап создан: {backup_file_path}")
        return jsonify({"status": "ok", "message": f"Бэкап БД {target_db} создан: {backup_file_path}"})

    except Exception as e:
        error_msg = str(e)
        log_user_action(user, 'backup_failed', target_db, f"Ошибка создания бэкапа: {error_msg}")
        return jsonify({"error": f"Ошибка создания бэкапа: {error_msg}"}), 500

@bp.route('/backups/restore', methods=['POST'])
def restore_from_user_backup():
    user = get_current_user()
    backup_id = request.form.get('backup_id')
    target_db = request.form.get('target_db') # БД, КУДА восстанавливаем

    # Проверим, принадлежит ли бэкап пользователю
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ub.backup_file_path, u.windows_login
        FROM UserBackups ub
        JOIN Users u ON ub.user_id = u.id
        WHERE ub.id = ? AND u.windows_login = ?
    """, backup_id, user)
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Бэкап не найден или доступ запрещён"}), 403

    backup_file_path = row[0]
    # Проверим, принадлежит ли target_db пользователю (или он админ)
    is_admin = is_user_admin(user)
    if not is_admin:
        db_config = get_db_config(target_db, user)
        if not db_config:
            conn.close()
            return jsonify({"error": "Нет доступа к целевой БД"}), 403

    # Проверим, существует ли файл
    if not os.path.exists(backup_file_path):
        conn.close()
        return jsonify({"error": "Файл бэкапа не найден на диске"}), 500

    conn.close()

    # --- Логика восстановления из backup_file_path в target_db ---
    # Эта логика аналогична perform_restore_job, но с конкретным .bak-файлом
    # и без проверки/создания бэкапа
    # Используем db_config для получения sql_login/password для target_db
    db_config = get_db_config(target_db, user)
    sql_login = db_config['sql_login']
    sql_password = db_config['sql_password']

    try:
        # Подключаемся к master
        sql_server_address = get_global_setting('sql_server_address') or 'localhost'
        restore_conn = pyodbc.connect(
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={sql_server_address};"
            f"DATABASE=master;"
            f"UID={sql_login};PWD={sql_password}"
        )
        restore_conn.autocommit = True
        restore_cursor = restore_conn.cursor()
        quoted_name = get_quoted_name(target_db)
        # Отключаем пользователей
        restore_cursor.execute(f"ALTER DATABASE [{quoted_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")

        # Восстанавливаем
        # Для простоты, используем MOVE с именами файлов из БД-источника
        # или из настроек БД. Для этого нужно знать логические имена файлов.
        # В простом случае, можно не указывать MOVE, и файлы будут восстановлены в те же папки.
        # Или использовать путь из GlobalSettings для DATA/LOG.
        restore_cursor.execute(f"""
            RESTORE DATABASE [{quoted_name}]
            FROM DISK = ?
            WITH REPLACE, RECOVERY, STATS = 5
        """, backup_file_path)

        # Вернём в строй
        restore_cursor.execute(f"ALTER DATABASE [{quoted_name}] SET MULTI_USER")
        
        # Остальные настройки: владелец, режим восстановления, сжатие
        restore_cursor.execute(f"ALTER AUTHORIZATION ON DATABASE::[{quoted_name}] TO [sa]")
        restore_cursor.execute(f"ALTER DATABASE [{quoted_name}] SET RECOVERY SIMPLE")
        restore_cursor.execute(f"""
            USE [{quoted_name}];
            DBCC SHRINKFILE (2, TRUNCATEONLY);
        """)

        restore_conn.close()

        # --- НОВОЕ: Работа с 1С (с пользовательскими логинами) ---
        extension_name = db_config.get('extension_name', '')
        app_login = db_config.get('app_login')
        app_password = db_config.get('app_password')

        if extension_name:
            run_1c_command_via_1cv8(target_db, f"DisconnectFromStorage;{extension_name}", app_login, app_password)

        header = db_config.get('header', 'Без заголовка')
        import datetime
        today_str = datetime.date.today().strftime("%d.%m.%Y")
        final_header = f"{header} {today_str}"
        run_1c_command_via_1cv8(target_db, f"SetTitle;{final_header}", app_login, app_password)

        if db_config.get('use_storage'):
            storage_user = db_config.get('storage_user')
            storage_password = db_config.get('storage_password')
            storage_path = db_config.get('storage_path')
            if storage_user and storage_password and storage_path:
                run_1c_command_via_1cv8(target_db, f"ConnectToStorage;{storage_path};{storage_user};{storage_password};{extension_name}", app_login, app_password)
                run_1c_command_via_1cv8(target_db, f"UpdateFromStorage;{extension_name}", app_login, app_password)
                run_1c_command_via_1cv8(target_db, f"UpdateDBCfg;{extension_name}", app_login, app_password)

        # Настройка параллелизма
        from .db_ops import set_parallelism
        set_parallelism(target_db, 0, sql_login, sql_password)
        set_parallelism(target_db, 1, sql_login, sql_password)

        log_user_action(user, 'restore_from_backup', target_db, f"Восстановлено из {backup_file_path}")
        return jsonify({"status": "ok", "message": f"Восстановление из бэкапа {backup_file_path} в {target_db} завершено"})

    except Exception as e:
        error_msg = str(e)
        log_user_action(user, 'restore_from_backup_failed', target_db, f"Ошибка восстановления из {backup_file_path}: {error_msg}")
        return jsonify({"error": f"Ошибка восстановления: {error_msg}"}), 500
    
@bp.route('/logs/stream/<int:job_id>')
def stream_logs(job_id):
    """SSE endpoint для потоковой передачи логов задачи."""
    user = get_current_user()
    is_admin = is_user_admin(user)

    # Проверяем, имеет ли пользователь доступ к задаче
    conn = get_svc_conn()
    cursor = conn.cursor()
    if is_admin:
        cursor.execute("SELECT id FROM RestoreQueue WHERE id = ?", job_id)
    else:
        cursor.execute("SELECT id FROM RestoreQueue WHERE id = ? AND windows_user = ?", job_id, user)
    row = cursor.fetchone()
    conn.close()

    if not row:
        return "Нет доступа к задаче", 403

    def generate():
        last_id = 0
        while True:
            conn = get_svc_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, message, timestamp
                FROM RestoreTaskLogs
                WHERE job_id = ? AND id > ?
                ORDER BY id
            """, job_id, last_id)
            rows = cursor.fetchall()
            conn.close()

            if rows:
                for row in rows:
                    log_id, message, timestamp = row
                    yield f" {json.dumps({'id': log_id, 'message': message, 'timestamp': timestamp.isoformat()})}\n\n"
                    last_id = log_id
            else:
                # Отправляем heartbeat, чтобы соединение не закрывалось
                yield " {}\n\n"

            time.sleep(1)  # Пауза 1 секунда

    return Response(stream_with_context(generate()), mimetype="text/event-stream")