#Requires -Version 5.1
# Read installer functions through the parser. Filesystem work is real except for
# the two operations a test cannot provoke honestly (copy failure, cleanup failure),
# so the assertions look at the actual tree rather than a log of intentions.
#
# 测试状态（要注入的故障、已执行的步骤）走临时文件，不走 $global:：在 5.1 里，
# 经 Invoke-Expression 提取出来的安装函数与 mock 之间用 $global: 传状态会时灵时不灵，
# 表现为「注入了故障却不触发」，查过两轮没有稳定结论，用文件最省事。
$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Installer syntax errors.' }
foreach ($name in @('Get-SkillsDirs', 'Resolve-InstallPath', 'Move-Dir', 'Move-Side', 'Install-Skill')) {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing function: $name" }
    Invoke-Expression $fn.Extent.Text
}

# Exercise real path construction on Windows PowerShell 5.1, not only the copy guard.
& {
    $Target = ''
    $expected = @(
        (Join-Path (Join-Path $HOME '.dsh') 'skills'),
        (Join-Path (Join-Path $HOME '.claude') 'skills'),
        (Join-Path (Join-Path $HOME '.codex') 'skills'),
        (Join-Path (Join-Path (Join-Path $HOME '.config') 'agents') 'skills')
    )
    function Test-Path { param([string]$Path) return $Path -in $expected }
    $actual = @(Get-SkillsDirs)
    if (($actual -join '|') -ne ($expected -join '|')) { throw 'Default skills discovery returned unexpected paths.' }
}
Write-Host 'PASS: automatic discovery of all supported skills directories'
& {
    $Target = ''
    function Test-Path { param([string]$Path) return $Path -eq (Join-Path $HOME '.claude') }
    $actual = @(Get-SkillsDirs)
    $expected = Join-Path (Join-Path $HOME '.claude') 'skills'
    if ($actual.Count -ne 1 -or $actual[0] -ne $expected) { throw 'Parent-only agent discovery failed.' }
}
Write-Host 'PASS: skills directory can be inferred from an agent parent'
& {
    $Target = ''
    function Test-Path { param([string]$Path) return $false }
    $actual = @(Get-SkillsDirs)
    $expected = Join-Path (Join-Path $HOME '.dsh') 'skills'
    if ($actual.Count -ne 1 -or $actual[0] -ne $expected) { throw 'No-agent fallback path is invalid.' }
}
Write-Host 'PASS: no-agent fallback constructs a valid DSH skills path'
& {
    $Target = 'explicit-target'
    function Test-Path { throw 'Explicit target must not invoke automatic discovery.' }
    $actual = @(Get-SkillsDirs)
    if ($actual.Count -ne 1 -or $actual[0] -ne $Target) { throw 'Explicit target was not preserved.' }
}
Write-Host 'PASS: explicit target skips discovery'

