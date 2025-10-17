# deploy.ps1

param(
    [string]$DeployPath = "C:\inetpub\wwwroot\sql-restore-web",
    [string]$PythonPath = "C:\Python311",
    [string]$IISAppName = "SQLRestore",
    [string]$BackupPath = "D:\SQLBackups"
)

Write-Host "🚀 Начинаю развёртывание..." -ForegroundColor Green

# 1. Проверка Python
if (-not (Test-Path "$PythonPath\python.exe")) {
    Write-Host "❌ Python не найден по пути: $PythonPath" -ForegroundColor Red
    exit 1
}

# 2. Установка зависимостей
Write-Host "📦 Устанавливаю зависимости Python..." -ForegroundColor Yellow
& "$PythonPath\python.exe" -m pip install -r "$DeployPath\requirements.txt"

# 3. Установка wfastcgi
Write-Host "📦 Устанавливаю wfastcgi..." -ForegroundColor Yellow
& "$PythonPath\python.exe" -m pip install wfastcgi
& "$PythonPath\python.exe" -m wfastcgi

# 4. Создание служебной БД
Write-Host "🗄️ Создаю служебную БД svc_sqlrestore..." -ForegroundColor Yellow
$SqlCmd = "sqlcmd -S localhost -i `"$DeployPath\scripts\create_svc_db.sql`""
Invoke-Expression $SqlCmd

# 5. Миграция конфигов
Write-Host "🔄 Мигрирую конфиги в БД..." -ForegroundColor Yellow
& "$PythonPath\python.exe" "$DeployPath\scripts\migrate_configs.py"

# 6. Создание папки бэкапов
Write-Host "📁 Создаю папку бэкапов: $BackupPath" -ForegroundColor Yellow
New-Item -ItemType Directory -Path $BackupPath -Force

# 7. Установка IIS (если нужно)
Write-Host "🌐 Настройка IIS..." -ForegroundColor Yellow
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole, IIS-WebServer, IIS-CommonHttpFeatures, IIS-HttpErrors, IIS-HttpRedirect, IIS-ApplicationDevelopment, IIS-NetFxExtensibility45, IIS-HealthAndDiagnostics, IIS-HttpLogging, IIS-Security, IIS-RequestFiltering, IIS-Performance, IIS-WebServerManagementTools, IIS-ManagementConsole, IIS-IIS6ManagementCompatibility, IIS-Metabase, IIS-ASPNET45

# 8. Создание сайта в IIS
Import-Module WebAdministration
if (Test-Path "IIS:\Sites\$IISAppName") {
    Remove-WebSite -Name $IISAppName
}
New-WebSite -Name $IISAppName -PhysicalPath $DeployPath -ApplicationPool ".NET v4.5"

# 9. Настройка web.config
$WebConfigPath = "$DeployPath\web.config"
$WebConfigContent = @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="PythonHandler" path="*" verb="*" 
           modules="FastCgiModule" 
           scriptProcessor="$PythonPath\python.exe|`$PythonPath\Lib\site-packages\wfastcgi.py"
           resourceType="Unspecified" requireAccess="Script" />
    </handlers>
    <security>
      <authentication>
        <windowsAuthentication enabled="true" />
        <anonymousAuthentication enabled="false" />
      </authentication>
    </security>
  </system.webServer>
  <appSettings>
    <add key="WSGI_HANDLER" value="run.app" />
    <add key="PYTHONPATH" value="$DeployPath" />
  </appSettings>
</configuration>
"@
Set-Content -Path $WebConfigPath -Value $WebConfigContent

Write-Host "✅ Развёртывание завершено!" -ForegroundColor Green
Write-Host "🌐 Веб-приложение доступно по: http://localhost/$IISAppName" -ForegroundColor Cyan
Write-Host "📅 Для ежедневного бэкапа добавьте задачу в планировщик: `n   & `"$PythonPath\python.exe`" `"$DeployPath\scripts\backup_task.py`"" -ForegroundColor Cyan