"""Measure long-content containment in the real Edge WebView2 workspace.

Uses disposable local records and no model/API calls. ``--keep-open`` leaves
the fixture window available for native screenshot and scroll verification.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

import webview

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from config import Settings
from learning.guidance import GuidanceNotebook
from web_ui import MoreThanDoneHTTPServer


METRICS = r"""(() => {
  const rect = selector => {
    const e = document.querySelector(selector);
    if (!e || getComputedStyle(e).display === 'none') return null;
    const r = e.getBoundingClientRect();
    return {left:r.left, right:r.right, width:r.width,
            client:e.clientWidth, scroll:e.scrollWidth};
  };
  const local = selector => [...document.querySelectorAll(selector)].map(e => ({
    client:e.clientWidth, scroll:e.scrollWidth,
    height:e.clientHeight, scrollHeight:e.scrollHeight,
    overflowX:getComputedStyle(e).overflowX,
    overflowY:getComputedStyle(e).overflowY,
    tabindex:e.getAttribute('tabindex'), role:e.getAttribute('role')
  })).filter(e => e.client > 0);
  return {
    viewport:innerWidth, body:rect('body'), main:rect('.main'),
    dialogue:rect('.dialogue-column'), chat:rect('.chat-viewport'), grid:rect('.messages'),
    composer:rect('.composer'), sidebar:rect('.sidebar'), lesson:rect('.lesson-panel'),
    messages:local('.message'), code:local('.markdown-body pre'),
    tables:local('.table-scroll'), users:local('.message.user .message-text'),
    toolOutputs:local('.tool-card pre'),
    codeControls:[...document.querySelectorAll('.code-block')].map(e => {
      const block=e.getBoundingClientRect(), button=e.querySelector('.copy-code')?.getBoundingClientRect();
      return button ? {left:block.left, right:block.right, buttonLeft:button.left, buttonRight:button.right} : null;
    }).filter(Boolean),
    count:document.querySelectorAll('.message').length
  };
})()"""


def fixture(workspace: Path) -> tuple[dict, dict]:
    project = ConversationStore.make_project("长内容布局验收", str(workspace))
    chat = ConversationStore.make_conversation(project["id"], "长代码与表格局部滚动")
    long_token = "unbroken_" + "0123456789abcdef" * 320
    code = "\n".join([f"# {i:02d}: " + "abcdefghij" * 12 for i in range(80)])
    columns = [f"列 {i}" for i in range(20)]
    table = "| " + " | ".join(columns) + " |\n| " + " | ".join(["---"] * 20) + " |\n"
    table += "\n".join("| " + " | ".join(f"row{row}-col{col}-" + "0123456789" * 6 for col in range(20)) + " |" for row in range(30))
    prose = "普通段落中的长链接应自动换行：\n\nhttps://example.invalid/" + "segment_" * 350
    markdown = (
        "### 宽表格只在表格框内滚动\n\n" + table +
        "\n\n" + prose +
        "\n\n### 长代码只在代码框内滚动\n\n```python\n" +
        long_token + "\n" + code + "\n```\n\n代码框以外的对话和输入框应保持正常宽度。"
    )
    events = [
        {"id": 1, "kind": "user", "text": ("这是一段很长的用户消息，用于验证局部纵向滚动。\n" * 100) + long_token},
        {"id": 2, "kind": "tool_start", "text": "调用 read_file", "tool": "read_file", "call_id": "layout-output", "arguments": {"path": "fixture.txt"}},
        {"id": 3, "kind": "tool_result", "text": "read_file 完成", "tool": "read_file", "call_id": "layout-output", "result": json.dumps({"ok": True, "content": long_token + "\n" + code})},
        {"id": 4, "kind": "assistant", "text": markdown},
    ]
    lesson = GuidanceNotebook()
    lesson.plan({"goal": "验证长内容不会撑宽界面", "steps": [{
        "title": "查看局部滚动条", "kind": "guided",
        "task": "代码、表格在自己的框里滚动，聊天区和输入框宽度固定。内联代码也应换行：`" + "abcdef" * 120 + "`",
        "success_criteria": "对话外层没有横向滚动条，内容未被截断。",
    }]})
    chat.update({"messages": [], "events": events, "lesson": lesson.export()})
    return project, chat


def assert_metrics(metrics: dict) -> None:
    assert metrics["count"] >= 2, "Fixture messages did not render"
    for name in ("body", "main", "dialogue", "chat", "composer", "grid"):
        box = metrics[name]
        assert box, f"{name} is absent"
        assert box["scroll"] <= box["client"] + 2, f"{name} horizontally overflowed: {box}"
        assert box["right"] <= metrics["viewport"] + 2, f"{name} extends beyond window: {box}"
        assert box["left"] >= -2, f"{name} starts outside window: {box}"
    for message in metrics["messages"]:
        assert message["scroll"] <= message["client"] + 2, f"Message overflowed: {message}"
    dialogue, composer = metrics["dialogue"], metrics["composer"]
    assert dialogue["left"] - 2 <= composer["left"] <= composer["right"] <= dialogue["right"] + 2
    assert metrics["code"], "Code scroll region did not render"
    assert any(e["scroll"] > e["client"] + 100 for e in metrics["code"]), "Long code should retain horizontal scrolling"
    assert any(e["scrollHeight"] > e["height"] + 100 for e in metrics["code"]), "Tall code should retain vertical scrolling"
    assert metrics["tables"], "Table scroll wrapper did not render"
    assert any(e["scroll"] > e["client"] + 100 for e in metrics["tables"]), "Wide table should scroll locally"
    assert any(e["scrollHeight"] > e["height"] + 100 for e in metrics["tables"]), "Tall table should scroll locally"
    assert any(e["scrollHeight"] > e["height"] + 100 for e in metrics["users"]), "Long user message should scroll locally"
    for region in metrics["code"] + metrics["tables"]:
        assert region["overflowX"] == "auto" and region["overflowY"] == "auto", region
        assert region["tabindex"] == "0" and region["role"] == "region", region
        assert region["height"] <= 362, f"Large content needs a bounded local viewport: {region}"
    assert metrics["codeControls"], "Code copy control did not render"
    for control in metrics["codeControls"]:
        assert control["left"] - 2 <= control["buttonLeft"] <= control["buttonRight"] <= control["right"] + 2, control
    if metrics["sidebar"]:
        assert metrics["sidebar"]["right"] <= metrics["dialogue"]["left"] + 2
    if metrics["lesson"]:
        assert metrics["lesson"]["right"] <= metrics["viewport"] + 2
        assert metrics["lesson"]["scroll"] <= metrics["lesson"]["client"] + 2


def verify_stream_scroll(window, source: str) -> dict:
    """Exercise our own fixture renderer, preserving both local scroll axes."""
    expected_code = source.split("```python\n", 1)[1].split("\n```", 1)[0] + "\n"
    script = r"""(() => {
      const source = SOURCE;
      const expectedCode = EXPECTED;
      const node = document.querySelector('.message.assistant .markdown-body');
      const root = document.querySelector('.chat-viewport');
      const lesson = document.querySelector('.lesson-panel');
      const snapshots = [];
      const positions = () => ({
        codeX:node.querySelector('.code-block pre').scrollLeft,
        codeY:node.querySelector('.code-block pre').scrollTop,
        tableX:node.querySelector('.table-scroll').scrollLeft,
        tableY:node.querySelector('.table-scroll').scrollTop,
        outerX:root.scrollLeft, outerY:root.scrollTop,
        lessonY:lesson.scrollTop,
        composer:document.querySelector('.composer').getBoundingClientRect().width
      });
      for (const focusSelector of ['.code-block pre', '.table-scroll']) {
        const code = node.querySelector('.code-block pre');
        const table = node.querySelector('.table-scroll');
        code.scrollLeft = 200; code.scrollTop = 120;
        table.scrollLeft = 160; table.scrollTop = 90;
        root.scrollTop = 100;
        node.querySelector(focusSelector).focus({preventScroll:true});
        const before = positions();
        markdown(node, source + '\n\n流式追加验收文本。');
        node.getBoundingClientRect();
        snapshots.push({
          focus:focusSelector, before, after:positions(),
          focusRetained:document.activeElement === node.querySelector(focusSelector),
          codeUnchanged:node.querySelector('.code-block pre code').textContent === expectedCode
        });
      }
      return snapshots;
    })()"""
    script = script.replace("SOURCE", json.dumps(source, ensure_ascii=False), 1)
    script = script.replace("EXPECTED", json.dumps(expected_code, ensure_ascii=False), 1)
    snapshots = window.evaluate_js(script)
    for snapshot in snapshots:
        assert snapshot["focusRetained"], snapshot
        assert snapshot["codeUnchanged"], "Rendered/copied code text changed after a streaming redraw"
        for key, before in snapshot["before"].items():
            assert abs(snapshot["after"][key] - before) <= 1, f"Streaming changed {key}: {snapshot}"
        assert all(snapshot["before"][key] > 0 for key in ("codeX", "codeY", "tableX", "tableY")), snapshot
    return {"regions": len(snapshots), "scrollPreserved": True, "focusPreserved": True, "codeUnchanged": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--title", default="More Than Done · 长内容布局验收")
    args = parser.parse_args()
    errors: list[str] = []
    with TemporaryDirectory(prefix="mtd-message-layout-", ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary)
        workspace = directory / "workspace"
        workspace.mkdir()
        config = AgentConfigStore(directory / "agent_config.json", settings_loader=lambda: Settings(api_key=""))
        config.update({"workspace": str(workspace), "model": "layout-offline"})
        project, chat = fixture(workspace)
        store = ConversationStore(directory / "conversations.json")
        store.save({"schema_version": 1, "projects": [project], "conversations": [chat],
                    "active_project_id": project["id"], "active_conversation_id": chat["id"]})
        server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=store)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        window = webview.create_window(args.title, f"http://127.0.0.1:{server.server_port}/workspace", width=1440, height=900, min_size=(800, 600))

        def verify() -> None:
            try:
                deadline = time.monotonic() + 20
                while True:
                    metrics = window.evaluate_js(METRICS)
                    if metrics.get("count", 0) >= 2:
                        break
                    assert time.monotonic() < deadline, "Timed out waiting for fixture"
                    time.sleep(.1)
                for width, height in ((1440, 900), (800, 600)):
                    window.resize(width, height)
                    # Let native resizing and browser layout complete, then require
                    # two successive matching viewport values before measuring.
                    last = None
                    for _ in range(30):
                        time.sleep(.1)
                        metrics = window.evaluate_js(METRICS)
                        if metrics["viewport"] == last:
                            break
                        last = metrics["viewport"]
                    assert_metrics(metrics)
                    stream = verify_stream_scroll(window, chat["events"][-1]["text"])
                    print(json.dumps({"window": [width, height], "viewport": metrics["viewport"],
                                      "chat": metrics["chat"]["client"], "composer": metrics["composer"]["client"],
                                      "codeRegions": len(metrics["code"]), "tableRegions": len(metrics["tables"]),
                                      "streaming": stream, "result": "PASS"}), flush=True)
                if args.keep_open:
                    window.resize(1440, 900)
                    print("Fixture window remains open for native scroll and screenshot checks.", flush=True)
            except Exception as error:
                errors.append(str(error))
                print(f"Layout verification FAILED: {error}", flush=True)
            finally:
                if not args.keep_open:
                    window.destroy()

        try:
            webview.start(verify, gui="edgechromium")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
