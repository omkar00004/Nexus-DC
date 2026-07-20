"""Submittal version retention: hashing, upload, listing, activate, delete."""
import json

import pytest
from fastapi import HTTPException

from tests.conftest import make_pdf

GOOD = make_pdf("Vendor Submittal for UPS-02A - rated output 2.1 MVA")
GOOD_B = make_pdf("Vendor Submittal for UPS-02A - rated output 1.8 MVA")
NO_TAG = make_pdf("Vendor Submittal for CRAH-11 - no UPS tag here")


def upload(client, data: bytes, doc_type="submittal", name="sub.pdf", by="QA"):
    return client.post("/documents/upload",
                       files={"file": (name, data, "application/pdf")},
                       data={"doc_type": doc_type, "uploaded_by": by})


# ------------------------------------------------------------------ hashing

def test_sha12_deterministic_and_hex(env):
    a = env.api_main._sha12(b"same bytes")
    b = env.api_main._sha12(b"same bytes")
    c = env.api_main._sha12(b"other bytes")
    assert a == b != c
    assert len(a) == 12 and int(a, 16) >= 0


def test_save_version_idempotent(env):
    vid1 = env.api_main._save_version(GOOD)
    vid2 = env.api_main._save_version(GOOD)
    assert vid1 == vid2
    assert len(list(env.api_main._VERSIONS_DIR.glob("*.pdf"))) == 1
    vid3 = env.api_main._save_version(GOOD_B)
    assert vid3 != vid1
    assert len(list(env.api_main._VERSIONS_DIR.glob("*.pdf"))) == 2


def test_live_vid_none_when_no_submittal(env):
    assert env.api_main._live_submittal_vid() is None


# ------------------------------------------------------------------- upload

def test_upload_submittal_retains_version(env, client):
    r = upload(client, GOOD)
    assert r.status_code == 200, r.text
    assert env.config.SUBMITTAL_PDF.read_bytes() == GOOD
    log = json.loads((env.data / "document_log.json").read_text())
    assert log[-1]["version_id"] == env.api_main._sha12(GOOD)
    assert log[-1]["uploaded_by"] == "QA"


def test_upload_rejects_non_pdf(client):
    r = upload(client, b"plain text, not a pdf")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_upload_rejects_wrong_equipment_tag(client):
    r = upload(client, NO_TAG)
    assert r.status_code == 400
    assert "UPS-02A" in r.json()["detail"]


def test_upload_rejects_empty_file(client):
    r = upload(client, b"")
    assert r.status_code == 400


def test_upload_rejects_oversize(client):
    r = upload(client, b"%PDF" + b"0" * (20 * 1024 * 1024))
    assert r.status_code == 413


def test_upload_reference_rejects_bad_extension(client):
    r = upload(client, b"x", doc_type="reference", name="notes.exe")
    assert r.status_code == 400


def test_upload_reference_cannot_shadow_pipeline_source(client):
    r = upload(client, b"x", doc_type="reference", name="schedule.xer")
    assert r.status_code == 400
    assert "pipeline source" in r.json()["detail"]


def test_upload_rejects_unknown_doc_type(client):
    r = upload(client, GOOD, doc_type="malware")
    assert r.status_code == 400


# ------------------------------------------------------------------ listing

def test_versions_listing_marks_live_and_deletable(env, client):
    upload(client, GOOD)
    upload(client, GOOD_B)          # supersedes -> GOOD_B is live
    versions = env.api_main._submittal_versions()
    assert len(versions) == 2
    assert versions[0]["live"] is True          # live sorts first
    assert versions[0]["version_id"] == env.api_main._sha12(GOOD_B)
    assert versions[0]["deletable"] is False    # live is never deletable
    assert versions[1]["deletable"] is True


def test_versions_listing_non_live_sorted_newest_first(env, client):
    third = make_pdf("UPS-02A third revision")
    for data in (GOOD, GOOD_B, third):          # third is live; GOOD oldest
        upload(client, data)
    versions = env.api_main._submittal_versions()
    assert [v["live"] for v in versions] == [True, False, False]
    non_live_ts = [v["ts"] for v in versions[1:]]
    assert non_live_ts == sorted(non_live_ts, reverse=True)   # newest first


def test_activation_keeps_original_upload_label(env, client):
    upload(client, GOOD, name="R2-Final-Signed.pdf")
    upload(client, GOOD_B)
    vid = env.api_main._sha12(GOOD)
    client.post(f"/documents/submittal/versions/{vid}/activate", json={"by": "PM"})
    versions = env.api_main._submittal_versions()
    labels = {v["version_id"]: v["label"] for v in versions}
    assert labels[vid] == "R2-Final-Signed.pdf"   # not "activated <vid>"


