#!/usr/bin/env bash
#
# Install the agenttime skill into one or more agent skill directories.
#
#   ./install.sh                    auto-detect
#   ./install.sh -t ~/.dsh/skills   explicit skills directory
#   ./install.sh -l                 symlink instead of copy
#   ./install.sh -f                 overwrite without asking
#
# Nothing outside the skill directories is touched, and no network access
# is performed.

set -euo pipefail

SKILL_NAME="agenttime"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET=""
LINK=0
FORCE=0

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while getopts "ht:lf" opt; do
    case "$opt" in
        h) usage 0 ;;
        t) TARGET="$OPTARG" ;;
        l) LINK=1 ;;
        f) FORCE=1 ;;
        *) usage 1 ;;
    esac
done

if [[ ! -f "$SOURCE/SKILL.md" ]]; then
    echo "SKILL.md not found in $SOURCE. Run this script from inside the agenttime checkout." >&2
    exit 1
fi

skills_dirs() {
    if [[ -n "$TARGET" ]]; then
        printf '%s\n' "$TARGET"
        return
    fi

    # 先声明再赋值：macOS 自带 bash 3.2 对 `local arr=(...)` 的解析不稳
    local candidates
    candidates=(
        "$HOME/.dsh/skills"            # DeepSeek Harness
        "$HOME/.claude/skills"         # Claude Code
        "$HOME/.codex/skills"          # Codex
        "$HOME/.config/agents/skills"
    )

    local found
    found=()
    local c
    for c in "${candidates[@]}"; do
        if [[ -d "$c" ]]; then
            found+=("$c")
        fi
    done

    if [[ ${#found[@]} -eq 0 ]]; then
        # Parent exists but skills/ does not.
        local parents
        parents=(".dsh" ".claude" ".codex")
        local p
        for p in "${parents[@]}"; do
            if [[ -d "$HOME/$p" ]]; then
                found+=("$HOME/$p/skills")
                break
            fi
        done
    fi

    if [[ ${#found[@]} -eq 0 ]]; then
        found=("$HOME/.dsh/skills")
    fi

    printf '%s\n' "${found[@]}"
}

canonical_path() {
    local path="$1"
    if [[ -d "$path" ]]; then
        (cd -P -- "$path" && pwd -P)
    else
        local parent
        parent="$(dirname -- "$path")"
        local resolved_parent
        resolved_parent="$(canonical_path "$parent")" || return 1
        printf '%s/%s\n' "$resolved_parent" "$(basename -- "$path")"
    fi
}

move_aside() {
    # Move an existing install out of the way under a hidden sibling name.
    # Prints the new path; returns non-zero if the move fails.
    local path="$1" parent base candidate
    parent="$(dirname -- "$path")"
    base="$(basename -- "$path")"
    candidate="$parent/.old-$base-$$-$RANDOM"
    mv -- "$path" "$candidate" || return 1
    printf '%s\n' "$candidate"
}

# restore_backup 要改调用者的局部变量，bash 没有闭包，只好借这两个中继。
AT_DEST=""
AT_BACKUP=""
restore_backup() {
    [[ -n "$AT_BACKUP" ]] || return 0
    if [[ -e "$AT_DEST" || -L "$AT_DEST" ]]; then
        rm -rf -- "$AT_DEST" 2>/dev/null || true
    fi
    if mv -- "$AT_BACKUP" "$AT_DEST" 2>/dev/null; then
        echo "   old installation restored." >&2
        AT_BACKUP=""
        return 0
    fi
    echo "   rollback failed; the previous copy is still at: $AT_BACKUP" >&2
    return 1
}

install_skill() {
    local skills_dir="$1"
    local dest="$skills_dir/$SKILL_NAME"

    echo "-> $dest"

    local source_path dest_path
    source_path="$(canonical_path "$SOURCE")" || return 1
    dest_path="$(canonical_path "$dest")" || return 1
    if [[ "$source_path" == "$dest_path" || "$SOURCE" -ef "$dest" ]]; then
        echo "   already running from this installation; unchanged."
        return 0
    fi
    if [[ "$source_path/" == "$dest_path/"* || "$dest_path/" == "$source_path/"* ]]; then
        echo "   source and destination must not contain each other." >&2
        return 1
    fi

    if [[ -e "$dest" || -L "$dest" ]]; then
        if [[ $FORCE -eq 0 ]]; then
            # 非交互（stdin 不是终端）时 read 会失败，set -e 会直接退出。
            # 那种情况下按「不覆盖」处理，让 -f 成为明确的选择。
            if [[ ! -t 0 ]]; then
                echo "   already exists and stdin is not a terminal; skipping."
                echo "   re-run with -f to overwrite."
                return 1
            fi
            local answer="n"
            read -r -p "   already exists. overwrite? [y/N] " answer
            if [[ ! "$answer" =~ ^[Yy] ]]; then
                echo "   skipped."
                return 1
            fi
        fi
    fi

    mkdir -p "$skills_dir" || return 1

    # 发布顺序：先备好新的，再把旧的挪开，最后一步换名。旧安装在中途任何
    # 一刻失败都还能拿回来；换名成功之后才算新安装生效，此后绝不再回滚。
    local backup="" staging="" published=0

    if [[ $LINK -eq 1 ]]; then
        if [[ -e "$dest" || -L "$dest" ]]; then
            backup="$(move_aside "$dest")" || return 1
        fi
        if ln -s "$SOURCE" "$dest" 2>/dev/null; then
            echo "   linked."
            published=1
        else
            echo "   link failed, copying instead."
            rm -f -- "$dest" 2>/dev/null || true
        fi
    fi

    if [[ $published -eq 0 ]]; then
        staging="$skills_dir/.new-$SKILL_NAME-$$-$RANDOM"
        if ! mkdir -p "$staging"; then
            # 链接失败时旧安装已经挪走了，这里退出必须把它换回来
            echo "   cannot prepare a staging directory; attempting to restore the previous installation." >&2
            AT_DEST="$dest"; AT_BACKUP="$backup"; restore_backup
            return 1
        fi
        # Copy everything except VCS metadata, caches, and the README screenshots.
        # 显式判断管道状态：本函数是在 `if install_skill ...` 里调的，那个语境下
        # errexit 不生效，tar 失败也会一路走到「copied.」并返回成功。
        if ! (cd "$SOURCE" && tar -cf - \
                --exclude='.git' \
                --exclude='__pycache__' \
                --exclude='.agenttime' \
                --exclude='docs' \
                .) | (cd "$staging" && tar -xf -); then
            rm -rf -- "$staging"
            if [[ -n "$backup" ]]; then
                # 链接失败时旧目录已经挪走了，这句话不能再说「没被动过」
                echo "   copy failed; attempting to restore the previous installation." >&2
            else
                echo "   copy failed; the existing installation was not changed." >&2
            fi
            AT_DEST="$dest"; AT_BACKUP="$backup"; restore_backup
            return 1
        fi

        if [[ -z "$backup" && ( -e "$dest" || -L "$dest" ) ]]; then
            backup="$(move_aside "$dest")" || { rm -rf -- "$staging"; return 1; }
        fi

        if ! mv -- "$staging" "$dest"; then
            echo "   publish failed; restoring the previous installation." >&2
            rm -rf -- "$staging"
            AT_DEST="$dest"; AT_BACKUP="$backup"; restore_backup
            return 1
        fi
        echo "   copied."
    fi

    # 新安装已就位。清理旧副本失败只警告：不能为了删备份再回滚。
    if [[ -n "$backup" && ( -e "$backup" || -L "$backup" ) ]]; then
        if ! rm -rf -- "$backup"; then
            echo "   warning: the new install is in place, but the old copy could not be removed: $backup" >&2
        fi
    fi
    return 0
}

echo "agenttime installer"
echo "  source: $SOURCE"
echo

installed=0
while IFS= read -r dir; do
    [[ -z "$dir" ]] && continue
    if install_skill "$dir"; then
        installed=$((installed + 1))
    fi
done < <(skills_dirs)

echo
if [[ $installed -eq 0 ]]; then
    echo "Nothing installed."
    exit 1
fi

echo "Done ($installed location(s))."
echo
echo "Next steps:"
echo "  1. pip install zstandard        # only needed to read DeepSeek Harness logs"
echo "  2. open your agent and run the agenttime skill"
