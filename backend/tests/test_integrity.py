"""完整性校验：真实文件哈希、只读保证、按批次隔离与错误路径。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.runs.journal import RunJournal
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.importer import hash_file
from dataset_factory.workdir.integrity import scan_integrity


def test_scan_reports_changes_without_modifying_records(tmp_path: Path) -> None:
    """对比打标锚点而非导入基线，校验前后登记文件字节完全不变。"""
    asset = tmp_path / "image.png"
    asset.write_bytes(b"original")
    import_assets(tmp_path)
    store = WorkdirStore(tmp_path)
    before = store.imports_file.read_bytes()
    anchor = hash_file(asset)
    asset.write_bytes(b"changed")

    changed = scan_integrity(tmp_path, {"image": anchor})
    valid = scan_integrity(tmp_path, {"image": hash_file(asset)})
    unknown = scan_integrity(tmp_path, {})

    assert changed[0].status == "changed"
    assert valid[0].status == "valid"
    assert unknown[0].status == "unknown"
    assert store.imports_file.read_bytes() == before


def test_missing_and_unregistered_are_distinguished(tmp_path: Path) -> None:
    """仅在册素材参与对账，缺失文件不能被未登记同主干文件冒充。"""
    store = WorkdirStore(tmp_path)
    store.append_import_record(
        {
            "imported_at": "now",
            "source": "",
            "files": [{"name": "a.png", "sha256": "x"}],
        }
    )
    (tmp_path / "a.jpg").write_bytes(b"different extension")

    rows = scan_integrity(tmp_path, {"a": "x"})

    assert len(rows) == 1
    assert rows[0].name == "a.png"
    assert rows[0].status == "missing"


@pytest.mark.parametrize("name", ["../outside.png", "..\\outside.png"])
def test_invalid_registered_paths_are_not_read(tmp_path: Path, name: str) -> None:
    """被篡改的登记路径报告不可读取，不读取工作目录外文件。"""
    store = WorkdirStore(tmp_path)
    store.append_import_record(
        {"imported_at": "now", "source": "", "files": [{"name": name, "sha256": "x"}]}
    )

    rows = scan_integrity(tmp_path, {})

    assert rows[0].status == "unreadable"
    assert rows[0].current_hash is None


def test_api_isolates_batches_and_excludes_hidden(
    tmp_path: Path, temp_data_root: Path
) -> None:
    """HTTP 校验按批次成功流水比对，隐藏批次不参与报告。"""
    asset = tmp_path / "a.png"
    asset.write_bytes(b"current image")
    import_assets(tmp_path)
    store = WorkdirStore(tmp_path)

    def add_batches(state: dict[str, object]) -> None:
        state["batches"] = [
            {
                "seq": seq,
                "name": f"s{seq}",
                "description": "",
                "snapshot": f"s{seq}.json",
                "active": seq != 3,
                "created_at": "now",
            }
            for seq in (1, 2, 3)
        ]

    store.mutate_state(add_batches)
    journal = RunJournal(store.runs_dir / "20260917")
    for seq, anchor in ((1, hash_file(asset)), (2, "old")):
        journal.append_item(
            {
                "item": "a",
                "batch": seq,
                "status": "succeeded",
                "attempt": 1,
                "asset_hash": anchor,
            }
        )
    entry = WorkdirRegistry.register(tmp_path)
    client = TestClient(create_app(frontend_dir=tmp_path / "frontend"))

    response = client.post(f"/api/workdirs/{entry.id}/integrity/scan")

    assert response.status_code == 200
    batches = response.json()["batches"]
    assert [row["batch"] for row in batches] == ["s1", "s2"]
    assert [row["items"][0]["status"] for row in batches] == ["valid", "changed"]
    assert (
        client.post(f"/api/workdirs/{entry.id}/integrity/scan?batch=s99").status_code
        == 404
    )
    assert client.post("/api/workdirs/unknown/integrity/scan").status_code == 404
