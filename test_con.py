# test_trusted_conn.py

import pyodbc

# Строка подключения, как в app_config.json при trusted_connection = true
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=server-dev1c;"  # Замените на ваш сервер
    "DATABASE=svc_sqlrestore;"  # Замените на вашу базу
    "Trusted_Connection=yes;"
)

try:
    print("Пытаюсь подключиться к SQL Server через Trusted Connection...")
    conn = pyodbc.connect(conn_str)
    print("✅ Подключение успешно установлено!")
    cursor = conn.cursor()
    # Выполним простой запрос, чтобы проверить живое ли соединение
    cursor.execute("SELECT @@VERSION AS SQLServerVersion;")
    row = cursor.fetchone()
    print(f"Информация о сервере: {row[0]}")
    conn.close()
    print("✅ Соединение закрыто.")

except pyodbc.OperationalError as e:
    print("❌ Ошибка подключения:")
    print(str(e))
except Exception as e:
    print("❌ Непредвиденная ошибка:")
    print(str(e))
