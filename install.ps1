#Requires -Version 5.1
<#
.SYNOPSIS
    Install the agenttime skill into one or more agent skill directories.

.DESCRIPTION
    Looks for skills directories belonging to known agents under your home
    directory and copies agenttime into every one it finds. If none exists,
    it creates ~/.dsh/skills.

    Nothing outside the skill directories is touched, and no network access
    is performed.

.PARAMETER Target
    Install into this skills directory instead of auto-detecting.
    The skill itself goes into <Target>\agenttime.

.PARAMETER Link
    Create a symbolic link instead of copying, so edits to this checkout take
    effect immediately. Requires Developer Mode or an elevated shell on
    Windows; falls back to copying when linking fails.

.PARAMETER Force
    Overwrite an existing installation without asking.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Target "$HOME\.dsh\skills" -Force
#>
[CmdletBinding()]
param(
    [string]$Target,
    [switch]$Link,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$SkillName = 'agenttime'
$Source = $PSScriptRoot
if (-not $Source) { $Source = Split-Path -Parent $MyInvocation.MyCommand.Path }

if (-not (Test-Path (Join-Path $Source 'SKILL.md'))) {
    throw "SKILL.md not found in $Source. Run this script from inside the agenttime checkout."
}

function Get-SkillsDirs {
    if ($Target) { return @($Target) }

    # Skill directories used by agents that support them.
    $candidates = @(
        (Join-Path (Join-Path $HOME '.dsh') 'skills')      # DeepSeek Harness
        (Join-Path (Join-Path $HOME '.claude') 'skills')   # Claude Code
        (Join-Path (Join-Path $HOME '.codex') 'skills')    # Codex
        (Join-Path (Join-Path (Join-Path $HOME '.config') 'agents') 'skills')
    )

    # Only install where an agent is actually present.
    $found = @()
    foreach ($c in $candidates) {
        if (Test-Path $c) { $found += $c }
    }

    # Fall back to probing for the parent directory even if skills/ is absent.
    if ($found.Count -eq 0) {
        $parents = @('.dsh', '.claude', '.codex')
        foreach ($p in $parents) {
            $parent = Join-Path $HOME $p
            if (Test-Path $parent) {
                $found += (Join-Path $parent 'skills')
                break
            }
        }
    }

    if ($found.Count -eq 0) {
        # Nothing installed yet: assume the user wants DeepSeek Harness.
        $found = @((Join-Path (Join-Path $HOME '.dsh') 'skills'))
    }

    return $found
}

function Resolve-InstallPath {
    param([string]$Path, [int]$Depth = 0)
    if ($Depth -gt 64) { throw 'Too many links while resolving installation path.' }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { return $full }
    $parent = Split-Path -Parent $full
    if ($parent -and $parent -ne $full) {
        $full = Join-Path (Resolve-InstallPath -Path $parent -Depth ($Depth + 1)) (Split-Path -Leaf $full)
    }
    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $linkTarget = @($item.Target)[0]
            if (-not $linkTarget) { throw "Cannot resolve link: $full" }
            if (-not [IO.Path]::IsPathRooted($linkTarget)) {
                $linkTarget = Join-Path (Split-Path -Parent $full) $linkTarget
            }
            return Resolve-InstallPath -Path $linkTarget -Depth ($Depth + 1)
        }
    }
    return $full.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Move-Side {
    # Move an existing install out of the way under a hidden sibling name.
    # Returns the new path; throws if the move fails.
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    $base = Split-Path -Leaf $Path
    $candidate = Join-Path $parent ('.old-' + $base + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    Move-Item -LiteralPath $Path -Destination $candidate
    return $candidate
}

function Install-Skill {
    param([string]$SkillsDir)

    $dest = Join-Path $SkillsDir $SkillName
    Write-Host "-> $dest"

    # Validate physical paths before asking about overwriting or removing anything.
    $sourcePath = Resolve-InstallPath -Path $Source
    $destPath = Resolve-InstallPath -Path $dest
    if ($sourcePath -eq $destPath) {
        Write-Host '   already running from this installation; unchanged.'
        return $true
    }
    if ($sourcePath.StartsWith($destPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $destPath.StartsWith($sourcePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source and destination must not contain each other.'
    }

    if (Test-Path $dest) {
        if (-not $Force) {
            # 输入被重定向（CI、管道）时 Read-Host 会抛，按「不覆盖」处理
            if ([Console]::IsInputRedirected) {
                Write-Host '   already exists and input is redirected; skipping.'
                Write-Host '   re-run with -Force to overwrite.'
                return $false
            }
            $answer = Read-Host "   already exists. overwrite? [y/N]"
            if ($answer -notmatch '^[Yy]') {
                Write-Host '   skipped.'
                return $false
            }
        }
    }

    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null

    # 发布顺序：先备好新的，再把旧的挪开，最后一步换名。旧安装在中途任何
    # 一刻失败都还能拿回来；换名成功之后才算新安装生效，此后绝不再回滚。
    $backup = $null
    $staging = $null
    $published = $false

    try {
        if ($Link) {
            if (Test-Path -LiteralPath $dest) { $backup = Move-Side -Path $dest }
            try {
                New-Item -ItemType SymbolicLink -Path $dest -Target $Source | Out-Null
                Write-Host '   linked.'
                $published = $true
            } catch {
                Write-Host "   link failed ($($_.Exception.Message)), copying instead."
                # 半成品链接要清掉，否则搬新的过去时会撞上
                if (Test-Path -LiteralPath $dest) {
                    Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }

        if (-not $published) {
            $staging = Join-Path $SkillsDir ('.new-' + $SkillName + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
            Copy-Item -LiteralPath $Source -Destination $staging -Recurse -Force -Exclude @(
                '.git', '__pycache__', '.agenttime', 'docs'
            )

            # Copy-Item -Exclude does not reliably prune directories; clean up after it.
            foreach ($junk in @('.git', '__pycache__', '.agenttime', 'docs')) {
                $p = Join-Path $staging $junk
                if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force }
            }

            if (-not $backup -and (Test-Path -LiteralPath $dest)) { $backup = Move-Side -Path $dest }
            Move-Item -LiteralPath $staging -Destination $dest
            $staging = $null
            $published = $true
            Write-Host '   copied.'
        }
    } catch {
        if ($staging -and (Test-Path -LiteralPath $staging)) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($backup) {
            # 发布没成功：把旧安装换回来。换不回来就保留备份并报出位置，
            # 让用户自己搬——删掉备份才是真的丢了。
            if ((Test-Path -LiteralPath $dest) -and -not $published) {
                Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
            }
            try {
                Move-Item -LiteralPath $backup -Destination $dest
                $backup = $null
                Write-Host '   old installation restored.'
            } catch {
                Write-Host "   rollback failed; the previous copy is still at: $backup"
            }
        }
        throw
    }

    # 到这里新安装已经就位。清理旧副本失败只警告：不能为了删备份再回滚。
    if ($backup) {
        try {
            Remove-Item -LiteralPath $backup -Recurse -Force
        } catch {
            Write-Host "   warning: the new install is in place, but the old copy could not be removed: $backup"
        }
    }
    return $true
}

Write-Host "agenttime installer"
Write-Host "  source: $Source"
Write-Host ""

$installed = 0
foreach ($dir in (Get-SkillsDirs)) {
    if (Install-Skill -SkillsDir $dir) { $installed++ }
}

Write-Host ""
if ($installed -eq 0) {
    Write-Host "Nothing installed."
    exit 1
}

Write-Host "Done ($installed location(s))."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. pip install zstandard        # only needed to read DeepSeek Harness logs"
Write-Host "  2. open your agent and run the agenttime skill"
exit 0
