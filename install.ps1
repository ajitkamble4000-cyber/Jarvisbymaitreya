# JARVIS AI installer for Windows.
#
# Paste this one line into PowerShell and press Enter:
#
#   irm https://raw.githubusercontent.com/ajitkamble4000-cyber/jarvisbymaitreya/main/install.ps1 | iex
#
# What it does, in order. It is safe to run again: it updates what is already there and keeps your settings.
#   1. Installs what is missing: Git, Node.js 22 or newer, and Docker Desktop (with winget).
#   2. Downloads JARVIS into %USERPROFILE%\JARVIS, or updates that copy.
#   3. Starts the backend and the web app with Docker Compose.
#   4. Builds the Windows desktop agent.
#   5. Creates shortcuts: JARVIS on the desktop and in the Start menu, JARVIS Web on the desktop.
#   6. Makes the agent start when you sign in, and opens the web app.
#
# Optional settings, set before the command:
#   $env:JARVIS_INSTALL_DIR = 'D:\JARVIS'   install somewhere else
#   $env:JARVIS_NO_LAUNCH = '1'             install and shortcuts, but do not start anything

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoUrl = 'https://github.com/ajitkamble4000-cyber/jarvisbymaitreya.git'
$WebUrl = 'http://127.0.0.1:5173'
$ApiHealthUrl = 'http://127.0.0.1:8000/api/v1/health/live'
$MinNodeMajor = 22

function Get-InstallDir {
    if ($env:JARVIS_INSTALL_DIR) { return $env:JARVIS_INSTALL_DIR }
    return (Join-Path $env:USERPROFILE 'JARVIS')
}

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('==> ' + $Text) -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host ('    OK  ' + $Text) -ForegroundColor Green
}

function Write-Note([string]$Text) {
    Write-Host ('    ' + $Text) -ForegroundColor Gray
}

function Update-SessionPath {
    # Programs installed a moment ago are not on this window's PATH yet. Reading the saved PATH fixes that.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = $machine + ';' + $user
}

function Test-CommandExists([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Native {
    # Runs a program and stops with a clear message when it fails. Its output stays visible.
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('"' + $File + ' ' + ($Arguments -join ' ') + '" failed with exit code ' + $LASTEXITCODE + '.')
    }
}

function Install-WithWinget([string]$Id, [string]$Label) {
    if (-not (Test-CommandExists 'winget')) {
        throw "winget is not available on this PC. Install 'App Installer' from the Microsoft Store, then run this command again."
    }
    Write-Note ('Installing ' + $Label + ' with winget. This can take a few minutes.')
    & winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
    # 0 = installed. 3010 = installed, restart needed. -1978335189 = already installed and up to date.
    $ok = @(0, 3010, -1978335189)
    if ($ok -notcontains $LASTEXITCODE) {
        throw ('winget could not install ' + $Label + ' (exit code ' + $LASTEXITCODE + ').')
    }
    Update-SessionPath
}

function Get-NodeMajor {
    if (-not (Test-CommandExists 'node')) { return 0 }
    $version = (& node --version) -replace '^v', ''
    return [int]($version.Split('.')[0])
}

function Test-DockerRunning {
    if (-not (Test-CommandExists 'docker')) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker info > $null 2>&1
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Until([string]$What, [scriptblock]$Test, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Test) { return }
        Start-Sleep -Seconds 3
    }
    throw ($What + ' did not start within ' + $TimeoutSeconds + ' seconds. Check Docker Desktop, then run this command again.')
}

function Add-EnvLineIfMissing([string]$Path, [string]$Key, [string]$Value) {
    $existing = @(Get-Content -Path $Path)
    foreach ($line in $existing) {
        if ($line -match ('^' + $Key + '=')) { return $false }
    }
    [System.IO.File]::AppendAllText($Path, "`r`n" + $Key + '=' + $Value + "`r`n")
    return $true
}

function New-ShortcutFile([string]$Path, [string]$Target, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    $link.WorkingDirectory = Split-Path -Parent $Target
    $link.IconLocation = $Target + ',0'
    $link.Description = $Description
    $link.Save()
}

function New-WebShortcutFile([string]$Path, [string]$Url) {
    Set-Content -Path $Path -Value ('[InternetShortcut]' + "`r`n" + 'URL=' + $Url + "`r`n") -Encoding ASCII
}

