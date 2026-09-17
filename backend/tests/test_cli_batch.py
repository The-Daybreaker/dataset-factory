"""批次命令的真实素材、快照、名单与前台执行闭环。"""

import json
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from dataset_factory.cli.main import app
from dataset_factory.llm import create_config
from dataset_factory.llm.errors import LLMBadRequestError
from dataset_factory.prompts import Prompt, delete_prompt, save_prompt
from dataset_factory.strategies import create_strategy, list_batches
from dataset_factory.strategies.batches import read_exclusions
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets
from dataset_factory.workdir.locks import RunLock

from .conftest import FakeCompleter

runner = CliRunner()


@pytest.fixture
def prepared(tmp_path: Path, temp_data_root: Path) -> Path:
    """隔离数据根，真实导入两份素材并通过 CLI 创建批次。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="caption", description="", body="Describe the image"))
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"image-a")
    (root / "b.jpg").write_bytes(b"image-b")
    WorkdirRegistry.register(root)
    import_assets(root)
    result = runner.invoke(
        app,
        [
            "batch",
            "add",
            str(root),
            "--name",
            "First",
            "--endpoint",
            "main",
            "--prompt",
            "caption",
        ],
    )
    assert result.exit_code == 0, result.stderr
    return root


def test_run_writes_products_report_and_resumes_without_model_calls(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前台跑批输出 JSON 与日志路径，第二次执行跳过已有且未变的素材。"""
    completer = FakeCompleter(["caption a", "caption b"])

    def assemble(endpoint: object) -> FakeCompleter:
        return completer

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)

    first = runner.invoke(app, ["run", str(prepared), "s1"])
    again = runner.invoke(app, ["run", str(prepared), "s1"])

    assert first.exit_code == again.exit_code == 0, first.stderr
    report = json.loads(first.stdout)
    assert report["counters"]["succeeded"] == 2
    assert Path(report["log_path"]).is_file()
    assert "item-updated" in first.stderr
    assert (prepared / "s1__a.txt").read_text(encoding="utf-8") == "caption a"
    assert json.loads(again.stdout)["counters"]["skipped"] == 2
    assert len(completer.calls) == 2


def test_ctrl_c_keeps_completed_item_and_restores_handler(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型调用中一次 Ctrl-C 不丢当前成功产物，退出码 130 且后续条目不跑。"""

    class InterruptingCompleter(FakeCompleter):
        def complete(self, messages: object) -> str:
            signal.raise_signal(signal.SIGINT)
            return "completed before stop"

    completer = InterruptingCompleter()

    def assemble(endpoint: object) -> FakeCompleter:
        return completer

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)
    previous = signal.getsignal(signal.SIGINT)

    result = runner.invoke(app, ["run", str(prepared), "s1"])

    assert result.exit_code == 130, result.stderr
    assert json.loads(result.stdout)["status"] == "interrupted"
    assert (prepared / "s1__a.txt").is_file()
    assert not (prepared / "s1__b.txt").exists()
    assert signal.getsignal(signal.SIGINT) == previous


def test_retry_eligibility_and_exclusion_lifecycle(prepared: Path) -> None:
    """整份拒绝不合资格条目，重试名单进出与排除撤销持久生效。"""
    (prepared / "s1__a.txt").write_text("caption", encoding="utf-8")

    refused = runner.invoke(
        app, ["batch", "retry", "add", str(prepared), "s1", "a", "b"]
    )
    empty = runner.invoke(app, ["batch", "retry", "list", str(prepared), "s1"])
    accepted = runner.invoke(app, ["batch", "retry", "add", str(prepared), "s1", "a"])
    removed = runner.invoke(app, ["batch", "retry", "remove", str(prepared), "s1", "a"])
    excluded = runner.invoke(app, ["batch", "exclude", str(prepared), "s1", "a"])
    undone = runner.invoke(
        app, ["batch", "exclude", str(prepared), "s1", "a", "--undo"]
    )

    assert refused.exit_code == 1
    assert json.loads(empty.stdout) == []
    assert (
        accepted.exit_code
        == removed.exit_code
        == excluded.exit_code
        == undone.exit_code
        == 0
    )
    assert json.loads(accepted.stdout) == ["a"]
    assert json.loads(removed.stdout) == []
    assert json.loads(excluded.stdout) == ["a"]
    assert read_exclusions(prepared, 1) == []


def test_library_creation_edit_and_visibility(prepared: Path) -> None:
    """库策略可按名称应用，批次改名和隐藏显示不修改快照。"""
    library = create_strategy(
        name="Library",
        endpoint="main",
        prompt="caption",
        skills=[],
        description="Reusable",
    )

    added = runner.invoke(
        app, ["batch", "add", str(prepared), "--from-library", library.name]
    )
    edited = runner.invoke(
        app, ["batch", "edit", str(prepared), "s2", "--name", "Second"]
    )
    hidden = runner.invoke(app, ["batch", "hide", str(prepared), "s2", "--yes"])
    shown = runner.invoke(app, ["batch", "unhide", str(prepared), "s2"])
    detail = runner.invoke(app, ["batch", "show", str(prepared), "s2"])

    assert (
        added.exit_code
        == edited.exit_code
        == hidden.exit_code
        == shown.exit_code
        == detail.exit_code
        == 0
    )
    assert json.loads(added.stdout)["seq"] == 2
    assert json.loads(edited.stdout)["name"] == "Second"
    assert json.loads(hidden.stdout)["active"] is False
    assert json.loads(shown.stdout)["active"] is True
    assert json.loads(detail.stdout)["snapshot"]["source"]["strategy_id"] == library.id


def test_retry_run_replaces_only_selected_caption(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试运行只覆盖名单内产物，成功后自动出列。"""
    (prepared / "s1__a.txt").write_text("old a", encoding="utf-8")
    (prepared / "s1__b.txt").write_text("old b", encoding="utf-8")
    completer = FakeCompleter(["new a"])

    def assemble(endpoint: object) -> FakeCompleter:
        return completer

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)
    selected = runner.invoke(app, ["batch", "retry", "add", str(prepared), "s1", "a"])

    result = runner.invoke(app, ["run", str(prepared), "s1", "--mode", "retry"])
    remaining = runner.invoke(app, ["batch", "retry", "list", str(prepared), "s1"])

    assert selected.exit_code == result.exit_code == remaining.exit_code == 0
    assert (prepared / "s1__a.txt").read_text(encoding="utf-8") == "new a"
    assert (prepared / "s1__b.txt").read_text(encoding="utf-8") == "old b"
    assert json.loads(remaining.stdout) == []
    assert len(completer.calls) == 1


