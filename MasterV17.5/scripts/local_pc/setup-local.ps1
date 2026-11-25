$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backendDependencies = Join-Path $projectRoot 'dependencies\backend'
$backendSource = Join-Path $projectRoot 'main_source_code\backend'
$frontendSource = Join-Path $projectRoot 'main_source_code\frontend'
$frontendDependencies = Join-Path $projectRoot 'dependencies\frontend'
$venvPython = Join-Path $backendDependencies '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv (Join-Path $backendDependencies '.venv')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv (Join-Path $backendDependencies '.venv')
    } else {
        throw 'Install Python 3.12 and Node.js 20 or later, then rerun setup.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual-environment creation failed.' }
}

& $venvPython -m pip install -r (Join-Path $backendDependencies 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }

if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Install Node.js 20 or later (including npm), then rerun setup.'
}
$nodeMajor = [int]((& node --version).Trim().TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) { throw 'Node.js 20 or later is required.' }

& npm --prefix $frontendDependencies install
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }

$backendEnv = Join-Path $backendSource '.env'
$frontendEnv = Join-Path $frontendSource '.env'
if (-not (Test-Path $backendEnv)) {
    Copy-Item (Join-Path $backendSource '.env.example') $backendEnv
}
if (-not (Test-Path $frontendEnv)) {
    Copy-Item (Join-Path $frontendSource '.env.example') $frontendEnv
}
if (-not (Test-Path (Join-Path $frontendSource 'node_modules'))) {
    New-Item -ItemType Junction -Path (Join-Path $frontendSource 'node_modules') -Target (Join-Path $frontendDependencies 'node_modules') | Out-Null
}

Write-Host 'Local dependencies are ready. Start MongoDB, then run scripts\local_pc\start-all.bat.'
if (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port 27017 -InformationLevel Quiet)) {
    Write-Warning 'MongoDB is not listening on 127.0.0.1:27017; start/install MongoDB before launching the backend.'
}
