<#
.SYNOPSIS
    确保本地模型文件就位（API_MODE=local 时 llama-server 使用的 GGUF）。

.DESCRIPTION
    1. 读取 .env 的 LOCAL_MODEL_FILE / LOCAL_MMPROJ_FILE（未配置时用 app.py 的默认名）。
    2. 两个文件都存在且大小合理 -> 直接退出（不重复下载）。
    3. 缺失时先检查已下载的官方文件，仍缺失则从 ModelScope 下载：
         modelscope download ornith-ai/Ornith-1.5-9B-GGUF
             Ornith-1.5-9B-Q6_K.gguf mmproj-Ornith-1.5-9B-BF16.gguf --local-dir models/
       下载完成后把 .env 的 LOCAL_MODEL_FILE / LOCAL_MMPROJ_FILE 指向下载到的文件，
       使 local 模式开箱可用（只改这两行，其它配置不动）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\model_download.ps1
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Repo       = 'ornith-ai/Ornith-1.5-9B-GGUF'
$ModelFile  = 'Ornith-1.5-9B-Q6_K.gguf'
$MmprojFile = 'mmproj-Ornith-1.5-9B-BF16.gguf'
$ModelDir   = Join-Path $Root 'models'
$EnvPath    = Join-Path $Root '.env'

# local 模式默认文件名（与 app.py 保持一致）
$DefaultModel  = 'models/Ornith-1.5-9B-uncensored.Q6_K.gguf'
$DefaultMmproj = 'models/Ornith-1.5-9B-uncensored.mmproj-Q8_0.gguf'

# 判定“文件可用”的最小体积：防止 0 字节 / 半截文件被当成已下载
$MinModelMB = 500
$MinMmprojMB = 50

function Get-EnvValue([string]$key) {
    if (-not (Test-Path $EnvPath)) { return $null }
    $raw = [System.IO.File]::ReadAllText($EnvPath, [System.Text.Encoding]::UTF8)
    $m = [regex]::Match($raw, "(?m)^" + [regex]::Escape($key) + "=([^\r\n]*)")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return $null
}

function Set-EnvValue([string]$key, [string]$value) {
    if (-not (Test-Path $EnvPath)) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($EnvPath)
    $hadBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $raw = [System.IO.File]::ReadAllText($EnvPath, [System.Text.Encoding]::UTF8)
    $pattern = "(?m)^" + [regex]::Escape($key) + "=.*$"
    if ([regex]::IsMatch($raw, $pattern)) {
        $raw = [regex]::Replace($raw, $pattern, { param($m) "$key=$value" })
    } else {
        $raw = $raw.TrimEnd("`r", "`n") + "`r`n$key=$value`r`n"
    }
    # 写回时保持原有 BOM 状态，避免 python-dotenv 读到 BOM 化的首键
    $enc = New-Object System.Text.UTF8Encoding($hadBom)
    [System.IO.File]::WriteAllText($EnvPath, $raw, $enc)
    return $true
}

function Resolve-FilePath([string]$p) {
    if ([System.IO.Path]::IsPathRooted($p)) { return $p }
    return (Join-Path $Root $p)
}

function Test-ModelFile([string]$path, [int]$minMB) {
    if (-not (Test-Path $path)) { return $false }
    $sizeMB = [math]::Round((Get-Item $path).Length / 1MB, 1)
    if ($sizeMB -lt $minMB) {
        Write-Host ("  [跳过] {0} 只有 {1} MB（阈值 {2} MB），视为未完整下载" -f (Split-Path $path -Leaf), $sizeMB, $minMB) -ForegroundColor Yellow
        return $false
    }
    Write-Host ("  [就绪] {0}  {1} MB" -f (Split-Path $path -Leaf), $sizeMB) -ForegroundColor Green
    return $true
}

