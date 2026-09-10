import json

import httpx

from backend.panel import google_drive


def test_auto_upload_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_DRIVE_AUTO_UPLOAD", raising=False)
    path = tmp_path / "export.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    assert google_drive.upload_export_if_enabled(str(path)) is None


def test_resumable_upload_uses_refresh_token_and_keeps_filename(monkeypatch, tmp_path):
    path = tmp_path / "聊天_20260910_160000_export.jsonl"
    path.write_text('{"hello":"world"}\n', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-123")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_DRIVE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.delenv("GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)

    requests = []

    def handler(request):
        requests.append(request)
        if request.url == httpx.URL(google_drive.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"location": "https://upload.example/session"},
            )
        return httpx.Response(
            200,
            json={
                "id": "file-123",
                "name": path.name,
                "size": str(path.stat().st_size),
                "webViewLink": "https://drive.google.com/file/d/file-123/view",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = google_drive.upload_file(str(path), client=client)

    assert result["id"] == "file-123"
    assert len(requests) == 3
    metadata = json.loads(requests[1].content.decode("utf-8"))
    assert metadata == {"name": path.name, "parents": ["folder-123"]}
    assert requests[2].content == path.read_bytes()


def test_upload_failure_does_not_mark_local_export_failed(monkeypatch, tmp_path):
    from backend import control_panel
    from common import paths

    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        google_drive,
        "upload_export_if_enabled",
        lambda _path: (_ for _ in ()).throw(RuntimeError("token invalid")),
    )
    monkeypatch.setattr(control_panel, "_do_database_export", lambda: str(tmp_path / "x.db"))
    (tmp_path / "x.db").write_bytes(b"db")
    control_panel._export_state.update(
        {"status": "idle", "file_path": None, "message": "", "drive_url": None}
    )

    control_panel._do_export("database", "", None)

    assert control_panel._export_state["status"] == "completed"
    assert control_panel._export_state["drive_status"] == "failed"
    assert "Google Drive 上传失败" in control_panel._export_state["message"]
