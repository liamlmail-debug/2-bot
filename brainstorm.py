#!/usr/bin/env python3
"""2-bot：讓 Claude 與 Gemini 輪流腦力激盪。

Claude 經 Claude Code（claude -p）使用你的 Claude 訂閱。
Gemini 有兩種連接方式：
  - agy：經 Antigravity CLI（agy -p）使用你的 Google AI 訂閱；
  - api：以 Gemini API key 直接連接（適用於不能安裝 agy 的環境）。
用法見 README.md，或執行：python3 brainstorm.py --help
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:  # 令中文輸入可以正常刪改
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

REPO_DIR = Path(__file__).resolve().parent
SESSIONS_DIR = Path(os.environ.get("TWOBOT_SESSIONS_DIR") or REPO_DIR / "sessions")
# 兩個 CLI 均在一個空資料夾內執行，避免讀取或改動其他檔案
WORK_DIR = Path(os.environ.get("TWOBOT_WORK_DIR") or Path.home() / ".2bot" / "work")
CALL_TIMEOUT = int(os.environ.get("TWOBOT_TIMEOUT", "900"))  # 每次發言最長等候秒數
RETRY_DELAY = float(os.environ.get("TWOBOT_RETRY_DELAY", "8"))  # 失敗後重試前的等候秒數
# agy 以命令列參數接收內容，而 Linux 單一參數上限約 128 KB，故預留空間
MAX_AGY_PROMPT_BYTES = 100_000

# 以 API key 連接 Gemini 時使用
GEMINI_API_BASE = os.environ.get(
    "TWOBOT_GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")
STATE_DIR = Path(os.environ.get("TWOBOT_STATE_DIR") or Path.home() / ".2bot")
KEY_FILE = STATE_DIR / "gemini_api_key"  # 存放 API key 的檔案（只保存在本機）
MODEL_CACHE = STATE_DIR / "gemini_api_model"  # 上次成功使用的型號
FALLBACK_MODEL = "gemini-3.8-flash"
SKIP_MODEL_WORDS = (
    "lite", "image", "tts", "audio", "live", "embed", "vision", "robotics",
    "computer", "exp", "learnlm", "nano", "veo", "imagen", "aqa", "thinking",
)
MODEL_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-([a-z][a-z0-9.-]*)$")

CLAUDE, GEMINI, USER = "Claude", "Gemini", "使用者"
LABEL = {CLAUDE: "Claude（發想者）", GEMINI: "Gemini（挑戰者）", USER: "使用者補充"}

ROLE = {
    CLAUDE: (
        "你是「發想者」，正與另一個 AI（Google 的 Gemini，擔任「挑戰者」）一同為使用者進行腦力激盪。"
        "你負責提出多元而具體的想法，並因應挑戰者的意見修正和收斂。"
    ),
    GEMINI: (
        "你是「挑戰者」，正與另一個 AI（Anthropic 的 Claude，擔任「發想者」）一同為使用者進行腦力激盪。"
        "你負責找出盲點、風險及可行性問題，並補充被忽略的角度；批評必須具體，並附改善方向。"
    ),
}
SUMMARIZER = (
    "你是一名中立的記錄員，負責整理 Claude（發想者）與 Gemini（挑戰者）之間的腦力激盪，"
    "並為使用者撰寫總結。"
)

RULES = """\
共同規則：
- 以{lang}回覆。
- 直接以文字回覆，不要建立或修改任何檔案，亦不要執行任何指令。
- 篇幅不超過約 {words} 字；內容要具體、可執行，避免空泛的套話。
- 不要重複對方已說過的內容，亦不要客套。
- 引用數字、法規或市場資料時，如未能確定，請註明「待查證」。
- 討論紀錄中如有「使用者補充」，須優先遵從。"""

SUMMARY_TASK = """\
討論已經結束。請以中立角度，公平反映雙方觀點，為使用者撰寫最終總結，供其決策之用。格式如下：
1. 結論：以一段話概括。
2. 推薦清單：以 Markdown 表格列出最值得做的 3 個想法，欄位為「排名｜想法｜核心價值｜主要風險｜第一步驗證方法」。
3. 雙方共識：列點。
4. 仍存在的分歧：列點；如無，寫「無」。
5. 下一步：使用者應做的 3 件事，須具體到本星期內可以開始。
只用文字和表格，不要使用任何圖表。"""

HINT = {
    CLAUDE: (
        "請檢查：執行 claude auth status 確認已登入；如未登入，執行 claude auth login。"
        "如已達用量上限，請稍後再試。"
    ),
    GEMINI: (
        "請檢查：執行 agy 確認已登入（完成後輸入 /quit）；如指定了 --gemini-model，"
        "可執行 agy models 查看可用型號。如已達用量上限，請稍後再試。"
    ),
}
HINT_API = (
    "請檢查：Gemini API key 是否正確；如提示超出額度（429），請稍後再試，或以 --gemini-model 指定其他型號；"
    "如未能連線，請確認網絡容許連接 generativelanguage.googleapis.com。"
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
RULE_LINE = "-" * 32


# ---------------------------------------------------------------- 討論內容


@dataclass
class Session:
    topic: str
    context: str
    rounds: int
    lang: str
    words: int
    ideas: int
    claude_model: str | None
    gemini_model: str | None
    summary_by: str
    gemini_api: GeminiAPI | None = None  # 以 API key 連接時使用；否則經 agy
    turns: list[dict] = field(default_factory=list)
    summary: str | None = None


def claude_task(rnd: int, rounds: int, ideas: int) -> str:
    head = f"這是第 {rnd} 輪（共 {rounds} 輪）。"
    if rnd == 1:
        return head + f"請就題目提出 {ideas} 個方向各異的想法，每個列出：名稱、一句說明、為何值得做。"
    return head + (
        "請回應挑戰者上一輪的意見：接受合理的批評，反駁你不同意之處並說明理由；"
        "然後提出經修訂的 3 至 5 個重點想法，每個寫明具體做法。"
    )


def gemini_task(rnd: int, rounds: int) -> str:
    head = f"這是第 {rnd} 輪（共 {rounds} 輪）。"
    if rnd == 1:
        return head + (
            "請評估發想者的想法：（1）選出最有潛力的 3 個並說明原因；"
            "（2）指出每個入選想法最大的風險或盲點；"
            "（3）補充 2 至 3 個發想者未有提及、角度不同的新想法。"
        )
    if rnd < rounds:
        return head + (
            "請為發想者修訂後的想法進行壓力測試，涵蓋成本、競爭及執行難度，"
            "並為每個想法提出最快、最便宜的驗證方法；如發現致命缺陷，請直接指出。"
        )
    return f"這是最後一輪（第 {rnd} 輪）。請給出最終判斷：你會優先推薦哪一個想法？原因為何？雙方仍有哪些分歧？"


def turn_heading(turn: dict) -> str:
    if turn["who"] == USER:
        return f"第 {turn['round']} 輪後・使用者補充"
    return f"第 {turn['round']} 輪・{LABEL[turn['who']]}"


def build_body(topic: str, context: str, turns: list[dict], task: str, trimmed: bool = False) -> str:
    parts = [f"【題目】\n{topic}"]
    if context:
        parts.append(f"【背景及限制】\n{context}")
    if turns:
        history = "\n\n".join(f"── {turn_heading(t)} ──\n{t['text'].strip()}" for t in turns)
        if trimmed:
            history = "（較早的發言已節錄）\n\n" + history
        parts.append(f"【討論紀錄】\n{history}")
    parts.append(f"【你現在的任務】\n{task}")
    return "\n\n".join(parts)


def excerpt(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "……（節錄）"


def fit_prompt(header: str, s: Session, task: str, max_bytes: int = MAX_AGY_PROMPT_BYTES) -> str:
    """組合給 agy 的完整內容；過長時先節錄較早的發言，確保不超過參數上限。"""

    def compose(topic: str, context: str, turns: list[dict], trimmed: bool) -> str:
        return header + "\n\n" + build_body(topic, context, turns, task, trimmed)

    def fits(text: str) -> bool:
        return len(text.encode("utf-8")) <= max_bytes

    prompt = compose(s.topic, s.context, s.turns, False)
    if fits(prompt):
        return prompt
    # 先節錄較早的發言（最近兩段及使用者補充保留原文）
    turns = [dict(t) for t in s.turns]
    for t in turns[:-2]:
        if t["who"] != USER:
            t["text"] = excerpt(t["text"], 400)
            prompt = compose(s.topic, s.context, turns, True)
            if fits(prompt):
                return prompt
    # 仍然太長：所有內容一律節錄，題目及背景亦然
    for limit in (3000, 1500, 600, 200):
        turns = [dict(t, text=excerpt(t["text"], limit)) for t in s.turns]
        prompt = compose(excerpt(s.topic, 1000), excerpt(s.context, 2000), turns, True)
        if fits(prompt):
            return prompt
    return prompt


# ---------------------------------------------------------------- 呼叫兩個 CLI


class CallError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def agy_env() -> dict:
    """agy 的執行環境。

    agy 平時把登入資料存於系統密碼庫（D-Bus keyring），但 Cloud Shell 之類的
    無桌面環境並沒有密碼庫；agy 偵測到 SSH 連線時則改用檔案儲存。
    因此在未偵測到 SSH 而又沒有可用密碼庫時，標記為遠端連線，令登入得以保存。
    setup.sh 登入時使用同一規則，兩者必須一致。
    """
    env = os.environ.copy()
    remote = env.get("SSH_CONNECTION") or env.get("SSH_CLIENT")
    no_keyring = env.get("CLOUD_SHELL") == "true" or not env.get("DBUS_SESSION_BUS_ADDRESS")
    if not remote and no_keyring:
        env["SSH_CONNECTION"] = "127.0.0.1 0 127.0.0.1 0"
    return env


def run_cli(cmd: list[str], *, stdin_text: str | None, env: dict, who: str) -> str:
    kwargs = dict(
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CALL_TIMEOUT,
        cwd=str(WORK_DIR),
        env=env,
    )
    if stdin_text is None:
        kwargs["stdin"] = subprocess.DEVNULL  # 未登入時直接報錯，而非停下等候輸入
    else:
        kwargs["input"] = stdin_text
    try:
        proc = subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        raise CallError(f"找不到 {cmd[0]} 指令，請先執行：bash {REPO_DIR / 'setup.sh'}", retryable=False)
    except subprocess.TimeoutExpired:
        raise CallError(f"{who} 超過 {CALL_TIMEOUT // 60} 分鐘仍未回覆。")
    out = ANSI_RE.sub("", proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        return out
    raw = ANSI_RE.sub("", (proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    detail = "\n".join(raw.splitlines()[-15:])
    message = f"{who} 回覆失敗（結束碼 {proc.returncode}）。"
    raise CallError(message + (f"\n{detail}" if detail else ""))


def call_claude(system: str, body: str, model: str | None) -> str:
    cmd = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--system-prompt", system]
    if model:
        cmd += ["--model", model]
    cmd += ["--tools", ""]  # 不使用任何工具，只負責思考及回覆；此參數須放最後
    return run_cli(cmd, stdin_text=body, env=os.environ.copy(), who=CLAUDE)


def call_gemini(prompt: str, model: str | None) -> str:
    cmd = ["agy", "-p", prompt]
    if model:
        cmd += ["--model", model]
    return run_cli(cmd, stdin_text=None, env=agy_env(), who=GEMINI)


# ---------------------------------------------------------------- 以 API key 連接 Gemini


class ApiError(CallError):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        # 400／401 多數是 API key 或請求本身有問題，重試亦無補於事
        fatal = status in (400, 401) and "model" not in message.lower()
        super().__init__(f"Gemini API 錯誤 {status}：{message}", retryable=not fatal)
        self.status = status
        self.api_message = message
        self.retry_after = retry_after

    @property
    def quota_zero(self) -> bool:
        """免費額度不包括此型號（limit: 0），等待亦無用，應改用其他型號。"""
        return "limit: 0" in self.api_message


def gemini_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        words = KEY_FILE.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return words[0] if words else None


def rank_models(names: list[str]) -> list[str]:
    """由新至舊排列適合對話的 Gemini 型號；同一版本以 pro 優先，preview 版殿後。"""
    scored = []
    for name in names:
        m = MODEL_RE.match(name)
        if not m or any(word in name for word in SKIP_MODEL_WORDS):
            continue
        variant = m.group(2)
        tier = 2 if variant.startswith("pro") else (0 if variant.startswith("flash") else 1)
        stable = 0 if "preview" in variant else 1
        scored.append(((float(m.group(1)), tier, stable), name))
    return [name for _, name in sorted(scored, reverse=True)]


def extract_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise CallError(f"Gemini 未有回覆（{reason or '原因不明'}）。")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise CallError(f"Gemini 回覆為空（finishReason：{candidates[0].get('finishReason')}）。")
    return text.strip()


class GeminiAPI:
    """以 API key 呼叫 Gemini API（generateContent）。

    未指定型號時，會由最新的型號開始嘗試；如免費額度不包括該型號，便自動改用下一個，
    並記下成功的型號供下次使用。
    """

    def __init__(self, key: str, model: str | None = None):
        self.key = key
        self.pinned = model
        self.model = model

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{GEMINI_API_BASE}/{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method=method,
            headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", "replace")
            message, retry_after = raw.strip()[:500] or str(err.reason), None
            try:
                info = json.loads(raw).get("error", {})
                message = info.get("message") or message
                for detail in info.get("details", []):
                    delay = str(detail.get("retryDelay", ""))
                    if delay.endswith("s"):
                        retry_after = float(delay[:-1] or 0)
            except (ValueError, AttributeError):
                pass
            raise ApiError(err.code, message, retry_after) from None
        except OSError as err:  # 包括連線被拒、逾時
            reason = getattr(err, "reason", err)
            raise CallError(f"未能連接 Gemini API：{reason}") from None

    def candidates(self) -> list[str]:
        if self.pinned:
            return [self.pinned]
        names: list[str] = []
        token = ""
        for _ in range(10):
            query = "models?pageSize=1000" + (f"&pageToken={urllib.parse.quote(token)}" if token else "")
            data = self._request("GET", query)
            for item in data.get("models", []):
                if "generateContent" in item.get("supportedGenerationMethods", []):
                    names.append(item.get("name", "").split("/", 1)[-1])
            token = data.get("nextPageToken") or ""
            if not token:
                break
        ranked = rank_models(names) or [FALLBACK_MODEL]
        try:
            cached = MODEL_CACHE.read_text(encoding="utf-8").strip()
        except OSError:
            cached = ""
        if cached in ranked:
            ranked.remove(cached)
            ranked.insert(0, cached)
        return ranked

    def generate(self, system: str, body: str) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": body}]}],
        }
        order = [self.model] if self.model else self.candidates()
        last: ApiError | None = None
        for model in order:
            for attempt in range(3):
                try:
                    text = extract_text(self._request("POST", f"models/{model}:generateContent", payload))
                except ApiError as err:
                    last = err
                    if err.status == 429 and not err.quota_zero and attempt < 2:
                        wait = err.retry_after if err.retry_after is not None else 15
                        time.sleep(min(wait, 60))  # 每分鐘次數限制：稍等再試
                        continue
                    if not err.retryable or self.pinned:
                        raise
                    break  # 此型號不可用，改試下一個
                if self.model != model:
                    self.model = model
                    try:
                        STATE_DIR.mkdir(parents=True, exist_ok=True)
                        MODEL_CACHE.write_text(model + "\n", encoding="utf-8")
                    except OSError:
                        pass
                return text
        assert last is not None
        raise last


def speak(s: Session, who: str, task: str, *, summary: bool = False) -> str:
    role = SUMMARIZER if summary else ROLE[who]
    words = max(s.words * 2, 900) if summary else s.words
    header = role + "\n\n" + RULES.format(lang=s.lang, words=words)
    if who == CLAUDE:
        return call_claude(header, build_body(s.topic, s.context, s.turns, task), s.claude_model)
    if s.gemini_api is not None:
        return s.gemini_api.generate(header, build_body(s.topic, s.context, s.turns, task))
    return call_gemini(fit_prompt(header, s, task), s.gemini_model)


# ---------------------------------------------------------------- 終端機顯示


class Spinner:
    """等候期間顯示已用時間（只在終端機顯示）。"""

    def __init__(self, label: str):
        self.label = label
        self.start = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Spinner":
        self.start = time.monotonic()
        if sys.stderr.isatty():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            print(f"{self.label}……", file=sys.stderr, flush=True)
        return self

    def _run(self) -> None:
        while not self._stop.wait(1):
            secs = int(time.monotonic() - self.start)
            sys.stderr.write(f"\r\033[K{self.label}…… {secs} 秒")
            sys.stderr.flush()

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.start)


def warn(message: str) -> None:
    print(f"！{message}", file=sys.stderr, flush=True)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def take_turn(s: Session, who: str, task: str, *, label: str, quiet: bool, summary: bool = False) -> str:
    """請其中一方發言；失敗時自動重試一次。"""

    def attempt() -> tuple[str, int]:
        with Spinner(f"{label}：{who} 思考中") as sp:
            text = speak(s, who, task, summary=summary)
            return text, sp.elapsed

    try:
        text, secs = attempt()
    except CallError as err:
        if not err.retryable:
            raise
        warn(f"{who} 未能回覆，{RETRY_DELAY:g} 秒後重試一次。\n{err}")
        time.sleep(RETRY_DELAY)
        text, secs = attempt()
    if not quiet and not summary:
        print(f"\n[{label}] {LABEL[who]}・{secs} 秒")
        print(RULE_LINE)
        print(text, flush=True)
    return text


# ---------------------------------------------------------------- 紀錄檔


def slugify(text: str, limit: int = 24) -> str:
    slug = re.sub(r"[^\w]+", "-", text).strip("-_")
    return slug[:limit].strip("-_") or "brainstorm"


def render_markdown(s: Session, started: dt.datetime, status: str | None) -> str:
    if s.gemini_api is not None:
        gemini_model = f"{s.gemini_api.model or '自動選擇'}（API）"
    else:
        gemini_model = s.gemini_model or "預設"
    lines = [
        f"# 腦力激盪：{s.topic}",
        "",
        "| 項目 | 內容 |",
        "|---|---|",
        f"| 日期 | {started:%Y-%m-%d %H:%M} |",
        "| 參與者 | Claude（發想者）、Gemini（挑戰者） |",
        f"| 輪數 | {s.rounds} |",
        f"| Claude 型號 | {s.claude_model or '預設'} |",
        f"| Gemini 型號 | {gemini_model} |",
        f"| 總結撰寫 | {CLAUDE if s.summary_by == 'claude' else GEMINI} |",
    ]
    if status:
        lines += ["", f"> 狀態：{status}"]
    if s.context:
        lines += ["", "## 背景及限制", "", s.context]
    if s.summary:
        lines += ["", "## 最終總結", "", s.summary.strip()]
    if s.turns:
        lines += ["", "## 討論過程"]
        current = None
        for t in s.turns:
            if t["round"] != current:
                current = t["round"]
                lines += ["", f"### 第 {current} 輪"]
            lines += ["", f"#### {LABEL[t['who']]}", "", t["text"].strip()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主程式


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="brainstorm",
        description="讓 Claude（發想者）與 Gemini（挑戰者）輪流腦力激盪，最後輸出總結。",
    )
    p.add_argument("topic", nargs="?", help="題目；省略則會逐步詢問")
    p.add_argument("-r", "--rounds", type=int, default=3, help="討論輪數，1 至 6（預設 3）")
    p.add_argument("-c", "--context", help="背景或限制，例如預算、時間、目標客群")
    p.add_argument("-s", "--steer", action="store_true", help="每輪結束後暫停，讓你補充意見或調整方向")
    p.add_argument("-q", "--quiet", action="store_true", help="過程中不顯示每段發言，只顯示最終總結")
    p.add_argument("--ideas", type=int, default=8, help="第一輪提出的想法數目，3 至 15（預設 8）")
    p.add_argument("--words", type=int, default=600, help="每段發言的大約字數上限，150 至 2000（預設 600）")
    p.add_argument("--lang", default="繁體中文（書面語）", help="回覆語言（預設：繁體中文（書面語））")
    p.add_argument("--summary-by", choices=["claude", "gemini"], default="claude", help="由誰撰寫最終總結（預設 claude）")
    p.add_argument("--claude-model", help="Claude 型號，例如 opus、sonnet（預設跟隨你的訂閱）")
    p.add_argument("--gemini-model", help="Gemini 型號（經 agy 時可執行 agy models 查看；經 API 時預設自動選擇）")
    p.add_argument(
        "--gemini-via",
        choices=["auto", "agy", "api"],
        default="auto",
        help="Gemini 的連接方式：agy（經 Antigravity CLI 使用訂閱）或 api（使用 API key）；"
        "預設 auto：有 agy 就用 agy，否則用 API key",
    )
    args = p.parse_args(argv)
    if not 1 <= args.rounds <= 6:
        p.error("輪數須介乎 1 至 6")
    if not 3 <= args.ideas <= 15:
        p.error("想法數目須介乎 3 至 15")
    if not 150 <= args.words <= 2000:
        p.error("字數上限須介乎 150 至 2000")
    return args


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    # Cloud Shell 新開的終端機未必已把 ~/.local/bin 加入 PATH
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + local_bin

    args = parse_args(argv)

    key = gemini_api_key()
    via = args.gemini_via
    if via == "auto":
        via = "api" if key and not shutil.which("agy") else "agy"
    missing = [name for name in ("claude", "agy") if not shutil.which(name) and (name == "claude" or via == "agy")]
    if missing:
        message = f"找不到指令：{'、'.join(missing)}。請先執行：bash {REPO_DIR / 'setup.sh'}"
        if "agy" in missing:
            message += f"\n（或改用 Gemini API key：設定 GEMINI_API_KEY，或把 key 存於 {KEY_FILE}）"
        print(message, file=sys.stderr)
        return 1
    if via == "api" and not key:
        print(f"未有 Gemini API key。請設定 GEMINI_API_KEY，或把 key 存於 {KEY_FILE}", file=sys.stderr)
        return 1

    topic = (args.topic or "").strip()
    context = (args.context or "").strip()
    if not topic:
        topic = ask("請輸入題目：")
        if not topic:
            print("未有題目，已取消。", file=sys.stderr)
            return 2
        if args.context is None:
            context = ask("背景或限制（可留空，直接按 Enter）：")

    s = Session(
        topic=topic,
        context=context,
        rounds=args.rounds,
        lang=args.lang,
        words=args.words,
        ideas=args.ideas,
        claude_model=args.claude_model,
        gemini_model=args.gemini_model,
        summary_by=args.summary_by,
        gemini_api=GeminiAPI(key, args.gemini_model) if via == "api" and key else None,
    )
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now()
    path = SESSIONS_DIR / f"{started:%Y%m%d-%H%M%S}_{slugify(topic)}.md"

    def save(status: str | None = None) -> None:
        path.write_text(render_markdown(s, started, status), encoding="utf-8")

    n_claude = s.rounds + (1 if s.summary_by == "claude" else 0)
    n_gemini = s.rounds + (1 if s.summary_by == "gemini" else 0)
    print(RULE_LINE)
    print("2-bot 腦力激盪")
    print(f"題目：{topic}")
    if context:
        print(f"背景：{context}")
    print(f"輪數：{s.rounds}（Claude {n_claude} 次、Gemini {n_gemini} 次）")
    print(f"Gemini 連接方式：{'API key' if s.gemini_api else 'Antigravity CLI（訂閱）'}")
    print(RULE_LINE, flush=True)
    save("進行中")

    stage = ""
    try:
        for rnd in range(1, s.rounds + 1):
            for who, task in ((CLAUDE, claude_task(rnd, s.rounds, s.ideas)), (GEMINI, gemini_task(rnd, s.rounds))):
                stage = f"第 {rnd} 輪・{who}"
                text = take_turn(s, who, task, label=f"第 {rnd} 輪", quiet=args.quiet)
                s.turns.append({"round": rnd, "who": who, "text": text})
                save("進行中")
            if args.steer:
                note = ask("\n你想補充意見或調整方向嗎？（直接按 Enter 繼續）\n> ")
                if note:
                    s.turns.append({"round": rnd, "who": USER, "text": note})
                    save("進行中")
        writer = CLAUDE if s.summary_by == "claude" else GEMINI
        stage = f"總結・{writer}"
        s.summary = take_turn(s, writer, SUMMARY_TASK, label="總結", quiet=args.quiet, summary=True)
        save()
    except KeyboardInterrupt:
        save(f"已於「{stage}」中止")
        print(f"\n已中止。已完成的部分已儲存：{path}", file=sys.stderr)
        return 130
    except CallError as err:
        who = GEMINI if stage.endswith(GEMINI) else CLAUDE
        hint = HINT_API if who == GEMINI and s.gemini_api else HINT[who]
        save(f"未完成：「{stage}」回覆失敗")
        print(f"\n{err}\n\n{hint}\n已完成的部分已儲存：{path}", file=sys.stderr)
        return 1

    print(f"\n{'=' * 12} 最終總結 {'=' * 12}")
    print(s.summary)
    print(f"\n完整紀錄已儲存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
