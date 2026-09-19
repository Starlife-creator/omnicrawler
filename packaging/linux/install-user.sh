#!/usr/bin/env bash
# =============================================================================
# install-user.sh — OmniCrawler 用户级安装（Linux，全程无需 root）
#
# 为什么是「搬进 prefix」而不是「就地注册」：
#   若 .desktop 的 Exec= 指向用户解压目录（多半是 ~/Downloads），用户清理下载
#   目录后应用即失效、且留下死链。所以安装 = 把整棵应用树搬进 prefix，再按
#   **最终绝对路径**生成 .desktop。
#
# 只碰 $HOME 下的四处：
#   1. <prefix>/                         应用树（默认 ~/.local/opt/OmniCrawler）
#   2. ~/.local/share/applications/omnicrawler.desktop
#   3. ~/.local/share/icons/hicolor/<N>x<N>/apps/omnicrawler.png   （7 个尺寸）
#   4. ~/.local/bin/omnicrawler           指向 <prefix>/omnicrawler 的软链
#
# 不做：不 sudo、不 apt、不改任何系统目录、不碰 ~/.omnicrawler/（热密钥）。
#
# 用法：
#   ./install-user.sh                         # 默认 prefix，cp -a
#   ./install-user.sh --prefix ~/apps/oc      # 自定义 prefix
#   ./install-user.sh --move                  # 装完询问是否删除源目录（默认不删）
#   ./install-user.sh --no-desktop-database   # 跳过 update-desktop-database（CI 冒烟）
#   ./install-user.sh --quiet
# =============================================================================
set -euo pipefail

PREFIX="${HOME}/.local/opt/OmniCrawler"
DO_MOVE=0
QUIET=0
RUN_DESKTOP_DB=1

info() { if [[ "$QUIET" -eq 0 ]]; then printf '%s\n' "$*"; fi; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
用法: install-user.sh [选项]

  --prefix <目录>          安装前缀（默认 ~/.local/opt/OmniCrawler）
  --move                   安装成功后询问是否删除源目录（确认后才删）
  --no-desktop-database    不调用 update-desktop-database
  --quiet                  少打印
  -h, --help               显示本帮助
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) [[ $# -ge 2 ]] || die "--prefix 需要一个路径"; PREFIX="$2"; shift 2 ;;
    --move) DO_MOVE=1; shift ;;
    --no-desktop-database) RUN_DESKTOP_DB=0; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) warn "未知参数：$1"; usage >&2; exit 2 ;;
  esac
done

