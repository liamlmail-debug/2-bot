"""以假 claude／agy 指令測試 brainstorm.py 及 setup.sh：不會連線，亦不會使用訂閱額度。

執行：python3 -m unittest discover -s tests -v
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE_BIN = REPO / "tests" / "fake-bin"
sys.path.insert(0, str(REPO))

import brainstorm  # noqa: E402

FAKE_SSH = "127.0.0.1 0 127.0.0.1 0"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sessions = root / "sessions"
        self.log = root / "calls.jsonl"
        self.home = root / "home"
        self.home.mkdir()
        dropped = {"SSH_CONNECTION", "SSH_CLIENT", "DBUS_SESSION_BUS_ADDRESS", "CLOUD_SHELL", "GEMINI_API_KEY"}
        env = {
            k: v for k, v in os.environ.items()
            if k not in dropped and not k.startswith(("FAKE_", "TWOBOT_"))
        }
        env.update(
            PATH=f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.home),
            TWOBOT_SESSIONS_DIR=str(self.sessions),
            TWOBOT_WORK_DIR=str(root / "work"),
            TWOBOT_RETRY_DELAY="0",
            FAKE_LOG=str(self.log),
            FAKE_STATE_DIR=str(root),
            PYTHONIOENCODING="utf-8",
        )
        self.env = env

    def tearDown(self):
        self.tmp.cleanup()

    def run_bot(self, *args, stdin="", **extra_env):
        return subprocess.run(
            [sys.executable, str(REPO / "brainstorm.py"), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(self.env, **extra_env),
            timeout=120,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def sequence(self):
        return [c["bin"] for c in self.calls()]

    def transcript(self):
        files = list(self.sessions.glob("*.md"))
        self.assertEqual(len(files), 1, files)
        return files[0].read_text(encoding="utf-8")

    @staticmethod
    def system_prompt(call):
        return call["args"][call["args"].index("--system-prompt") + 1]


class FullRun(Base):
    def test_two_rounds_alternate_then_summarise(self):
        r = self.run_bot("如何推廣社區二手書店", "-r", "2", "-c", "預算有限")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sequence(), ["claude", "agy", "claude", "agy", "claude"])
        calls = self.calls()

        for c in calls:
            if c["bin"] == "claude":
                self.assertEqual(c["args"][-2:], ["--tools", ""], "Claude 應關閉所有工具，且參數放最後")
                self.assertIn("--no-session-persistence", c["args"])
                self.assertIn("以繁體中文（書面語）回覆", self.system_prompt(c))
                self.assertIn("【題目】\n如何推廣社區二手書店", c["stdin"])
                self.assertIn("【背景及限制】\n預算有限", c["stdin"])
            else:
                self.assertEqual(c["ssh_connection"], FAKE_SSH, "無密碼庫時應令 agy 用檔案儲存登入")
                self.assertFalse(c["stdin_is_tty"])
                self.assertIn("你是「挑戰者」", c["prompt"])
                self.assertIn("【題目】\n如何推廣社區二手書店", c["prompt"])

        self.assertIn("你是「發想者」", self.system_prompt(calls[0]))
        self.assertIn("中立的記錄員", self.system_prompt(calls[-1]))
        self.assertIn("討論已經結束", calls[-1]["stdin"])
        self.assertIn("提出 8 個方向各異的想法", calls[0]["stdin"])
        # 第二輪的 Claude 看得到第一輪雙方發言，並且被要求回應批評
        self.assertIn("── 第 1 輪・Gemini（挑戰者） ──\nGemini 假回覆", calls[2]["stdin"])
        self.assertIn("請回應挑戰者上一輪的意見", calls[2]["stdin"])
        self.assertIn("這是最後一輪", calls[3]["prompt"])

        text = self.transcript()
        self.assertIn("# 腦力激盪：如何推廣社區二手書店", text)
        self.assertLess(text.index("## 最終總結"), text.index("## 討論過程"))
        self.assertIn("### 第 2 輪", text)
        self.assertNotIn("狀態：", text)  # 完成後不應再標示「進行中」
        self.assertNotIn("\x1b[", text)  # 已清除色碼
        self.assertIn("[第 1 輪] Claude（發想者）", r.stdout)
        self.assertIn("完整紀錄已儲存", r.stdout)

    def test_summary_by_gemini(self):
        r = self.run_bot("題目", "-r", "1", "--summary-by", "gemini")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sequence(), ["claude", "agy", "agy"])
        last = self.calls()[-1]["prompt"]
        self.assertIn("中立的記錄員", last)
        self.assertTrue(last.rstrip().endswith("不要使用任何圖表。"))
        self.assertIn("Gemini 型號 | 預設", self.transcript())

    def test_model_options_are_passed_through(self):
        r = self.run_bot("題目", "-r", "1", "--claude-model", "opus", "--gemini-model", "gemini-4-argon-high")
        self.assertEqual(r.returncode, 0, r.stderr)
        claude_args = self.calls()[0]["args"]
        self.assertEqual(claude_args[claude_args.index("--model") + 1], "opus")
        self.assertEqual(claude_args[-2:], ["--tools", ""])
        self.assertEqual(self.calls()[1]["args"], ["--model", "gemini-4-argon-high"])

    def test_existing_ssh_session_is_left_alone(self):
        self.run_bot("題目", "-r", "1", SSH_CONNECTION="10.0.0.1 5 10.0.0.2 22")
        self.assertEqual(self.calls()[1]["ssh_connection"], "10.0.0.1 5 10.0.0.2 22")

    def test_desktop_with_keyring_is_left_alone(self):
        self.run_bot("題目", "-r", "1", DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus")
        self.assertIsNone(self.calls()[1]["ssh_connection"])

    def test_cloud_shell_uses_file_storage_even_with_dbus(self):
        self.run_bot("題目", "-r", "1", DBUS_SESSION_BUS_ADDRESS="unix:path=/x", CLOUD_SHELL="true")
        self.assertEqual(self.calls()[1]["ssh_connection"], FAKE_SSH)


class Failures(Base):
    def test_retries_once_then_succeeds(self):
        r = self.run_bot("題目", "-r", "1", FAKE_CLAUDE_FAIL="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("重試", r.stderr)
        self.assertEqual(self.sequence(), ["claude", "claude", "agy", "claude"])

    def test_gemini_failure_keeps_partial_transcript(self):
        r = self.run_bot("題目", "-r", "2", FAKE_AGY_FAIL="9")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.sequence(), ["claude", "agy", "agy"])
        self.assertIn("authentication required", r.stderr)
        self.assertIn("執行 agy 確認已登入", r.stderr)
        text = self.transcript()
        self.assertIn("未完成：「第 1 輪・Gemini」回覆失敗", text)
        self.assertIn("Claude 假回覆", text)

    def test_claude_failure_shows_claude_hint(self):
        r = self.run_bot("題目", "-r", "1", FAKE_CLAUDE_FAIL="9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Not logged in", r.stderr)
        self.assertIn("claude auth login", r.stderr)

    def test_unknown_gemini_model_points_to_agy_models(self):
        r = self.run_bot("題目", "-r", "1", "--gemini-model", "bad-model")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown model", r.stderr)
        self.assertIn("agy models", r.stderr)

    def test_missing_cli_stops_before_any_call(self):
        only = Path(self.tmp.name) / "only-claude"
        only.mkdir()
        (only / "claude").symlink_to(FAKE_BIN / "claude")
        r = self.run_bot("題目", PATH=f"{only}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("找不到指令：agy", r.stderr)
        self.assertIn("setup.sh", r.stderr)
        self.assertEqual(self.calls(), [])


class Interaction(Base):
    def test_asks_for_topic_and_context(self):
        r = self.run_bot("-r", "1", stdin="社區活動點子\n只限週末\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.transcript()
        self.assertIn("# 腦力激盪：社區活動點子", text)
        self.assertIn("## 背景及限制\n\n只限週末", text)

    def test_empty_topic_cancels(self):
        r = self.run_bot(stdin="\n")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_steer_note_reaches_both_sides(self):
        r = self.run_bot("題目", "-r", "2", "-s", stdin="請集中討論低成本方案\n\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertIn("── 第 1 輪後・使用者補充 ──\n請集中討論低成本方案", calls[2]["stdin"])
        self.assertIn("請集中討論低成本方案", calls[3]["prompt"])
        self.assertIn("#### 使用者補充\n\n請集中討論低成本方案", self.transcript())

    def test_quiet_prints_only_summary(self):
        r = self.run_bot("題目", "-r", "1", "-q")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("[第 1 輪]", r.stdout)
        self.assertIn("最終總結", r.stdout)

    def test_invalid_rounds_rejected(self):
        r = self.run_bot("題目", "-r", "9")
        self.assertEqual(r.returncode, 2)
        self.assertIn("輪數須介乎 1 至 6", r.stderr)
        self.assertEqual(self.calls(), [])


class PromptSize(Base):
    def test_huge_replies_are_trimmed_for_agy_but_kept_in_transcript(self):
        r = self.run_bot("題目", "-r", "3", FAKE_AGY_HUGE="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        agy_calls = [c for c in self.calls() if c["bin"] == "agy"]
        self.assertEqual(len(agy_calls), 3)
        for c in agy_calls:
            self.assertLessEqual(c["prompt_bytes"], brainstorm.MAX_AGY_PROMPT_BYTES)
        self.assertTrue(agy_calls[-1]["prompt"].endswith("雙方仍有哪些分歧？"))
        self.assertIn("長" * 40000, self.transcript())

    def test_fit_prompt_keeps_task_and_user_notes(self):
        s = brainstorm.Session(
            topic="題目", context="", rounds=3, lang="中文", words=600, ideas=8,
            claude_model=None, gemini_model=None, summary_by="claude",
        )
        speakers = [brainstorm.CLAUDE, brainstorm.GEMINI]
        s.turns = [{"round": i // 2 + 1, "who": speakers[i % 2], "text": "字" * 20000} for i in range(6)]
        s.turns.insert(2, {"round": 1, "who": brainstorm.USER, "text": "使用者的重要補充"})
        prompt = brainstorm.fit_prompt("開首", s, "任務內容", max_bytes=50_000)
        self.assertLessEqual(len(prompt.encode("utf-8")), 50_000)
        self.assertTrue(prompt.startswith("開首"))
        self.assertTrue(prompt.endswith("任務內容"))
        self.assertIn("使用者的重要補充", prompt)
        self.assertIn("（較早的發言已節錄）", prompt)

    def test_short_prompt_is_untouched(self):
        s = brainstorm.Session(
            topic="題目", context="背景", rounds=1, lang="中文", words=600, ideas=8,
            claude_model=None, gemini_model=None, summary_by="claude",
        )
        s.turns = [{"round": 1, "who": brainstorm.CLAUDE, "text": "想法甲"}]
        prompt = brainstorm.fit_prompt("開首", s, "任務")
        self.assertNotIn("節錄", prompt)
        self.assertIn("想法甲", prompt)


QUOTA_ZERO = (429, {"error": {
    "code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: x",
}})


class FakeGemini:
    """模擬 Gemini API 的本機伺服器：預設一律成功回覆，亦可為個別型號預設錯誤。"""

    def __init__(self):
        self.requests = []  # (方法, 路徑, API key, 內容)
        self.models = [
            "gemini-3.8-flash", "gemini-3.1-pro", "gemini-3.8-flash-lite",
            "gemini-3.8-flash-image", "text-embedding-004",
        ]
        self.queued = {}  # 型號 -> [(狀態碼, 內容), ...]，按次序使用
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(("GET", self.path, self.headers.get("x-goog-api-key"), None))
                listed = [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]} for m in fake.models]
                self._send(200, {"models": listed})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                fake.requests.append(("POST", self.path, self.headers.get("x-goog-api-key"), body))
                model = self.path.split("/models/", 1)[1].split(":", 1)[0]
                if fake.queued.get(model):
                    return self._send(*fake.queued[model].pop(0))
                text = body["contents"][0]["parts"][0]["text"]
                task = text.split("【你現在的任務】", 1)[-1].strip().splitlines()[0]
                parts = [{"text": "內部推理", "thought": True}, {"text": f"Gemini API 假回覆（{model}）：{task[:30]}"}]
                self._send(200, {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1beta"

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def posted_models(self):
        return [r[1].split("/models/", 1)[1].split(":", 1)[0] for r in self.posts()]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ApiBackend(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeGemini()
        self.addCleanup(self.fake.close)
        only = Path(self.tmp.name) / "only-claude"
        only.mkdir()
        (only / "claude").symlink_to(FAKE_BIN / "claude")
        self.env.update(
            PATH=f"{only}{os.pathsep}{os.environ['PATH']}",
            TWOBOT_GEMINI_API_BASE=self.fake.base,
            GEMINI_API_KEY="test-key",
        )

    def test_auto_uses_api_and_skips_models_outside_free_tier(self):
        self.fake.queued["gemini-3.8-flash"] = [QUOTA_ZERO]
        r = self.run_bot("題目", "-r", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Gemini 連接方式：API key", r.stdout)
        self.assertEqual(self.fake.posted_models(), ["gemini-3.8-flash", "gemini-3.1-pro"])
        method, path, key, body = self.fake.posts()[-1]
        self.assertEqual(key, "test-key")
        self.assertNotIn("test-key", path)
        self.assertIn("你是「挑戰者」", body["systemInstruction"]["parts"][0]["text"])
        self.assertIn("【題目】\n題目", body["contents"][0]["parts"][0]["text"])
        self.assertIn("── 第 1 輪・Claude（發想者） ──", body["contents"][0]["parts"][0]["text"])
        text = self.transcript()
        self.assertIn("Gemini API 假回覆（gemini-3.1-pro）", text)
        self.assertNotIn("內部推理", text)
        self.assertIn("| Gemini 型號 | gemini-3.1-pro（API） |", text)
        self.assertEqual((self.home / ".2bot" / "gemini_api_model").read_text().strip(), "gemini-3.1-pro")

    def test_remembered_model_is_tried_first(self):
        (self.home / ".2bot").mkdir()
        (self.home / ".2bot" / "gemini_api_model").write_text("gemini-3.1-pro\n")
        r = self.run_bot("題目", "-r", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.fake.posted_models(), ["gemini-3.1-pro", "gemini-3.1-pro"])

    def test_rate_limit_waits_then_retries_same_model(self):
        busy = (429, {"error": {"code": 429, "message": "Resource exhausted. Please retry.", "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0s"}]}})
        self.fake.queued["gemini-3.8-flash"] = [busy]
        r = self.run_bot("題目", "-r", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.fake.posted_models(), ["gemini-3.8-flash", "gemini-3.8-flash"])

    def test_invalid_key_stops_without_retrying(self):
        bad = (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}})
        self.fake.queued["gemini-3.8-flash"] = [bad]
        r = self.run_bot("題目", "-r", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.fake.posts()), 1)
        self.assertIn("API key not valid", r.stderr)
        self.assertIn("Gemini API key 是否正確", r.stderr)

    def test_pinned_model_never_falls_back(self):
        self.fake.queued["gemini-3.1-pro"] = [QUOTA_ZERO, QUOTA_ZERO]
        r = self.run_bot("題目", "-r", "1", "--gemini-model", "gemini-3.1-pro")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.fake.posted_models(), ["gemini-3.1-pro", "gemini-3.1-pro"])
        self.assertFalse([req for req in self.fake.requests if req[0] == "GET"])

    def test_unreachable_api_points_to_network_setting(self):
        r = self.run_bot("題目", "-r", "1", TWOBOT_GEMINI_API_BASE="http://127.0.0.1:9/v1beta")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未能連接 Gemini API", r.stderr)
        self.assertIn("generativelanguage.googleapis.com", r.stderr)

    def test_key_file_is_used_when_no_environment_variable(self):
        (self.home / ".2bot").mkdir()
        (self.home / ".2bot" / "gemini_api_key").write_text("file-key\n")
        r = self.run_bot("題目", "-r", "1", GEMINI_API_KEY="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.fake.posts()[0][2], "file-key")

    def test_api_without_key_explains_how_to_add_one(self):
        r = self.run_bot("題目", "--gemini-via", "api", GEMINI_API_KEY="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未有 Gemini API key", r.stderr)
        self.assertEqual(self.fake.requests, [])

    def test_explicit_agy_without_agy_installed_suggests_api_key(self):
        r = self.run_bot("題目", "--gemini-via", "agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("找不到指令：agy", r.stderr)
        self.assertIn("GEMINI_API_KEY", r.stderr)

    def test_auto_prefers_agy_when_installed(self):
        r = self.run_bot("題目", "-r", "1", PATH=f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertIn("agy", self.sequence())

    def test_rank_models(self):
        names = [
            "gemini-3.8-flash", "gemini-3.1-pro", "gemini-4-argon", "gemini-3.8-flash-lite",
            "gemini-3-pro-preview", "gemini-3.8-flash-image", "gemini-2.5-pro",
            "text-embedding-004", "gemma-3-27b-it",
        ]
        self.assertEqual(
            brainstorm.rank_models(names),
            ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.1-pro", "gemini-3-pro-preview", "gemini-2.5-pro"],
        )


class Setup(Base):
    def run_setup(self, **extra_env):
        return subprocess.run(
            ["bash", str(REPO / "setup.sh")],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(self.env, **extra_env),
            timeout=120,
        )

    def test_setup_is_idempotent_and_keeps_existing_bashrc(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text("export FOO=1\n", encoding="utf-8")
        for _ in range(2):
            r = self.run_setup()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        content = bashrc.read_text(encoding="utf-8")
        self.assertEqual(content.count("# >>> 2-bot >>>"), 1)
        self.assertEqual(content.count("export FOO=1"), 1)
        self.assertIn(f"alias brainstorm='python3 \"{REPO}/brainstorm.py\"'", content)
        self.assertIn("全部完成", r.stdout)
        check = subprocess.run(
            ["bash", "-c", "source ~/.bashrc && type agy >/dev/null && alias brainstorm && agy --version"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("1.0.0", check.stdout)

    def test_setup_stops_when_claude_login_fails(self):
        r = self.run_setup(FAKE_CLAUDE_LOGGED_IN="0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Claude 尚未登入成功", r.stderr)
        self.assertFalse((self.home / ".bashrc").exists())

    def test_setup_reports_gemini_failure(self):
        r = self.run_setup(FAKE_AGY_FAIL="9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Gemini 測試失敗", r.stderr)
        self.assertIn("authentication required", r.stdout)
        agy_calls = [c for c in self.calls() if c["bin"] == "agy"]
        self.assertTrue(agy_calls)
        self.assertTrue(all(c["ssh_connection"] == FAKE_SSH for c in agy_calls))


if __name__ == "__main__":
    unittest.main()
