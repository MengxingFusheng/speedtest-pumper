param(
    [string]$InstallDir = "$HOME\speedtest-pumper",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/MengxingFusheng/speedtest-pumper.git"

function Assert-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "未找到命令 $Name，请先安装后再运行此脚本"
    }
}

Assert-Command "git"
Assert-Command "docker"

if (Test-Path -LiteralPath $InstallDir) {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir ".git"))) {
        throw "安装目录已存在但不是 git 仓库: $InstallDir"
    }
    Write-Host "更新已有目录: $InstallDir"
    git -C $InstallDir pull --ff-only
} else {
    Write-Host "克隆仓库到: $InstallDir"
    git clone $RepoUrl $InstallDir
}

Set-Location $InstallDir
if ($NoStart) {
    .\deploy.ps1 -NoStart
} else {
    .\deploy.ps1
}
