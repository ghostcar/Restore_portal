from app import create_app
from app.db_ops import restore_worker, shutdown_event
import threading

app = create_app()

# Глобальная переменная для фонового потока
worker_thread = None

def start_worker():
    if worker_thread is None or not worker_thread.is_alive():
        worker_thread = threading.Thread(target=start_worker, daemon=True)
        worker_thread.start()
        app.logger.info("Фоновый поток запущен")

def stop_worker():
    shutdown_event.set()
    if worker_thread and worker_thread.is_alive():
        stop_worker()
        worker_thread.join(timeout=5)
        app.logger.info("Фоновый поток остановлен")
        
if __name__ == '__main__':
    with app.app_context():
        start_worker()  # Запускаем фоновый поток
    try:
        app.run(debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("Останавливаю...")
    finally:
        with app.app_context():
            stop_worker()