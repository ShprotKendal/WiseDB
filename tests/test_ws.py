from __future__ import annotations

import json

from tests.server_helpers import ensure_database, login, server_client


def _recv_json(ws) -> dict:
    return json.loads(ws.receive_text())


def test_ws_login_execute_layout(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "main")
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"id": "1", "op": "login", "username": "root", "password": "root-secret"}))
            ok = _recv_json(ws)
            assert ok["type"] == "ok"
            assert "token" in ok
            ws.send_text(
                json.dumps(
                    {
                        "id": "2",
                        "op": "execute",
                        "database": "main",
                        "query": "SELECT * FROM layout",
                    }
                )
            )
            frame = _recv_json(ws)
            assert frame["type"] == "result"
            assert frame["result"]["status"] == "ok"
            assert "folders" in frame["result"]


def test_ws_stream_items(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "main")
        client.post(
            "/databases/main/execute",
            headers=headers,
            json={
                "query": (
                    "INSERT INTO pages (slug, markdown) VALUES "
                    "('n1', '---\\ntype: concept\\nslug: n1\\ntitle: One\\n---\\nOne')"
                ),
            },
        )
        client.post(
            "/databases/main/execute",
            headers=headers,
            json={
                "query": (
                    "INSERT INTO pages (slug, markdown) VALUES "
                    "('n2', '---\\ntype: concept\\nslug: n2\\ntitle: Two\\n---\\nTwo')"
                ),
            },
        )
        stream_q = "SELECT * FROM pages WS[SELECT markdown FROM pages WHERE slug = $.slug]"
        with client.websocket_connect("/ws", headers=headers) as ws:
            ws.send_text(
                json.dumps({"id": "s1", "op": "execute", "database": "main", "query": stream_q})
            )
            items = []
            while True:
                frame = _recv_json(ws)
                if frame["type"] == "item":
                    items.append(frame)
                elif frame["type"] == "done":
                    assert frame["count"] == len(items)
                    break
                else:
                    raise AssertionError(frame)
            assert len(items) == 2


def test_http_rejects_ws_syntax(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "main")
        resp = client.post(
            "/databases/main/execute",
            headers=headers,
            json={"query": "SELECT * FROM pages WS[SELECT * FROM pages]"},
        )
        assert resp.status_code == 422
        assert "WebSocket" in resp.json()["detail"]
