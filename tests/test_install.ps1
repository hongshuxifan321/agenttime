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
foreach ($name in @('Get-SkillsDirs', 'Resolve-InstallPath', 'Move-Dir', 'Move-Side', 'Get-DirectoryId', 'Take-Backup', 'Install-Skill')) {
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
$foreignDest = ''   # 复制过程中要抢占的目标（由 Copy-Item 的 mock 使用）

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
    param([string[]]$Failure = @(), [switch]$Occupy, [switch]$OccupyCopy, [switch]$OccupyLink, [switch]$OccupyReplace)
    [IO.File]::WriteAllText($failFile, ($Failure -join ','))
    [IO.File]::WriteAllText($logFile, '')
    $mode = ''
    if ($Occupy -and $OccupyCopy) { $mode = 'both' }
    elseif ($Occupy) { $mode = 'publish' }
    elseif ($OccupyCopy) { $mode = 'copy' }
    elseif ($OccupyLink) { $mode = 'link' }
    elseif ($OccupyReplace) { $mode = 'replace' }
    [IO.File]::WriteAllText($occupyFile, $mode)
}
function Get-OccupyMode {
    ([IO.File]::ReadAllText($occupyFile)).Trim()
}
function New-ForeignDest {
    param([string]$Path)
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    [IO.File]::WriteAllText((Join-Path $Path 'foreign-keep.txt'), 'foreign')
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
    # 模拟复制过程中另一个进程把目标建出来 / 移走再原位重建
    $mode = Get-OccupyMode
    if ($script:foreignDest -and ($mode -eq 'copy' -or $mode -eq 'both')) {
        New-ForeignDest -Path $script:foreignDest
    }
    if ($script:foreignDest -and $mode -eq 'replace') {
        if ([IO.Directory]::Exists($script:foreignDest)) { [IO.Directory]::Delete($script:foreignDest, $true) }
        New-ForeignDest -Path $script:foreignDest
    }
}

