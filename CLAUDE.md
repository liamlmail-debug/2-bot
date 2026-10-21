# 2-bot：給 Claude 的工作指引

本 repository 讓 Claude（經 `claude -p`）與 Gemini 輪流腦力激盪。使用者在 Claude app 的「Code」分頁開啟本 repository 的 session，並要求 brainstorm 時，請按以下步驟處理。

## 1. Gemini API key

- 使用者在訊息中提供 Gemini API key 時，請立即儲存。切勿在回覆中重複顯示 key，切勿寫入 repository，亦切勿 commit：

  ```bash
  mkdir -p ~/.2bot && (umask 077 && printf '%s\n' 'KEY' > ~/.2bot/gemini_api_key)
  ```

- 如未有 key（`GEMINI_API_KEY` 未設定，而且 `~/.2bot/gemini_api_key` 不存在），請使用者到 aistudio.google.com 建立免費 API key 後貼上，毋須啟用付費。
- Google 只容許在其支援地區使用 AI Studio API key。如使用者表示身處香港等不支援地區，請說明此限制，並建議改經 Google Cloud（Gemini Enterprise Agent Platform）使用 Gemini；不要協助以 VPN 等方式規避。

## 2. 執行

在 repository 根目錄執行。整個過程約需 5 至 10 分鐘，請以背景方式執行，完成後再讀取結果：

```bash
python3 brainstorm.py "題目" -c "背景或限制" --gemini-via api -q
```

- 使用者指定輪數時，加上 `-r N`（1 至 6）。
- 題目及背景按使用者原意填寫；使用者未提供背景時，可省略 `-c`。

## 3. 回覆使用者

- 完成後，讀取 `sessions/` 內最新的 Markdown 檔，把「最終總結」完整轉述給使用者，並提供完整紀錄檔。
- 失敗時，按程式印出的提示處理：API key 無效，請使用者提供新的 key；超出額度（429），可稍後再試，或以 `--gemini-model` 指定其他型號。

## 4. 注意

- 本 repository 為公開。切勿 commit `sessions/`、API key 或任何討論內容。
- 除非使用者要求，不要推送任何改動。
