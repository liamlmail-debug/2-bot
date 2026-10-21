# 2-bot：Claude × Gemini 腦力激盪

讓 Claude 與 Gemini 就同一題目輪流討論：Claude 擔任「發想者」提出想法，Gemini 擔任「挑戰者」找出盲點並補充新角度，最後輸出一份附推薦清單的總結。兩者均以你現有的訂閱登入，無需 API 金鑰，用量計入各自訂閱的額度。

## 運作方式

| 階段 | Claude（發想者） | Gemini（挑戰者） |
|---|---|---|
| 第 1 輪 | 提出 8 個方向各異的想法 | 選出最有潛力的 3 個、指出風險，並補充新想法 |
| 中間各輪 | 回應批評，收斂為 3 至 5 個重點想法 | 就成本、競爭及執行難度作壓力測試，並提出驗證方法 |
| 最後一輪 | 再次回應及修訂 | 給出最終判斷及仍存在的分歧 |
| 總結 | 以中立角度撰寫結論、推薦表格、共識、分歧及下一步 | — |

預設共 3 輪，每次討論使用 Claude 4 次、Gemini 3 次。

## 事前準備

| 項目 | 要求 |
|---|---|
| Claude 訂閱 | Pro 或 Max |
| Google AI 訂閱 | Google AI Pro 或 Ultra |
| 執行環境 | Google Cloud Shell（免費，以瀏覽器開啟，手機亦可使用） |

## 第一次設定（約 10 分鐘）

1. 以瀏覽器開啟 <https://shell.cloud.google.com>。
2. 複製以下指令，貼到 Cloud Shell，然後按 Enter：

   ```bash
   git clone https://github.com/liamlmail-debug/2-bot.git ~/2-bot && bash ~/2-bot/setup.sh
   ```

3. 程式會自動安裝 Claude Code 及 Antigravity CLI，然後請你逐一登入：
   - **Claude**：點按畫面上的連結，登入並授權，再把網頁顯示的代碼貼回 Cloud Shell。
   - **Gemini**：選擇「Google OAuth」，點按連結，以訂閱了 Google AI Pro／Ultra 的帳戶登入，再把代碼貼回 Cloud Shell；然後輸入 `/model` 選擇 Gemini 型號，最後輸入 `/quit` 離開。
4. 設定完成後，程式會問你是否即時試用一次。

登入資料保存在 Cloud Shell 之中，此後毋須重新設定。

## 日常使用

開啟 Cloud Shell，輸入：

```bash
brainstorm
```

再按提示輸入題目，以及背景或限制（可留空）。亦可一次過寫明：

```bash
brainstorm "香港上班族週末可經營的網上小生意" -c "啟動資金不多於港幣三萬元"
```

| 選項 | 作用 |
|---|---|
| `-r 5` | 討論 5 輪（預設 3，上限 6） |
| `-c "…"` | 加入背景或限制，例如預算、時間、目標客群 |
| `-s` | 每輪結束後暫停，讓你補充意見或調整方向 |
| `-q` | 過程中不顯示每段發言，只顯示最終總結 |
| `--ideas 12` | 第一輪提出的想法數目（預設 8） |
| `--words 400` | 每段發言的大約字數上限（預設 600） |
| `--claude-model opus` | 指定 Claude 型號 |
| `--gemini-model <型號>` | 指定 Gemini 型號（可先執行 `agy models` 查看） |
| `--summary-by gemini` | 改由 Gemini 撰寫最終總結 |
| `--lang English` | 改用其他語言回覆 |

輸入 `brainstorm --help` 可查看全部選項。

## 討論紀錄

每次討論均儲存為一個 Markdown 檔案，位於 `~/2-bot/sessions/`，最終總結置於檔案開首。即使中途斷線或出錯，已完成的部分亦會保留。查看最近的紀錄：

```bash
ls -t ~/2-bot/sessions | head
```

## 私隱

- 本 repository 為公開，但 `sessions/` 已列入 `.gitignore`，討論紀錄只保存在你的 Cloud Shell，不會上載至 GitHub。
- 題目及討論內容會分別傳送至 Anthropic 及 Google 處理，請勿輸入客戶個人資料或公司機密資料。

## 更新

```bash
cd ~/2-bot && git pull && bash setup.sh
```

## 疑難排解

| 情況 | 處理方法 |
|---|---|
| 輸入 `brainstorm` 後顯示 command not found | 先輸入 `source ~/.bashrc`；或直接執行 `python3 ~/2-bot/brainstorm.py` |
| Claude 回覆失敗 | 執行 `claude auth status` 檢查登入狀態；如未登入，執行 `claude auth login`。如已達用量上限，請稍後再試 |
| Gemini 回覆失敗 | 執行 `agy`，按指示重新登入，然後輸入 `/quit`。如已達用量上限，請稍後再試 |
| 提示 Gemini 型號不可用 | 移除 `--gemini-model`，或執行 `agy models` 查看可用型號 |
| Gemini 每次都要求重新登入 | 再執行一次 `bash ~/2-bot/setup.sh` |
| 貼上登入代碼時逾時 | 再執行一次 `bash ~/2-bot/setup.sh`，取得連結後盡快完成登入 |
| 不希望 Gemini 額度用完後動用 AI 積分 | 執行 `agy`，輸入 `/settings`，關閉「Use G1 Credits」 |

## 為何使用 Antigravity CLI 而非 Gemini CLI

Google 已於 2026 年 6 月 18 日停止以 Google AI Pro／Ultra 訂閱使用 Gemini CLI，並以 Antigravity CLI（指令為 `agy`）取代，因此本工具經 `agy` 使用 Gemini。

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `brainstorm.py` | 主程式：安排兩個 AI 輪流發言，並儲存紀錄 |
| `setup.sh` | 一次性安裝及登入，可重複執行 |
| `tests/` | 以模擬指令測試主程式及安裝程式，不會連線，亦不會使用訂閱額度。執行方法：`python3 -m unittest discover -s tests` |
