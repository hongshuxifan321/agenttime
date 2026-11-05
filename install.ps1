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
        (Join-Path $HOME '.dsh' 'skills')      # DeepSeek Harness
        (Join-Path $HOME '.claude' 'skills')   # Claude Code
        (Join-Path $HOME '.codex' 'skills')    # Codex
        (Join-Path $HOME '.config' 'agents' 'skills')
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
        $found = @((Join-Path $HOME '.dsh' 'skills'))
    }

    return $found
}

function Install-Skill {
    param([string]$SkillsDir)

    $dest = Join-Path $SkillsDir $SkillName
    Write-Host "-> $dest"

    if (Test-Path $dest) {
        if (-not $Force) {
            $answer = Read-Host "   already exists. overwrite? [y/N]"
            if ($answer -notmatch '^[Yy]') {
                Write-Host '   skipped.'
                return $false
            }
        }
        Remove-Item -LiteralPath $dest -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null

    if ($Link) {
        try {
            New-Item -ItemType SymbolicLink -Path $dest -Target $Source | Out-Null
            Write-Host '   linked.'
            return $true
        } catch {
            Write-Host "   link failed ($($_.Exception.Message)), copying instead."
        }
    }

    Copy-Item -LiteralPath $Source -Destination $dest -Recurse -Force -Exclude @(
        '.git', '__pycache__', '.agenttime'
    )

    # Copy-Item -Exclude does not reliably prune directories; clean up after it.
    foreach ($junk in @('.git', '__pycache__', '.agenttime')) {
        $p = Join-Path $dest $junk
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force }
    }

    Write-Host '   copied.'
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
