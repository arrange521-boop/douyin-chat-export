"""Optional Google Drive upload for completed exports.

The Railway service cannot reuse a user's ChatGPT/Google connector session.
Instead it uses a standard Google OAuth refresh token supplied as Railway
environment variables.  Local export/download remains available when Drive is
disabled or an upload fails.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path

import httpx


TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_TRUE_VALUES = {"1", "true", "yes", "on"}


class GoogleDriveUploadError(RuntimeError):
    """Raised when Drive is enabled but an export cannot be uploaded."""


def auto_upload_enabled() -> bool:
    return os.getenv("GOOGLE_DRIVE_AUTO_UPLOAD", "").strip().lower() in _TRUE_VALUES


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise GoogleDriveUploadError(f"缺少环境变量 {name}")
    return value


def _access_token(client: httpx.Client) -> str:
    # A fixed access token is useful for short-lived diagnostics.  Production
    # should use a refresh token because access tokens normally expire hourly.
    fixed = os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN", "").strip()
    if fixed:
        return fixed

    response = client.post(
        TOKEN_URL,
        data={
            "client_id": _required_env("GOOGLE_DRIVE_CLIENT_ID"),
            "client_secret": _required_env("GOOGLE_DRIVE_CLIENT_SECRET"),
            "refresh_token": _required_env("GOOGLE_DRIVE_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
    )
    if response.status_code >= 400:
        raise GoogleDriveUploadError(
            f"Google OAuth 刷新失败 (HTTP {response.status_code})"
        )
    token = response.json().get("access_token")
    if not token:
        raise GoogleDriveUploadError("Google OAuth 未返回 access_token")
    return token


def _put_with_retry(
    client: httpx.Client,
    session_url: str,
    path: Path,
    headers: dict[str, str],
) -> httpx.Response:
    last_response = None
    for attempt in range(3):
        with path.open("rb") as payload:
            response = client.put(session_url, headers=headers, content=payload)
        last_response = response
        if response.status_code not in {429, 500, 502, 503, 504}:
            return response
        time.sleep(2**attempt)
    assert last_response is not None
    return last_response


def upload_file(path: str, *, client: httpx.Client | None = None) -> dict:
    """Upload *path* to the configured folder and return Drive metadata."""
    export_path = Path(path)
    if not export_path.is_file():
        raise GoogleDriveUploadError("待上传的导出文件不存在")

    folder_id = _required_env("GOOGLE_DRIVE_FOLDER_ID")
    mime_type = mimetypes.guess_type(export_path.name)[0] or "application/octet-stream"
    size = export_path.stat().st_size

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0))

    try:
        token = _access_token(client)
        start = client.post(
            UPLOAD_URL,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": "id,name,size,webViewLink",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size),
            },
            content=json.dumps(
                {"name": export_path.name, "parents": [folder_id]},
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        if start.status_code >= 400:
            raise GoogleDriveUploadError(
                f"Google Drive 创建上传任务失败 (HTTP {start.status_code})"
            )
        session_url = start.headers.get("location")
        if not session_url:
            raise GoogleDriveUploadError("Google Drive 未返回上传地址")

        uploaded = _put_with_retry(
            client,
            session_url,
            export_path,
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": mime_type,
                "Content-Length": str(size),
            },
        )
        if uploaded.status_code >= 400:
            raise GoogleDriveUploadError(
                f"Google Drive 上传失败 (HTTP {uploaded.status_code})"
            )
        result = uploaded.json()
        if not result.get("id"):
            raise GoogleDriveUploadError("Google Drive 未返回文件 ID")
        return result
    except httpx.HTTPError as exc:
        raise GoogleDriveUploadError(f"Google Drive 网络错误: {exc}") from exc
    finally:
        if owns_client:
            client.close()


def upload_export_if_enabled(path: str) -> dict | None:
    if not auto_upload_enabled():
        return None
    return upload_file(path)
