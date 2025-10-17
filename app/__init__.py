# app/__init__.py

from flask import Flask
from . import auth, db_ops, admin_ops

def create_app():
    # Указываем пути к папкам templates и static относительно расположения этого файла (__init__.py)
    # ../templates означает: подняться на один уровень вверх (..) и зайти в папку templates
    app = Flask(
        __name__,
        template_folder='../templates', # Путь к папке с шаблонами
        static_folder='../static'       # Путь к папке со статикой
    )
    app.config['SECRET_KEY'] = 'your-secret-key-here'
    app.register_blueprint(auth.bp)
    app.register_blueprint(db_ops.bp)
    app.register_blueprint(admin_ops.bp, url_prefix='/admin')
    return app