# -*- coding: utf-8 -*-
<#
    律核 LawGate 演示服务一键启动（Windows PowerShell 5.1 与 PowerShell 7 均可）

        powershell -ExecutionPolicy Bypass -File .\启动服务.ps1

    启动内容：
      * Gradio 双栏对照界面   http://127.0.0.1:7860   （演示主界面）
      * FastAPI 接口服务       http://127.0.0.1:8010/docs （答辩看 trace 用）

    与实验脚本的区别：这里**不做任何实验**，只把已经建好的知识库
    （data/kb/legal_facts.db + data/kb/chroma）与最终回答模型起成服务。

    最终回答模型（D30 之后，2026-09-13 改默认）：默认走**本机权重
    models/fuzi-mingcha-v1_0**（夫子·明察 6.7B，CPU fp16，约 1 token/s），
    加 -LlmBackend deepseek 可临时切回 DeepSeek 官方 API（密钥在 .env）。

    退出：在本窗口按 Ctrl+C（会连同两个服务一起停掉）。

    注意：本文件必须保存为 **UTF-8 with BOM**，否则 Windows PowerShell 5.1
    会按 GBK 解析中文注释而报语法错（本仓库既有约定）。
#>
[CmdletBinding()]
param(
    [switch]$NoApi,        # 只起 Gradio，不起 FastAPI
    [switch]$OpenBrowser,  # 就绪后自动打开浏览器
    [int]$Threads = 8,     # torch 线程数（CPU-only；8 线程已能跑满单条生成）
    [int]$UiMaxTokens = 192,# UI 生成长度上限（**左右两栏共用**；默认 192 = RouterOptions 默认值）
    # 最终回答模型的服务形态：留空 = 按 .env/configs/base.yaml（当前是 hf 本地权重）；
    # 填 deepseek 可临时切回 DeepSeek API（有 .env 密钥时）；填 hf/rule 钉死本地
    [string]$LlmBackend = '',
    # API 端口默认 8010 而非手册写的 8000：本机 8000 已被**另一个项目**
    # （D:\桌面\心理 的「心语 · 多模态情感关怀助手」后端，python3.13 进程）
    # 长期占用。不能去杀别人的服务，故本项目让开端口；-ApiPort 可改。
    [int]$ApiPort = 8010
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 路径与环境
$Root = $PSScriptRoot
Set-Location $Root

$Script:LogDir = Join-Path $Root 'docs'
if (-not (Test-Path $Script:LogDir)) { New-Item -ItemType Directory -Path $Script:LogDir | Out-Null }

$Py = 'E:\Anaconda\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "找不到 Python 解释器：$Py 也不在 PATH 中。" }
    $Py = $cmd.Source
}

$Script:EnvBlock = @{
    KMP_DUPLICATE_LIB_OK   = 'TRUE'   # 不设会 libiomp5md.dll already initialized 直接崩
    TOKENIZERS_PARALLELISM = 'false'
    HF_HUB_OFFLINE         = '1'      # 本环境 huggingface_hub 证书校验失败，强制离线（D0）
    PYTHONIOENCODING       = 'utf-8'  # 控制台是 GBK，中文输出会 UnicodeEncodeError
    PYTHONUTF8             = '1'
    GRADIO_ANALYTICS_ENABLED = 'False' # 本机网络被中间层劫持，禁止 Gradio 联网统计
    LAWGATE_THREADS        = "$Threads"
    LAWGATE_UI_MAXTOK      = "$UiMaxTokens"   # 左右两栏共用的生成上限（serve_one → RouterOptions）
}
# -LlmBackend 显式覆盖服务形态（deepseek / hf / rule）。
# DeepSeek 的密钥不在这里传：由 lawgate/env_setup.py 读仓库根 .env（D30）。
if ($LlmBackend) { $Script:EnvBlock['LAWGATE_LLM_PROVIDER'] = $LlmBackend }

# ---------------------------------------------------------------- 小工具
function Write-Head($text) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkGray
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkGray
}

