"""strategies 数据域：用户级策略库 + 目录内批次生命周期（二期新增）。

公开面：LibraryStrategy 与库 CRUD / copy / rebind / 引用健康度（store）、
StrategySnapshot 与快照装配（snapshot）、批次生命周期与排除名单（batches）、
类型化异常（errors）。依赖方向：strategies → workdir（门面落盘）+ llm（读端点
配置）+ prompts / skills（读全文）。
"""

from .batches import (
    BatchEntry,
    add_exclusions,
    apply_library_strategy,
    create_batch,
    delete_batch,
    get_batch,
    list_batches,
    parse_seq,
    product_count,
    read_snapshot,
    remove_exclusions,
    set_batch_active,
    update_batch,
)
from .errors import (
    BatchNotFoundError,
    StrategyError,
    StrategyNameError,
    StrategyNotFoundError,
    StrategyRefsError,
)
from .snapshot import StrategySnapshot, build_snapshot
from .store import (
    LibraryStrategy,
    copy_strategy,
    create_strategy,
    delete_strategy,
    get_strategy,
    list_strategies,
    missing_refs,
    rebind_strategy,
    require_refs_exist,
    strategy_content_hash,
    update_strategy,
)

__all__ = [
    "BatchEntry",
    "BatchNotFoundError",
    "LibraryStrategy",
    "StrategyError",
    "StrategyNameError",
    "StrategyNotFoundError",
    "StrategyRefsError",
    "StrategySnapshot",
    "add_exclusions",
    "apply_library_strategy",
    "build_snapshot",
    "copy_strategy",
    "create_batch",
    "create_strategy",
    "delete_batch",
    "delete_strategy",
    "get_batch",
    "get_strategy",
    "list_batches",
    "list_strategies",
    "missing_refs",
    "parse_seq",
    "product_count",
    "read_snapshot",
    "rebind_strategy",
    "remove_exclusions",
    "require_refs_exist",
    "set_batch_active",
    "strategy_content_hash",
    "update_batch",
    "update_strategy",
]
