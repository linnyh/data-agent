"""数据源域接口：注册与只读查询（依赖倒置，domain 不依赖任何引擎）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd


@dataclass
class RegisteredTable:
    """注册到查询引擎的一张逻辑表。"""

    canonical: str
    aliases: list[str] = field(default_factory=list)
    source_type: str = ""
    source_rel: str = ""
    sqlite_table: str | None = None


class IDataSourceRegistry(Protocol):
    """把 csv/json/sqlite 数据源注册为可统一 SQL 查询的表。

    实现必须保证：注册后通过 :meth:`run_query` 可查全部表；
    只读约束（拒绝写操作）由实现保证。
    """

    def register_directory(self, root: Path, context_dir: str = "context") -> None:
        """扫描 root/context_dir 下的全部数据源并注册。"""
        ...

    def run_query(self, sql: str) -> pd.DataFrame:
        """执行只读 SQL，返回完整结果（不分页、不加 LIMIT）。

        写操作（INSERT/UPDATE/DROP 等）必须被拒绝。
        """
        ...

    def tables(self) -> list[RegisteredTable]:
        """已注册的逻辑表清单。"""
        ...


class IDataSourceDescriber(Protocol):
    """生成数据源目录的人类可读描述（列、类型、样例）。"""

    def describe_context_dir(
        self,
        base_path: Path,
        rel_dir: str = "context",
        *,
        skip_knowledge: bool = False,
        collapse_keys: set[str] | None = None,
        per_file_chars: int | None = None,
    ) -> str:
        """遍历目录生成描述；collapse_keys 中的表只渲染一行折叠提示（软过滤）；
        per_file_chars 限制单文件描述长度（截断样例行，保证每个文件都被列出）。"""
        ...