function Test-DockerAutostart {
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $entries = Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue
    if ($entries -and ($entries.PSObject.Properties.Name -contains 'Docker Desktop')) { return $true }
    return (Test-Path (Join-Path ([Environment]::GetFolderPath('Startup')) 'Docker Desktop.lnk'))
}

function Install-Jarvis {
    # Set inside the function, so the PowerShell window that ran "iex" keeps its own settings afterwards.
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $installDir = Get-InstallDir

    Write-Step 'Checking the tools JARVIS needs'
    if (Test-CommandExists 'git') {
        Write-Ok 'Git is installed.'
    } else {
        Install-WithWinget 'Git.Git' 'Git'
        if (-not (Test-CommandExists 'git')) { throw 'Git was installed, but this PowerShell window cannot see it yet. Open a new PowerShell window and run this command again.' }
        Write-Ok 'Git installed.'
    }

    $nodeMajor = Get-NodeMajor
    if ($nodeMajor -ge $MinNodeMajor) {
        Write-Ok ('Node.js ' + $nodeMajor + ' is installed.')
    } else {
        Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS'
        $nodeMajor = Get-NodeMajor
        if ($nodeMajor -lt $MinNodeMajor) { throw ('Node.js ' + $MinNodeMajor + ' or newer is needed. Open a new PowerShell window and run this command again.') }
        Write-Ok ('Node.js ' + $nodeMajor + ' installed.')
    }

    $dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-CommandExists 'docker')) {
        Install-WithWinget 'Docker.DockerDesktop' 'Docker Desktop'
        Write-Note 'Docker Desktop was just installed. If Windows asks you to restart or to turn on WSL 2, do that first, then run this command again.'
    }
    $dockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if ((Test-Path $dockerBin) -and -not (Test-CommandExists 'docker')) {
        $env:Path = $env:Path + ';' + $dockerBin
    }
    if (Test-DockerRunning) {
        Write-Ok 'Docker is running.'
    } else {
        if (-not (Test-Path $dockerExe)) { throw 'Docker Desktop is installed but cannot be found. Start it from the Start menu, then run this command again.' }
        Write-Note 'Starting Docker Desktop. The first start can take a minute or two.'
        Start-Process -FilePath $dockerExe
        Wait-Until 'Docker' { Test-DockerRunning } 300
        Write-Ok 'Docker is running.'
    }

    Write-Step ('Downloading JARVIS to ' + $installDir)
    if (Test-Path (Join-Path $installDir '.git')) {
        Invoke-Native 'git' @('-C', $installDir, 'pull', '--ff-only')
        Write-Ok 'JARVIS is up to date.'
    } else {
        if ((Test-Path $installDir) -and @(Get-ChildItem -Path $installDir -Force).Count -gt 0) {
            throw ($installDir + ' already exists and is not a JARVIS download. Move it away, or set JARVIS_INSTALL_DIR to another folder.')
        }
        Invoke-Native 'git' @('clone', $RepoUrl, $installDir)
        Write-Ok 'JARVIS downloaded.'
    }

    Write-Step 'Preparing the settings file'
    $envFile = Join-Path $installDir '.env'
    if (-not (Test-Path $envFile)) {
        Copy-Item -Path (Join-Path $installDir '.env.example') -Destination $envFile
        Write-Ok 'Created .env from .env.example.'
    }
    if ((Test-PortOpen 5432) -and (Add-EnvLineIfMissing $envFile 'POSTGRES_HOST_PORT' '5433')) {
        Write-Note 'Port 5432 is used by another PostgreSQL on this PC, so JARVIS uses port 5433 for its database.'
    }

    Write-Step 'Starting the backend and the web app with Docker (the first time takes several minutes)'
    Push-Location $installDir
    try {
        Invoke-Native 'docker' @('compose', 'up', '-d', '--build')
    } finally {
        Pop-Location
    }
    Wait-Until 'The backend' { Test-Url $ApiHealthUrl } 600
    Wait-Until 'The web app' { Test-Url $WebUrl } 120
    Write-Ok ('The web app is running at ' + $WebUrl)

    Write-Step 'Building the Windows desktop agent'
    $desktopDir = Join-Path $installDir 'apps\desktop'
    Push-Location $desktopDir
    try {
        if (-not (Test-Path 'node_modules')) { Invoke-Native 'npm' @('ci') }
        Invoke-Native 'npm' @('run', 'build')
        # The agent cannot be replaced while it runs, so stop it first.
        Get-Process -Name 'JARVIS Agent' -ErrorAction SilentlyContinue | Stop-Process -Force
        Invoke-Native 'npx' @('electron-builder', '--config', 'electron-builder.yml', '--win', 'dir', '--x64', '--publish', 'never')
    } finally {
        Pop-Location
    }
    $agentExe = Join-Path $desktopDir 'release\win-unpacked\JARVIS Agent.exe'
    if (-not (Test-Path $agentExe)) { throw ('The desktop agent was not built: ' + $agentExe + ' is missing.') }
    Write-Ok 'The desktop agent is built.'

    Write-Step 'Creating shortcuts and the sign-in start'
    $desktopFolder = [Environment]::GetFolderPath('Desktop')
    $startMenuFolder = Join-Path ([Environment]::GetFolderPath('Programs')) 'JARVIS'
    $startupFolder = [Environment]::GetFolderPath('Startup')
    New-Item -ItemType Directory -Force -Path $startMenuFolder | Out-Null
    New-ShortcutFile (Join-Path $desktopFolder 'JARVIS.lnk') $agentExe 'JARVIS AI desktop agent'
    New-ShortcutFile (Join-Path $startMenuFolder 'JARVIS.lnk') $agentExe 'JARVIS AI desktop agent'
    New-WebShortcutFile (Join-Path $desktopFolder 'JARVIS Web.url') $WebUrl
    New-ShortcutFile (Join-Path $startupFolder 'JARVIS Agent.lnk') $agentExe 'Starts the JARVIS agent when you sign in'
    Write-Ok 'Desktop shortcuts "JARVIS" and "JARVIS Web" are created.'
    Write-Ok 'The JARVIS agent starts when you sign in.'
    if (Test-DockerAutostart) {
        Write-Ok 'Docker Desktop starts when you sign in, so JARVIS comes back after a restart.'
    } elseif (Test-Path $dockerExe) {
        New-ShortcutFile (Join-Path $startupFolder 'Docker Desktop.lnk') $dockerExe 'Starts Docker Desktop when you sign in'
        Write-Ok 'Docker Desktop is set to start when you sign in.'
    }

    if ($env:JARVIS_NO_LAUNCH -eq '1') {
        Write-Note 'Not starting anything now, because JARVIS_NO_LAUNCH is set.'
    } else {
        Write-Step 'Starting JARVIS'
        Start-Process -FilePath $agentExe
        Start-Process -FilePath $WebUrl
    }

    Write-Host ''
    Write-Host 'JARVIS is installed.' -ForegroundColor Green
    Write-Host ''
    Write-Host ('  Web app:        ' + $WebUrl)
    Write-Host '  Desktop agent:  the JARVIS icon on your desktop. It starts when you sign in.'
    Write-Host '  Quick window:   press Ctrl+Space anywhere.'
    Write-Host ''
    Write-Host 'Next steps, once, in the web app:'
    Write-Host '  1. Create the owner account (a password of at least 12 characters).'
    Write-Host '  2. Open AI Models, paste your Google Gemini API key, press Save key, then Test connection.'
    Write-Host '  3. Open Devices, press Create pairing code, then in the JARVIS agent window enter'
    Write-Host '     http://127.0.0.1:8000 and that code.'
    Write-Host ''
    Write-Host 'To update JARVIS later, run the same command again. Your settings and data are kept.'
    Write-Host ('Guide: ' + (Join-Path $installDir 'docs\TUTORIAL.md'))
}

# Runs only on Windows. Loading this file on another system defines the functions and does nothing else.
if ($env:OS -eq 'Windows_NT') {
    try {
        Install-Jarvis
    } catch {
        Write-Host ''
        Write-Host ('JARVIS could not finish: ' + $_.Exception.Message) -ForegroundColor Red
        Write-Host 'Fix the problem above, then run the same command again. Nothing is lost.' -ForegroundColor Yellow
    }
} else {
    Write-Host 'This installer is for Windows. On macOS or Linux, follow docs/TUTORIAL.md (Appendix A).'
}