def test_seeded_assets_always_retained_and_not_deletable(env, client):
    (env.assets / "ups_submittal_rev2.pdf").write_bytes(GOOD_B)
    (env.assets / "ups_submittal_rev3.pdf").write_bytes(GOOD)
    versions = env.api_main._submittal_versions()
    assert {v["label"] for v in versions} == {
        "Revision R2 - 1.8 MVA (bundled)", "Revision R3 - 2.1 MVA (bundled)"}
    assert all(v["deletable"] is False for v in versions)
    assert all(v["uploaded_by"] == "bundled asset" for v in versions)


def test_documents_endpoint_embeds_versions_for_submittal(env, client):
    upload(client, GOOD)
    r = client.get("/documents")
    assert r.status_code == 200
    subs = [d for d in r.json()["documents"] if d["name"] == "ups_submittal.pdf"]
    assert len(subs) == 1
    assert len(subs[0]["submittal_versions"]) == 1
    assert subs[0]["submittal_versions"][0]["live"] is True


# ----------------------------------------------------------------- activate

def test_activate_swaps_live_submittal(env, client):
    upload(client, GOOD)
    upload(client, GOOD_B)
    old_vid = env.api_main._sha12(GOOD)
    r = client.post(f"/documents/submittal/versions/{old_vid}/activate",
                    json={"by": "PM"})
    assert r.status_code == 200
    assert r.json()["status"] == "activated"
    assert env.config.SUBMITTAL_PDF.read_bytes() == GOOD
    log = json.loads((env.data / "document_log.json").read_text())
    assert log[-1]["original_name"] == f"activated {old_vid}"
    assert log[-1]["uploaded_by"] == "PM"


def test_activate_already_live_is_noop(env, client):
    upload(client, GOOD)
    vid = env.api_main._sha12(GOOD)
    before = env.config.SUBMITTAL_PDF.stat().st_mtime_ns
    r = client.post(f"/documents/submittal/versions/{vid}/activate", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "already live"
    assert env.config.SUBMITTAL_PDF.stat().st_mtime_ns == before


def test_activate_unknown_version_404(client):
    r = client.post("/documents/submittal/versions/abcdef123456/activate", json={})
    assert r.status_code == 404


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "../../etc/passwd", "",
                                 "ABCDEF123456", "abcdef12345", "abc\x00def12345"])
def test_activate_rejects_traversal_ids(env, bad):
    from api.main import ActivateBody, activate_submittal_version
    with pytest.raises(HTTPException) as exc:
        activate_submittal_version(bad, ActivateBody())
    assert exc.value.status_code == 404


# ------------------------------------------------------------------- delete

def test_delete_live_version_blocked(env, client):
    upload(client, GOOD)
    vid = env.api_main._sha12(GOOD)
    r = client.delete(f"/documents/submittal/versions/{vid}")
    assert r.status_code == 400
    assert env.config.SUBMITTAL_PDF.exists()


def test_delete_unknown_version_404(client):
    r = client.delete("/documents/submittal/versions/abcdef123456")
    assert r.status_code == 404


@pytest.mark.parametrize("bad", ["..", "a/b", "../../etc/passwd",
                                 "ABCDEF123456", "abc\x00def12345"])
def test_delete_rejects_traversal_ids(env, bad):
    from api.main import delete_submittal_version
    with pytest.raises(HTTPException) as exc:
        delete_submittal_version(bad)
    assert exc.value.status_code == 404


def test_delete_removes_file_and_doclog_entries(env, client):
    upload(client, GOOD)
    upload(client, GOOD_B)
    old_vid = env.api_main._sha12(GOOD)
    r = client.delete(f"/documents/submittal/versions/{old_vid}")
    assert r.status_code == 200
    assert not (env.api_main._VERSIONS_DIR / f"{old_vid}.pdf").exists()
    log = json.loads((env.data / "document_log.json").read_text())
    assert all(e.get("version_id") != old_vid for e in log)


def test_deleted_seed_respawns_on_next_listing(env, client):
    (env.assets / "ups_submittal_rev2.pdf").write_bytes(GOOD_B)
    upload(client, GOOD)                       # live is GOOD, seed is GOOD_B
    env.api_main._submittal_versions()         # first listing materialises seeds
    seed_vid = env.api_main._sha12(GOOD_B)
    r = client.delete(f"/documents/submittal/versions/{seed_vid}")
    assert r.status_code == 200                # delete succeeds...
    versions = env.api_main._submittal_versions()
    assert seed_vid in {v["version_id"] for v in versions}   # ...but it respawns


# -------------------------------------------------------------- safe source

@pytest.mark.parametrize("bad", ["../secret.pdf", ".hidden", "a/b.pdf"])
def test_safe_source_rejects_bad_names(env, bad):
    from api.main import _safe_source
    with pytest.raises(HTTPException) as exc:
        _safe_source(bad)
    assert exc.value.status_code in (400, 404)


def test_get_document_serves_uploaded_reference(env, client):
    upload(client, b"ref bytes", doc_type="reference", name="site notes.pdf")
    r = client.get("/documents/site notes.pdf")
    assert r.status_code == 200
    assert r.content == b"ref bytes"
