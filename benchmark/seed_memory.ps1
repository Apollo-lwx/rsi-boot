# RepoMem Bench C 组记忆播种：rsi knowledge add × N + MCP 批准
# 用法：powershell -File benchmark/seed_memory.ps1 <workdir-name>
# workdir-name 为 benchmark/.workdir/ 下的目录名（= 任务 id）
param([Parameter(Mandatory=$true)][string]$TaskId)

$ErrorActionPreference = 'Stop'
$root = "D:\project\study\rsi-boot"
$workdir = Join-Path $root "benchmark\.workdir\$TaskId"
if (-not (Test-Path $workdir)) { throw "workdir 不存在: $workdir" }

# 每个任务的记忆条目（title/content/type/tags）
$seeds = @{
  'packaging-prerelease-gt-lt' = @(
    @('测试命令', 'python -m pytest tests/ -q；测试依赖 pytest 与 pretend；先 pip install -e . 再跑', 'convention', 'test,python'),
    @('代码风格', '全库用 ruff 做 lint 与格式化（不用 black）；错误消息中的文件名/说明符用 !r 格式化', 'convention', 'style,ruff'),
    @('修复纪律', '修复只动 src/ 下的实现，不改测试；过期断言留给维护者处理，不要自行更新', 'prohibition', 'discipline'),
    @('源码布局', '实现在 src/packaging/；说明符逻辑在 specifiers.py，版本解析在 version.py', 'convention', 'layout')
  )
  'packaging-filename-no-raise' = @(
    @('测试命令', 'python -m pytest tests/ -q；测试依赖 pytest 与 pretend；先 pip install -e . 再跑', 'convention', 'test,python'),
    @('修复纪律', '修复只动 src/ 下的实现，不改测试；过期断言留给维护者处理，不要自行更新', 'prohibition', 'discipline'),
    @('源码布局', '实现在 src/packaging/；文件名解析在 utils.py，异常定义在 exceptions.py', 'convention', 'layout')
  )
  'jinja-empty-required-block' = @(
    @('测试命令', 'python -m pytest tests/ -q；async 测试模块硬依赖 trio 与 pytest-trio', 'convention', 'test,python'),
    @('代码风格', '用 Black 格式化（pre-commit 自动执行）；文档与 docstring 按 72 列换行', 'convention', 'style,black'),
    @('修复纪律', '修复只动 src/ 下的实现，不改测试；过期断言留给维护者处理，不要自行更新', 'prohibition', 'discipline'),
    @('源码布局', '实现在 src/jinja2/：模板解析 parser.py、代码生成 compiler.py、过滤器 filters.py', 'convention', 'layout')
  )
  'mi-windowed-invalid-size' = @(
    @('测试命令', 'python -m pytest tests/ -q；先 pip install -e . 再跑', 'convention', 'test,python'),
    @('修复纪律', '修复只动 more_itertools/ 下的实现，不改测试；过期断言留给维护者处理，不要自行更新', 'prohibition', 'discipline'),
    @('源码布局', '实现在 more_itertools/（more.py 为主），测试在 tests/', 'convention', 'layout')
  )
}

if (-not $seeds.ContainsKey($TaskId)) { throw "未配置该任务的种子: $TaskId" }

Push-Location $workdir
foreach ($e in $seeds[$TaskId]) {
  rsi knowledge add --title $e[0] --content $e[1] --type $e[2] --tags $e[3] | Out-Null
  Write-Output "added: $($e[0])"
}
Pop-Location

python (Join-Path $root 'benchmark\mcp_call.py') $workdir rsi_knowledge_review "@$root\benchmark\.workdir\approve.json"
Write-Output "approved: $TaskId"

