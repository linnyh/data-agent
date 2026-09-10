"""M1 验收：DuckDB 适配器与 内置资产 实现的对齐测试。

- describe 输出与 内置资产 describe_tool 逐字节一致
- 注册表名/别名与 内置资产 datasource_runtime 一致
- run_sql 归一化行为一致（引号方言 + 列名空格）
- 只读白名单拒绝写操作
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data_agent.adapters.duckdb import DuckDBDataSourceRegistry, FileDescriber


@pytest.fixture()
def sample_context(tmp_path: Path) -> Path:
    """构造覆盖多类型的样例 context 目录。"""
    ctx = tmp_path / "context"
    (ctx / "csv").mkdir(parents=True)
    (ctx / "json").mkdir(parents=True)
    (ctx / "db").mkdir(parents=True)
    (ctx / "doc").mkdir(parents=True)

    # csv：中文列名 + null + 混合类型
    (ctx / "csv" / "基金表现.csv").write_text(
        "基金代码,在任基金数(只),规模(亿),备注\n"
        "A001,3,91.54,正常\n"
        "B002,5,114.75,\n"
        "C003,,55.20,异常\n",
        encoding="utf-8",
    )
    # 根级 csv
    (ctx / "root_level.csv").write_text(
        "id,value\n1,10\n2,20\n", encoding="utf-8"
    )
    # json：records-shaped，table 名与文件名不同
    (ctx / "json" / "b.json").write_text(
        '{"table": "customers", "records": ['
        '{"name": "张三", "age": 30}, {"name": "李四", "age": null}]}',
        encoding="utf-8",
    )
    # sqlite：两表，其一与 csv canonical 冲突（基金表现）
    db_path = ctx / "db" / "c.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO members VALUES (1, '王五'), (2, '赵六')")
    conn.execute("CREATE TABLE 基金表现 (代码 TEXT, 收益 REAL)")
    conn.execute("INSERT INTO 基金表现 VALUES ('X', 0.05)")
    conn.commit()
    conn.close()
    # markdown + knowledge
    (ctx / "doc" / "readme.md").write_text(
        "# 说明\n\n一些内容\n\n## 小节\n", encoding="utf-8"
    )
    (ctx / "knowledge.md").write_text("# 领域知识\n\n字段口径说明\n", encoding="utf-8")
    return tmp_path


def test_describe_parity(sample_context: Path):
    """新实现与 内置资产 describe_context_dir 逐字节一致。"""
    from data_agent.assets.tools_v2 import describe_tool as assets_desc

    new_out = FileDescriber().describe_context_dir(
        sample_context, "context", skip_knowledge=True
    )
    assets_out = assets_desc.describe_context_dir(
        str(sample_context), "context", skip_knowledge=True
    )
    assert new_out == assets_out


def test_describe_collapse_parity(sample_context: Path):
    """软过滤折叠输出与 内置资产 一致。"""
    from data_agent.assets.tools_v2 import describe_tool as assets_desc

    collapse = {"context/csv/基金表现.csv"}
    new_out = FileDescriber().describe_context_dir(
        sample_context, "context", collapse_keys=collapse
    )
    assets_out = assets_desc.describe_context_dir(
        str(sample_context), "context", collapse_keys=collapse
    )
    assert new_out == assets_out
    assert "已折叠" in new_out


def test_registry_tables_parity(sample_context: Path):
    """注册表名/别名与 内置资产 一致。"""
    from data_agent.assets.tools_v2 import datasource_runtime as assets_rt

    new_reg = DuckDBDataSourceRegistry()
    new_reg.register_directory(sample_context)
    assets_state = assets_rt.build_datasource_state(sample_context)

    new_sig = {
        (t.canonical, tuple(t.aliases), t.source_type, t.source_rel)
        for t in new_reg.tables()
    }
    assets_sig = {
        (t.canonical, tuple(t.aliases), t.source_type, t.source_rel)
        for t in assets_state.tables
    }
    assert new_sig == assets_sig


def test_query_parity_with_dialect_normalization(sample_context: Path):
    """反引号 + 列名空格归一后，新旧实现查询结果一致。"""
    from data_agent.assets.tools_v2 import datasource_runtime as assets_rt

    new_reg = DuckDBDataSourceRegistry()
    new_reg.register_directory(sample_context)
    assets_state = assets_rt.build_datasource_state(sample_context)

    sql = "SELECT `基金代码` FROM df_基金表现 WHERE `在任基金数 (只)` > 4"
    new_df = new_reg.run_query(sql)
    assets_df = assets_rt.run_sql(assets_state, sql)
    pd.testing.assert_frame_equal(new_df, assets_df)


def test_run_query_rejects_writes(sample_context: Path):
    reg = DuckDBDataSourceRegistry()
    reg.register_directory(sample_context)
    with pytest.raises(ValueError):
        reg.run_query("DROP TABLE df_基金表现")
    with pytest.raises(ValueError):
        reg.run_query("INSERT INTO df_基金表现 VALUES (1)")


def test_run_query_meta_statements(sample_context: Path):
    reg = DuckDBDataSourceRegistry()
    reg.register_directory(sample_context)
    df = reg.run_query("SHOW TABLES")
    assert not df.empty
    df = reg.run_query("DESCRIBE df_基金表现")
    assert not df.empty
