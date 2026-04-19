"""
tests/test_server.py
──────────────────────
Security-axis tests for server.py (DX-SEC-001, DX-SEC-002, DX-SEC-003).

Run from G:/dxf2svg/:
    G:/dxf2svg/.venv/Scripts/pytest tests/test_server.py -v
"""

import io
import json
import os
import sys

import pytest

# Make sure dxf2svg package is importable when running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app  # noqa: E402 — server.py is in repo root

FIXTURE_DXF = os.path.join(os.path.dirname(__file__), "fixtures", "minimal.dxf")


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _dxf_bytes():
    with open(FIXTURE_DXF, "rb") as f:
        return f.read()


# ── DX-SEC-001: debug mode ────────────────────────────────────────────────────

def test_debug_mode_off_by_default():
    """FLASK_DEBUG env var must be absent / falsy for debug to be off (DX-SEC-001)."""
    env_val = os.environ.get("FLASK_DEBUG", "")
    assert env_val.lower() != "true", (
        "FLASK_DEBUG=true is set in the environment — the server will start with "
        "Werkzeug debugger enabled. Unset it for production use."
    )


# ── DX-SEC-002: upload size cap ───────────────────────────────────────────────

def test_upload_size_cap_is_configured():
    """MAX_CONTENT_LENGTH must be set to a finite value (DX-SEC-002)."""
    cap = app.config.get("MAX_CONTENT_LENGTH")
    assert cap is not None, "MAX_CONTENT_LENGTH is not set — large uploads will OOM the server"
    assert cap <= 100 * 1024 * 1024, f"MAX_CONTENT_LENGTH={cap} is unreasonably large"


def test_oversized_upload_returns_413(client):
    """Flask auto-rejects bodies larger than MAX_CONTENT_LENGTH with 413 (DX-SEC-002)."""
    original_cap = app.config["MAX_CONTENT_LENGTH"]
    try:
        # Temporarily lower the cap so we can test with a small payload
        app.config["MAX_CONTENT_LENGTH"] = 64
        payload = b"0\n" * 100  # 200 bytes > 64 byte cap
        data = {"file": (io.BytesIO(payload), "too_big.dxf")}
        resp = client.post("/api/convert", data=data, content_type="multipart/form-data")
        assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"
    finally:
        app.config["MAX_CONTENT_LENGTH"] = original_cap


# ── DX-SEC-003: file type validation ─────────────────────────────────────────

def test_non_dxf_extension_rejected(client):
    """Files with a non-.dxf extension must be rejected with 400 (DX-SEC-003)."""
    data = {"file": (io.BytesIO(_dxf_bytes()), "drawing.svg")}
    resp = client.post("/api/convert", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "dxf" in body.get("error", "").lower()


def test_binary_file_rejected(client):
    """A binary blob renamed to .dxf must be rejected with 400 (DX-SEC-003)."""
    # PNG magic bytes
    fake_dxf = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 100
    data = {"file": (io.BytesIO(fake_dxf), "not_really.dxf")}
    resp = client.post("/api/convert", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "dxf" in body.get("error", "").lower() or "non-text" in body.get("error", "").lower()


def test_valid_dxf_accepted(client):
    """A real .dxf file must pass validation (DX-SEC-003 non-regression)."""
    data = {
        "file": (io.BytesIO(_dxf_bytes()), "minimal.dxf"),
        "options": json.dumps({"flipY": True}),
    }
    resp = client.post("/api/convert", data=data, content_type="multipart/form-data")
    # Accept 200 (converted) or 500 (ezdxf parse error on minimal fixture) —
    # what must NOT happen is a 400 from our own validation gate.
    assert resp.status_code != 400, (
        f"Valid .dxf was wrongly rejected: {json.loads(resp.data)}"
    )


def test_blocks_endpoint_validates_extension(client):
    """The /api/blocks endpoint applies the same file type check (DX-SEC-003)."""
    data = {"file": (io.BytesIO(_dxf_bytes()), "drawing.txt")}
    resp = client.post("/api/blocks", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
