CREATE DATABASE svc_sqlrestore;
GO

USE svc_sqlrestore;
GO

-- Источники бэкапа
CREATE TABLE BackupSources (
    source_db_name NVARCHAR(128) PRIMARY KEY,
    source_server NVARCHAR(255) NOT NULL,
    sql_login NVARCHAR(128) NOT NULL,
    sql_password NVARCHAR(255) NOT NULL,
    datafile_name NVARCHAR(128) NOT NULL
);

-- Пользователи Windows
CREATE TABLE Users (
    id INT IDENTITY(1,1) PRIMARY KEY,
    windows_login NVARCHAR(100) NOT NULL UNIQUE,
    full_name NVARCHAR(255),
    is_admin BIT NOT NULL DEFAULT 0,
    email NVARCHAR(255) NULL
);

-- Пользовательские БД
CREATE TABLE UserDatabases (
    id INT IDENTITY(1,1) PRIMARY KEY,
    user_id INT NOT NULL FOREIGN KEY REFERENCES Users(id),
    source_db_name NVARCHAR(128) NOT NULL, -- из BackupSources
    restore_target_db NVARCHAR(128) NOT NULL UNIQUE,
    backup_path_template NVARCHAR(512) NOT NULL,
    user_app_login NVARCHAR(128) NULL;
    user_app_password NVARCHAR(255) NULL;
    user_storage_login NVARCHAR(128) NULL,
    user_storage_password NVARCHAR(255) NULL,
    header NVARCHAR(255) NULL,
    use_storage BIT NOT NULL DEFAULT 0,
    infobase_guid NVARCHAR(36) NULL, -- из ClusterInfo
    notify_user BIT NOT NULL DEFAULT 0
);

-- Общие БД
CREATE TABLE CommonDatabases (
    id INT IDENTITY(1,1) PRIMARY KEY,
    source_db_name NVARCHAR(128) NOT NULL, -- из BackupSources
    restore_target_db NVARCHAR(128) NOT NULL UNIQUE,
    backup_path_template NVARCHAR(512) NOT NULL,
    header NVARCHAR(255) NOT NULL,
    is_admin_only BIT NOT NULL DEFAULT 0
    infobase_guid NVARCHAR(36) NULL -- из ClusterInfo
);

-- Актуальные бэкапы
CREATE TABLE Backups (
    id INT IDENTITY(1,1) PRIMARY KEY,
    source_db_name NVARCHAR(128) NOT NULL,
    backup_file_path NVARCHAR(512) NOT NULL,
    backup_date DATE NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETDATE()
);
CREATE UNIQUE INDEX IX_Backups_db_date ON Backups(source_db_name, backup_date);

-- Логи восстановлений
CREATE TABLE RestoreJobs (
    id INT IDENTITY(1,1) PRIMARY KEY,
    windows_user NVARCHAR(100) NOT NULL,
    target_db NVARCHAR(128) NOT NULL,
    started_at DATETIME2 NOT NULL DEFAULT GETDATE(),
    finished_at DATETIME2 NULL,
    status NVARCHAR(20) NOT NULL,
    error_message NVARCHAR(MAX) NULL
);


-- Таблица для хранения общих параметров (включая пути к 1С)
CREATE TABLE GlobalSettings (
    id INT IDENTITY(1,1) PRIMARY KEY,
    setting_key NVARCHAR(100) NOT NULL UNIQUE,
    setting_value NVARCHAR(512) NOT NULL,
    description NVARCHAR(512) NULL
);

-- Добавим туда пути к 1С
INSERT INTO GlobalSettings (setting_key, setting_value, description) VALUES
('path_to_1cv8', 'C:\Program Files\1cv8\8.3.27.1688\bin\1cv8.exe', 'Путь к 1cv8.exe'),
('path_to_rac', 'C:\Program Files\1cv8\8.3.27.1688\bin\rac.exe', 'Путь к rac.exe'),
('app_server', 'server-dev1c', 'Сервер приложений 1С'),
('app_port', '1541', 'Порт сервера приложений 1С'),
('app_user', 'Робот', 'Пользователь 1С'),
('app_password', 'robotcon', 'Пароль 1С'),
('unlock_code', '67890', 'Код разблокировки'),
('smtp_server', 'smtp.company.local', 'SMTP-сервер'),
('smtp_port', '587', 'Порт SMTP'),
('smtp_login', 'sql-restore@company.local', 'Логин SMTP'),
('smtp_password', '...', 'Пароль SMTP'),
('smtp_from', 'sql-restore@company.local', 'От кого (адрес)'),
('smtp_tls', '1', 'Использовать TLS (1/0)');

