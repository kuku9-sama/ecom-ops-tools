import socket
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import requests

from ecommerce.cli import (
    cmd_extract,
    cmd_import_orders,
    cmd_import_products,
    cmd_inventory,
    cmd_reconcile,
    cmd_report,
    cmd_sync_orders,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_mock(port: int):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mock_server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"Mock 启动失败:\n{output}")
        try:
            resp = requests.post(
                f"{base_url}/rest/auth/token/create",
                params={"app_key": "mock"},
                timeout=1,
            )
            if resp.status_code == 200:
                return process, base_url
        except requests.RequestException:
            pass
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Mock 启动超时")


def _run(name: str, code: int) -> None:
    if code != 0:
        raise RuntimeError(f"{name} 失败，退出码 {code}")


def main() -> int:
    db_path = DATA / "demo_run.db"
    db_path.unlink(missing_ok=True)
    OUTPUTS.mkdir(exist_ok=True)
    since = "2026-10-01T00:00:00+00:00"

    process = None
    try:
        port = _free_port()
        process, base_url = _start_mock(port)
        print(f"Mock: {base_url}")

        _run("订单导入", cmd_import_orders(
            Namespace(csv=str(DATA / "orders.csv"), db=str(db_path))
        ))
        _run("Shopee 同步", cmd_sync_orders(
            Namespace(
                platform="shopee_mock",
                since=since,
                base_url=base_url,
                page_size=100,
                db=str(db_path),
            )
        ))
        _run("Lazada 同步", cmd_sync_orders(
            Namespace(
                platform="lazada",
                since=since,
                base_url=base_url,
                page_size=2,
                db=str(db_path),
            )
        ))
        _run("商品草稿", cmd_import_products(
            Namespace(
                csv=str(DATA / "products.csv"),
                platform="shopee_mock",
                base_url=base_url,
                db=str(db_path),
            )
        ))
        _run("结构化提取", cmd_extract(
            Namespace(
                input=str(DATA / "raw_products.jsonl"),
                provider="mock",
                out=str(OUTPUTS / "extracted_products.csv"),
                db=str(db_path),
            )
        ))
        _run("对账", cmd_reconcile(
            Namespace(
                orders=str(DATA / "orders.csv"),
                bills=str(DATA / "bills.csv"),
                fx=str(DATA / "fx_rates.csv"),
                out=str(OUTPUTS / "reconciliation.csv"),
                summary=str(OUTPUTS / "reconciliation_summary.json"),
                unmatched=str(OUTPUTS / "unmatched.csv"),
                data_quality=str(OUTPUTS / "data_quality.csv"),
            )
        ))
        _run("库存比对", cmd_inventory(
            Namespace(
                platform="lazada",
                base_url=base_url,
                local=str(DATA / "local_stock.csv"),
                out=str(OUTPUTS / "inventory_diff.csv"),
            )
        ))
        _run("报告", cmd_report(Namespace(db=str(db_path))))
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    print()
    print("演示完成。输出目录：outputs/")
    print(
        "看板：ECOMMERCE_DB=data/demo_run.db "
        "./.venv/Scripts/python.exe -m ecommerce.cli serve"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
