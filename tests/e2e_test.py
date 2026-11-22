"""端到端测试：需要先启动 mock 上游（18081）与聊天服务（18080）。

用法：
    set DEEPSEEK_BASE_URL=http://127.0.0.1:18081
    set ADMIN_PASSWORD=admin12345
    set DATA_DIR=./data-test
    python run.py --port 18080
    python tests/e2e_test.py
"""
from __future__ import annotations

import json
import re
import sys

import httpx

BASE = "http://127.0.0.1:18080"
ADMIN_PASSWORD = "admin12345"
failures: list[str] = []


def check(condition: bool, name: str, detail: str = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        failures.append(name)
        print(f"FAIL  {name}  {detail}")


def login(client: httpx.Client, username: str, password: str) -> httpx.Response:
    return client.post("/api/auth/login", json={"username": username, "password": password})


def chat(client: httpx.Client, messages: list[dict], model: str = "deepseek-flash", session_id: str | None = None) -> str:
    text = ""
    headers = {
        "Accept": "text/event-stream",
        "anthropic-beta": "files-api-2025-04-14",
        "x-api-key": "managed-by-server",
    }
    if session_id:
        headers["X-Chat-Session-Id"] = session_id
    with client.stream(
        "POST",
        "/anthropic/v1/messages",
        json={"model": model, "messages": messages, "stream": True},
        headers=headers,
        timeout=60,
    ) as resp:
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}: {resp.read().decode('utf-8', 'replace')}"
        for line in resp.iter_lines():
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except Exception:
                    continue
                if data.get("type") == "content_block_delta":
                    delta = data.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text += delta.get("text", "")
    return text


