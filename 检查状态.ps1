# -*- coding: utf-8 -*-
<#
    律核 LawGate 服务状态检查（一键诊断）

        powershell -ExecutionPolicy Bypass -File .\检查状态.ps1

    检查项：
      1. 端口 7860 / 8010 是否在监听，占用进程是谁；
      2. http://127.0.0.1:7860/ 与 http://127.0.0.1:8010/health 是否可访问
         （/health 里的 provenance 会显示**实际生效**的模型与后端）；
      3. 知识库与权重文件是否齐全；
      4. 报告不是"跑实验"的状态：E1/E5/E3/E2 产物是否已落盘，便于区分
         "服务能跑"与"结论已出"。

    注意：API 默认端口是 8010，不是手册写的 8000 —— 本机 8000 被
    D:\桌面\心理 的「心语」后端长期占用，本项目让端口。

    本文件须保存为 UTF-8 with BOM（PowerShell 5.1 会用 GBK 解析中文）。
#>
[CmdletBinding()]
param(
    [int]$UiPort = 7860,
    [int]$ApiPort = 8010
)

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
Set-Location $Root

function Write-Head($text) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkGray
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkGray
}

function Test-Url([string]$Url, [int]$TimeoutSec = 5) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return $r
    } catch { return $null }
}

Write-Head '律核 LawGate · 状态检查'

# ---------------------------------------------------------------- 1. 端口
# Get-NetTCPConnection 在本机可能被安全软件拦掉（实测 Get-CimInstance 报"拒绝访问"），
# 故失败时兜一层 netstat 解析，否则会把"被占用的端口"误报成"空闲"。
function Get-PortOwner([int]$Port) {
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

foreach ($p in @($UiPort, $ApiPort)) {
    $opid = Get-PortOwner $p
    if ($opid -gt 0) {
        $proc = Get-Process -Id $opid -ErrorAction SilentlyContinue
        $ws = if ($proc) { [int]($proc.WorkingSet64 / 1MB) } else { 0 }
        Write-Host ("  [占用] 端口 {0}  pid={1}  {2}  内存 {3} MB  {4}" -f
                    $p, $opid, ($proc.ProcessName), $ws, ($proc.Path)) -ForegroundColor Green
    } else {
        Write-Host ("  [空闲] 端口 {0} —— 服务未起（运行 启动服务.ps1）" -f $p) -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- 2. HTTP
Write-Head 'HTTP 探活'

$ui = Test-Url "http://127.0.0.1:$UiPort/"
if ($ui) {
    Write-Host ("  [OK] Gradio   http://127.0.0.1:{0}/   HTTP {1}" -f $UiPort, $ui.StatusCode) -ForegroundColor Green
} else {
    Write-Host ("  [--] Gradio   http://127.0.0.1:{0}/   无响应" -f $UiPort) -ForegroundColor Yellow
}

$health = Test-Url "http://127.0.0.1:$ApiPort/health"
if ($health) {
    Write-Host ("  [OK] FastAPI  /health   HTTP {0}" -f $health.StatusCode) -ForegroundColor Green
    try {
        $j = $health.Content | ConvertFrom-Json
        Write-Host ("       实际模型: {0}  /  向量模型: {1}" -f
                    $j.provenance.causal_model, $j.provenance.embed_model)
        if ($j.provenance.causal_model_dtype) {
            Write-Host ("       精度    : {0}   来源: {1}" -f
                        $j.provenance.causal_model_dtype,
                        $(if ($j.provenance.causal_model_source) { $j.provenance.causal_model_source } else { '未记录' }))
        }
        Write-Host ("       硬件      : {0}" -f $j.provenance.hardware)
        Write-Host ("       router 已加载: {0}   加载错误: {1}" -f
                    $j.router_loaded, ($(if ($j.load_error) { $j.load_error } else { '无' })))
        if ($j.cache) {
            Write-Host ("       生成缓存条目: {0}（累计省下 {1:N0} s 算力）" -f
                        $j.cache.total_entries, $j.cache.total_compute_seconds)
        }
    } catch {
        Write-Host '       （/health 返回内容不是预期 JSON，原文前 200 字：）' -ForegroundColor DarkGray
        Write-Host ("       " + $health.Content.Substring(0, [Math]::Min(200, $health.Content.Length))) -ForegroundColor DarkGray
    }
} else {
    Write-Host ("  [--] FastAPI  http://127.0.0.1:{0}/health  无响应" -f $ApiPort) -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 3. 数据与权重
Write-Head '数据与权重'
$files = @(
    @('知识库 SQLite', 'data\kb\legal_facts.db'),
    @('向量库 chroma', 'data\kb\chroma\chroma.sqlite3'),
    @('评测集总分片', 'data\benchmark\test_e1.jsonl'),
    @('Qwen3-4B 索引', 'models\Qwen3-4B\model.safetensors.index.json'),
    @('Qwen3-4B 配置', 'models\Qwen3-4B\config.json'),
    @('兜底 fuzi 索引', 'models\fuzi-mingcha-v1_0\pytorch_model.bin.index.json'),
    @('向量模型 bge', 'models\bge-small-zh-v1.5\config.json'),
    @('桶级阈值', 'configs\thresholds.json')
)
foreach ($f in $files) {
    if (Test-Path (Join-Path $Root $f[1])) {
        $len = (Get-Item (Join-Path $Root $f[1])).Length
        Write-Host ("  [OK] {0,-16} {1}  ({2:N0} B)" -f $f[0], $f[1], $len) -ForegroundColor Green
    } else {
        Write-Host ("  [缺失] {0,-16} {1}" -f $f[0], $f[1]) -ForegroundColor Red
    }
}

# ---------------------------------------------------------------- 4. 实验产物
Write-Head '实验产物是否落盘（“服务能跑” ≠ “结论已出”）'
$exp = @(
    @('E1 主对比', 'results\e1\summary.csv'),
    @('E1 主对比(基线)', 'results\e1\summary_neverrag_seed0_test_e1.json'),
    @('E5 时效陷阱', 'results\e5\tvc_by_trap.json'),
    @('E3 多轮', 'results\e3\e3_report.json'),
    @('E2 消融', 'results\e2\ablation_rows.json'),
    @('E6 案号核验', 'results\e6\prf.json')
)
foreach ($e in $exp) {
    if (Test-Path (Join-Path $Root $e[1])) {
        Write-Host ("  [已出] {0,-18} {1}" -f $e[0], $e[1]) -ForegroundColor Green
    } else {
        Write-Host ("  [未跑] {0,-18} {1}" -f $e[0], $e[1]) -ForegroundColor DarkGray
    }
}

$lock = Join-Path $Root 'docs\pipeline.lock'
if (Test-Path $lock) {
    $rec = Get-Content $lock -Raw -Encoding UTF8
    Write-Host "  [注意] docs\pipeline.lock 存在：$rec" -ForegroundColor Yellow
    Write-Host '         若确认没有流水线在跑（见 docs\pipeline.log 末尾），可删除后重跑实验。' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '  提示：控制台是 GBK，脚本产出的中文内容请用编辑器打开对应文件查看。' -ForegroundColor DarkGray