def test_failed_run_reports_counts_and_nonzero_exit(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不可重试模型错误返回失败计数和退出码 1，不生成产物。"""

    class RejectingCompleter(FakeCompleter):
        def complete(self, messages: object) -> str:
            raise LLMBadRequestError("Unsupported media")

    def assemble(endpoint: object) -> FakeCompleter:
        return RejectingCompleter()

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)

    result = runner.invoke(app, ["run", str(prepared), "s1"])

    assert result.exit_code == 1, result.stderr
    assert json.loads(result.stdout)["counters"]["failed"] == 2
    assert not list(prepared.glob("s1__*.txt"))


def test_cli_export_plan_and_zip_follow_exclusions(prepared: Path) -> None:
    """导出预览与真实 ZIP 使用同一排除名单，文件平铺且 caption 同名配对。"""
    (prepared / "s1__a.txt").write_text("caption a", encoding="utf-8")
    (prepared / "s1__b.txt").write_text("caption b", encoding="utf-8")
    runner.invoke(app, ["batch", "exclude", str(prepared), "s1", "b"])
    destination = prepared.parent / "dataset.zip"

    plan = runner.invoke(app, ["export", "plan", str(prepared), "s1"])
    exported = runner.invoke(
        app, ["export", "run", str(prepared), "s1", "-o", str(destination)]
    )
    with ZipFile(destination) as archive:
        names = archive.namelist()
        caption = archive.read("001.txt")
        image = archive.read("001.jpg")

    assert plan.exit_code == exported.exit_code == 0, exported.stderr
    assert json.loads(plan.stdout)["excluded"][0]["reason"] == "用户排除"
    assert json.loads(exported.stdout)["path"] == str(destination)
    assert names == ["001.jpg", "001.txt"]
    assert caption == b"caption a"
    assert image == b"image-a"


def test_cli_export_preserves_existing_zip_and_original_names(prepared: Path) -> None:
    """原名模式保留素材名，输出路径已存在时干净报错且不覆盖。"""
    (prepared / "s1__a.txt").write_text("caption a", encoding="utf-8")
    command = ["export", "run", str(prepared), "s1", "--original-names"]

    created = runner.invoke(app, command)
    destination = Path(json.loads(created.stdout)["path"])
    original = destination.read_bytes()
    repeated = runner.invoke(app, [*command, "-o", str(destination)])
    with ZipFile(destination) as archive:
        names = archive.namelist()

    assert created.exit_code == 0, created.stderr
    assert names == ["a.jpg", "a.txt"]
    assert repeated.exit_code == 1
    assert "已存在" in repeated.stderr
    assert destination.read_bytes() == original


def test_strategy_crud_and_missing_reference_rebind(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库策略失效可查看并重新绑定，复制与删除不影响已有批次。"""
    tokens = iter(["-first12345x", "-copy123456x"])

    def random_token(size: int) -> str:
        return next(tokens)

    monkeypatch.setattr(
        "dataset_factory.strategies.store.secrets.token_urlsafe", random_token
    )
    created = runner.invoke(
        app,
        ["strategy", "add", "Reusable", "--endpoint", "main", "--prompt", "caption"],
    )
    identity = json.loads(created.stdout)["id"]
    delete_prompt("caption")
    missing = runner.invoke(app, ["strategy", "show", identity])
    save_prompt(Prompt(name="replacement", description="", body="New prompt"))

    rebound = runner.invoke(
        app, ["strategy", "rebind", identity, "--prompt", "replacement"]
    )
    edited = runner.invoke(
        app,
        [
            "strategy",
            "edit",
            identity,
            "--name",
            "Renamed",
            "--desc",
            "New description",
        ],
    )
    copied = runner.invoke(app, ["strategy", "copy", identity])
    refused = runner.invoke(app, ["strategy", "rm", identity])
    removed = runner.invoke(app, ["strategy", "rm", identity, "--yes"])
    remaining = runner.invoke(app, ["strategy", "list"])

    for result in (created, missing, rebound, edited, copied, removed, remaining):
        assert result.exit_code == 0, result.stderr
    assert json.loads(missing.stdout)["available"] is False
    assert json.loads(rebound.stdout)["available"] is True
    assert json.loads(edited.stdout)["description"] == "New description"
    assert json.loads(copied.stdout)["id"] != identity
    assert refused.exit_code == 2
    assert len(json.loads(remaining.stdout)) == 1
    assert (prepared / ".dsf" / "strategies" / "s1.json").is_file()


def test_legacy_strategy_id_accepts_cli_option_terminator(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧版连字符开头的 ID 可经标准参数分隔符查看、编辑和删除。"""

    def legacy_id() -> str:
        return "-legacy1234"

    monkeypatch.setattr("dataset_factory.strategies.store._generate_id", legacy_id)
    entry = create_strategy(
        name="Legacy", endpoint="main", prompt="caption", skills=[], description=""
    )

    shown = runner.invoke(app, ["strategy", "show", "--", entry.id])
    edited = runner.invoke(
        app, ["strategy", "edit", "--name", "Renamed", "--", entry.id]
    )
    removed = runner.invoke(app, ["strategy", "rm", "--yes", "--", entry.id])

    for result in (shown, edited, removed):
        assert result.exit_code == 0, result.stderr
    assert json.loads(shown.stdout)["id"] == entry.id
    assert json.loads(edited.stdout)["name"] == "Renamed"
    assert json.loads(removed.stdout)["deleted"] == entry.id


def test_batch_removal_requires_confirmation_and_clears_attached_state(
    prepared: Path,
) -> None:
    """删除批次须确认，并一并出清名单而保留原始素材。"""
    (prepared / "s1__a.txt").write_text("caption", encoding="utf-8")
    runner.invoke(app, ["batch", "retry", "add", str(prepared), "s1", "a"])
    runner.invoke(app, ["batch", "exclude", str(prepared), "s1", "a"])

    refused = runner.invoke(app, ["batch", "rm", str(prepared), "s1"])
    removed = runner.invoke(app, ["batch", "rm", str(prepared), "s1", "--yes"])

    assert refused.exit_code == 2
    assert removed.exit_code == 0, removed.stderr
    assert json.loads(removed.stdout) == {"deleted": "s1", "product_count": 1}
    assert list_batches(prepared) == []
    assert WorkdirStore(prepared).read_state()["retry_list"] == []
    assert WorkdirStore(prepared).read_state()["exclusions"] == {}
    assert (prepared / "a.jpg").read_bytes() == b"image-a"
    assert not (prepared / "s1__a.txt").exists()


def test_batch_removal_refuses_occupied_directory_without_changing_files(
    prepared: Path,
) -> None:
    """另一线程持运行锁时删除被拒，批次快照与产物全部保留。"""
    (prepared / "s1__a.txt").write_text("caption", encoding="utf-8")
    acquired = Event()
    release = Event()

    def hold_lock() -> None:
        lock = RunLock(prepared / ".dsf")
        lock.acquire({"batch": 1})
        try:
            acquired.set()
            assert release.wait(10)
        finally:
            lock.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold_lock)
        try:
            assert acquired.wait(10)
            result = runner.invoke(app, ["batch", "rm", str(prepared), "s1", "--yes"])
        finally:
            release.set()
        future.result(timeout=10)

    assert result.exit_code == 1
    assert "占用" in result.stderr
    assert (prepared / "s1__a.txt").read_text(encoding="utf-8") == "caption"
    assert (prepared / ".dsf" / "strategies" / "s1.json").is_file()
    assert len(list_batches(prepared)) == 1


def test_run_requires_acknowledgement_for_unimported_files(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未登记与不支持文件先汇总确认，显式继续只处理已登记素材。"""
    (prepared / "extra.jpg").write_bytes(b"not imported")
    (prepared / "notes.txt").write_text("notes", encoding="utf-8")
    completer = FakeCompleter(["caption a", "caption b"])

    def assemble(endpoint: object) -> FakeCompleter:
        return completer

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)

    refused = runner.invoke(app, ["run", str(prepared), "s1"])
    calls_before_confirmation = len(completer.calls)
    accepted = runner.invoke(app, ["run", str(prepared), "s1", "--yes"])

    assert refused.exit_code == 2
    assert "extra.jpg" in refused.stderr
    assert "notes.txt" in refused.stderr
    assert calls_before_confirmation == 0
    assert accepted.exit_code == 0, accepted.stderr
    assert json.loads(accepted.stdout)["counters"]["succeeded"] == 2
    assert not (prepared / "s1__extra.txt").exists()


def test_status_and_stop_control_runner_in_another_process(prepared: Path) -> None:
    """独立进程跑批可被 CLI 查询和停止，当前产物保留、后续条目不运行。"""
    ready = prepared.parent / "ready"
    proceed = prepared.parent / "proceed"
    script = """
import sys
import time
from pathlib import Path
from dataset_factory.runs import BatchRunner
from tests.conftest import FakeCompleter

root, ready, proceed = map(Path, sys.argv[1:])
class WaitingCompleter(FakeCompleter):
    def complete(self, messages):
        ready.touch()
        deadline = time.monotonic() + 20
        while not proceed.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test gate timed out')
            time.sleep(0.02)
        return 'finished current item'

report = BatchRunner(root, 1, WaitingCompleter(), mode='full', trigger='cli').run()
assert report.status == 'interrupted', report
"""

    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script, str(prepared), str(ready), str(proceed)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        status = runner.invoke(app, ["batch", "status", str(prepared), "s1"])
        stopped = runner.invoke(app, ["batch", "stop", str(prepared), "s1"])
        proceed.touch()
        stdout, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    assert process.returncode == 0, stdout + stderr
    assert status.exit_code == stopped.exit_code == 0, status.stderr + stopped.stderr
    assert json.loads(status.stdout)["current_item"] == "a"
    assert json.loads(stopped.stdout)["stop_requested"] is True
    assert (prepared / "s1__a.txt").read_text(
        encoding="utf-8"
    ) == "finished current item"
    assert not (prepared / "s1__b.txt").exists()
    ended = runner.invoke(app, ["batch", "status", str(prepared), "s1"])
    assert ended.exit_code == 1


def test_stale_status_and_stop_files_do_not_control_a_new_run(
    prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """残留快照不表示运行中，旧运行的停止请求不会取消下一次跑批。"""
    dsf = prepared / ".dsf"
    (dsf / "run-info.json").write_text(
        json.dumps({"batch": "s1", "run_id": "old"}), encoding="utf-8"
    )
    (dsf / "run-stop.json").write_text(json.dumps({"run_id": "old"}), encoding="utf-8")
    completer = FakeCompleter(["a", "b"])

    def assemble(endpoint: object) -> FakeCompleter:
        return completer

    monkeypatch.setattr("dataset_factory.cli.run.completer_for_snapshot", assemble)

    stale = runner.invoke(app, ["batch", "status", str(prepared), "s1"])
    refused = runner.invoke(app, ["batch", "stop", str(prepared), "s1"])
    started = runner.invoke(app, ["run", str(prepared), "s1"])

    assert stale.exit_code == refused.exit_code == 1
    assert started.exit_code == 0, started.stderr
    assert json.loads(started.stdout)["counters"]["succeeded"] == 2