def main() -> int:
    admin = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)

    # 未登录访问
    r = admin.get("/")
    check(r.status_code == 302 and "/login" in r.headers.get("location", ""), "未登录访问首页跳转登录页", str(r.status_code))
    r = admin.get("/login")
    check(r.status_code == 200 and "登录" in r.text, "登录页可访问")
    check("GuGuAI" in r.text and "Powered by Deepseek." in r.text, "登录页使用 GuGuAI 品牌")
    r = admin.post("/anthropic/v1/messages", json={"messages": []})
    check(r.status_code == 401 and r.json().get("error", {}).get("message"), "未登录调用聊天接口返回 401 JSON")
    check(r.headers.get("x-session-expired") == "1", "鉴权失败的 401 带会话过期标记")
    r = admin.get("/api/sync/sessions")
    check(r.status_code == 401, "未登录不能读取会话同步列表", str(r.status_code))

    # 登录失败
    r = login(admin, "admin", "wrong-password")
    check(r.status_code == 401, "错误密码被拒绝", str(r.status_code))

    # 管理员登录
    r = login(admin, "admin", ADMIN_PASSWORD)
    check(r.status_code == 200 and r.json()["user"]["role"] == "admin", "管理员登录成功", r.text)
    r = admin.get("/api/auth/me")
    check(r.status_code == 200 and r.json()["username"] == "admin", "会话有效")

    # API Key 未配置时应拒绝
    r = admin.get("/models")
    check(r.status_code == 503, "未配置 API Key 时 /models 返回 503", str(r.status_code))

    # 配置 API Key
    raw_key = "sk-test-1234567890abcdef"
    r = admin.put("/api/admin/apikey", json={"api_key": raw_key})
    check(r.status_code == 200 and r.json()["configured"], "管理员保存 API Key", r.text)
    r = admin.get("/api/admin/apikey")
    body = r.json()
    check(body["masked"] != raw_key and "*" in body["masked"], "API Key 仅返回脱敏值", body["masked"])
    check("sk-test" not in json.dumps(body) or body["masked"].startswith("sk-tes"), "完整 Key 未泄露")
    r = admin.get("/models")
    check(r.status_code == 200, "配置后可校验模型列表", str(r.status_code))

    # 流式聊天 + 服务端存储
    turn1 = [{"role": "user", "content": [{"type": "text", "text": "你好"}]}]
    reply1 = chat(admin, turn1)
    check(reply1 == "[mock] 你好", "流式对话返回内容", reply1)

    r = admin.get("/api/admin/conversations")
    convs = r.json()["conversations"]
    check(len(convs) == 1, "会话已写入服务器", str(len(convs)))
    conv_id = convs[0]["id"] if convs else 0
    detail = admin.get(f"/api/admin/conversations/{conv_id}").json()
    check(detail["message_count"] == 2, "用户消息与助手回复均已入库", str(detail["message_count"]))
    check(detail["messages"][0]["role"] == "user", "首条为 user")
    check(detail["messages"][1]["role"] == "assistant", "次条为 assistant")

    # 第二轮：完整历史应归入同一会话
    turn2 = turn1 + [
        {"role": "assistant", "content": [{"type": "text", "text": "[mock] 你好"}]},
        {"role": "user", "content": [{"type": "text", "text": "继续"}]},
    ]
    chat(admin, turn2)
    detail = admin.get(f"/api/admin/conversations/{conv_id}").json()
    check(detail["message_count"] == 4, "第二轮消息归入同一会话", str(detail["message_count"]))
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 1, "未误建新会话")

    # 第三轮：助手历史被前端重新渲染（文本不同）时，仍应命中同一会话
    turn3 = [
        turn1[0],
        {"role": "assistant", "content": [{"type": "text", "text": "前端重新渲染的文本"}]},
        {"role": "user", "content": [{"type": "text", "text": "继续"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "另一段重新渲染"}]},
        {"role": "user", "content": [{"type": "text", "text": "第三轮"}]},
    ]
    chat(admin, turn3)
    detail = admin.get(f"/api/admin/conversations/{conv_id}").json()
    check(detail["message_count"] == 6, "助手文本变化不应拆分会话", str(detail["message_count"]))
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 1, "仍然只有一个会话")

    # 用户管理
    r = admin.post("/api/admin/users", json={"username": "alice", "password": "alice12345", "role": "user"})
    check(r.status_code == 200, "创建普通用户", r.text)
    alice_id = r.json()["id"]

    r = admin.post("/api/admin/users", json={"username": "longpw", "password": "a" * 80, "role": "user"})
    check(r.status_code == 400, "超长密码被拒绝", str(r.status_code))

    alice = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)
    r = login(alice, "alice", "alice12345")
    check(r.status_code == 200 and r.json()["user"]["role"] == "user", "普通用户登录成功")

    r = alice.get("/api/admin/users")
    check(r.status_code == 403, "普通用户禁止访问用户管理", str(r.status_code))
    r = alice.get("/api/admin/apikey")
    check(r.status_code == 403, "普通用户禁止读取 API Key", str(r.status_code))
    r = alice.get("/admin")
    check(r.status_code == 302, "普通用户访问后台被重定向", str(r.status_code))

    # 普通用户聊天使用服务器 Key，且会话归属正确
    alice_msgs = [{"role": "user", "content": [{"type": "text", "text": "alice 你好"}]}]
    reply = chat(alice, alice_msgs)
    check(reply.startswith("[mock] alice"), "普通用户可聊天", reply)
    r = alice.get("/api/my/conversations")
    check(r.status_code == 200 and len(r.json()["conversations"]) == 1, "用户可查看自己的服务端会话")
    alice_conv_id = r.json()["conversations"][0]["id"]
    r = alice.get(f"/api/admin/conversations/{alice_conv_id}")
    check(r.status_code == 403, "普通用户不能读取他人会话")

    # 管理员查看全部会话
    r = admin.get("/api/admin/conversations")
    check(len(r.json()["conversations"]) == 2, "管理员可查看全部会话", str(len(r.json()["conversations"])))

    # 前端构建产物
    r = admin.get("/")
    check(r.status_code == 200, "登录后可访问前端首页")
    check("<title>GuGuAI</title>" in r.text, "网页标题为 GuGuAI")
    match = re.search(r"static/js/(main\.[0-9a-f]+\.js)", r.text)
    check(match is not None, "首页引用构建后的主脚本")
    bundle = admin.get(f"/static/js/{match.group(1)}").text if match else ""
    check("managed-by-server" in bundle, "前端使用服务端托管 Key 占位")
    check("X-Chat-Session-Id" in bundle, "前端请求携带会话同步头")
    check("api.deepseek.com" not in bundle, "前端不再直连 DeepSeek")
    check("GuGuAI" in bundle and "Powered by Deepseek." in bundle, "聊天界面使用 GuGuAI 品牌与副标题")
    check("__SERVER_MANAGED__" not in bundle, "构建期常量已注入")

    # ------------------------------------------------------------------
    # 服务端会话同步（新版前端）
    # ------------------------------------------------------------------
    r = admin.post("/api/admin/users", json={"username": "syncuser", "password": "syncpass123", "role": "user"})
    check(r.status_code == 200, "创建同步测试用户", r.text)
    sync_user = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)
    r = login(sync_user, "syncuser", "syncpass123")
    check(r.status_code == 200, "同步测试用户登录")

    def make_session(sid: str, title: str, status: str, reply: str) -> dict:
        session = {
            "id": sid,
            "title": title,
            "createdAt": 1700000000000,
            "updatedAt": 1700000001000,
            "rootMessageId": None,
            "messages": [
                {"id": f"{sid}-u", "role": "user", "content": "你好", "files": [], "createdAt": 1700000000000},
                {
                    "id": f"{sid}-a",
                    "role": "assistant",
                    "createdAt": 1700000001000,
                    "model": "deepseek-flash",
                    "prefix": True,
                    "status": status,
                    "blocks": [{"type": "text", "text": reply}],
                    "usage": {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 5,
                    },
                },
            ],
        }
        return session

    sid1 = "sync-session-0001"
    pending = make_session(sid1, "同步会话一", "streaming", "生成中")
    r = sync_user.put(f"/api/sync/sessions/{sid1}", json=pending)
    check(r.status_code == 200 and r.json()["projected"] is False, "生成中的会话只存快照不投影", r.text)
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 2, "生成中不新增后台会话")

    r = sync_user.get("/api/sync/sessions")
    check(r.status_code == 200 and len(r.json()["sessions"]) == 1, "同步列表返回会话摘要")
    r = sync_user.get(f"/api/sync/sessions/{sid1}")
    check(r.status_code == 200 and r.json()["session"]["title"] == "同步会话一", "同步详情可回读")

    r = sync_user.patch(f"/api/sync/sessions/{sid1}", json={"title": "重命名后的会话"})
    check(r.status_code == 200, "同步会话可重命名", r.text)
    check(sync_user.get("/api/sync/sessions").json()["sessions"][0]["title"] == "重命名后的会话", "重命名已生效")

    settled = make_session(sid1, "重命名后的会话", "done", "你好呀")
    r = sync_user.put(f"/api/sync/sessions/{sid1}", json=settled)
    check(r.status_code == 200 and r.json()["projected"] is True, "完成后的会话投影到后台", r.text)
    older = make_session(sid1, "旧快照", "done", "旧内容")
    older["updatedAt"] = settled["updatedAt"] - 1000
    r = sync_user.put(f"/api/sync/sessions/{sid1}", json=older)
    check(r.status_code == 200 and r.json()["stored"] is False, "旧快照不会覆盖新快照", r.text)
    r = sync_user.get(f"/api/sync/sessions/{sid1}")
    check(r.json()["session"]["title"] == "重命名后的会话", "服务端仍保留较新的快照")
    bad = make_session("other-id", "错误 ID", "done", "x")
    r = sync_user.put(f"/api/sync/sessions/{sid1}", json=bad)
    check(r.status_code == 400, "会话 ID 与路径不一致被拒绝", str(r.status_code))
    convs = admin.get("/api/admin/conversations").json()["conversations"]
    check(len(convs) == 3, "后台新增投影会话", str(len(convs)))
    projected = next(c for c in convs if c["title"] == "重命名后的会话")
    detail = admin.get(f"/api/admin/conversations/{projected['id']}").json()
    check(detail["message_count"] == 2, "后台可见同步消息", str(detail["message_count"]))

    # 带会话头的聊天请求不应再生成一条重复会话
    chat(sync_user, [{"role": "user", "content": [{"type": "text", "text": "你好"}]}], session_id=sid1)
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 3, "带会话头的聊天不重复建会话")

    sid2 = "sync-session-0002"
    r = sync_user.put(f"/api/sync/sessions/{sid2}", json=make_session(sid2, "同步会话二", "done", "第二个"))
    check(r.status_code == 200, "可同步多个会话")
    check(len(sync_user.get("/api/sync/sessions").json()["sessions"]) == 2, "同步列表包含两个会话")
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 4, "后台可见两个同步会话")

    # 历史导入/删除全部：整体替换该用户的会话
    r = sync_user.post("/api/sync/sessions/replace", json={"sessions": [settled]})
    check(r.status_code == 200 and r.json()["count"] == 1, "整体替换同步会话", r.text)
    check(len(sync_user.get("/api/sync/sessions").json()["sessions"]) == 1, "替换后只剩一个会话")
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 3, "替换后后台会话数正确")

    r = sync_user.delete(f"/api/sync/sessions/{sid1}")
    check(r.status_code == 200, "删除同步会话")
    check(len(sync_user.get("/api/sync/sessions").json()["sessions"]) == 0, "删除后同步列表为空")
    check(len(admin.get("/api/admin/conversations").json()["conversations"]) == 2, "删除后后台会话数正确")

    r = alice.get("/api/sync/sessions")
    check(r.status_code == 200 and len(r.json()["sessions"]) == 0, "普通用户只能看到自己的同步会话")

    r = admin.get("/admin")
    check(r.status_code == 200 and "GuGuAI 管理后台" in r.text, "管理后台使用 GuGuAI 品牌")

    # 修改密码后旧会话失效
    old_cookie = alice.cookies.get("ds_chat_session")
    r = alice.post("/api/auth/change-password", json={"current_password": "alice12345", "new_password": "alice67890"})
    check(r.status_code == 200, "修改密码成功", r.text)
    r = alice.get("/api/auth/me")
    check(r.status_code == 200, "修改密码后新会话有效")
    stale = httpx.Client(base_url=BASE, timeout=30, cookies={"ds_chat_session": old_cookie or "invalid"})
    r = stale.get("/api/auth/me")
    check(r.status_code == 401, "修改密码后旧会话已失效", str(r.status_code))

    # 用户侧服务器历史页
    r = alice.get("/ui/history")
    check(r.status_code == 200 and "我的会话记录" in r.text, "用户可访问服务器历史页")

    # 退出
    r = admin.post("/api/auth/logout")
    check(r.status_code == 200, "退出登录")
    r = admin.get("/api/auth/me")
    check(r.status_code == 401, "退出后会话失效")

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for f in failures:
            print(" -", f)
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
