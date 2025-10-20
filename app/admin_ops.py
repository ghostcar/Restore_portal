from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash, Response
from .auth import get_current_user, is_user_admin
from .config_loader import get_svc_conn, is_user_admin, get_global_setting
from .db_actions import get_backup_path_for_db, allow_dynamic_backup
from .queue import running_tasks
import threading
import time
import datetime
import pyodbc
import os
import json
import io
import csv

bp = Blueprint('admin_ops', __name__, url_prefix='/admin')

def is_user_admin_check():
    user = get_current_user()
    if not is_user_admin(user):
        return False
    return True

@bp.route('/')
def index():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    return render_template('admin/index.html')

@bp.route('/users')
def users():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, windows_login, full_name, email, is_admin FROM Users
        ORDER BY windows_login
    """)
    users = cursor.fetchall()
    conn.close()
    return render_template('admin/users.html', users=users)

@bp.route('/users/add', methods=['POST'])
def add_user():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    login = request.form['login'].strip().lower()
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    is_admin = request.form.get('is_admin') == 'on'
    
    if not login:
        flash("Логин не может быть пустым", "error")
        return redirect(url_for('admin_ops.users'))

    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO Users (windows_login, full_name, email, is_admin)
            VALUES (?, ?, ?, ?)
        """, login, name, email, is_admin)
        conn.commit()
        flash(f"Пользователь {login} добавлен", "success")
    except Exception as e:
        flash(f"Ошибка добавления пользователя: {e}", "error")
    conn.close()
    return redirect(url_for('admin_ops.users'))

@bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        is_admin = request.form.get('is_admin') == 'on'
        
        try:
            cursor.execute("""
                UPDATE Users
                SET full_name = ?, email = ?, is_admin = ?
                WHERE id = ?
            """, name, email, is_admin, user_id)
            conn.commit()
            flash("Пользователь обновлён", "success")
        except Exception as e:
            flash(f"Ошибка обновления пользователя: {e}", "error")
        conn.close()
        return redirect(url_for('admin_ops.users'))

    cursor.execute("""
        SELECT id, windows_login, full_name, email, is_admin FROM Users WHERE id = ?
    """, user_id)
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        flash("Пользователь не найден", "error")
        return redirect(url_for('admin_ops.users'))
        
    return render_template('admin/edit_user.html', user=user)

@bp.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM Users WHERE id = ?", user_id)
        conn.commit()
        flash("Пользователь удалён", "success")
    except Exception as e:
        flash(f"Ошибка удаления пользователя: {e}", "error")
    conn.close()
    return redirect(url_for('admin_ops.users'))

@bp.route('/databases')
def databases():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    # Получаем общие БД
    cursor.execute("""
        SELECT id, source_db_name, restore_target_db, header, is_admin_only
        FROM CommonDatabases
        ORDER BY restore_target_db
    """)
    common_dbs = cursor.fetchall()
    
    # Получаем пользовательские БД
    cursor.execute("""
        SELECT ud.id, u.windows_login, ud.restore_target_db, ud.source_db_name, ud.header, ud.use_storage
        FROM UserDatabases ud
        JOIN Users u ON ud.user_id = u.id
        ORDER BY u.windows_login, ud.restore_target_db
    """)
    user_dbs = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/databases.html', common_dbs=common_dbs, user_dbs=user_dbs)

@bp.route('/settings')
def settings():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    # Получаем GlobalSettings
    cursor.execute("""
        SELECT setting_key, setting_value, description
        FROM GlobalSettings
        ORDER BY setting_key
    """)
    global_settings = cursor.fetchall()
    
    # Получаем GlobalLimits
    cursor.execute("""
        SELECT setting_key, setting_value, description
        FROM GlobalLimits
        ORDER BY setting_key
    """)
    limits = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/settings.html', global_settings=global_settings, limits=limits)

