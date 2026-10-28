"""Local API: access protection, secrets handling and core endpoints."""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from medleser.context import build_context
from medleser.documents.model import Block, Document
from medleser.logging_setup import redaction_filter, setup_logging
from medleser.server import COOKIE, create_app
from medleser.storage.secrets import SecretStore

from .test_elevenlabs import KEY, FakeElevenLabs

H = {"X-Medleser": "1"}


@pytest.fixture
def fake_api():
    return FakeElevenLabs()


@pytest.fixture
def ctx(app_paths, memory_keyring, fake_api):
    context = build_context(app_paths, secret_store=SecretStore(), http_transport=httpx.MockTransport(fake_api.handler))
    yield context
    context.close()


@pytest.fixture
def client(ctx):
    app = create_app(ctx, testing=True)
    with TestClient(app) as c:
        r = c.get(f"/start?token={ctx.token}", follow_redirects=False)
        assert r.status_code == 303
        yield c


def test_requires_session_cookie(ctx):
    app = create_app(ctx, testing=True)
    with TestClient(app) as c:
        assert c.get("/api/app").status_code == 401
        assert c.get("/").status_code == 401
        assert c.get("/start?token=wrong", follow_redirects=False).status_code == 401
        r = c.get(f"/start?token={ctx.token}", follow_redirects=False)
        assert r.status_code == 303
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and COOKIE in cookie
        assert c.get("/api/app").status_code == 200


def test_security_headers_and_static_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>Medleser</title>" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/app").headers["cache-control"] == "no-store"


