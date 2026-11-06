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
    Windows; falls back to copying when linking is not permitted. If the
    destination is claimed by another process while linking, the install fails
    and leaves that directory and the previous copy alone.

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

function Move-Dir {
    # 目录换名。目标已存在时 Directory.Move 会拒绝——换成 Move-Item 的话，
    # 目标若在检查之后被别人重新建出来，源目录会被挪进目标里面，装完看着
    # 像成功，实际数据在 $dest\.new-xxx 里。
    param([string]$From, [string]$To)
    [IO.Directory]::Move($From, $To)
}

function Move-Side {
    # Move an existing install out of the way under a hidden sibling name.
    # Returns the new path; throws if the move fails.
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    $base = Split-Path -Leaf $Path
    $candidate = Join-Path $parent ('.old-' + $base + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    Move-Dir -From $Path -To $candidate
    return $candidate
}

$script:FileIdState = ''
function Get-DirectoryId {
    # 目录身份：卷序列号 + 文件 ID。换名不改它；被别的进程「移走再建一个新的」
    # 就会变。取不到（或无法编译 P/Invoke）时返回 $null，调用方按「无法确认」处理。
    param([string]$Path)
    if (-not $script:FileIdState) {
        try {
            Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AgentTimeFileId {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFileW(string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition, uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(IntPtr hFile, out BY_HANDLE_FILE_INFORMATION info);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION {
        // FILETIME 是 8 字节但要 4 字节对齐：必须写成两个 uint。写成 long 会
        // 把后面的字段整体推到偏移 8，卷号和文件 ID 全部读错（结构体 56 字节）。
        public uint dwFileAttributes;
        public uint ftCreationTimeLow;
        public uint ftCreationTimeHigh;
        public uint ftLastAccessTimeLow;
        public uint ftLastAccessTimeHigh;
        public uint ftLastWriteTimeLow;
        public uint ftLastWriteTimeHigh;
        public uint dwVolumeSerialNumber;
        public uint nFileSizeHigh;
        public uint nFileSizeLow;
        public uint nNumberOfLinks;
        public uint nFileIndexHigh;
        public uint nFileIndexLow;
    }

    public static bool ShapeIsValid() {
        return Marshal.SizeOf(typeof(BY_HANDLE_FILE_INFORMATION)) == 52
            && Marshal.OffsetOf(typeof(BY_HANDLE_FILE_INFORMATION), "dwVolumeSerialNumber").ToInt32() == 28
            && Marshal.OffsetOf(typeof(BY_HANDLE_FILE_INFORMATION), "nFileIndexHigh").ToInt32() == 44
            && Marshal.OffsetOf(typeof(BY_HANDLE_FILE_INFORMATION), "nFileIndexLow").ToInt32() == 48;
    }

    public static string Of(string path) {
        if (!ShapeIsValid()) { return null; }
        IntPtr handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (handle == IntPtr.Zero || handle == new IntPtr(-1)) { return null; }
        try {
            BY_HANDLE_FILE_INFORMATION info;
            if (!GetFileInformationByHandle(handle, out info)) { return null; }
            ulong index = ((ulong)info.nFileIndexHigh << 32) | info.nFileIndexLow;
            return info.dwVolumeSerialNumber.ToString() + ":" + index.ToString();
        } finally {
            CloseHandle(handle);
        }
    }
}
'@
            $script:FileIdState = 'ready'
        } catch {
            $script:FileIdState = 'failed'
        }
    }
    if ($script:FileIdState -ne 'ready') { return $null }
    try {
        $id = [AgentTimeFileId]::Of($Path)
        if (-not $id) { return $null }
        # 文件 ID 会被文件系统回收复用（NTFS 会重新分配释放的 MFT 记录），
        # 所以创建时间必须一起算身份；换名两者都不变。
        $created = (Get-Item -LiteralPath $Path -Force).CreationTimeUtc.Ticks
        return ($id + '|' + $created)
    } catch {
        return $null
    }
}

$script:BackupVerified = $false
function Take-Backup {
    # 把旧安装挪成备份，并核对挪走的确实是安装开始时看到的那一份目录。
    # 「开始时存在」不等于「现在还是它」：别的进程可以在中间移走旧目录、在
    # 原路径建个新的。身份对不上就不接管——能放回就放回，放不回就保留并报路径。
    param([string]$Path, [string]$ExpectedId)
    $script:BackupVerified = $false
    $moved = Move-Side -Path $Path
    if (-not $ExpectedId) { return $moved }
    $movedId = Get-DirectoryId -Path $moved
    if ($movedId -ne $ExpectedId) {
        $kept = $moved
        try {
            Move-Dir -From $moved -To $Path
            $kept = $null
        } catch { }
        if ($kept) {
            Write-Host "   publish failed; the destination was replaced by another process, and the directory found there is kept at: $kept"
        } else {
            Write-Host '   publish failed; the destination was replaced by another process.'
        }
        throw "The destination was replaced by another process: $Path"
    }
    $script:BackupVerified = $true
    return $moved
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

    # 记下「确认覆盖时目标是不是旧安装」以及它的目录身份。后面再看到目标存在
    # 时，只有身份仍然对得上才算旧安装；中途冒出来的一律当别人的目录。
    $destExisted = Test-Path -LiteralPath $dest
    $destId = $null
    if ($destExisted) { $destId = Get-DirectoryId -Path $dest }
    if ($destExisted) {
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
    $script:BackupVerified = $false

    try {
        if ($Link) {
            if ($destExisted) { $backup = Take-Backup -Path $dest -ExpectedId $destId }
            # 链接先建在唯一暂存名上，再用拒绝覆盖的换名发布。这样失败时只可能
            # 留下本次的暂存链接，$dest 里是谁的东西都不归我们删除——之前这里是
            # 「建链失败就把 $dest 整个删掉再来一遍」，别人刚建好的目录因此被删。
            $linkStaging = Join-Path $SkillsDir ('.new-' + $SkillName + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
            $linkError = $null
            try {
                New-Item -ItemType SymbolicLink -Path $linkStaging -Target $Source | Out-Null
                Move-Dir -From $linkStaging -To $dest
                Write-Host '   linked.'
                $published = $true
            } catch {
                $linkError = $_.Exception.Message
            }
            if ($linkError) {
                # 只清理本次在唯一暂存名上建的链接（Directory.Delete 只删链接本身，
                # 不会顺着 reparse point 进到目标目录里）
                if (Test-Path -LiteralPath $linkStaging) {
                    try {
                        [IO.Directory]::Delete($linkStaging)
                    } catch {
                        Remove-Item -LiteralPath $linkStaging -Force -ErrorAction SilentlyContinue
                    }
                }
                if (Test-Path -LiteralPath $dest) {
                    throw "link failed ($linkError) and the destination was created by another process: $dest"
                }
                Write-Host "   link failed ($linkError), copying instead."
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

            # 旧安装在确认覆盖时还是它自己，才可以挪去当备份；一开始就没有的
            # 目标现在出现，只可能是别的进程建的，一律不接管、不删除。
            if (Test-Path -LiteralPath $dest) {
                if (-not $destExisted) {
                    throw "The destination was created by another process: $dest"
                }
                if (-not $backup) { $backup = Take-Backup -Path $dest -ExpectedId $destId }
            }
            if (Test-Path -LiteralPath $dest) {
                throw "The destination was created by another process: $dest"
            }
            Move-Dir -From $staging -To $dest
            $staging = $null
            $published = $true
            Write-Host '   copied.'
        }
    } catch {
        if ($staging -and (Test-Path -LiteralPath $staging)) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($backup) {
            # 发布没成功：把旧安装换回来。目标若已被别的进程占用，Move-Dir 会拒绝，
            # 备份原样保留并报出路径——不能为了「恢复」再去删别人的目录。
            try {
                Move-Dir -From $backup -To $dest
                $backup = $null
                Write-Host '   old installation restored.'
            } catch {
                Write-Host "   rollback failed; the previous copy is still at: $backup"
            }
        }
        throw
    }

    # 到这里新安装已经就位。清理旧副本失败只警告：不能为了删备份再回滚。
    # 身份没核对过的备份一律不删——删错就找不回来了。
    if ($backup) {
        if (-not $script:BackupVerified) {
            Write-Host "   warning: the new install is in place, but the old copy could not be verified; kept at: $backup"
        } else {
            try {
                Remove-Item -LiteralPath $backup -Recurse -Force
            } catch {
                Write-Host "   warning: the new install is in place, but the old copy could not be removed: $backup"
            }
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