# ---- 路径规范化 -------------------------------------------------------------
# `--prefix "~/x"` 不会被 shell 展开，这里补一次。
PREFIX="${PREFIX/#\~/$HOME}"
[[ -n "$PREFIX" ]] || die "--prefix 不能为空"
# ★ 归一化必须**显式失败**，不能用"算不出来就落空串"：一旦 "$(cd … && pwd)" 落空，
#   PREFIX 会退化成 "/<basename>"（例如 /prefix）——那就把整棵应用树写到了一个
#   谁也不认识的位置（实测真发生过）。故：先建父目录 → 解析 → 空即报错。
case "$PREFIX" in
  /*|[A-Za-z]:/*|[A-Za-z]:\\*) ;;   # POSIX 绝对 / 盘符绝对
  *) PREFIX="$PWD/$PREFIX" ;;
esac
_prefix_parent="$(dirname "$PREFIX")"
mkdir -p "$_prefix_parent" || die "无法创建前缀父目录：$_prefix_parent"
_prefix_parent="$(cd "$_prefix_parent" && pwd)"
[[ -n "$_prefix_parent" && "$_prefix_parent" != "/" ]] \
  || die "无法解析前缀父目录（拒绝在根目录下安装）：$PREFIX"
PREFIX="$_prefix_parent/$(basename "$PREFIX")"
case "$PREFIX" in
  /|"") die "拒绝空前缀：'$PREFIX'" ;;
  *"|"*) die "prefix 不能包含 '|'（用于 .desktop 模板替换）：$PREFIX" ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ★ 已在 prefix 内运行 ⇒ **跳过复制**，只重做注册（幂等：重复执行不报错、不叠加）。
#   这里刻意**不能**报错退出：安装脚本自己的收尾提示就写着
#   「卸载: $PREFIX/installer/uninstall-user.sh」，用户很容易顺手在 prefix 里再跑一次
#   安装；而且"装完再装一次无副作用"是可重复操作的基本要求。
IN_PLACE=0
case "$APP_ROOT" in
  "$PREFIX"|"$PREFIX"/*) IN_PLACE=1 ;;
esac

info "OmniCrawler 用户级安装"
info "  源目录 : $APP_ROOT"
info "  前缀   : $PREFIX"

# ---- 前置检查：只检测 + 报告，绝不自动 apt（要 root）------------------------
# 清单与 CI 完全一致（reusable-build-linux.yml 的 Qt 运行库 + quality.yml 的中文字体）。
check_runtime_deps() {
  local missing=()
  local pairs=(
    "libegl1:libEGL.so.1"
    "libxcb-icccm4:libxcb-icccm.so.4"
    "libxcb-keysyms1:libxcb-keysyms.so.1"
    "libxcb-image0:libxcb-image.so.0"
    "libxcb-shape0:libxcb-shape.so.0"
    "libxcb-render-util0:libxcb-render-util.so.0"
    "libxcb-xkb1:libxcb-xkb.so.1"
    "libxkbcommon-x11-0:libxkbcommon-x11.so.0"
    "libxcb-cursor0:libxcb-cursor.so.0"
    "libxcb-xinerama0:libxcb-xinerama.so.0"
    "libxcb-xfixes0:libxcb-xfixes.so.0"
    "libxcb-randr0:libxcb-randr.so.0"
    "libxcb-glx0:libxcb-glx.so.0"
  )
  command -v ldconfig >/dev/null 2>&1 || { info "未找到 ldconfig，跳过运行库检测"; return 0; }
  local listing
  listing="$(ldconfig -p 2>/dev/null || true)"
  local pair pkg soname
  for pair in "${pairs[@]}"; do
    pkg="${pair%%:*}"; soname="${pair##*:}"
    if ! grep -qF -- "$soname" <<< "$listing"; then missing+=("$pkg"); fi
  done
  if command -v fc-list >/dev/null 2>&1; then
    if ! fc-list : family 2>/dev/null | grep -qi "Noto Sans CJK"; then
      missing+=("fonts-noto-cjk")
    fi
  fi
  if [[ "${#missing[@]}" -gt 0 ]]; then
    warn "缺少以下运行时依赖（不影响安装，但 GUI 可能启动失败）："
    warn "  ${missing[*]}"
    warn "  安装：sudo apt-get install -y --no-install-recommends ${missing[*]}"
  else
    info "运行时依赖自检：通过"
  fi
}

# ---- 1. 复制应用树（幂等） --------------------------------------------------
if [[ -x "$APP_ROOT/OmniCrawler" && -e "$APP_ROOT/omnicrawler" ]]; then
  :
else
  die "源目录不像 OmniCrawler 便携包（缺 OmniCrawler / omnicrawler）：$APP_ROOT"
fi

# 前置检查放在复制之前：复制可能要搬几百 MB，缺库这种事应该先说。
check_runtime_deps

if [[ "$IN_PLACE" -eq 1 ]]; then
  info "已在安装前缀内运行，跳过复制（幂等）"
else
  mkdir -p "$PREFIX"
  cp -a "$APP_ROOT/." "$PREFIX/"
  info "应用树已就位：$PREFIX"
fi

# ---- 2. hicolor 图标 --------------------------------------------------------
ICON_SRC="$PREFIX/installer/icons/hicolor"
ICON_DEST="$HOME/.local/share/icons/hicolor"
icon_count=0
if [[ -d "$ICON_SRC" ]]; then
  while IFS= read -r -d '' icon; do
    rel="${icon#"$ICON_SRC"/}"
    mkdir -p "$ICON_DEST/$(dirname "$rel")"
    cp -f "$icon" "$ICON_DEST/$rel"
    icon_count=$((icon_count + 1))
  done < <(find "$ICON_SRC" -type f -name '*.png' -print0 | sort -z)
fi
if [[ "$icon_count" -gt 0 ]]; then
  info "已安装 hicolor 图标：$icon_count 个 -> $ICON_DEST"
else
  warn "包内未找到 hicolor 图标（${ICON_SRC}）；桌面条目将无自定义图标。"
fi

# ---- 3. .desktop（绝对路径是硬要求） ---------------------------------------
TEMPLATE="$PREFIX/installer/omnicrawler.desktop.in"
[[ -f "$TEMPLATE" ]] || die "缺少 .desktop 模板：$TEMPLATE"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
DESKTOP_FILE="$DESKTOP_DIR/omnicrawler.desktop"
sed "s|^Exec=@PREFIX@|Exec=$PREFIX|" "$TEMPLATE" > "$DESKTOP_FILE"
if ! grep -qF "Exec=$PREFIX/OmniCrawler" "$DESKTOP_FILE"; then
  die "生成的 .desktop 未指向绝对路径：$DESKTOP_FILE"
fi
info "桌面条目已写入：$DESKTOP_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DESKTOP_FILE" || die ".desktop 语法校验失败"
else
  warn "未找到 desktop-file-validate，跳过语法校验（可安装 desktop-file-utils）"
fi

# ---- 4. CLI 进 PATH ---------------------------------------------------------
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sfn "$PREFIX/omnicrawler" "$BIN_DIR/omnicrawler"
info "CLI 软链：$BIN_DIR/omnicrawler -> $PREFIX/omnicrawler"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "提示：$BIN_DIR 不在 PATH 中，命令行需用 $PREFIX/omnicrawler" ;;
esac

if [[ "$RUN_DESKTOP_DB" -eq 1 ]]; then
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 \
      || warn "update-desktop-database 返回非零（通常无害）"
  else
    warn "未找到 update-desktop-database（可安装 desktop-file-utils）"
  fi
fi

# ---- 5. --move：确认后才删源 -----------------------------------------------
# ★ 就地运行时源目录**就是**安装好的应用树 —— 这里必须挡住，否则 --move 会把
#   刚装好的东西删掉（"删源"与"删安装"变成了同一次操作）。
if [[ "$DO_MOVE" -eq 1 && "$IN_PLACE" -eq 1 ]]; then
  warn "--move 已忽略：本次是就地注册（源目录＝安装目录，没有可清理的另一份副本）"
elif [[ "$DO_MOVE" -eq 1 ]]; then
  info ""
  warn "源目录：$APP_ROOT"
  warn "确认应用已能正常启动后，可删除源目录以释放磁盘（约与已安装的一份等大）。"
  printf '删除源目录 %s ？输入 yes 确认：' "$APP_ROOT"
  read -r answer || answer=""
  if [[ "$answer" == "yes" ]]; then
    rm -rf -- "$APP_ROOT"
    info "源目录已删除：$APP_ROOT"
  else
    info "已保留源目录。"
  fi
fi

info ""
info "安装完成。"
info "  启动     : 应用菜单搜索 OmniCrawler，或直接运行 $PREFIX/OmniCrawler"
info "  命令行   : $BIN_DIR/omnicrawler"
info "  卸载     : $PREFIX/installer/uninstall-user.sh"
info "  数据位置 : 便携模式下在应用目录内（卸载默认保护，不会被删）"