@bp.route('/settings/update', methods=['POST'])
def update_setting():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    key = request.form['key']
    value = request.form['value']
    table = request.form['table']  # 'GlobalSettings' или 'GlobalLimits'
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    try:
        if table == 'GlobalSettings':
            cursor.execute("""
                UPDATE GlobalSettings
                SET setting_value = ?
                WHERE setting_key = ?
            """, value, key)
        elif table == 'GlobalLimits':
            cursor.execute("""
                UPDATE GlobalLimits
                SET setting_value = ?
                WHERE setting_key = ?
            """, value, key)
        conn.commit()
        flash(f"Настройка {key} обновлена", "success")
    except Exception as e:
        flash(f"Ошибка обновления настройки: {e}", "error")
    conn.close()
    
    return redirect(url_for('admin_ops.settings'))

@bp.route('/logs')
def logs():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    
    conn = get_svc_conn()
    cursor = conn.cursor()
    
    # Получаем логи RestoreJobs
    cursor.execute("""
        SELECT windows_user, target_db, status, started_at, finished_at, error_message
        FROM RestoreJobs
        ORDER BY started_at DESC
    """)
    logs = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/logs.html', logs=logs)

@bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    if request.method == 'POST':
        email = request.form['email']
        cursor.execute("UPDATE Users SET email = ? WHERE id = ?", email, user_id)
        conn.commit()
        flash("Почта обновлена", "success")
        return redirect(url_for('admin_ops.users'))

    cursor.execute("SELECT windows_login, full_name, email FROM Users WHERE id = ?", user_id)
    user = cursor.fetchone()
    conn.close()
    return render_template('admin/edit_user.html', user=user)

