# run.py

from app import create_app
from app.db_ops import worker_loop, shutdown_event  # <-- Импортируем worker_loop
import threading
import logging
from logging.handlers import RotatingFileHandler
import os

# Глобальная переменная для фонового потока
worker_thread = None

app = create_app()

# Включаем логирование в файл
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

def start_background_worker():
    """Запускает фоновый поток для обработки очереди восстановлений."""
    global worker_thread
    if worker_thread is None or not worker_thread.is_alive():
        worker_thread = threading.Thread(target=worker_loop, daemon=True)  # <-- worker_loop, а не start_worker!
        worker_thread.start()
        app.logger.info("Фоновый поток запущен")

def stop_background_worker():
    """Останавливает фоновый поток."""
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        shutdown_event.set()  # <-- Сигнализируем потоку остановиться
        worker_thread.join(timeout=5)
        app.logger.info("Фоновый поток остановлен")

# Обработка ошибок
@app.errorhandler(500)
def internal_error(error):
    app.logger.error('Ошибка сервера: %s', error)
    return "Ошибка сервера, проверьте логи.", 500

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.exception('Необработанное исключение: %s', e)
    return "Необработанная ошибка, проверьте логи.", 500

if __name__ == '__main__':
    with app.app_context():
        start_background_worker()  # <-- Запускаем фоновый поток
    try:
        app.run(debug=False, host='0.0.0.0', port=5000)  # <-- debug=False для IIS
    except KeyboardInterrupt:
        print("Останавливаю...")
    finally:
        with app.app_context():
            stop_background_worker()  # <-- Останавливаем фоновый поток
            