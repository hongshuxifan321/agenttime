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

    local candidates=(
        "$HOME/.dsh/skills"            # DeepSeek Harness
        "$HOME/.claude/skills"         # Claude Code
        "$HOME/.codex/skills"          # Codex
        "$HOME/.config/agents/skills"
    )

    local found=()
    local c
    for c in "${candidates[@]}"; do
        if [[ -d "$c" ]]; then
            found+=("$c")
        fi
    done

    if [[ ${#found[@]} -eq 0 ]]; then
        # Parent exists but skills/ does not.
        local parents=(".dsh" ".claude" ".codex")
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

install_skill() {
    local skills_dir="$1"
    local dest="$skills_dir/$SKILL_NAME"

    echo "-> $dest"

    if [[ -e "$dest" || -L "$dest" ]]; then
        if [[ $FORCE -eq 0 ]]; then
            read -r -p "   already exists. overwrite? [y/N] " answer
            if [[ ! "$answer" =~ ^[Yy] ]]; then
                echo "   skipped."
                return 1
            fi
        fi
        rm -rf -- "$dest"
    fi

    mkdir -p "$skills_dir"

    if [[ $LINK -eq 1 ]]; then
        if ln -s "$SOURCE" "$dest" 2>/dev/null; then
            echo "   linked."
            return 0
        fi
        echo "   link failed, copying instead."
    fi

    mkdir -p "$dest"
    # Copy everything except VCS metadata and caches.
    (cd "$SOURCE" && tar -cf - \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='.agenttime' \
        .) | (cd "$dest" && tar -xf -)

    echo "   copied."
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
