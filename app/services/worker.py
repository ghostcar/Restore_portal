# app/services/worker.py
"""
Модуль фонового воркера для обработки очереди RestoreQueue.
Старый код восстановления остается в app/db_ops.py; пока мы делегируем фактическую работу туда.
"""

import threading
import time
import traceback
import os
from datetime import datetime

# импорт вспомогательных функций/подключений из существующего проекта
# db_utils должен предоставить get_svc_conn() или аналог; в твоём проекте может называться иначе
try:
    from app import db_utils
except Exception:
    db_utils = None

# Импортируем старую реализацию restore как fallback
try:
    from app import db_ops as legacy_db_ops
except Exception:
    legacy_db_ops = None

# Параметры воркера можно брать из config или явно задавать здесь
DEFAULT_POLL_INTERVAL = 5         # seconds
DEFAULT_RESET_INTERVAL = 600      # seconds
DEFAULT_STUCK_HOURS = 24
DEFAULT_MAX_CONCURRENT = 1

shutdown_event = threading.Event()
_worker_thread = None
_running_jobs = set()
_lock = threading.Lock()

def read_worker_config():
    """
    Попытаться прочитать конфигные значения из существующего конфигуратора.
    Если нет, вернуть дефолтные.
    """
    poll = DEFAULT_POLL_INTERVAL
    reset_interval = DEFAULT_RESET_INTERVAL
    stuck_hours = DEFAULT_STUCK_HOURS
    max_concurrent = DEFAULT_MAX_CONCURRENT

    # если в проекте есть config_loader или config файлы, пробуем получить оттуда
    try:
        from app.config_loader import ОбщиеПараметры  # пример старого проекта
        # если такой объект есть, пробуем получить значения
        try:
            poll = int(ОбщиеПараметры.Параметр("worker.poll_interval") or poll)
        except Exception:
            pass
        try:
            reset_interval = int(ОбщиеПараметры.Параметр("worker.reset_check_interval") or reset_interval)
        except Exception:
            pass
        try:
            stuck_hours = int(ОбщиеПараметры.Параметр("worker.stuck_hours") or stuck_hours)
        except Exception:
            pass
        try:
            max_concurrent = int(ОбщиеПараметры.Параметр("worker.max_concurrent") or max_concurrent)
        except Exception:
            pass
    except Exception:
        # нет config_loader — используем env переменные или дефолты
        try:
            poll = int(os.environ.get("WORKER_POLL_INTERVAL", poll))
            reset_interval = int(os.environ.get("WORKER_RESET_INTERVAL", reset_interval))
            stuck_hours = int(os.environ.get("WORKER_STUCK_HOURS", stuck_hours))
            max_concurrent = int(os.environ.get("WORKER_MAX_CONCURRENT", max_concurrent))
        except Exception:
            pass

    return poll, reset_interval, stuck_hours, max_concurrent

def fetch_and_lock_next_job():
    """
    Атомарный захват следующей pending задачи из RestoreQueue.
    Возвращает dict {id, windows_user, target_db} или None.
    Реализовано через pyodbc/DB-connection (использует db_utils.get_svc_conn()).
    """
    if db_utils is None:
        return None

    conn = None
    try:
        conn = db_utils.get_svc_conn()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("""
            SELECT TOP 1 id, windows_user, target_db
            FROM RestoreQueue WITH (UPDLOCK, READPAST)
            WHERE status = 'pending'
            ORDER BY created_at
        """)
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None

        job_id, windows_user, target_db = row[0], row[1], row[2]
        # Попытка захвата: обновить статус только если он всё еще pending
        cur.execute("UPDATE RestoreQueue SET status = 'running', started_at = GETDATE() WHERE id = ? AND status = 'pending'", job_id)
        if cur.rowcount == 1:
            conn.commit()
            return {'id': job_id, 'windows_user': windows_user, 'target_db': target_db}
        else:
            conn.rollback()
            return None
    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except:
            pass
        print("[worker] fetch_and_lock_next_job error:", e)
        return None
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

