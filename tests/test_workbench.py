import json

import pytest
from fastapi.testclient import TestClient

from ecommerce.api.app import app
from ecommerce.repositories.sqlite import connect, init_db


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    db_path = tmp_path / "workbench.db"
    conn = connect(db_path)
    init_db(conn)
    conn.close()

    monkeypatch.setenv("ECOMMERCE_DB", str(db_path))
    monkeypatch.setenv("RECON_DETAIL_PATH", str(tmp_path / "reconciliation.csv"))
    monkeypatch.setenv("RECON_SUMMARY_PATH", str(tmp_path / "summary.json"))
    monkeypatch.setenv("RECON_UNMATCHED_PATH", str(tmp_path / "unmatched.csv"))
    monkeypatch.setenv("RECON_DATA_QUALITY_PATH", str(tmp_path / "data_quality.csv"))
    return tmp_path


def test_reconciliation_page_empty(workbench):
    client = TestClient(app)

    resp = client.get("/reconciliation")

    assert resp.status_code == 200
    assert "导入对账文件" in resp.text
    assert "还没有对账结果" in resp.text


def test_run_with_sample_data(workbench):
    client = TestClient(app)

    resp = client.post(
        "/reconciliation/run",
        data={"use_sample": "1"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    summary = json.loads((workbench / "summary.json").read_text(encoding="utf-8"))
    assert summary["rows"] == 13

    page = client.get("/reconciliation")
    assert "金额不符" in page.text
    assert "O1011" in page.text


def test_run_rejects_missing_upload(workbench):
    client = TestClient(app)

    resp = client.post(
        "/reconciliation/run",
        data={},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    page = client.get(resp.headers["location"])
    assert "请上传三个 CSV" in page.text


def test_run_reports_bad_csv(workbench):
    client = TestClient(app)

    resp = client.post(
        "/reconciliation/run",
        data={"use_sample": "1"},
        files={"orders": ("orders.csv", "bad\n", "text/csv")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "CSV 缺少列" in page.text


def _run_sample(client):
    return client.post(
        "/reconciliation/run",
        data={"use_sample": "1"},
        follow_redirects=False,
    )


def _handle_amount_mismatch(client, note="已联系平台"):
    return client.post(
        "/reconciliation/action",
        data={
            "platform": "shopee_mock",
            "platform_order_id": "O1014",
            "line_id": "L1",
            "category": "amount_mismatch",
            "status": "handled",
            "note": note,
            "next_category": "",
            "next_status": "",
        },
        follow_redirects=False,
    )


def test_action_saves_status_and_note(workbench):
    client = TestClient(app)
    _run_sample(client)

    resp = _handle_amount_mismatch(client)

    assert resp.status_code == 303
    page = client.get("/reconciliation?status=handled")
    assert "待处理 12" in page.text
    assert "已处理 1" in page.text
    assert "已联系平台" in page.text


def test_action_rejects_unknown_status(workbench):
    client = TestClient(app)
    _run_sample(client)

    resp = client.post(
        "/reconciliation/action",
        data={
            "platform": "shopee_mock",
            "platform_order_id": "O1014",
            "line_id": "L1",
            "category": "amount_mismatch",
            "status": "done",
            "note": "",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "无效的处理状态" in page.text


def test_worklist_filters_by_category_and_status(workbench):
    client = TestClient(app)
    _run_sample(client)
    _handle_amount_mismatch(client)

    handled = client.get("/reconciliation?status=handled")
    assert "O1014" in handled.text
    assert "O1011" not in handled.text

    mismatch = client.get("/reconciliation?category=amount_mismatch")
    assert "O1014" in mismatch.text
    assert "O1011" not in mismatch.text


def test_export_worklist_csv(workbench):
    client = TestClient(app)
    _run_sample(client)
    _handle_amount_mismatch(client)

    resp = client.get("/reconciliation/export?status=handled")

    assert resp.status_code == 200
    assert resp.content.startswith(b"\xef\xbb\xbf")
    text = resp.content.decode("utf-8-sig")
    assert "status_label" in text
    assert "已处理" in text
    assert "已联系平台" in text
    assert "O1014" in text
    assert "O1011" not in text


def test_upload_size_limit(workbench, monkeypatch):
    import ecommerce.api.app as app_module

    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 10)
    client = TestClient(app)

    resp = client.post(
        "/reconciliation/run",
        data={"use_sample": "1"},
        files={"orders": ("orders.csv", b"x" * 11, "text/csv")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "文件超过" in page.text


def test_note_is_html_escaped(workbench):
    client = TestClient(app)
    _run_sample(client)
    _handle_amount_mismatch(client, note="<script>alert(1)</script>")

    page = client.get("/reconciliation?status=handled")

    assert "&lt;script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text


def test_export_escapes_formula_like_text(workbench):
    client = TestClient(app)
    _run_sample(client)
    _handle_amount_mismatch(client, note='=HYPERLINK("http://example.com")')

    resp = client.get("/reconciliation/export?status=handled")
    text = resp.content.decode("utf-8-sig")

    assert "'=HYPERLINK" in text
    assert "-1000" in text