$base = $env:AGENTTIME_TEST_TMP
if (-not $base) { $base = [IO.Path]::GetTempPath() }
$stateRoot = Join-Path $base ('agenttime-state-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($stateRoot) | Out-Null
$failFile = Join-Path $stateRoot 'fail.txt'
$logFile = Join-Path $stateRoot 'log.txt'
$occupyFile = Join-Path $stateRoot 'occupy.txt'
[IO.File]::WriteAllText($failFile, '')
[IO.File]::WriteAllText($logFile, '')
[IO.File]::WriteAllText($occupyFile, '')

function Get-FailList {
    @(([IO.File]::ReadAllText($failFile)) -split ',' | Where-Object { $_ })
}
function Get-LogText {
    (@(Get-Content -LiteralPath $logFile -ErrorAction SilentlyContinue) -join ',')
}
function Note {
    param([string]$Step)
    [IO.File]::AppendAllText($logFile, $Step + "`n")
}
function Start-Case {
    param([string[]]$Failure = @(), [switch]$Occupy)
    [IO.File]::WriteAllText($failFile, ($Failure -join ','))
    [IO.File]::WriteAllText($logFile, '')
    [IO.File]::WriteAllText($occupyFile, $(if ($Occupy) { 'yes' } else { '' }))
}
function Expect-Log {
    param([string]$Want)
    $actual = Get-LogText
    if ($actual -ne $Want) { throw "unexpected steps: got '$actual', want '$Want'" }
}
function Expect-Throw {
    param([scriptblock]$Body, [string]$Pattern, [string]$Label)
    $caught = $null
    try { & $Body | Out-Null } catch { $caught = $_ }
    if (-not $caught) { throw "$Label did not fail." }
    if ($Pattern -and $caught.Exception.Message -notlike $Pattern) {
        throw "$Label failed for the wrong reason: $($caught.Exception.Message)"
    }
}

function Copy-Item {
    [CmdletBinding()]
    param($LiteralPath, $Destination, $Exclude, [switch]$Recurse, [switch]$Force)
    Note 'copy'
    if ((Get-FailList) -contains 'copy') { throw 'mock copy failure' }
    # 造一个够用的暂存目录：发布之后 $dest\SKILL.md 必须存在，否则算装错
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    [IO.File]::WriteAllText((Join-Path $Destination 'SKILL.md'), 'staged')
}

function Move-Dir {
    [CmdletBinding()]
    param([string]$From, [string]$To)
    if ($From -match '\\\.new-') { $step = 'publish' }
    elseif ($From -match '\\\.old-') { $step = 'rollback' }
    else { $step = 'aside' }
    Note $step
    if ((Get-FailList) -contains $step) { throw "mock $step failure" }
    if ($step -eq 'publish' -and [IO.File]::ReadAllText($occupyFile) -eq 'yes') {
        [IO.Directory]::CreateDirectory($To) | Out-Null   # 另一个进程抢先建好了目标
    }
    [IO.Directory]::Move($From, $To)
}

function Remove-Item {
    [CmdletBinding()]
    param($LiteralPath, [switch]$Recurse, [switch]$Force)
    if ($LiteralPath -match '\\\.old-') {
        Note 'cleanup'
        if ((Get-FailList) -contains 'cleanup') { throw 'mock cleanup failure' }
    } elseif ($LiteralPath -match '\\\.new-') {
        Note 'drop-staging'
    } else {
        Note 'drop-dest'
    }
    if (Test-Path -LiteralPath $LiteralPath) {
        Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Recurse -Force
    }
}

function New-Item {
    # 只关心建符号链接；建目录本来就会成功，不必记录。
    [CmdletBinding()]
    param($Path, $ItemType, $Target, [switch]$Force)
    if ($ItemType -eq 'SymbolicLink') { Note 'link' }
}

$aliasPath = ''
$aliasTarget = ''
function Get-Item {
    [CmdletBinding()]
    param($LiteralPath, [switch]$Force)
    if ($aliasPath -and $LiteralPath -eq $aliasPath) {
        return [pscustomobject]@{ Attributes = [IO.FileAttributes]::ReparsePoint; Target = $aliasTarget }
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force
}

$Source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$originalSource = $Source
$SkillName = Split-Path -Leaf $Source
$Force = $true
$Link = $false

Start-Case
$result = Install-Skill -SkillsDir (Split-Path -Parent $Source)
Expect-Log ''
if (-not $result) { throw 'Self-installation was not skipped safely.' }
Write-Host 'PASS: self-installation leaves source unchanged'

$Source = Join-Path $originalSource 'adapters'
Start-Case
Expect-Throw { Install-Skill -SkillsDir (Split-Path -Parent $originalSource) } '*must not contain*' 'Ancestor target'
Expect-Log ''
Write-Host 'PASS: destination cannot contain source'

$Source = $originalSource
$SkillName = 'nested-install-test'
Start-Case
Expect-Throw { Install-Skill -SkillsDir $Source } '*must not contain*' 'Descendant target'
Expect-Log ''
Write-Host 'PASS: source cannot contain destination'

# Model a junction or symbolic link without creating one on the user's disk.
$aliasPath = Join-Path $Source 'adapters'
$aliasTarget = $Source
$SkillName = 'adapters'
Start-Case
$result = Install-Skill -SkillsDir $Source
Expect-Log ''
if (-not $result) { throw 'Link alias to source was not skipped.' }
Write-Host 'PASS: link alias resolves to source before deletion'
$SkillName = 'nested-install-test'
Start-Case
Expect-Throw { Install-Skill -SkillsDir $aliasPath } '*must not contain*' 'Linked parent'
Expect-Log ''
Write-Host 'PASS: links in parent directories are resolved'

# ── 更新一个已存在的安装：断言看真实目录 ──
$aliasPath = ''
$fixtureRoot = Join-Path $base ('agenttime-install-' + [Guid]::NewGuid().ToString('N'))
$destPath = Join-Path $fixtureRoot 'existing'
$keepFile = Join-Path $destPath 'keep.txt'

function Reset-Fixture {
    Start-Case
    if ([IO.Directory]::Exists($fixtureRoot)) { [IO.Directory]::Delete($fixtureRoot, $true) }
    [IO.Directory]::CreateDirectory($destPath) | Out-Null
    [IO.File]::WriteAllText($keepFile, 'old install')
    $script:SkillName = 'existing'
    $script:Source = $originalSource
    $script:Force = $true
    $script:Link = $false
}
function Get-Backups {
    @(Get-ChildItem -LiteralPath $fixtureRoot -Directory -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like '.old-existing-*' })
}

Reset-Fixture

# 1) 复制失败：旧安装还没被碰过
Start-Case 'copy'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*copy failure*' 'Copy failure'
Expect-Log 'copy'
if (-not [IO.File]::Exists($keepFile)) { throw 'A failed copy removed the existing installation.' }
Write-Host 'PASS: copy failure leaves the existing installation untouched'

# 2) 换名发布失败：旧安装被换回来
Start-Case 'publish'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*publish failure*' 'Publish failure'
Expect-Log 'copy,aside,publish,drop-staging,rollback'
if (-not [IO.File]::Exists($keepFile)) { throw 'A failed publish did not restore the existing installation.' }
if ((Get-Backups).Count -ne 0) { throw 'A successful rollback left the backup behind.' }
Write-Host 'PASS: publish failure restores the previous installation'

# 3) 发布失败且回滚也失败：备份保留，不能删
Reset-Fixture
Start-Case 'publish,rollback'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*publish failure*' 'Publish plus rollback failure'
Expect-Log 'copy,aside,publish,drop-staging,rollback'
if ((Get-Backups).Count -ne 1) { throw 'A failed rollback did not keep exactly one backup.' }
Write-Host 'PASS: failed rollback keeps the backup instead of deleting it'

# 4) 发布成功但清理备份失败：保留新安装，只警告，不回滚
Reset-Fixture
Start-Case 'cleanup'
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,aside,publish,cleanup'
if (-not $result) { throw 'A failed backup cleanup was reported as a failed install.' }
if (-not (Test-Path -LiteralPath (Join-Path $destPath 'SKILL.md'))) { throw 'A failed cleanup lost the new installation.' }
if ((Get-Backups).Count -ne 1) { throw 'A failed cleanup deleted the backup anyway.' }
Write-Host 'PASS: failed backup cleanup keeps the new install and does not roll back'

# 5) 正常更新与全新安装
Reset-Fixture
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,aside,publish,cleanup'
if (-not $result) { throw 'Updating an existing installation failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $destPath 'SKILL.md'))) { throw 'A normal update did not publish into the destination root.' }
if ((Get-Backups).Count -ne 0) { throw 'A clean update left the backup behind.' }
Write-Host 'PASS: a normal update stages, swaps, then cleans up'

Start-Case
$SkillName = 'fresh-install-test'
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,publish'
if (-not $result) { throw 'A fresh installation failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'fresh-install-test\SKILL.md'))) { throw 'A fresh install did not publish.' }
Write-Host 'PASS: a fresh installation skips the backup steps'

# 6) 链接安装：不拷文件，但同样先备份再建链
Reset-Fixture
$Link = $true
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'aside,link,cleanup'
if (-not $result) { throw 'A link installation failed.' }
Write-Host 'PASS: link installation backs up the old copy and cleans up'

# 7) 旧安装移走之后目标被别人重新建出来：不能把暂存塞进目标里面
Reset-Fixture
$Link = $false
Start-Case -Occupy
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*' 'Occupied destination'
Expect-Log 'copy,aside,publish,drop-staging,rollback'
if ((Get-Backups).Count -ne 1) { throw 'An occupied destination did not keep the backup.' }
if (Test-Path -LiteralPath (Join-Path $destPath 'SKILL.md')) { throw 'Staging was published inside the foreign destination.' }
$nested = @(Get-ChildItem -LiteralPath $destPath -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '.new-*' })
if ($nested.Count -ne 0) { throw 'The staged directory was left inside the foreign destination.' }
Write-Host 'PASS: a re-created destination is refused, backup kept'

if ([IO.Directory]::Exists($fixtureRoot)) { [IO.Directory]::Delete($fixtureRoot, $true) }
[IO.Directory]::Delete($stateRoot, $true)
