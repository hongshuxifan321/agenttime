#Requires -Version 5.1
# Read installer functions through the parser. All filesystem mutations are mocked.
$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Installer syntax errors.' }
foreach ($name in @('Get-SkillsDirs', 'Resolve-InstallPath', 'Move-Side', 'Install-Skill')) {
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

# Every filesystem mutation is recorded instead of performed. A guard test must
# leave the log empty; an install test asserts the exact order of steps.
$global:Log = @()
$global:FailAt = @()
$global:AliasPath = ''
$global:AliasTarget = ''

function Note { param([string]$Step) $global:Log += $Step }

function Copy-Item {
    [CmdletBinding()]
    param($LiteralPath, $Destination, $Exclude, [switch]$Recurse, [switch]$Force)
    Note 'copy'
    if ($global:FailAt -contains 'copy') { throw 'mock copy failure' }
}
function Move-Item {
    [CmdletBinding()]
    param($LiteralPath, $Destination)
    if ($LiteralPath -match '\.new-') { $step = 'publish' }
    elseif ($LiteralPath -match '\.old-') { $step = 'rollback' }
    else { $step = 'aside' }
    Note $step
    if ($global:FailAt -contains $step) { throw "mock $step failure" }
}
function Remove-Item {
    [CmdletBinding()]
    param($LiteralPath, [switch]$Recurse, [switch]$Force)
    if ($LiteralPath -match '\.old-') {
        Note 'cleanup'
        if ($global:FailAt -contains 'cleanup') { throw 'mock cleanup failure' }
    } elseif ($LiteralPath -match '\.new-') {
        Note 'drop-staging'
    } else {
        Note 'drop-dest'
    }
}
function New-Item {
    # 只关心建符号链接这一步；目录创建本来就会成功，不必记录。
    [CmdletBinding()]
    param($Path, $ItemType, $Target, [switch]$Force)
    if ($ItemType -eq 'SymbolicLink') { Note 'link' }
}
function Get-Item {
    [CmdletBinding()]
    param($LiteralPath, [switch]$Force)
    if ($global:AliasPath -and $LiteralPath -eq $global:AliasPath) {
        return [pscustomobject]@{ Attributes = [IO.FileAttributes]::ReparsePoint; Target = $global:AliasTarget }
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force
}

function Start-Case {
    param([string[]]$Failure = @())
    $global:Log = @()
    $global:FailAt = @($Failure | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
}
function Expect-Log {
    param([string]$Want)
    $actual = $global:Log -join ','
    if ($actual -ne $Want) { throw "unexpected mutation order: got '$actual', want '$Want'" }
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
$global:AliasPath = Join-Path $Source 'adapters'
$global:AliasTarget = $Source
$SkillName = 'adapters'
Start-Case
$result = Install-Skill -SkillsDir $Source
Expect-Log ''
if (-not $result) { throw 'Link alias to source was not skipped.' }
Write-Host 'PASS: link alias resolves to source before deletion'
$SkillName = 'nested-install-test'
Start-Case
Expect-Throw { Install-Skill -SkillsDir $global:AliasPath } '*must not contain*' 'Linked parent'
Expect-Log ''
Write-Host 'PASS: links in parent directories are resolved'

# ── 更新一个已存在的安装：失败时旧安装必须还在 ──
# 夹具放在源目录之外，否则「源和目标是包含关系」的保护会先拦住安装。
$global:AliasPath = ''
$fixtureBase = $env:AGENTTIME_TEST_TMP
if (-not $fixtureBase) { $fixtureBase = [IO.Path]::GetTempPath() }
$fixtureRoot = Join-Path $fixtureBase ('agenttime-install-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory((Join-Path $fixtureRoot 'existing')) | Out-Null
$keepFile = Join-Path $fixtureRoot 'existing\keep.txt'
[IO.File]::WriteAllText($keepFile, 'old install')

$Source = $originalSource
$SkillName = 'existing'
$Force = $true
$Link = $false

# 1) 复制失败：旧安装还没被碰过，也不该出现布局以外的动作
Start-Case -Failure 'copy'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*copy failure*' 'Copy failure'
Expect-Log 'copy'
if (-not [IO.File]::Exists($keepFile)) { throw 'A failed copy removed the existing installation.' }
Write-Host 'PASS: copy failure leaves the existing installation untouched'

# 2) 换名发布失败：旧安装被换回来
Start-Case -Failure 'publish'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*publish failure*' 'Publish failure'
Expect-Log 'copy,aside,publish,drop-dest,rollback'
if (-not [IO.File]::Exists($keepFile)) { throw 'A failed publish removed the existing installation.' }
Write-Host 'PASS: publish failure restores the previous installation'

# 3) 发布失败且回滚也失败：备份不能被删，异常要指出备份还在
Start-Case -Failure 'publish,rollback'
Expect-Throw { Install-Skill -SkillsDir $fixtureRoot } '*publish failure*' 'Publish plus rollback failure'
Expect-Log 'copy,aside,publish,drop-dest,rollback'
if ($global:Log -contains 'cleanup') { throw 'A failed rollback deleted the backup copy.' }
Write-Host 'PASS: failed rollback keeps the backup instead of deleting it'

# 4) 发布成功但清理备份失败：保留新安装，只警告，不回滚
Start-Case -Failure 'cleanup'
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,aside,publish,cleanup'
if (-not $result) { throw 'A failed backup cleanup was reported as a failed install.' }
if ($global:Log -contains 'rollback') { throw 'A failed backup cleanup rolled the new install back.' }
Write-Host 'PASS: failed backup cleanup keeps the new install and does not roll back'

# 5) 更新成功的正常顺序，以及全新安装（没有旧目录可搬）
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,aside,publish,cleanup'
if (-not $result) { throw 'Updating an existing installation failed.' }
Write-Host 'PASS: a normal update stages, swaps, then cleans up'
$SkillName = 'fresh-install-test'
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'copy,publish'
if (-not $result) { throw 'A fresh installation failed.' }
Write-Host 'PASS: a fresh installation skips the backup steps'

# 6) 链接安装走另一条分支：不拷文件，但同样要先把旧目录挪开、最后清理
$SkillName = 'existing'
[IO.Directory]::CreateDirectory((Join-Path $fixtureRoot 'existing')) | Out-Null
[IO.File]::WriteAllText($keepFile, 'old install')
$Link = $true
Start-Case
$result = Install-Skill -SkillsDir $fixtureRoot
Expect-Log 'aside,link,cleanup'
if (-not $result) { throw 'A link installation failed.' }
Write-Host 'PASS: link installation backs up the old copy and cleans up'

[IO.Directory]::Delete($fixtureRoot, $true)
