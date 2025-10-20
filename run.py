# run.py
from app import create_app
from app.services import worker as services_worker  # ✅ импортируем модуль с воркером
from app import db_ops  # ✅ импортируем для старой совместимости (временно)

import threading
import logging
from logging.handlers import RotatingFileHandler
import os

# --- глобальные переменные ---
worker_thread = None
app = create_app()

# --- логирование ---
if not os.path.exists('logs'):
    os.mkdir('logs')

file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('=== Приложение запущено ===')

# --- запуск фонового воркера ---
def start_background_worker():
    """
    Запускает фоновый поток для обработки очереди восстановлений.
    Новая версия делегирует работу модулю app.services.worker.
    """
    global worker_thread

    if worker_thread is None or not worker_thread.is_alive():
        try:
            worker_thread = threading.Thread(
                target=services_worker.worker_loop,  # ✅ новая реализация
                daemon=True
            )
            worker_thread.start()
            app.logger.info("Фоновый поток запущен через services_worker")
        except Exception as e:
            app.logger.error(f"Ошибка при запуске фонового потока: {e}")
            # fallback: старая логика
            try:
                worker_thread = threading.Thread(
                    target=db_ops.worker_loop,
                    daemon=True
                )
                worker_thread.start()
                app.logger.warning("Фоновый поток запущен через db_ops (legacy)")
            except Exception as e2:
                app.logger.critical(f"Не удалось запустить фоновый поток: {e2}")

def stop_background_worker():
    """Останавливает фоновый поток."""
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        try:
            services_worker.shutdown_event.set()  # сигнал завершения
            worker_thread.join(timeout=10)
            app.logger.info("Фоновый поток остановлен")
        except Exception as e:
            app.logger.error(f"Ошибка при остановке воркера: {e}")

# --- обработчики ошибок ---
@app.errorhandler(500)
def internal_error(error):
    app.logger.error('Ошибка сервера: %s', error)
    return "Ошибка сервера, проверьте логи.", 500

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.exception('Необработанное исключение: %s', e)
    return "Необработанная ошибка, проверьте логи.", 500


# --- основной запуск ---
if __name__ == '__main__':
    try:
        start_background_worker()
        app.run(debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("Останавливаю...")
    finally:
        stop_background_worker()