@bp.route('/databases/user/edit/<int:db_id>', methods=['GET', 'POST'])
def edit_user_db(db_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    if request.method == 'POST':
        notify_user = request.form.get('notify_user') == 'on'
        cursor.execute("UPDATE UserDatabases SET notify_user = ? WHERE id = ?", notify_user, db_id)
        conn.commit()
        flash("Настройки обновлены", "success")
        return redirect(url_for('admin_ops.databases'))

    cursor.execute("""
        SELECT ud.id, u.windows_login, ud.restore_target_db, ud.notify_user
        FROM UserDatabases ud
        JOIN Users u ON ud.user_id = u.id
        WHERE ud.id = ?
    """, db_id)
    db = cursor.fetchone()
    if not db:
        flash("База не найдена", "error")
        return redirect(url_for('admin_ops.databases'))

    conn.close()
    return render_template('admin/edit_user_db.html', db=db)

@bp.route('/databases/user/delete/<int:db_id>', methods=['POST'])
def delete_user_db(db_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM UserDatabases WHERE id = ?", db_id)
    conn.commit()
    conn.close()
    flash("Пользовательская БД удалена", "success")
    return redirect(url_for('admin_ops.databases'))

@bp.route('/logs/full')
def full_logs():
    if not is_user_admin_check():
        return "Доступ запрещён", 403

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    user_filter = request.args.get('user')
    log_type = request.args.get('log_type')  # 'auth', 'action', '1c'

    conn = get_svc_conn()
    cursor = conn.cursor()

    query_parts = []
    params = []

    base_query = """
        SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp
        FROM AuthLog
    """
    if start_date:
        base_query += " WHERE timestamp >= ?"
        params.append(start_date)
    if end_date:
        if params:
            base_query += " AND timestamp <= ?"
        else:
            base_query += " WHERE timestamp <= ?"
        params.append(end_date)
    if user_filter:
        if params:
            base_query += " AND windows_user = ?"
        else:
            base_query += " WHERE windows_user = ?"
        params.append(user_filter)
    if log_type == 'auth':
        query_parts.append(base_query)

    base_query_action = """
        SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp
        FROM UserActionsLog
    """
    if start_date or end_date or user_filter:
        where_clause = []
        if start_date:
            where_clause.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clause.append("timestamp <= ?")
            params.append(end_date)
        if user_filter:
            where_clause.append("windows_user = ?")
            params.append(user_filter)
        base_query_action += " WHERE " + " AND ".join(where_clause)
    if log_type == 'action' or not log_type:
        query_parts.append(base_query_action)

    base_query_1c = """
        SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, timestamp
        FROM OneCOperationLog ocl
    """
    if start_date or end_date or user_filter:
        where_clause = []
        base_query_1c += " JOIN RestoreQueue rq ON ocl.job_id = rq.id "
        if start_date:
            where_clause.append("ocl.timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clause.append("ocl.timestamp <= ?")
            params.append(end_date)
        if user_filter:
            where_clause.append("rq.windows_user = ?")
            params.append(user_filter)
        base_query_1c += " WHERE " + " AND ".join(where_clause)
    if log_type == '1c' or not log_type:
        query_parts.append(base_query_1c)

    if not query_parts:
        query_parts.append("""
            SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp FROM AuthLog
            UNION ALL
            SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp FROM UserActionsLog
            UNION ALL
            SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, ocl.timestamp FROM OneCOperationLog ocl JOIN RestoreQueue rq ON ocl.job_id = rq.id
        """)
        if start_date or end_date or user_filter:
            where_clauses = []
            if start_date:
                where_clauses.append("timestamp >= ?")
                params.append(start_date)
            if end_date:
                where_clauses.append("timestamp <= ?")
                params.append(end_date)
            if user_filter:
                where_clauses.append("windows_user = ?")
                params.append(user_filter)
            if where_clauses:
                query_parts = [f"({q}) WHERE {' AND '.join(where_clauses)}" for q in [
                    "SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp FROM AuthLog",
                    "SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp FROM UserActionsLog",
                    "SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, ocl.timestamp FROM OneCOperationLog ocl JOIN RestoreQueue rq ON ocl.job_id = rq.id"
                ]]

    full_query = " UNION ALL ".join(query_parts) + " ORDER BY timestamp DESC"

    cursor.execute(full_query, params)
    logs = cursor.fetchall()
    conn.close()

    return render_template('admin/full_logs.html', logs=logs, start_date=start_date, end_date=end_date, user_filter=user_filter, log_type=log_type)

@bp.route('/logs/full/export')
def export_full_logs():
    if not is_user_admin_check():
        return "Доступ запрещён", 403

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    user_filter = request.args.get('user')
    log_type = request.args.get('log_type')

    conn = get_svc_conn()
    cursor = conn.cursor()

    # Используем тот же запрос, что и выше
    query_parts = []
    params = []

    base_query = """
        SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp
        FROM AuthLog
    """
    if start_date:
        base_query += " WHERE timestamp >= ?"
        params.append(start_date)
    if end_date:
        if params:
            base_query += " AND timestamp <= ?"
        else:
            base_query += " WHERE timestamp <= ?"
        params.append(end_date)
    if user_filter:
        if params:
            base_query += " AND windows_user = ?"
        else:
            base_query += " WHERE windows_user = ?"
        params.append(user_filter)
    if log_type == 'auth':
        query_parts.append(base_query)

    base_query_action = """
        SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp
        FROM UserActionsLog
    """
    if start_date or end_date or user_filter:
        where_clause = []
        if start_date:
            where_clause.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clause.append("timestamp <= ?")
            params.append(end_date)
        if user_filter:
            where_clause.append("windows_user = ?")
            params.append(user_filter)
        base_query_action += " WHERE " + " AND ".join(where_clause)
    if log_type == 'action' or not log_type:
        query_parts.append(base_query_action)

    base_query_1c = """
        SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, timestamp
        FROM OneCOperationLog ocl
        JOIN RestoreQueue rq ON ocl.job_id = rq.id
    """
    if start_date or end_date or user_filter:
        where_clause = []
        if start_date:
            where_clause.append("ocl.timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clause.append("ocl.timestamp <= ?")
            params.append(end_date)
        if user_filter:
            where_clause.append("rq.windows_user = ?")
            params.append(user_filter)
        base_query_1c += " WHERE " + " AND ".join(where_clause)
    if log_type == '1c' or not log_type:
        query_parts.append(base_query_1c)

    if not query_parts:
        query_parts.append("""
            SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp
            FROM AuthLog
            UNION ALL
            SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp
            FROM UserActionsLog
            UNION ALL
            SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, ocl.timestamp
            FROM OneCOperationLog ocl
            JOIN RestoreQueue rq ON ocl.job_id = rq.id
        """)
        if start_date or end_date or user_filter:
            where_clauses = []
            if start_date:
                where_clauses.append("timestamp >= ?")
                params.append(start_date)
            if end_date:
                where_clauses.append("timestamp <= ?")
                params.append(end_date)
            if user_filter:
                where_clauses.append("windows_user = ?")
                params.append(user_filter)
            if where_clauses:
                query_parts = [f"({q}) WHERE {' AND '.join(where_clauses)}" for q in [
                    "SELECT 'Auth' as type, windows_user, ip_address, action as event, details, timestamp FROM AuthLog",
                    "SELECT 'Action' as type, windows_user, ip_address, action_type as event, details, timestamp FROM UserActionsLog",
                    "SELECT '1C' as type, (SELECT windows_user FROM RestoreQueue WHERE id = ocl.job_id) as windows_user, NULL as ip_address, operation as event, ISNULL(log_text, error_message) as details, ocl.timestamp FROM OneCOperationLog ocl JOIN RestoreQueue rq ON ocl.job_id = rq.id"
                ]]

    full_query = " UNION ALL ".join(query_parts) + " ORDER BY timestamp DESC"

    cursor.execute(full_query, params)
    logs = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Тип', 'Пользователь', 'IP', 'Событие', 'Детали', 'Время'])
    for log in logs:
        writer.writerow([log.type, log.windows_user, log.ip_address, log.event, log.details, log.timestamp])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=full_logs.csv"}
    )

@bp.route('/settings/global')
def global_settings():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, setting_key, setting_value, description FROM GlobalSettings ORDER BY setting_key")
    settings = cursor.fetchall()
    conn.close()
    return render_template('admin/global_settings.html', settings=settings)

@bp.route('/settings/global/edit/<int:setting_id>', methods=['GET', 'POST'])
def edit_global_setting(setting_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    if request.method == 'POST':
        new_value = request.form['value']
        cursor.execute("UPDATE GlobalSettings SET setting_value = ? WHERE id = ?", new_value, setting_id)
        conn.commit()
        flash("Настройка обновлена", "success")
        return redirect(url_for('admin_ops.global_settings'))

    cursor.execute("SELECT id, setting_key, setting_value, description FROM GlobalSettings WHERE id = ?", setting_id)
    setting = cursor.fetchone()
    if not setting:
        flash("Настройка не найдена", "error")
        return redirect(url_for('admin_ops.global_settings'))
    conn.close()
    return render_template('admin/edit_global_setting.html', setting=setting)

@bp.route('/settings/limits')
def global_limits():
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, setting_key, setting_value, description FROM GlobalLimits ORDER BY setting_key")
    limits = cursor.fetchall()
    conn.close()
    return render_template('admin/global_limits.html', limits=limits)

@bp.route('/settings/limits/edit/<int:limit_id>', methods=['GET', 'POST'])
def edit_global_limit(limit_id):
    if not is_user_admin_check():
        return "Доступ запрещён", 403
    conn = get_svc_conn()
    cursor = conn.cursor()
    if request.method == 'POST':
        new_value = request.form['value']
        cursor.execute("UPDATE GlobalLimits SET setting_value = ? WHERE id = ?", new_value, limit_id)
        conn.commit()
        flash("Ограничение обновлено", "success")
        return redirect(url_for('admin_ops.global_limits'))

    cursor.execute("SELECT id, setting_key, setting_value, description FROM GlobalLimits WHERE id = ?", limit_id)
    limit = cursor.fetchone()
    if not limit:
        flash("Ограничение не найдено", "error")
        return redirect(url_for('admin_ops.global_limits'))
    conn.close()
    return render_template('admin/edit_global_limit.html', limit=limit)