-- Очередь задач восстановления
CREATE TABLE RestoreQueue (
    id INT IDENTITY(1,1) PRIMARY KEY,
    windows_user NVARCHAR(100) NOT NULL,
    target_db NVARCHAR(128) NOT NULL,
    status NVARCHAR(20) NOT NULL DEFAULT 'pending', -- 'pending', 'running', 'failed', 'completed'
    priority INT NOT NULL DEFAULT 10, -- чем меньше — тем выше приоритет
    created_at DATETIME2 NOT NULL DEFAULT GETDATE(),
    started_at DATETIME2 NULL,
    finished_at DATETIME2 NULL,
    error_message NVARCHAR(MAX) NULL
);

-- Новая таблица: Информация из 1CV8Clst.lst
CREATE TABLE ClusterInfo (
    id INT IDENTITY(1,1) PRIMARY KEY,
    infobase_guid NVARCHAR(36) NOT NULL UNIQUE, -- UUID базы данных
    cluster_guid NVARCHAR(36) NOT NULL,         -- UUID кластера
    name NVARCHAR(255) NOT NULL,                -- имя базы (например, TST_ERP_YD)
    description NVARCHAR(512) NULL,             -- описание
    db_server NVARCHAR(255) NULL,               -- сервер БД
    db_name NVARCHAR(255) NULL,                 -- имя БД на SQL Server
    updated_at DATETIME2 NOT NULL DEFAULT GETDATE()
);

CREATE TABLE AuthLog (
    id INT IDENTITY(1,1) PRIMARY KEY,
    windows_user NVARCHAR(100) NOT NULL,
    ip_address NVARCHAR(45) NULL, -- IPv4/v6
    action NVARCHAR(50) NOT NULL, -- 'login', 'logout', 'access_denied'
    timestamp DATETIME2 NOT NULL DEFAULT GETDATE(),
    details NVARCHAR(512) NULL
);

CREATE TABLE UserActionsLog (
    id INT IDENTITY(1,1) PRIMARY KEY,
    windows_user NVARCHAR(100) NOT NULL,
    ip_address NVARCHAR(45) NULL,
    action_type NVARCHAR(50) NOT NULL, -- 'restore_requested', 'settings_updated', 'profile_updated'
    target_db NVARCHAR(128) NULL,
    details NVARCHAR(1024) NULL,
    timestamp DATETIME2 NOT NULL DEFAULT GETDATE()
);

CREATE TABLE OneCOperationLog (
    id INT IDENTITY(1,1) PRIMARY KEY,
    job_id INT NULL, -- из RestoreQueue
    target_db NVARCHAR(128) NOT NULL,
    operation NVARCHAR(100) NOT NULL, -- 'SetTitle', 'DisconnectFromStorage', 'ConnectToStorage', 'RestoreDB'
    status NVARCHAR(20) NOT NULL, -- 'started', 'success', 'error'
    log_text NVARCHAR(MAX) NULL, -- полный лог, как в OScript
    timestamp DATETIME2 NOT NULL DEFAULT GETDATE(),
    error_message NVARCHAR(MAX) NULL
);

-- Таблица для хранения личных бэкапов пользователей
CREATE TABLE UserBackups (
    id INT IDENTITY(1,1) PRIMARY KEY,
    user_id INT NOT NULL FOREIGN KEY REFERENCES Users(id), -- Кто создал
    target_db_name NVARCHAR(128) NOT NULL, -- Имя БД, из которой сделан бэкап
    backup_file_path NVARCHAR(512) NOT NULL, -- Полный путь к .bak-файлу
    created_at DATETIME2 NOT NULL DEFAULT GETDATE(), -- Когда создан
    description NVARCHAR(512) NULL -- Описание (по желанию пользователя)
);
CREATE INDEX IX_UserBackups_user_db_date ON UserBackups(user_id, target_db_name, created_at DESC);

-- Новая таблица: Ограничения
CREATE TABLE GlobalLimits (
    id INT IDENTITY(1,1) PRIMARY KEY,
    setting_key NVARCHAR(100) NOT NULL UNIQUE,
    setting_value NVARCHAR(512) NOT NULL,
    description NVARCHAR(512) NULL
);

INSERT INTO GlobalLimits (setting_key, setting_value, description) VALUES
('max_concurrent_restores', '2', 'Максимальное количество одновременных восстановлений'),
('allow_dynamic_backup_creation', '1', 'Разрешить создание бэкапа при отсутствии (0/1)');