function Get-PortOwner([int]$Port) {
    # CIM/Get-NetTCPConnection 在本机可能被安全软件拦掉（实测 Get-CimInstance 报
    # "拒绝访问"），因此再兜一层 netstat 解析，避免"看不见占用者 → 服务反复重启"。
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        if ($c) { return [int]$c[0].OwningProcess }
    } catch { }
    try {
        $line = (netstat -ano | Select-String ":$Port\s+.*LISTENING" | Select-Object -First 1)
        if ($line) {
            $parts = ($line.Line -split '\s+') | Where-Object { $_ -ne '' }
            return [int]$parts[-1]
        }
    } catch { }
    return 0
}

function Test-PortBindable([int]$Port) {
    # 比"看 LISTEN 连接"更严格：真正去 bind 一次。
    # 踩过的坑（2026-09-11）：仅靠 Get-NetTCPConnection 判断"空闲"就起服务，
    # uvicorn 仍报 WSAEADDRINUSE（10048），随后被守护循环当成"服务崩了"反复重启。
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $l.Start()
        $l.Stop()
        return $true
    } catch { return $false }
}

function Wait-PortFree {
    param([int]$Port, [int]$TimeoutSec = 20)
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec) {
        if (Test-PortBindable $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Stop-PortOwner([int]$Port) {
    # 注意：变量名不能叫 $pid —— 那是 PowerShell 的只读自动变量（当前进程 id）
    $opid = Get-PortOwner $Port
    if ($opid -gt 0) {
        $proc = Get-Process -Id $opid -ErrorAction SilentlyContinue
        Write-Host ("  端口 {0} 被 pid={1}（{2}）占用，先结束它" -f
                    $Port, $opid, $proc.ProcessName) -ForegroundColor Yellow
        # /T 连同子进程一起结束，避免留下占着端口的僵尸体
        & taskkill /F /T /PID $opid 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }
    if (-not (Wait-PortFree -Port $Port -TimeoutSec 20)) {
        $still = Get-PortOwner $Port
        Write-Host ("  [警告] 端口 {0} 20s 后仍无法 bind（占用 pid={1}）；" -f $Port, $still) -ForegroundColor Red
        Write-Host '         服务可能起不来。可用 检查状态.ps1 看占用者，或手工结束该进程。' -ForegroundColor Red
        return $false
    }
    return $true
}

function Test-Url([string]$Url, [int]$TimeoutSec = 4) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

function Wait-Ready {
    param([string]$Name, [string]$Url, [int]$TimeoutSec = 300)
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec) {
        if (Test-Url $Url) { return $true }
        Start-Sleep -Seconds 3
        $el = [int]((Get-Date) - $t0).TotalSeconds
        if ($el % 30 -lt 3) { Write-Host ("  等待 {0} 就绪… {1}s" -f $Name, $el) -ForegroundColor DarkGray }
    }
    return $false
}

function Start-Service-Job {
    param([string]$Name, [string]$Service, [int]$Port = 0)
    $log = Join-Path $Script:LogDir "_serve_$Service.log"
    $sb = {
        param($Py, $Root, $Service, $Log, $EnvBlock, $Port)
        Set-Location $Root
        foreach ($k in $EnvBlock.Keys) { Set-Item -Path "env:$k" -Value $EnvBlock[$k] }
        $a = @((Join-Path $Root 'scripts\serve_one.py'), '--service', $Service,
               '--log-file', $Log)
        if ($Port -gt 0) { $a += @('--port', "$Port") }
        # 这里踩过的坑，改动前请先读完整段：
        #  * `| Tee-Object` / `>` 在 Windows PowerShell 下写 **UTF-16LE** → 日志成二进制；
        #  * `*>&1` 会把原生命令 stderr 包成 ErrorRecord → 日志塞满 NativeCommandError 噪声；
        #  * 更致命的一条：用 `& python … | ForEach-Object { … } | Out-File` 起 Gradio 时，
        #    子进程 stdout 是**管道**，权重加载进度条把管道写满后阻塞 → `launch()` 卡在
        #    建服之前，端口 300 s 都不监听（实测；手工直接跑只 6 s 就监听）。
        # 故：子进程**不接任何管道**，由 serve_one.py 自己把 fd 1/2 重定向到日志文件
        # （见 scripts\serve_one.py 的 _redirect_stdio_to_log）。这里只等它退出。
        & $Py @a | Out-Null
    }
    return Start-Job -Name $Name -ScriptBlock $sb `
        -ArgumentList $Py, $Root, $Service, $log, $Script:EnvBlock, $Port
}

function Show-JobTail([string]$Name, [int]$Lines = 20) {
    # 服务日志由子进程直接写文件，Job 自身的输出通道是空的，所以看文件而不是 Receive-Job。
    $svc = if ($Name -eq 'lawgate-ui') { 'ui' } else { 'api' }
    $f = Join-Path $Script:LogDir "_serve_$svc.log"
    if (Test-Path $f) {
        Write-Host "  ---- docs\_serve_$svc.log 末尾 ----" -ForegroundColor DarkGray
        try {
            Get-Content $f -Encoding utf8 -Tail $Lines -ErrorAction Stop |
                ForEach-Object { Write-Host "  | $_" -ForegroundColor DarkGray }
        } catch {
            Write-Host "  （日志被占用，稍后再看）" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  ---- docs\_serve_$svc.log 不存在 ----" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------- 前置自检
Write-Head '律核 LawGate · 演示服务启动'

foreach ($f in @('data\kb\legal_facts.db', 'data\kb\chroma\chroma.sqlite3',
                 'models\bge-small-zh-v1.5\config.json', '.env')) {
    if (Test-Path (Join-Path $Root $f)) {
        Write-Host "  [OK] $f" -ForegroundColor Green
    } else {
        Write-Host "  [缺失] $f" -ForegroundColor Red
    }
}
# 最终回答模型到底是谁？这里做一次**秒级**的解析（不加载权重、不联网）：
#   * 走 API：确认 .env 里有密钥（只报"有没有"，绝不回显明文）；
#   * 走本地：确认权重目录存在。
$Script:Provider = 'deepseek'
try {
    $probe = & $Py -c "import sys;sys.path.insert(0,r'$Root');import lawgate;from lawgate.config import get_settings;s=get_settings();print(s.llm_provider);print(s.deepseek_model);print(bool(s.deepseek_api_key));print(s.model_label('draft'))" 2>&1
    $Script:Provider = ($probe | Select-Object -First 1)
    $modelName = ($probe | Select-Object -Index 1)
    $hasKey = ($probe | Select-Object -Index 2)
    $draftName = ($probe | Select-Object -Index 3)
    if ($Script:Provider -eq 'deepseek') {
        $keyText = if ($hasKey -eq 'True') { '已读到（.env）' } else { '缺失 → 会 401，请在 .env 填 DEEPSEEK_API_KEY' }
        Write-Host "  回答模型 : DeepSeek API · $modelName　Key $keyText" -ForegroundColor Cyan
        Write-Host "  门控草稿 : 本机 $draftName（回答走 API，门控信号仍需本地 logprobs，见 D30）" -ForegroundColor DarkGray
    } else {
        Write-Host "  回答模型 : 本机权重（LAWGATE_LLM_PROVIDER/$($Script:Provider)）" -ForegroundColor Cyan
    }
} catch {
    Write-Host "  [警告] 模型配置解析失败：$_" -ForegroundColor Yellow
}
Write-Host "  Python : $Py"
Write-Host "  线程数 : $Threads（CPU-only）"
Write-Host "  日志   : docs\_serve_ui.log / docs\_serve_api.log"

# 残留的流水线锁会让 run_pipeline 拒绝启动；与演示服务无关，这里只提示不删除
$lock = Join-Path $Script:LogDir 'pipeline.lock'
if (Test-Path $lock) {
    Write-Host "  [提示] docs\pipeline.lock 仍存在（实验流水线的单实例锁）；" -ForegroundColor Yellow
    Write-Host "         若确认没有流水线在跑，可手工删除后再续跑实验。" -ForegroundColor Yellow
}

# 先确保端口可用；[void] 必须写 —— 否则函数返回值 0/1 会额外打印成一行 "True"
[void](Stop-PortOwner 7860)
$Script:ApiEnabled = -not $NoApi
if ($Script:ApiEnabled) {
    if (-not (Stop-PortOwner $ApiPort)) {
        Write-Host ("  [降级] 端口 {0} 无法释放（可能是别的项目的服务在跑）：" -f $ApiPort) -ForegroundColor Yellow
        Write-Host '         本次只起 Gradio，跳过 FastAPI；可用 -ApiPort 换端口。' -ForegroundColor Yellow
        $Script:ApiEnabled = $false
    }
}

# ---------------------------------------------------------------- 起服务
$Script:Jobs = @()

$Script:Jobs += Start-Service-Job -Name 'lawgate-ui' -Service 'ui'
Write-Host ''
Write-Host '  已拉起 Gradio 进程，等待 7860 就绪（首次要加载 1.5B 权重 + 向量库，约 1–2 分钟）…' -ForegroundColor Yellow

if (-not (Wait-Ready -Name 'Gradio' -Url 'http://127.0.0.1:7860/' -TimeoutSec 420)) {
    Write-Host '  Gradio 未在 300s 内就绪。最近的输出：' -ForegroundColor Red
    Show-JobTail -Name 'lawgate-ui' -Lines 40
    throw 'Gradio 启动失败，详见 docs\_serve_ui.log'
}
Write-Host '  [就绪] Gradio  → http://127.0.0.1:7860' -ForegroundColor Green

if ($Script:ApiEnabled) {
    $Script:Jobs += Start-Service-Job -Name 'lawgate-api' -Service 'api' -Port $ApiPort
    Write-Host ("  已拉起 FastAPI 进程，等待 {0} 就绪…" -f $ApiPort) -ForegroundColor Yellow
    if (Wait-Ready -Name 'FastAPI' -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSec 120) {
        Write-Host ("  [就绪] FastAPI → http://127.0.0.1:{0}/docs" -f $ApiPort) -ForegroundColor Green
    } else {
        Write-Host '  [警告] FastAPI 未就绪；本次只保留 Gradio（详见 docs\_serve_api.log）' -ForegroundColor Yellow
        Show-JobTail -Name 'lawgate-api'
        # 端口被占的情况下重启是徒劳的，直接停掉这个 Job
        Get-Job -Name 'lawgate-api' -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Job -Job $_ -ErrorAction SilentlyContinue
            Remove-Job -Job $_ -Force -ErrorAction SilentlyContinue
        }
        $Script:Jobs = @($Script:Jobs | Where-Object { $_.Name -ne 'lawgate-api' })
        $Script:ApiEnabled = $false
    }
}

Write-Host ''
Write-Host '  ┌────────────────────────────────────────────────────────────┐' -ForegroundColor Cyan
Write-Host '  │ 演示界面  http://127.0.0.1:7860                            │' -ForegroundColor Cyan
if ($Script:ApiEnabled) {
    $apiLine = ("  │ 接口文档  http://127.0.0.1:{0}/docs" -f $ApiPort)
    Write-Host ($apiLine + (' ' * [Math]::Max(1, 62 - $apiLine.Length)) + '│') -ForegroundColor Cyan
}
Write-Host '  │ 停止服务  在本窗口按 Ctrl+C                                │' -ForegroundColor Cyan
Write-Host '  └────────────────────────────────────────────────────────────┘' -ForegroundColor Cyan
Write-Host '  说明：两栏都是**流式输出**（边生成边显示）。' -ForegroundColor DarkGray
if ($Script:Provider -eq 'deepseek') {
    Write-Host '        最终回答模型 = DeepSeek 官方 API（deepseek-v4-flash，思考模式关）。' -ForegroundColor DarkGray
    Write-Host "        单条 192 token 约 1–3 秒；命中内容缓存后 ≈ 0.05 秒。" -ForegroundColor DarkGray
    Write-Host '        门控草稿用本机 Qwen2.5-0.5B（首次提问时加载，约 10–15 秒一次性开销）。' -ForegroundColor DarkGray
    Write-Host '        想切回本机大模型：.\启动服务.ps1 -LlmBackend hf' -ForegroundColor DarkGray
} else {
    Write-Host '        最终回答模型 = 本机 fuzi-mingcha-v1_0（夫子·明察 6.7B，fp16/CPU）。' -ForegroundColor DarkGray
    Write-Host "        整段最长 $UiMaxTokens token，CPU 上约 2–4 分钟/栏；命中内容缓存后 ≈ 0.1 s。" -ForegroundColor DarkGray
    Write-Host '        想缩短等待可加 -UiMaxTokens 80（左右两栏会一起变短，口径仍一致）。' -ForegroundColor DarkGray
    Write-Host '        模型未加载完成前页面可能转圈，稍等即可（首次提问才加载 13 GB 权重）。' -ForegroundColor DarkGray
    Write-Host '        想临时切回 DeepSeek API：.\启动服务.ps1 -LlmBackend deepseek' -ForegroundColor DarkGray
}

if ($OpenBrowser) { Start-Process 'http://127.0.0.1:7860' }

# ---------------------------------------------------------------- 守护循环
# Gradio/FastAPI 都是常驻进程；这里只做"挂了就重启并记录原因"，不做其他干预。
# 每个服务最多重启 5 次：超过就说明是**端口/依赖**这类改不了的环境问题，
# 再重启只会刷屏（2026-09-11 实测：8000 被别的项目占用时出现的刷屏式重启）。
$MAX_RESTART = 5
$restarts = @{}
foreach ($j in $Script:Jobs) { $restarts[$j.Name] = 0 }

try {
    while ($true) {
        Start-Sleep -Seconds 8
        foreach ($j in @(Get-Job -ErrorAction SilentlyContinue | Where-Object { $_.Name })) {
            if ($j.State -notin @('Completed', 'Failed', 'Stopped')) { continue }
            if (-not $restarts.ContainsKey($j.Name)) { $restarts[$j.Name] = 0 }
            $restarts[$j.Name] = [int]$restarts[$j.Name] + 1
            $svc = if ($j.Name -eq 'lawgate-ui') { 'ui' } else { 'api' }
            Write-Host ("[{0}] {1} 已退出（第 {2} 次）" -f
                        (Get-Date -Format 'HH:mm:ss'), $j.Name, $restarts[$j.Name]) -ForegroundColor Yellow
            Show-JobTail -Name $j.Name -Lines 15
            Remove-Job -Job $j -Force
            if ($restarts[$j.Name] -gt $MAX_RESTART) {
                Write-Host ("[{0}] {1} 连续退出超过 {2} 次，不再自动重启。" -f
                            (Get-Date -Format 'HH:mm:ss'), $j.Name, $MAX_RESTART) -ForegroundColor Red
                Write-Host '         请查看对应日志（docs\_serve_*.log）后手工重启。' -ForegroundColor Red
                continue
            }
            if ($svc -eq 'api' -and -not $Script:ApiEnabled) { continue }
            Write-Host ("[{0}] 正在重启 {1}…" -f (Get-Date -Format 'HH:mm:ss'), $j.Name) -ForegroundColor Yellow
            if ($svc -eq 'api') {
                [void](Start-Service-Job -Name $j.Name -Service $svc -Port $ApiPort)
            } else {
                [void](Start-Service-Job -Name $j.Name -Service $svc)
            }
            if ($svc -eq 'ui') {
                [void](Wait-Ready -Name 'Gradio' -Url 'http://127.0.0.1:7860/' -TimeoutSec 420)
            }
            Write-Host ("[{0}] {1} 已重启（日志 docs\_serve_{2}.log）" -f
                        (Get-Date -Format 'HH:mm:ss'), $j.Name, $svc) -ForegroundColor Green
        }
    }
} finally {
    Write-Host ''
    Write-Host '  正在停止演示服务…' -ForegroundColor Yellow
    Get-Job -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Job -Job $_ -ErrorAction SilentlyContinue } catch { }
        try { Receive-Job -Job $_ -Keep -ErrorAction SilentlyContinue | Out-Null } catch { }
        try { Remove-Job -Job $_ -Force -ErrorAction SilentlyContinue } catch { }
    }
    # 兜底：任务被强杀时端口仍可能被占用
    [void](Stop-PortOwner 7860)
    if (-not $NoApi) { [void](Stop-PortOwner $ApiPort) }
    Write-Host '  已停止。' -ForegroundColor Green
}