def reset_stuck_jobs(stuck_hours=None):
    """
    Переводит очень старые running задачи обратно в pending (и инкрементирует attempts если нужна).
    По умолчанию использует конфигный порог.
    """
    if db_utils is None:
        return 0
    if stuck_hours is None:
        _, _, stuck_hours, _ = read_worker_config()
    conn = None
    try:
        conn = db_utils.get_svc_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE RestoreQueue
            SET status = 'pending', started_at = NULL
            WHERE status = 'running' AND DATEDIFF(hour, started_at, GETDATE()) >= ?
        """, stuck_hours)
        affected = cur.rowcount
        conn.commit()
        if affected:
            print(f"[worker] reset_stuck_jobs: reset {affected} jobs older than {stuck_hours} hours")
        return affected
    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except:
            pass
        print("[worker] reset_stuck_jobs error:", e)
        return 0
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

def perform_restore_job(job):
    """
    Выполняет фактическую логику восстановления.
    Пока делегируем в legacy_db_ops.perform_restore_job(job) если доступно,
    иначе имитируем короткую работу (для теста).
    После выполнения ставим status = completed / failed.
    """
    job_id = job.get('id')
    target_db = job.get('target_db')
    user = job.get('windows_user')
    print(f"[worker] starting job {job_id} target={target_db} user={user} at {datetime.now().isoformat()}")

    try:
        # Если в проекте есть старая реализация perform_restore_job в db_ops, вызовем её
        if legacy_db_ops and hasattr(legacy_db_ops, 'perform_restore_job'):
            # legacy expects parameters differently in your code — adjust as needed
            try:
                legacy_db_ops.perform_restore_job(job)
            except Exception as e:
                raise
        else:
            # demo/safe mode: просто симулируем длительную операцию
            time.sleep(5)

        # пометить завершенным
        conn = None
        if db_utils:
            conn = db_utils.get_svc_conn()
            cur = conn.cursor()
            cur.execute("UPDATE RestoreQueue SET status='completed', finished_at = GETDATE() WHERE id = ?", job_id)
            conn.commit()
            conn.close()

        print(f"[worker] completed job {job_id} at {datetime.now().isoformat()}")

    except Exception as e:
        print(f"[worker] job {job_id} failed: {e}")
        traceback.print_exc()
        try:
            if db_utils:
                conn = db_utils.get_svc_conn()
                cur = conn.cursor()
                cur.execute("UPDATE RestoreQueue SET status='failed', finished_at = GETDATE(), error_message = ? WHERE id = ?", str(e), job_id)
                conn.commit()
                conn.close()
        except Exception as ex:
            print("[worker] error updating failed status:", ex)
    finally:
        with _lock:
            _running_jobs.discard(job_id)

def _worker_loop():
    print("[worker] background service worker started")
    poll_interval, reset_interval, stuck_hours, max_concurrent = read_worker_config()[0:4]
    last_reset = 0
    while not shutdown_event.is_set():
        try:
            # периодически сбрасываем зависшие
            now = time.time()
            if now - last_reset > reset_interval:
                reset_stuck_jobs(stuck_hours)
                last_reset = now

            # пока есть место для параллели — брать задания
            while len(_running_jobs) < max_concurrent:
                job = fetch_and_lock_next_job()
                if not job:
                    break
                jid = job.get('id')
                with _lock:
                    if jid in _running_jobs:
                        # уже запущено
                        continue
                    _running_jobs.add(jid)
                t = threading.Thread(target=perform_restore_job, args=(job,), daemon=True)
                t.start()

            # ждем
            time.sleep(poll_interval)
        except Exception as e:
            print("[worker] loop exception:", e)
            traceback.print_exc()
            time.sleep(5)

    print("[worker] background service worker exiting")

def start_background_worker():
    """
    Запуск фонового воркера (создает daemon-thread, если уже не запущен).
    """
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
    print("[worker] start_background_worker called")

def stop_background_worker(timeout=5):
    shutdown_event.set()
    global _worker_thread
    if _worker_thread:
        _worker_thread.join(timeout=timeout)