def test_state_changing_requests_need_header_and_same_origin(client, ctx):
    assert client.put("/api/settings/reading", json={"font_size": 30}).status_code == 403
    assert client.put("/api/settings/reading", json={"font_size": 30}, headers=H).status_code == 200
    ctx.port = 4321
    try:
        r = client.put("/api/settings/reading", json={}, headers={**H, "Host": "127.0.0.1:4321", "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = client.get("/api/app", headers={"Host": "evil.example:4321"})
        assert r.status_code == 403  # DNS-rebinding protection
        r = client.get("/api/app", headers={"Host": "127.0.0.1:4321"})
        assert r.status_code == 200
    finally:
        ctx.port = None


def test_api_key_is_stored_securely_and_never_leaks(client, ctx, memory_keyring, app_paths):
    setup_logging(app_paths.logs)
    redaction_filter().add_secret(ctx.token)
    r = client.post("/api/providers/elevenlabs/key", json={"api_key": KEY}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"]["state"] == "ready"
    assert KEY not in r.text
    assert memory_keyring.store[("Medleser", "elevenlabs-api-key")] == KEY
    providers = client.get("/api/providers")
    assert KEY not in providers.text
    logging.getLogger("test").warning("Attempt to log a key: %s", KEY)
    logging.shutdown()
    for path in app_paths.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), f"key found in {path}"
    # Disconnect removes the key.
    assert client.delete("/api/providers/elevenlabs/key", headers=H).status_code == 200
    assert ("Medleser", "elevenlabs-api-key") not in memory_keyring.store


def test_invalid_key_is_not_kept(client, memory_keyring):
    r = client.post("/api/providers/elevenlabs/key", json={"api_key": "sk_invalid_key_123"}, headers=H)
    assert r.json()["status"]["state"] == "error"
    assert ("Medleser", "elevenlabs-api-key") not in memory_keyring.store


def _make_project(ctx, tmp_path, provider="elevenlabs", voice="voice123"):
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    pid = ctx.store.create(src, "a.txt", 1)
    doc = Document(title="Test", source_name="a.txt", source_type="txt", size_bytes=1, language="nb",
                   blocks=[Block("b00000", "paragraph", "Dette er en kort tekst som skal leses opp.")])
    ctx.store.save_document(pid, doc)
    ctx.store.update_settings(pid, provider=provider, voice_id=voice, model_id="eleven_flash_v2_5", language="nb")
    ctx.db.update_project(pid, status="ready")
    return pid


def test_paid_generation_requires_confirmation(client, ctx, tmp_path, fake_api):
    client.post("/api/providers/elevenlabs/key", json={"api_key": KEY}, headers=H)
    pid = _make_project(ctx, tmp_path)
    est = client.get(f"/api/projects/{pid}/estimate").json()
    assert est["provider"]["is_paid"] and est["characters_pending"] == 42
    assert est["usage"]["exact"] is False and est["usage"]["credits"] == pytest.approx(21)  # Flash: 0.5 per char
    calls_before = len(fake_api.requests)
    r = client.post(f"/api/projects/{pid}/generate", json={}, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_required"
    r = client.post(f"/api/projects/{pid}/generate", json={"confirmation_token": "forged"}, headers=H)
    assert r.status_code == 409
    assert len(fake_api.requests) == calls_before  # nothing was sent
    r = client.post(f"/api/projects/{pid}/generate", json={"confirmation_token": est["confirmation_token"]}, headers=H)
    assert r.status_code == 200
    job_id = r.json()["job"]["id"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running"):
            break
        import time
        time.sleep(0.05)
    assert job["state"] == "done", job
    sync = client.get(f"/api/projects/{pid}/sync").json()
    assert sync["precision"] == "word" and len(sync["words"]) == 9
    audio = client.get(f"/api/projects/{pid}/audio", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.content) == 100  # seeking works in the player
    # Re-generating does not send anything again.
    est2 = client.get(f"/api/projects/{pid}/estimate").json()
    assert est2["segments_pending"] == 0


def test_edit_and_restore_block_keeps_original_and_footnote_markers(client, ctx, tmp_path):
    from medleser.documents.model import Span

    src = tmp_path / "f.txt"
    src.write_text("x", encoding="utf-8")
    pid = ctx.store.create(src, "f.txt", 1)
    text = "Lesing er viktig.1 Se https://example.org for mer."
    marker = text.index("1")
    url = text.index("https")
    block = Block("b00000", "paragraph", text,
                  spans=[Span(marker, marker + 1, "footnote_ref"), Span(url, url + len("https://example.org"), "url")])
    ctx.store.save_document(pid, Document(title="F", source_name="f.txt", source_type="txt", size_bytes=1,
                                          language="nb", blocks=[block]))
    r = client.put(f"/api/projects/{pid}/blocks/b00000", json={"text": "Lesing er svært viktig. Se https://example.org."},
                   headers=H)
    assert r.status_code == 200
    edited = r.json()["block"]
    assert edited["original_text"] == text
    assert [s["kind"] for s in edited["spans"]] == ["url"]  # spans recomputed for the new text
    r = client.post(f"/api/projects/{pid}/blocks/b00000/restore", headers=H)
    restored = r.json()["block"]
    assert restored["text"] == text and "original_text" not in restored
    assert {s["kind"] for s in restored["spans"]} == {"footnote_ref", "url"}
    assert {s["kind"] for s in r.json()["decision"]["skipped_spans"]} == {"footnote_ref", "url"}


def test_paragraph_choices_are_merged_not_replaced(client, ctx, tmp_path):
    pid = _make_project(ctx, tmp_path, provider="windows", voice="x")
    client.patch(f"/api/projects/{pid}", json={"options": {"block_overrides": {"b00000": False}}}, headers=H)
    r = client.patch(f"/api/projects/{pid}", json={"options": {"block_overrides": {"b00007": False}}}, headers=H)
    overrides = r.json()["settings"]["options"]["block_overrides"]
    assert overrides == {"b00000": False, "b00007": False}  # the first choice is kept


def test_validation_errors_never_echo_submitted_values(client):
    secret = "sk_" + "x" * 400
    r = client.post("/api/providers/elevenlabs/key", json={"api_key": secret}, headers=H)
    assert r.status_code == 422
    assert secret not in r.text and "xxxx" not in r.text


def test_cached_paid_sample_is_not_counted_twice(client, ctx, tmp_path):
    client.post("/api/providers/elevenlabs/key", json={"api_key": KEY}, headers=H)
    pid = _make_project(ctx, tmp_path)
    body = {"provider": "elevenlabs", "voice_id": "voice123", "language": "nb", "model_id": "eleven_flash_v2_5",
            "project_id": pid, "use_document_text": True}
    assert client.post("/api/samples", json=body, headers=H).status_code == 409  # needs confirmation
    for _ in range(2):
        assert client.post("/api/samples", json={**body, "confirm_paid": True}, headers=H).status_code == 200
    assert ctx.db.usage_for_project(pid, "elevenlabs") == 42  # one sample, recorded once


def test_deleted_project_folder_is_not_recreated(ctx, tmp_path):
    from medleser.errors import UserFacingError

    pid = _make_project(ctx, tmp_path, provider="windows", voice="x")
    ctx.store.delete(pid)
    with pytest.raises(UserFacingError):
        ctx.store.segments_dir(pid)
    assert not (ctx.paths.projects / pid).exists()


def test_tracebacks_in_log_are_redacted(app_paths):
    setup_logging(app_paths.logs)
    try:
        raise RuntimeError(f"request failed with key {KEY}")
    except RuntimeError:
        logging.getLogger("test").exception("Synthesis failed")
    logging.shutdown()
    text = (app_paths.logs / "medleser.log").read_text(encoding="utf-8")
    assert "Synthesis failed" in text and KEY not in text


def test_free_sample_and_project_delete(client, ctx, tmp_path):
    pid = _make_project(ctx, tmp_path, provider="windows", voice="x")
    r = client.delete(f"/api/projects/{pid}", headers=H)
    assert r.status_code == 200 and not ctx.store.exists(pid)
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/projects/../../etc").status_code in (404, 405)


def test_external_links_are_allow_listed(client, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert client.post("/api/system/open-link", json={"target": "elevenlabs_api_keys"}, headers=H).status_code == 200
    assert opened == ["https://elevenlabs.io/app/settings/api-keys"]
    r = client.post("/api/system/open-link", json={"target": "https://evil.example"}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/system/open-folder", json={"target": "exported", "path": "C:\\Windows\\notepad.exe"}, headers=H)
    assert r.status_code == 400


def test_save_mp3_dialog_uses_a_file_filter_pywebview_accepts(client, ctx, tmp_path, monkeypatch):
    import webview
    from webview.util import parse_file_type

    pid = _make_project(ctx, tmp_path, provider="windows", voice="x")
    ctx.store.audio_path(pid).parent.mkdir(parents=True, exist_ok=True)
    ctx.store.audio_path(pid).write_bytes(b"ID3fake")
    dest = tmp_path / "ut" / "Test"

    class FakeWindow:
        def create_file_dialog(self, dialog_type, directory="", save_filename="", file_types=()):
            assert dialog_type == webview.FileDialog.SAVE
            for file_type in file_types:
                parse_file_type(file_type)  # raises ValueError like the real dialog
            return str(dest)

    monkeypatch.setattr(ctx, "window", FakeWindow())
    monkeypatch.setattr(ctx, "export_dir", lambda: tmp_path / "ut")
    r = client.post(f"/api/projects/{pid}/export", json={"mode": "single"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == [str(dest.with_suffix(".mp3"))]
    assert dest.with_suffix(".mp3").read_bytes() == b"ID3fake"
