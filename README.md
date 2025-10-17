# SQL Restore Web

Веб-интерфейс для самостоятельного восстановления пользовательских баз данных MS SQL с доменной авторизацией.

## Требования

- Windows Server
- Python 3.9+
- MS SQL Server
- IIS

## Установка

1. Скопируйте проект в `C:\inetpub\wwwroot\sql-restore-web`
2. Убедитесь, что `configs/*.json` находятся в папке `configs/`
3. Запустите PowerShell от имени администратора:
   ```powershell
   .\deploy.ps1