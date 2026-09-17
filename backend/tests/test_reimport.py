"""重新导入与选择性补登记的真实文件测试。"""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataset_factory.cli.main import app
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.assets import registered_origins
from dataset_factory.workdir.errors import WorkdirPathError
from dataset_factory.workdir.reimport import reimport_missing

pytestmark = pytest.mark.usefixtures("temp_data_root")
runner = CliRunner()


def test_cli_registers_only_selected_existing_material_with_its_hash(
    tmp_path: Path,
) -> None:
    """就地补登记只收所选文件，记录真实哈希且不改写素材。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"material-a")
    (root / "b.jpg").write_bytes(b"material-b")
    WorkdirRegistry.register(root)

    result = runner.invoke(app, ["workdir", "import", str(root), "--name", "b.jpg"])
    records = WorkdirStore(root).read_import_records()

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["imported"] == ["b.jpg"]
    assert set(registered_origins(WorkdirStore(root))) == {"b.jpg"}
    assert records[-1]["files"] == [
        {"name": "b.jpg", "sha256": hashlib.sha256(b"material-b").hexdigest()}
    ]
    assert (root / "b.jpg").read_bytes() == b"material-b"


def test_reimport_restores_only_selected_missing_material(tmp_path: Path) -> None:
    """单条恢复不带入同来源的新文件，也不恢复未选中的缺失素材。"""
    root = tmp_path / "work"
    source = tmp_path / "source"
    root.mkdir()
    source.mkdir()
    for name in ("a.jpg", "b.jpg"):
        (source / name).write_bytes(name.encode())
    WorkdirRegistry.register(root)
    import_assets(root, source)
    for name in ("a.jpg", "b.jpg"):
        (root / name).rename(tmp_path / name)
    (source / "extra.jpg").write_bytes(b"extra")

    result = runner.invoke(app, ["workdir", "reimport", str(root), "--name", "a.jpg"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["imports"][0]["imported"] == ["a.jpg"]
    assert (root / "a.jpg").read_bytes() == b"a.jpg"
    assert not (root / "b.jpg").exists()
    assert not (root / "extra.jpg").exists()


def test_reimport_reports_unavailable_source_without_creating_files(
    tmp_path: Path,
) -> None:
    """就地采用文件缺失后来源不可用，报告逐条处置而不假装恢复成功。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"a")
    import_assets(root)
    (root / "a.jpg").rename(tmp_path / "saved.jpg")

    result = reimport_missing(root)

    assert result["imports"] == []
    assert result["unavailable"][0]["name"] == "a.jpg"
    assert not (root / "a.jpg").exists()


def test_selected_import_rejects_path_and_unknown_reimport_name(tmp_path: Path) -> None:
    """选择参数不能含路径，重新导入仅接受登记过的文件名。"""
    root = tmp_path / "work"
    root.mkdir()

    with pytest.raises(WorkdirPathError):
        import_assets(root, names={"../a.jpg"})
    with pytest.raises(WorkdirPathError):
        reimport_missing(root, {"a.jpg"})

    assert WorkdirStore(root).read_import_records() == []
