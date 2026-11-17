# 本地一键：装依赖 -> 跑用例 -> 出报告（需要已安装 Allure CLI）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
$python = ".\.venv\Scripts\python.exe"
& $python -m pip install -q -r requirements.txt

$mode = if ($args.Count -gt 0) { $args } else { @() }
& $python -m pytest @mode

if (Get-Command allure -ErrorAction SilentlyContinue) {
    allure generate allure-results -o allure-report --clean
    Write-Host "报告已生成：allure-report/index.html（或执行 allure open allure-report）"
} else {
    Write-Warning "未检测到 allure CLI，仅生成 allure-results；安装见 docs 手册"
}
