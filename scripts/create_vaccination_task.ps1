# Run this script as Administrator to register a scheduled task that runs the
# Django management command `create_due_vaccination_alerts` every 15 minutes.

# Determine repository root (parent of this scripts folder)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$managePy = Join-Path $repoRoot 'manage.py'

# Locate python executable
$pythonCmd = $null
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if ($py) { $pythonCmd = $py.Source }

if (-not $pythonCmd -and $env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    if (Test-Path $candidate) { $pythonCmd = $candidate }
}

if (-not $pythonCmd) {
    Write-Host "Python not found automatically. Please enter full path to python.exe:"
    $pythonCmd = Read-Host "python.exe path"
}

if (-not (Test-Path $pythonCmd)) {
    Write-Error "python executable not found at: $pythonCmd"
    exit 1
}
if (-not (Test-Path $managePy)) {
    Write-Error "manage.py not found at: $managePy"
    exit 1
}

$taskName = 'POULTRYIQ_CreateDueVaccinationAlerts'
$action = "`"$pythonCmd`" `"$managePy`" create_due_vaccination_alerts"

Write-Host "Registering scheduled task '$taskName' to run every 15 minutes..."
Write-Host "Command: $action"

# Create or update the scheduled task. This will require Administrator privileges.
$arguments = @('/Create', '/SC', 'MINUTE', '/MO', '15', '/TN', $taskName, '/TR', $action, '/F')

try {
    Start-Process -FilePath schtasks -ArgumentList $arguments -Wait -NoNewWindow -Verb RunAs
    Write-Host "Scheduled task created or updated successfully."
} catch {
    Write-Error "Failed to create scheduled task. Run this script as Administrator. Error: $_"
}
