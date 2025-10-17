import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .config_loader import get_global_setting

def send_email(to_email, subject, body, html_body=None):
    smtp_server = get_global_setting('smtp_server')
    smtp_port = int(get_global_setting('smtp_port') or 587)
    smtp_login = get_global_setting('smtp_login')
    smtp_password = get_global_setting('smtp_password')
    smtp_from = get_global_setting('smtp_from')
    use_tls = get_global_setting('smtp_tls') == '1'

    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = to_email
    msg['Subject'] = subject

    if html_body:
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    else:
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        if use_tls:
            server.starttls()
        server.login(smtp_login, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"✅ Письмо отправлено на {to_email}")
    except Exception as e:
        print(f"❌ Ошибка отправки письма на {to_email}: {e}")

def notify_user_on_restore_complete(user_email, target_db, status, error_msg=None):
    if not user_email:
        return
    subject = f"Восстановление базы {target_db} завершено"
    body = f"""
    Здравствуйте,

    Восстановление базы данных '{target_db}' завершено.

    Статус: {status}
    """
    if error_msg:
        body += f"Ошибка: {error_msg}\n"
    body += "\nС уважением,\nСистема восстановления БД"
    send_email(user_email, subject, body)

def notify_admin_on_backup_failure(error_msg):
    admin_email = get_global_setting('admin_email')  # можно добавить в GlobalSettings
    if not admin_email:
        print("⚠️ Email администратора не задан")
        return
    subject = "Ошибка ежедневного бэкапа"
    body = f"""
    Здравствуйте,

    При выполнении ежедневного бэкапа произошла ошибка:

    {error_msg}

    С уважением,\nСистема восстановления БД
    """
    send_email(admin_email, subject, body)