Write-Host "== 模型文件检查 ==" -ForegroundColor Cyan
$targetModel  = Get-EnvValue 'LOCAL_MODEL_FILE'
if (-not $targetModel)  { $targetModel = $DefaultModel }
$targetMmproj = Get-EnvValue 'LOCAL_MMPROJ_FILE'
if (-not $targetMmproj) { $targetMmproj = $DefaultMmproj }
$targetModelPath  = Resolve-FilePath $targetModel
$targetMmprojPath = Resolve-FilePath $targetMmproj
Write-Host ("  目标模型: {0}" -f $targetModelPath)
Write-Host ("  目标视觉投影: {0}" -f $targetMmprojPath)

$modelOk  = Test-ModelFile $targetModelPath $MinModelMB
$mmprojOk = Test-ModelFile $targetMmprojPath $MinMmprojMB
if ($modelOk -and $mmprojOk) {
    Write-Host "模型已就绪，无需下载。" -ForegroundColor Green
    exit 0
}

# 已下载过官方文件（文件名与配置不同）时，直接让配置指向它们
$dlModelPath  = Join-Path $ModelDir $ModelFile
$dlMmprojPath = Join-Path $ModelDir $MmprojFile
$dlModelOk  = Test-ModelFile $dlModelPath $MinModelMB
$dlMmprojOk = Test-ModelFile $dlMmprojPath $MinMmprojMB
if ($dlModelOk -and $dlMmprojOk) {
    Write-Host "检测到已下载的官方模型文件，更新 .env 指向它们 ..." -ForegroundColor Yellow
    if ((Set-EnvValue 'LOCAL_MODEL_FILE' "models/$ModelFile") -and (Set-EnvValue 'LOCAL_MMPROJ_FILE' "models/$MmprojFile")) {
        Write-Host "  .env 已更新：LOCAL_MODEL_FILE=models/$ModelFile, LOCAL_MMPROJ_FILE=models/$MmprojFile" -ForegroundColor Green
    } else {
        Write-Host "  未找到 .env，请手动设置 LOCAL_MODEL_FILE=models/$ModelFile 与 LOCAL_MMPROJ_FILE=models/$MmprojFile" -ForegroundColor Yellow
    }
    exit 0
}

# 下载：pip install modelscope + modelscope download
Write-Host "开始从 ModelScope 下载模型（约 8 GB，请耐心等待）..." -ForegroundColor Cyan
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "未找到 python，请先运行 prepare_env.ps1 准备环境。" }

& $py -c "import modelscope" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 modelscope ..." -ForegroundColor Cyan
    & $py -m pip install modelscope
    if ($LASTEXITCODE -ne 0) { throw "modelscope 安装失败（exit $LASTEXITCODE）。" }
}

$msExe = Join-Path (Split-Path $py) 'modelscope.exe'
if (-not (Test-Path $msExe)) {
    $cmd = Get-Command modelscope -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "未找到 modelscope 命令，请确认 modelscope 安装成功。" }
    $msExe = $cmd.Source
}

New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
& $msExe download $Repo $ModelFile $MmprojFile --local-dir $ModelDir
if ($LASTEXITCODE -ne 0) { throw "模型下载失败（exit $LASTEXITCODE）。" }

# 下载后校验并同步 .env
Write-Host "== 下载结果校验 ==" -ForegroundColor Cyan
$ok = (Test-ModelFile $dlModelPath $MinModelMB) -and (Test-ModelFile $dlMmprojPath $MinMmprojMB)
if (-not $ok) { throw "下载完成但文件不完整，请重新运行本脚本。" }

if ((Set-EnvValue 'LOCAL_MODEL_FILE' "models/$ModelFile") -and (Set-EnvValue 'LOCAL_MMPROJ_FILE' "models/$MmprojFile")) {
    Write-Host "  .env 已更新：LOCAL_MODEL_FILE=models/$ModelFile, LOCAL_MMPROJ_FILE=models/$MmprojFile" -ForegroundColor Green
} else {
    Write-Host "  未找到 .env，请手动设置 LOCAL_MODEL_FILE=models/$ModelFile 与 LOCAL_MMPROJ_FILE=models/$MmprojFile" -ForegroundColor Yellow
}

Write-Host "完成。将 .env 的 API_MODE 设为 local 后启动应用即可使用本地模型。" -ForegroundColor Green
