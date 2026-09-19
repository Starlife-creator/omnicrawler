#!/usr/bin/env bash
# =============================================================================
# uninstall-user.sh — OmniCrawler 用户级卸载（Linux，全程无需 root）
#
# 默认只删「安装脚本自己生成的」三样：
#   1. ~/.local/share/applications/omnicrawler.desktop
#   2. ~/.local/share/icons/hicolor/*/apps/omnicrawler.png
#   3. ~/.local/bin/omnicrawler（仅当它确实指向本 prefix）
#
# ★ 一个真陷阱：便携模式下**数据根就在应用目录内**（PORTABLE.flag /
#   data-mode.json / work/ data/ output/ logs/）。所以「删掉整个应用目录」会
#   连带删掉用户数据 —— 卸载因此**默认不删应用树**；要删必须显式
#   `--remove-prefix`，而检测到数据根在其中时还要再加 `--purge-data`。
#
# 绝不碰：~/.omnicrawler/（热密钥）、任何外部数据根、系统目录。
#
# 用法：
#   ./uninstall-user.sh                     # 只摘桌面条目 / 图标 / 软链
#   ./uninstall-user.sh --remove-prefix     # 连应用树一起删（有数据则拒绝）
#   ./uninstall-user.sh --purge-data --remove-prefix   # 连数据一起删（不可逆）
#   ./uninstall-user.sh --prefix ~/apps/oc
# =============================================================================
set -euo pipefail

PREFIX="${HOME}/.local/opt/OmniCrawler"
PREFIX_GIVEN=0
DO_REMOVE_PREFIX=0
PURGE_DATA=0
QUIET=0

info() { if [[ "$QUIET" -eq 0 ]]; then printf '%s\n' "$*"; fi; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
用法: uninstall-user.sh [选项]

  --prefix <目录>      安装前缀（默认读已装 .desktop 的 Exec=，否则 ~/.local/opt/OmniCrawler）
  --remove-prefix      同时删除应用树（检测到用户数据则拒绝，见 --purge-data）
  --purge-data         允许连同应用树内的用户数据一起删除（不可逆）
  --quiet              少打印
  -h, --help           显示本帮助
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) [[ $# -ge 2 ]] || die "--prefix 需要一个路径"; PREFIX="$2"; PREFIX_GIVEN=1; shift 2 ;;
    --remove-prefix) DO_REMOVE_PREFIX=1; shift ;;
    --purge-data) PURGE_DATA=1; DO_REMOVE_PREFIX=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) warn "未知参数：$1"; usage >&2; exit 2 ;;
  esac
done

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/omnicrawler.desktop"
ICON_DEST="$HOME/.local/share/icons/hicolor"
BIN_LINK="$HOME/.local/bin/omnicrawler"

# ---- 1. 摘桌面条目（顺带反推真实 prefix，自带描述、不依赖记忆） --------------
if [[ -f "$DESKTOP_FILE" ]]; then
  if [[ "$PREFIX_GIVEN" -eq 0 ]]; then
    exec_line="$(grep -m1 '^Exec=' "$DESKTOP_FILE" || true)"
    derived="${exec_line#Exec=}"; derived="${derived%/OmniCrawler}"
    if [[ -n "$derived" && "$derived" != "$exec_line" ]]; then PREFIX="$derived"; fi
  fi
  rm -f -- "$DESKTOP_FILE"
  info "已移除桌面条目：$DESKTOP_FILE"
else
  info "桌面条目不存在，跳过：$DESKTOP_FILE"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

# ---- 2. 摘 hicolor 图标（只删我们装的那个文件名） ---------------------------
icon_count=0
if [[ -d "$ICON_DEST" ]]; then
  while IFS= read -r -d '' icon; do
    rm -f -- "$icon"
    rmdir --ignore-fail-on-non-empty -- "$(dirname "$icon")" 2>/dev/null || true
    icon_count=$((icon_count + 1))
  done < <(find "$ICON_DEST" -type f -name 'omnicrawler.png' -print0)
fi
info "已移除 hicolor 图标：$icon_count 个"

# ---- 3. 摘 CLI 软链（仅当它指向本 prefix，避免误删用户自己的东西） ---------
if [[ -L "$BIN_LINK" ]]; then
  target="$(readlink "$BIN_LINK" || true)"
  case "$target" in
    "$PREFIX"/*) rm -f -- "$BIN_LINK"; info "已移除 CLI 软链：$BIN_LINK" ;;
    *) warn "跳过 $BIN_LINK：它指向 $target，不属于本 prefix" ;;
  esac
fi

# ---- 4. 应用树：默认保留；有数据时更需显式同意 ------------------------------
if [[ "$DO_REMOVE_PREFIX" -eq 0 ]]; then
  info ""
  info "应用树已保留：$PREFIX"
  info "如需删除，请显式重跑：uninstall-user.sh --remove-prefix"
  exit 0
fi

PREFIX="${PREFIX/#\~/$HOME}"
case "$PREFIX" in
  ""|"/"|"$HOME") die "拒绝在该路径上执行删除：'$PREFIX'" ;;
  *"/.."|*"/.") die "拒绝在含相对段的路径上删除：$PREFIX" ;;
esac
if [[ "$PREFIX" != /* ]]; then die "prefix 必须是绝对路径：$PREFIX"; fi
[[ -d "$PREFIX" ]] || { info "应用树不存在，无需删除：$PREFIX"; exit 0; }
if [[ ! -e "$PREFIX/OmniCrawler" && ! -d "$PREFIX/installer" ]]; then
  die "该目录不像 OmniCrawler 安装（缺 OmniCrawler / installer）：$PREFIX"
fi

# 数据根检测：便携模式下数据就在应用目录内
# ★ 必须写成 `if`，不能写 `[[ -e X ]] && data_hint=...`：后者在 **bash 3.2**
#   （macOS 自带的就是 3.2）下会让 `for` 循环返回非零状态，被 `set -e` 直接终止 ——
#   表现为**静默退出、rc=1、没有任何消息**，于是"拒绝删树"看起来是对的，
#   其实根本没走到拒绝那一段（偶然正确）。bash 4/5 不作此处理，所以只在 macOS 上暴露。
data_hint=""
for marker in PORTABLE.flag portable.flag data-mode.json; do
  if [[ -e "$PREFIX/$marker" ]]; then
    data_hint="$PREFIX"
    break
  fi
done
if [[ -n "$data_hint" && "$PURGE_DATA" -eq 0 ]]; then
  warn ""
  warn "检测到用户数据位于应用目录内：$PREFIX"
  warn "  其中可能包含 work/ data/ output/ logs/ 等你的抓取结果。"
  warn "  为防不可逆丢失，已拒绝删除整个应用树。"
  warn "  确要认真删掉全部数据，请显式重跑："
  warn "    uninstall-user.sh --prefix '$PREFIX' --purge-data"
  exit 1
fi

if [[ -n "$data_hint" ]]; then
  warn "★ --purge-data 已生效：将不可逆地删除 $PREFIX（含其中的用户数据）。"
fi
rm -rf -- "$PREFIX"
info "已删除应用树：$PREFIX"
info ""
info "卸载完成。（~/.omnicrawler/ 中的密钥未被触碰）"
