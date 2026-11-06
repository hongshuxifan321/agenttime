#!/usr/bin/env bash
# Only execute the path guards and the update-failure paths; every mutation is
# intercepted and recorded, and nothing outside a temporary fixture is touched.
set -uo pipefail
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SKILL_NAME="$(basename "$SOURCE")"
FORCE=1
LINK=0

OPS="$(mktemp)"
FAIL_AT=""
note() { printf '%s\n' "$1" >> "$OPS"; }
current_log() { tr '\n' ',' < "$OPS" | sed 's/,$//'; }

mv() {
    local src="${*: -2:1}" dst="${*: -1}" step="other"
    if [[ "$src" == */.new-* ]]; then
        step=publish
    elif [[ "$dst" == */.old-* ]]; then
        step=aside
    elif [[ "$src" == */.old-* ]]; then
        step=rollback
    fi
    note "$step"
    if [[ "$step" != other && "$FAIL_AT" == *"$step"* ]]; then
        return 1
    fi
    command mv "$@"
}

rm() {
    local path="${*: -1}" exists=0
    if [[ -e "$path" || -L "$path" ]]; then exists=1; fi
    if [[ "$path" == */.old-* ]]; then
        if [[ $exists -eq 1 ]]; then note cleanup; fi
        if [[ "$FAIL_AT" == *cleanup* ]]; then return 1; fi
        command rm -rf -- "$path"
        return 0
    fi
    if [[ $exists -eq 1 ]]; then
        if [[ "$path" == */.new-* ]]; then
            note drop-staging
        else
            note drop-dest
        fi
    fi
    command rm -rf -- "$path"
}

mkdir() {
    local path="${*: -1}"
    if [[ "$path" == */.new-* && "$FAIL_AT" == *staging* ]]; then
        note mkdir-staging
        return 1
    fi
    command mkdir "$@"
}

tar() {
    if [[ " $* " == *" -cf "* ]]; then
        note copy
        if [[ "$FAIL_AT" == *copy* ]]; then return 1; fi
    fi
    return 0
}

ln() {
    note link
    if [[ "$FAIL_AT" == *link* ]]; then return 1; fi
    return 0
}

# Load function definitions, excluding the installer's top-level entry point.
source <(sed -n '/^canonical_path()/,/^echo "agenttime installer"/p' "$SOURCE/install.sh" | sed '$d')

expect_log() {
    local got
    got="$(current_log)"
    if [[ "$got" != "$1" ]]; then
        echo "unexpected mutation order: got [$got], want [$1]" >&2
        exit 1
    fi
}
start_case() { : > "$OPS"; FAIL_AT="${1:-}"; }

# ── 路径保护 ──
original_source="$SOURCE"
: > "$OPS"
install_skill "$(dirname "$SOURCE")" > /dev/null
[[ -z "$(current_log)" ]] || { echo 'self-installation touched the filesystem' >&2; exit 1; }
echo 'PASS: self-installation leaves source unchanged'

SOURCE="$original_source/adapters"
LOG=""
if install_skill "$(dirname "$original_source")" > /dev/null 2>&1; then
    echo 'Ancestor destination was accepted' >&2; exit 1
fi
[[ -z "$(current_log)" ]] || { echo 'ancestor rejection touched the filesystem' >&2; exit 1; }
echo 'PASS: destination cannot contain source'

SOURCE="$original_source"
SKILL_NAME='nested-install-test'
LOG=""
if install_skill "$SOURCE/nonexistent-parent" > /dev/null 2>&1; then
    echo 'Descendant destination was accepted' >&2; exit 1
fi
[[ -z "$(current_log)" ]] || { echo 'descendant rejection touched the filesystem' >&2; exit 1; }
echo 'PASS: source cannot contain destination, including nonexistent parents'

# ── 更新一个已存在的安装：失败时旧安装必须还在 ──
WORK="$(mktemp -d)"
cleanup_work() { command rm -rf -- "$WORK"; command rm -f -- "$OPS"; }
trap cleanup_work EXIT
SKILLS="$WORK/skills"
mkdir -p "$SKILLS/existing"
printf 'old install\n' > "$SKILLS/existing/keep.txt"
SKILL_NAME='existing'
SOURCE="$original_source"
reset_fixture() {
    SKILL_NAME='existing'
    command rm -rf -- "$SKILLS/existing" "$SKILLS"/.old-existing-* "$SKILLS"/.new-existing-*
    mkdir -p "$SKILLS/existing"
    printf 'old install\n' > "$SKILLS/existing/keep.txt"
}
reset_fixture

start_case copy
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'copy failure did not fail' >&2; exit 1
fi
expect_log 'copy,drop-staging'
[[ -f "$SKILLS/existing/keep.txt" ]] || { echo 'a failed copy removed the old install' >&2; exit 1; }
echo 'PASS: copy failure leaves the existing installation untouched'

start_case publish
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'publish failure did not fail' >&2; exit 1
fi
expect_log 'copy,aside,publish,drop-staging,rollback'
[[ -f "$SKILLS/existing/keep.txt" ]] || { echo 'a failed publish did not restore the old install' >&2; exit 1; }
echo 'PASS: publish failure restores the previous installation'

start_case 'publish,rollback'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'publish and rollback failure did not fail' >&2; exit 1
fi
expect_log 'copy,aside,publish,drop-staging,rollback'
[[ "$(current_log)" != *cleanup* ]] || { echo 'a failed rollback deleted the backup' >&2; exit 1; }
[[ -n "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]] \
    || { echo 'the backup copy was not kept after a failed rollback' >&2; exit 1; }
echo 'PASS: failed rollback keeps the backup instead of deleting it'

reset_fixture
start_case cleanup
if ! install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a failed backup cleanup was reported as a failed install' >&2; exit 1
fi
expect_log 'copy,aside,publish,cleanup'
[[ -n "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]] \
    || { echo 'a failed cleanup reported success but the backup is gone' >&2; exit 1; }
echo 'PASS: failed backup cleanup keeps the new install and does not roll back'

reset_fixture
start_case
if ! install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'updating an existing installation failed' >&2; exit 1
fi
expect_log 'copy,aside,publish,cleanup'
[[ -z "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]] \
    || { echo 'a clean update left the backup behind' >&2; exit 1; }
echo 'PASS: a normal update stages, swaps, then cleans up'

SKILL_NAME='fresh-install-test'
start_case
if ! install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a fresh installation failed' >&2; exit 1
fi
expect_log 'copy,publish'
echo 'PASS: a fresh installation skips the backup steps'

reset_fixture
LINK=1
start_case
if ! install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a link installation failed' >&2; exit 1
fi
expect_log 'aside,link,cleanup'
LINK=0
echo 'PASS: link installation backs up the old copy and cleans up'

# 链接失败后转拷贝，如果连暂存目录都建不出来，旧安装必须换回来
reset_fixture
LINK=1
start_case 'link,staging'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a failed staging mkdir did not fail' >&2; exit 1
fi
expect_log 'aside,link,mkdir-staging,rollback'
[[ -f "$SKILLS/existing/keep.txt" ]] || { echo 'a failed staging mkdir lost the old install' >&2; exit 1; }
LINK=0
echo 'PASS: failed staging setup restores the previous installation'
