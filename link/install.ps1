<#
.SYNOPSIS
    NEO//LINK — instalador para Windows 10/11, em PowerShell nativo.

.DESCRIPTION
    Instala o agente NEO//LINK em %USERPROFILE%\.neo-x1\link e cria o atalho
    `neo-link`. Não precisa de Bash, WSL, Git, Chocolatey, Docker ou
    permissões de administrador.

    A instalação vive só na pasta do utilizador. O instalador não abre
    portas, não toca na firewall, não altera o router, não instala serviços
    e não activa o controlo remoto (allow_control fica a false).

.EXAMPLE
    Forma recomendada — descarregar e depois executar, para poder rever:

        irm https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link/install.ps1 -OutFile install.ps1
        .\install.ps1

.EXAMPLE
    Com um nome de node:

        .\install.ps1 -Name MEU-NODE

.EXAMPLE
    Diagnóstico:

        .\install.ps1 -Check

.NOTES
    A lógica de instalação é partilhada com os outros sistemas através de
    link/bootstrap.py. Este ficheiro limita-se a preparar o Python e a
    encaminhar para essa lógica.
#>
[CmdletBinding()]
param(
    # Nome lógico do node. Sem este valor usa-se o hostname da máquina.
    [string]$Name,

    # Endereço onde o LINK escuta. Por omissão só localhost.
    [string]$HostAddress,

    [int]$Port = 8765,

    # Directorio de instalação. Por omissão %USERPROFILE%\.neo-x1\link
    [string]$InstallDir,

    [switch]$Check,
    [switch]$Update,
    [switch]$Uninstall,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

$RepoRaw = 'https://raw.githubusercontent.com/DarkHareVideoGames/NEO-SENTINEL/main/link'
$BootstrapUrl = "$RepoRaw/bootstrap.py"

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "    OK   $text" -ForegroundColor Green }
function Write-Info($text) { Write-Host "    $text" }
function Write-Warn($text) { Write-Host "    aviso: $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host "`nerro: $text" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

function Get-CommandPath {
    param([string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

function Resolve-SystemPython {
    <#
        Procura um Python 3.9+ sem depender do alias da Microsoft Store,
        que pode abrir a Store em vez de devolver a versão.
    #>
    $candidates = @()
    foreach ($launcher in @('py', 'python', 'python3')) {
        $path = Get-CommandPath $launcher
        if ($path) { $candidates += $path }
    }
    foreach ($candidate in $candidates) {
        try {
            $out = & $candidate -c "import sys; print(1 if sys.version_info >= (3, 9) else 0)" 2>$null
            if ($out -eq '1') { return $candidate }
        } catch { continue }
    }
    return $null
}

Write-Step 'A verificar pré-requisitos'

$python = Resolve-SystemPython
if (-not $python) {
    Fail @"
nenhum Python 3.9+ encontrado.

Instale o Python para Windows de https://www.python.org/downloads/
(assinale "Add python.exe to PATH" durante a instalação) e volte a correr.
"@
}
Write-Ok "Python: $python"

# ---------------------------------------------------------------------------
# Directorio de instalação
# ---------------------------------------------------------------------------

if ($InstallDir) {
    $linkDir = [System.IO.Path]::GetFullPath($InstallDir)
} else {
    $linkDir = Join-Path $HOME '.neo-x1\link'
}

New-Item -ItemType Directory -Force -Path $linkDir | Out-Null

# ---------------------------------------------------------------------------
# Bootstrap partilhado
# ---------------------------------------------------------------------------

$bootstrap = Join-Path $linkDir 'bootstrap.py'
Write-Step 'A obter a lógica de instalação'

try {
    Invoke-WebRequest -Uri $BootstrapUrl -OutFile $bootstrap -UseBasicParsing
} catch {
    Fail "não foi possível descarregar o bootstrap: $($_.Exception.Message)"
}
Write-Ok 'bootstrap.py obtido'

# ---------------------------------------------------------------------------
# Atalhos
# ---------------------------------------------------------------------------

function New-NeoLinkCommand {
    param([string]$LinkDir)

    $binDir = Join-Path $HOME '.neo-x1\bin'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $cmdPath = Join-Path $binDir 'neo-link.cmd'
    $pythonExe = Join-Path $LinkDir '.venv\Scripts\python.exe'
    $entry = Join-Path $LinkDir 'neo_link.py'

    @"
@echo off
REM NEO//LINK - gerado pelo instalador
"$pythonExe" "$entry" %*
"@ | Set-Content -Path $cmdPath -Encoding ASCII

    # Acrescenta ao PATH do utilizador (nunca ao do sistema).
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    if ($userPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable(
            'Path', ($userPath.TrimEnd(';') + ';' + $binDir), 'User')
        Write-Ok "PATH do utilizador actualizado"
    }
    Write-Ok "comando criado em $cmdPath"
}

# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

$bootstrapArgs = @($bootstrap, '--base-url', $RepoRaw)
if ($Name)         { $bootstrapArgs += @('--name', $Name) }
if ($HostAddress)  { $bootstrapArgs += @('--host', $HostAddress) }
if ($Port)         { $bootstrapArgs += @('--port', "$Port") }
$bootstrapArgs += @('--root', $linkDir)
if ($Check)        { $bootstrapArgs += '--check' }
if ($Update)       { $bootstrapArgs += '--update' }
if ($Uninstall)    { $bootstrapArgs += '--uninstall' }
if ($Yes)          { $bootstrapArgs += '--yes' }

& $python @bootstrapArgs
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0 -and -not $Check -and -not $Uninstall) {
    Write-Step 'A criar o comando neo-link'
    New-NeoLinkCommand -LinkDir $linkDir

    Write-Host ''
    Write-Host 'Instalação concluída.'
    Write-Host ''
    Write-Host '  1. Abra um terminal novo (para o PATH carregar)'
    Write-Host "  2. Confirme:      neo-link --check"
    Write-Host '  3. Arrancar:      neo-link'
    Write-Host '  4. Emparelhar:    neo-link --pair'
    Write-Host ''
    Write-Host '  O LINK escuta apenas em localhost e nao aceita comandos.'
    Write-Host '  Para o usar a partir de outra maquina, exponha-o explicitamente:'
    Write-Host '      neo-link --host 0.0.0.0'
    Write-Host ''
    Write-Host '  Arranque automatico: opcional e manual.'
    Write-Host '  Menu Inicio > Executar > "neo-link" para o correr no login.'
    Write-Host ''
}

exit $exitCode
