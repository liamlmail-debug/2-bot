#!/usr/bin/env bash
# 2-bot 一次性設定：安裝 Claude Code 及 Antigravity CLI（agy），並逐一登入。
# 為 Google Cloud Shell 而設，亦適用於一般 Linux。可重複執行，已完成的步驟會自動略過。
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${TWOBOT_WORK_DIR:-$HOME/.2bot/work}"
BASHRC="$HOME/.bashrc"
MARK_BEGIN="# >>> 2-bot >>>"
MARK_END="# <<< 2-bot <<<"
CHECK_PROMPT="只回覆兩個字：收到"

export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$WORK_DIR"
ERR_FILE="$(mktemp)"
trap 'rm -f "$ERR_FILE"' EXIT
LAST_OUT=""

if [ -t 1 ]; then
  C_T=$'\033[1;36m'; C_OK=$'\033[1;32m'; C_E=$'\033[1;31m'; C_0=$'\033[0m'
else
  C_T=""; C_OK=""; C_E=""; C_0=""
fi
step() { printf '\n%s▶ %s%s\n' "$C_T" "$*" "$C_0"; }
ok()   { printf '%s✔ %s%s\n' "$C_OK" "$*" "$C_0"; }
die()  { printf '%s✘ %s%s\n' "$C_E" "$*" "$C_0" >&2; exit 1; }
pause() { if [ -t 0 ]; then read -r -p "準備好後按 Enter 繼續……" _; fi; }
show_err() {
  if [ -n "$LAST_OUT" ]; then printf '%s\n' "$LAST_OUT" | tail -n 15; fi
  if [ -s "$ERR_FILE" ]; then tail -n 15 "$ERR_FILE"; fi
}

# 與 brainstorm.py 的 agy_env() 規則相同：沒有系統密碼庫（例如 Cloud Shell）時，
# 標記為遠端連線，令 agy 把登入資料存於檔案，之後毋須每次重新登入。
AGY_ENV=()
if [ -z "${SSH_CONNECTION:-}${SSH_CLIENT:-}" ] && { [ "${CLOUD_SHELL:-}" = "true" ] || [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; }; then
  AGY_ENV=(SSH_CONNECTION="127.0.0.1 0 127.0.0.1 0")
fi

claude_logged_in() {
  claude auth status --json 2>/dev/null \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("loggedIn") else 1)' 2>/dev/null
}

claude_check() {  # 以極短的問題測試，確認訂閱可用
  LAST_OUT="$(cd "$WORK_DIR" && printf '%s' "$CHECK_PROMPT" \
    | timeout 180 claude -p --no-session-persistence --tools "" 2>"$ERR_FILE")"
  local rc=$?
  [ "$rc" -eq 0 ] && [ -n "$LAST_OUT" ]
}

agy_check() {  # 未登入時會直接報錯（不會停下等候輸入）
  LAST_OUT="$(cd "$WORK_DIR" && timeout 180 env ${AGY_ENV[@]+"${AGY_ENV[@]}"} \
    agy -p "$CHECK_PROMPT" </dev/null 2>"$ERR_FILE")"
  local rc=$?
  [ "$rc" -eq 0 ] && [ -n "$LAST_OUT" ]
}

install_cli() {  # $1 指令名稱  $2 顯示名稱  $3 安裝程式網址
  if command -v "$1" >/dev/null 2>&1; then
    ok "已安裝（$("$1" --version 2>/dev/null | head -n 1)）"
    return
  fi
  curl -fsSL "$3" | bash || die "$2 安裝失敗，請稍後再執行一次：bash $REPO_DIR/setup.sh"
  hash -r
  command -v "$1" >/dev/null 2>&1 || die "安裝後仍找不到 $1 指令，請開一個新的 Cloud Shell 分頁再執行一次 setup.sh。"
  ok "安裝完成（$("$1" --version 2>/dev/null | head -n 1)）"
}

command -v python3 >/dev/null 2>&1 || die "找不到 python3。"
echo "2-bot 設定：會安裝兩個指令列工具，然後請你分別登入 Claude 及 Google 帳戶。"

