import re
import pyodbc
from pathlib import Path
from app.config_loader import get_global_setting
def parse_1cv8clst_lst(file_path):
    """
    Парсит файл 1CV8Clst.lst и извлекает информацию о базах.
    Возвращает: (cluster_guid, list of infobases)
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Извлечение cluster_guid из начала файла (обычно первый GUID в списке кластеров)
    cluster_guid_match = re.search(r'\{([a-fA-F0-9-]{36}),"Локальный кластер"', content)
    cluster_guid = cluster_guid_match.group(1) if cluster_guid_match else None

    if not cluster_guid:
        print("⚠️ Не удалось извлечь идентификатор кластера")
        return None, []

    # Извлечение информации о базах данных
    # Пример строки: {GUID,"name","descr",... DB=имя_бд; ...
    pattern = r'\{([a-fA-F0-9-]{36}),"([^"]+)","([^"]*?)"[^}]*?DB=([^;]+);'
    matches = re.findall(pattern, content)

    infobases = []
    for guid, name, description, db_name in matches:
        # Извлечение DBSrvr
        start_pos = content.find(guid)
        end_pos = content.find('}', start_pos)
        snippet = content[start_pos:end_pos]
        db_server_match = re.search(r'DBSrvr=([^;]+);', snippet)
        db_server = db_server_match.group(1) if db_server_match else None

        infobases.append({
            'infobase_guid': guid,
            'name': name,
            'description': description,
            'db_name': db_name,
            'db_server': db_server
        })

    return cluster_guid, infobases

def sync_cluster_info_to_db(cluster_guid, infobases):
    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=svc_sqlrestore;"
        "Trusted_Connection=yes;"
    )
    cursor = conn.cursor()

    # Обновляем/вставляем данные
    for ib in infobases:
        cursor.execute("""
            MERGE ClusterInfo AS target
            USING (SELECT ? AS infobase_guid, ? AS cluster_guid, ? AS name, ? AS description, ? AS db_name, ? AS db_server) AS source
            ON target.infobase_guid = source.infobase_guid
            WHEN MATCHED THEN
                UPDATE SET cluster_guid = source.cluster_guid, name = source.name, description = source.description,
                           db_name = source.db_name, db_server = source.db_server, updated_at = GETDATE()
            WHEN NOT MATCHED THEN
                INSERT (infobase_guid, cluster_guid, name, description, db_name, db_server)
                VALUES (source.infobase_guid, source.cluster_guid, source.name, source.description, source.db_name, source.db_server);
        """, ib['infobase_guid'], cluster_guid, ib['name'], ib['description'], ib['db_name'], ib['db_server'])

    conn.commit()
    conn.close()

def link_user_databases_to_cluster():
    """
    Сопоставляет существующие UserDatabases с ClusterInfo по имени (restore_target_db = name).
    Обновляет UserDatabases, добавляя поле infobase_guid.
    """
    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=svc_sqlrestore;"
        "Trusted_Connection=yes;"
    )
    cursor = conn.cursor()

    # Добавим поле в UserDatabases, если его нет
    try:
        cursor.execute("ALTER TABLE UserDatabases ADD infobase_guid NVARCHAR(36) NULL;")
    except:
        pass  # уже есть

    try:
        cursor.execute("ALTER TABLE CommonDatabases ADD infobase_guid NVARCHAR(36) NULL;")
    except:
        pass  # уже есть

    # Обновляем UserDatabases
    cursor.execute("""
        UPDATE ud
        SET infobase_guid = ci.infobase_guid
        FROM UserDatabases ud
        JOIN ClusterInfo ci ON ud.restore_target_db = ci.name;
    """)

    # Обновляем CommonDatabases
    cursor.execute("""
        UPDATE cd
        SET infobase_guid = ci.infobase_guid
        FROM CommonDatabases cd
        JOIN ClusterInfo ci ON cd.restore_target_db = ci.name;
    """)

    conn.commit()
    conn.close()

def main():
    file_path = get_global_setting('cluster_info_file_path') or r"C:\Program Files\1cv8\srvinfo\reg_1541\1CV8Clst.lst" # <-- Из БД или значение по умолчанию
#    file_path = r"C:\Program Files\1cv8\srvinfo\reg_1541\1CV8Clst.lst"
    if not Path(file_path).exists():
        print(f"❌ Файл не найден: {file_path}")
        return

    cluster_guid, infobases = parse_1cv8clst_lst(file_path)
    if not cluster_guid:
        print("⚠️ Не удалось извлечь идентификатор кластера")
    else:
        print(f"✅ Кластер: {cluster_guid}")
    print(f"📦 Найдено баз: {len(infobases)}")

    if infobases:
        sync_cluster_info_to_db(cluster_guid, infobases)
        print("✅ Информация о кластере синхронизирована с БД")
        link_user_databases_to_cluster()
        print("✅ Существующие БД сопоставлены с кластером")

if __name__ == '__main__':
    main()