function Move-Dir {
    [CmdletBinding()]
    param([string]$From, [string]$To)
    if ($From -match '\\\.new-') { $step = 'publish' }
    elseif ($From -match '\\\.old-') { $step = 'rollback' }
    else { $step = 'aside' }
    Note $step
    if ((Get-FailList) -contains $step) { throw "mock $step failure" }
    $mode = Get-OccupyMode
    if ($step -eq 'publish' -and ($mode -eq 'publish' -or $mode -eq 'both')) {
        New-ForeignDest -Path $To   # 另一个进程抢先建好了目标
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
    if ($ItemType -eq 'SymbolicLink') {
        Note 'link'
        if ((Get-FailList) -contains 'link') { throw 'mock link failure (A required privilege is not held by the client)' }
        # 模拟「建链之前另一个进程占住了目标」：目标被建出来，New-Item 因
        # 路径已存在而失败——这正是 review 里的复现步骤。
        if ($script:foreignDest -and (Get-OccupyMode) -eq 'link') {
            New-ForeignDest -Path $script:foreignDest
            throw 'mock New-Item: an item with the specified name already exists'
        }
        if ([IO.Directory]::Exists($Path) -or [IO.File]::Exists($Path)) {
            throw 'mock New-Item: an item with the specified name already exists'
        }
        # 用一个空目录代表链接工件，后续的换名才有东西可搬
        [IO.Directory]::CreateDirectory($Path) | Out-Null
    }
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

# 0) 身份读取本身要对：结构体布局、换名不变、新建的不同
#    （FILETIME 曾按 long 声明，结构体变 56 字节、卷号和文件 ID 全部错位）
$probeA = Join-Path $fixtureRoot 'id-probe-a'
[IO.Directory]::CreateDirectory($probeA) | Out-Null
$idA = Get-DirectoryId -Path $probeA
if (-not $idA) { throw 'Directory identity is unavailable.' }
if (-not [AgentTimeFileId]::ShapeIsValid()) { throw 'BY_HANDLE_FILE_INFORMATION has the wrong size or field offsets.' }
[IO.Directory]::Move($probeA, (Join-Path $fixtureRoot 'id-probe-b'))
if ((Get-DirectoryId -Path (Join-Path $fixtureRoot 'id-probe-b')) -ne $idA) {
    throw 'Directory identity changed across a rename.'
}
[IO.Directory]::CreateDirectory($probeA) | Out-Null
if ((Get-DirectoryId -Path $probeA) -eq $idA) { throw 'A new directory got the same identity.' }
Write-Host 'PASS: directory identity is read with the native layout and follows renames'

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

# 6) 链接安装：不拷文件，但同样先建在暂存名上，再换名发布
Reset-Fixture
$Link = $true
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'aside,link,publish,cleanup'
if (-not $result) { throw 'A link installation failed.' }
if ((Get-Backups).Count -ne 0) { throw 'A link installation left the backup behind.' }
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

# 8) 链接模式：建链之前目标被别人占住（New-Item 因路径已存在而失败），
#    只清理本次的暂存链接；不许删掉别人刚建的目录
Reset-Fixture
$Link = $true
$foreignDest = $destPath
Start-Case -OccupyLink
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*created by another process*' 'Occupied destination during link'
Expect-Log 'aside,link,rollback'
$foreignDest = ''
if ((Get-Backups).Count -ne 1) { throw 'A failed link attempt lost the backup.' }
if (-not (Test-Path -LiteralPath (Join-Path $destPath 'foreign-keep.txt'))) {
    throw 'A failed link attempt deleted a directory it did not create.'
}
if (Test-Path -LiteralPath (Join-Path $destPath 'SKILL.md')) { throw 'The staging link was published inside the foreign destination.' }
$nested = @(Get-ChildItem -LiteralPath $destPath -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '.new-*' })
if ($nested.Count -ne 0) { throw 'A staging link was left inside the foreign destination.' }
Write-Host 'PASS: a failed link never deletes a directory it did not create'

# 9) 全新安装：目标在复制过程中被别人建出来，不能把它当成旧安装接管再删掉
if ([IO.Directory]::Exists($fixtureRoot)) { [IO.Directory]::Delete($fixtureRoot, $true) }
[IO.Directory]::CreateDirectory($fixtureRoot) | Out-Null
Start-Case -OccupyCopy
$SkillName = 'existing'
$Source = $originalSource
$Force = $true
$Link = $false
$foreignDest = $destPath
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*created by another process*' 'Destination appeared during copy'
$foreignDest = ''
Expect-Log 'copy,drop-staging'
if (-not (Test-Path -LiteralPath (Join-Path $destPath 'foreign-keep.txt'))) {
    throw 'A foreign directory created during the copy was removed or adopted.'
}
if (Test-Path -LiteralPath (Join-Path $destPath 'SKILL.md')) { throw 'The staging copy was published into the foreign destination.' }
if ((Get-Backups).Count -ne 0) { throw 'A foreign directory was taken over as a backup.' }
Write-Host 'PASS: a destination created during the copy is refused, not adopted'

# 10) 目标原本存在，但复制期间被别的进程移走并原位重建：身份对不上，
#     不能把后来出现的目录当成旧安装备份再删掉
Reset-Fixture
$Link = $false
$foreignDest = $destPath
Start-Case -OccupyReplace
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*replaced by another process*' 'Replaced destination'
$foreignDest = ''
Expect-Log 'copy,aside,rollback,drop-staging'
if ((Get-Backups).Count -ne 0) { throw 'A replaced destination was adopted as a backup.' }
if (-not (Test-Path -LiteralPath (Join-Path $destPath 'foreign-keep.txt'))) {
    throw 'The replaced destination was removed or modified.'
}
$nested = @(Get-ChildItem -LiteralPath $destPath -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '.new-*' -or $_.Name -like '.old-*' })
if ($nested.Count -ne 0) { throw 'Staging or backup leftovers were left inside the replaced destination.' }
Write-Host 'PASS: a destination replaced during the copy is not adopted'

if ([IO.Directory]::Exists($fixtureRoot)) { [IO.Directory]::Delete($fixtureRoot, $true) }
[IO.Directory]::Delete($stateRoot, $true)