step "1/4 安裝 Claude Code"
install_cli claude "Claude Code" "https://claude.ai/install.sh"

step "2/4 安裝 Antigravity CLI（agy，用於 Gemini）"
install_cli agy "Antigravity CLI" "https://antigravity.google/cli/install.sh"

step "3/4 登入 Claude（使用你的 Claude 訂閱）"
if claude_logged_in; then
  ok "已登入"
else
  cat <<'EOF'
稍後畫面會顯示一條登入連結：
  1. 點按連結（或複製到瀏覽器開啟），以你的 Claude 帳戶登入並按「授權」。
  2. 網頁會顯示一段代碼，請複製。
  3. 回到這個分頁，把代碼貼上，然後按 Enter。
EOF
  pause
  (cd "$WORK_DIR" && claude auth login --claudeai) || true
  claude_logged_in || die "Claude 尚未登入成功。請再執行一次 bash $REPO_DIR/setup.sh；或輸入 claude，再於 Claude Code 內輸入 /login。"
  ok "登入成功"
fi
echo "正在測試 Claude……"
if claude_check; then
  ok "Claude 可以使用"
else
  show_err
  die "Claude 測試失敗。請確認你的訂閱為 Pro 或 Max，然後再執行一次 setup.sh。"
fi

step "4/4 登入 Gemini（使用你的 Google AI 訂閱）"
echo "正在檢查 Gemini 登入狀態……"
if agy_check; then
  ok "已登入，Gemini 可以使用"
else
  cat <<'EOF'
稍後會開啟 Antigravity CLI：
  1. 選擇「Google OAuth」，然後按 Enter。
  2. 點按畫面上的連結，以訂閱了 Google AI Pro／Ultra 的 Google 帳戶登入。
  3. 複製網頁顯示的代碼，回到這個分頁貼上，然後按 Enter（請盡快完成，逾時須重新開始）。
  4. 見到輸入框後，輸入 /model 並選擇一個 Gemini 型號（例如 Gemini 4 Argon）。
  5. 輸入 /quit 離開。
如出現其他設定畫面，按 Enter 接受預設即可。
EOF
  pause
  (cd "$WORK_DIR" && env ${AGY_ENV[@]+"${AGY_ENV[@]}"} agy) || true
  echo "正在測試 Gemini……"
  if agy_check; then
    ok "Gemini 可以使用"
  else
    show_err
    die "Gemini 測試失敗。請確認以訂閱了 Google AI Pro／Ultra 的帳戶登入，然後再執行一次 setup.sh。"
  fi
fi

step "加入 brainstorm 指令"
touch "$BASHRC"
sed -i "/^${MARK_BEGIN}\$/,/^${MARK_END}\$/d" "$BASHRC"  # 先移除舊設定，避免重複
{
  echo "$MARK_BEGIN"
  cat <<'EOF'
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
EOF
  printf "alias brainstorm='python3 \"%s/brainstorm.py\"'\n" "$REPO_DIR"
  cat <<'EOF'
# 沒有系統密碼庫（例如 Cloud Shell）時，令 agy 把登入資料存於檔案（詳見 brainstorm.py）
agy() {
  if [ -z "${SSH_CONNECTION:-}${SSH_CLIENT:-}" ] && { [ "${CLOUD_SHELL:-}" = "true" ] || [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; }; then
    SSH_CONNECTION="127.0.0.1 0 127.0.0.1 0" command agy "$@"
  else
    command agy "$@"
  fi
}
EOF
  echo "$MARK_END"
} >> "$BASHRC"
ok "已加入"

echo
ok "全部完成！"
echo "之後每次使用：開啟 Cloud Shell，輸入 brainstorm 即可。"
echo "（如這個分頁顯示 command not found，請先輸入：source ~/.bashrc）"
if [ -t 0 ]; then
  read -r -p "要即刻試一次嗎？(Y/n) " ans
  case "${ans:-Y}" in
    [nN]*) ;;
    *) python3 "$REPO_DIR/brainstorm.py" ;;
  esac
fi
