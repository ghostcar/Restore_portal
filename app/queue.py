# app/queue.py

import threading
import time
from flask import current_app
from .config_loader import get_svc_conn

shutdown_event = threading.Event()

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
                from .db_actions import perform_restore_job
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