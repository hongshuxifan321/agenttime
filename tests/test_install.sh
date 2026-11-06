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
# FAIL_AT 用逗号分隔的精确名匹配：claim-publish 这种名字不能被当成 publish。
has_fail() { [[ ",$FAIL_AT," == *",$1,"* ]]; }

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
    if [[ "$step" != other ]] && has_fail "$step"; then
        return 1
    fi
    # 模拟「检查通过之后、换名之前」另一个进程占住目标：目标里已经有它自己
    # 的 SKILL.md，所以「目标里有 SKILL.md」不能当作发布成功的证据。
    if [[ "$step" != other ]] && has_fail "claim-$step"; then
        command mkdir -p "$dst"
        printf 'foreign\n' > "$dst/SKILL.md"
    fi
    command mv "$@"
}

rm() {
    local path="${*: -1}" exists=0
    if [[ -e "$path" || -L "$path" ]]; then exists=1; fi
    if [[ "$path" == */.old-* ]]; then
        if [[ $exists -eq 1 ]]; then note cleanup; fi
        if has_fail cleanup; then return 1; fi
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
    if [[ "$path" == */.new-* ]] && has_fail staging; then
        note mkdir-staging
        return 1
    fi
    command mkdir "$@"
}

tar() {
    if [[ " $* " == *" -cf "* ]]; then
        note copy
        if has_fail copy; then return 1; fi
        return 0
    fi
    # 解包这一步跑在暂存目录里：造一个标记文件，模拟真实拷贝的产物
    if has_fail claim-copy; then
        # 模拟复制期间另一个进程把目标目录建出来
        command mkdir -p "$OCCUPY_DEST"
        printf 'foreign\n' > "$OCCUPY_DEST/SKILL.md"
    fi
    if has_fail claim-copy-replace; then
        # 模拟复制期间另一个进程把旧目录移走、在原路径建一个新的
        command rm -rf -- "$OCCUPY_DEST"
        command mkdir -p "$OCCUPY_DEST"
        printf 'foreign\n' > "$OCCUPY_DEST/SKILL.md"
    fi
    printf 'staged\n' > SKILL.md
    return 0
}

ln() {
    note link
    if has_fail link; then return 1; fi
    # 造一个「链接工件」，后面的换名才有东西可搬
    command mkdir -p "${*: -1}"
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
OCCUPY_DEST="$SKILLS/existing"
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
expect_log 'aside,link,publish,cleanup'
LINK=0
echo 'PASS: link installation backs up the old copy and cleans up'

# 旧安装移走之后、换名之前目标被别人占住，且目标里有它自己的 SKILL.md：
# 换名必须失败，暂存不能被塞进目标里面，旧备份也不能被删。
reset_fixture
start_case 'claim-publish'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a re-created destination did not fail' >&2; exit 1
fi
expect_log 'copy,aside,publish,drop-staging'
if [[ -z "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]]; then
    echo 'the backup was lost when the destination reappeared' >&2; exit 1
fi
if [[ "$(cat "$SKILLS/existing/SKILL.md" 2>/dev/null)" != foreign ]]; then
    echo 'the foreign destination was modified' >&2; exit 1
fi
if [[ -n "$(find "$SKILLS/existing" -maxdepth 1 -name '.new-*' -print -quit 2>/dev/null)" ]]; then
    echo 'the staged directory was nested inside the foreign destination' >&2; exit 1
fi
echo 'PASS: a destination claimed just before the rename is refused, backup kept'

# 回滚同理：检查目标不存在之后、换名之前目标重现，备份不能被塞进目标里面，
# 也不能在失败的情况下宣称「old installation restored」。
reset_fixture
start_case 'publish,claim-rollback'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a re-created destination during rollback did not fail' >&2; exit 1
fi
expect_log 'copy,aside,publish,drop-staging,rollback'
if [[ -z "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]]; then
    echo 'the backup was lost when the destination reappeared during rollback' >&2; exit 1
fi
if [[ "$(cat "$SKILLS/existing/SKILL.md" 2>/dev/null)" != foreign ]]; then
    echo 'the foreign destination was modified during rollback' >&2; exit 1
fi
if [[ -n "$(find "$SKILLS/existing" -maxdepth 1 -name '.old-*' -print -quit 2>/dev/null)" ]]; then
    echo 'the backup was nested inside the foreign destination' >&2; exit 1
fi
echo 'PASS: a destination claimed just before the rollback rename keeps the backup'

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

# 没有可靠换名原语（既无 GNU mv -T 也无 perl）：直接拒绝安装，一个文件都不动
reset_fixture
saved_mv_t="$AT_MV_T"; saved_perl="$AT_PERL"
AT_MV_T=0; AT_PERL=0
start_case
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'an install without a no-replace rename was attempted' >&2; exit 1
fi
[[ -z "$(current_log)" ]] || { echo 'the refusal path touched the fixture' >&2; exit 1; }
[[ -f "$SKILLS/existing/keep.txt" ]] || { echo 'the refusal path lost the old install' >&2; exit 1; }
AT_MV_T="$saved_mv_t"; AT_PERL="$saved_perl"
echo 'PASS: no rename primitive refuses the install instead of moving anything'

# 全新安装：复制期间目标被别人建出来，不能当成旧安装接管后再删掉
command rm -rf -- "$SKILLS/existing" "$SKILLS"/.old-existing-* "$SKILLS"/.new-existing-*
start_case 'claim-copy'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a destination created during the copy did not fail' >&2; exit 1
fi
expect_log 'copy,drop-staging'
if [[ "$(cat "$SKILLS/existing/SKILL.md" 2>/dev/null)" != foreign ]]; then
    echo 'the foreign destination was removed or taken over' >&2; exit 1
fi
if [[ -n "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]]; then
    echo 'a foreign directory was adopted as a backup' >&2; exit 1
fi
echo 'PASS: a destination created during the copy is refused, not adopted'

# 目标原本存在，但复制期间被别的进程「移走 + 原位重建」：身份对不上，
# 不能把后来出现的目录当成旧安装备份再删掉
reset_fixture
start_case 'claim-copy-replace'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a replaced destination was adopted as the backup' >&2; exit 1
fi
expect_log 'copy,aside,rollback,drop-staging'
if [[ "$(cat "$SKILLS/existing/SKILL.md" 2>/dev/null)" != foreign ]]; then
    echo 'the replaced destination was removed or modified' >&2; exit 1
fi
if [[ -n "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]]; then
    echo 'a directory this run did not create was left as a backup' >&2; exit 1
fi
echo 'PASS: a destination replaced during the copy is not adopted'

# 链接模式：换名失败时目标被别人占住，只清理本次的暂存链接
reset_fixture
LINK=1
start_case 'claim-publish'
if install_skill "$SKILLS" > /dev/null 2>&1; then
    echo 'a link publish into an occupied destination did not fail' >&2; exit 1
fi
expect_log 'aside,link,publish,drop-staging'
if [[ -z "$(find "$SKILLS" -maxdepth 1 -name '.old-existing-*' -print -quit)" ]]; then
    echo 'the backup was lost when the link publish failed' >&2; exit 1
fi
if [[ "$(cat "$SKILLS/existing/SKILL.md" 2>/dev/null)" != foreign ]]; then
    echo 'the foreign destination was modified by the failed link' >&2; exit 1
fi
if [[ -n "$(find "$SKILLS/existing" -maxdepth 1 -name '.new-*' -print -quit 2>/dev/null)" ]]; then
    echo 'the staging link was left inside the foreign destination' >&2; exit 1
fi
LINK=0
echo 'PASS: a failed link removes only its own staging link'
