from flask import Blueprint, request, abort
from .logger_db import log_auth
import os

bp = Blueprint('auth', __name__)

#def get_current_user():
#    user = request.environ.get('REMOTE_USER')
#    if not user:
#        log_auth("ANONYMOUS", "access_denied", f"REMOTE_USER отсутствует, IP: {request.environ.get('REMOTE_ADDR')}")
#        abort(401)
#    username = user.split('\\')[-1].lower()
#    log_auth(username, "login", f"Успешная аутентификация, IP: {request.environ.get('REMOTE_ADDR')}")
#    return username
def get_current_user():
    # Заглушка для разработки
    # В production должно работать через IIS + REMOTE_USER
    user = request.environ.get('REMOTE_USER')
    if not user:
        # Если запущено локально (не под IIS), использовать тестового пользователя
        # Только для разработки!
#        if os.getenv('FLASK_ENV') == 'development':
            return 'gorbunov' # <-- Замените на имя тестового пользователя из вашей БД
 #       else:
  #          abort(401) # Для безопасности на сервере
    # Формат: DOMAIN\username -> берём только username
    return user.split('\\')[-1].lower()