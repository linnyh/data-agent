"""M6 验收：评分机制验证（官方 DABench 列签名匹配规范）。

compare_csv 为 内置资产 评分器资产（原样复用）；本测试验证其机制正确性，
并守护自建标注集（离线集恢复后替换对标）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# 内置资产 评分器资产
from data_agent.assets.run_compare_to_gt import compare_csv

BENCH = Path(__file__).resolve().parents[1] / "benchmark"
GOLD_DIR = BENCH / "gold"


@pytest.fixture()
def gold_dir(tmp_path: Path) -> Path:
    d = tmp_path / "gold"
    d.mkdir()
    return d


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_exact_match_scores_1(gold_dir: Path):
    gold = _write(gold_dir / "g.csv", "col\n91.54\n114.75\n")
    pred = _write(gold_dir / "p.csv", "col\n91.54\n114.75\n")
    r = compare_csv(gold, pred)
    assert r["score"] == 1.0
    assert r["matched_cols"] == 1


def test_column_order_and_name_ignored(gold_dir: Path):
    """列名与行序不参与评分：按列内容签名匹配。"""
    gold = _write(gold_dir / "g.csv", "规模\n91.54\n114.75\n")
    pred = _write(gold_dir / "p.csv", "x\n114.75\n91.54\n")
    r = compare_csv(gold, pred)
    assert r["score"] == 1.0


def test_numeric_normalization(gold_dir: Path):
    """数值归一：Decimal 2 位 ROUND_HALF_UP。"""
    gold = _write(gold_dir / "g.csv", "v\n1.00\n2.55\n")
    pred = _write(gold_dir / "p.csv", "v\n1.0\n2.549\n")
    r = compare_csv(gold, pred)
    assert r["score"] == 1.0


def test_null_normalization(gold_dir: Path):
    """NULL 归一为空串（NULL 是签名的一部分）。

    注意: pandas read_csv 默认跳过纯空行, 故用双列让空值行不被跳过
    （真实 gold.csv 的空值行同样伴随其他列有值）。
    """
    gold = _write(gold_dir / "g.csv", "v,w\n1,a\n,b\n2,c")
    pred = _write(gold_dir / "p.csv", "v,w\n1,a\nnull,b\n2,c")
    r = compare_csv(gold, pred)
    assert r["score"] == 1.0


def test_extra_column_penalty(gold_dir: Path):
    """多余列扣分：λ=0.1, 1 gold + 1 extra → 1.0 - 0.1*(1/2) = 0.95。"""
    gold = _write(gold_dir / "g.csv", "v\n1\n2\n")
    pred = _write(gold_dir / "p.csv", "v,extra\n1,9\n2,8\n")
    r = compare_csv(gold, pred)
    assert r["score"] == pytest.approx(0.95)
    assert r["extra_cols"] == 1


def test_missing_prediction_zero(gold_dir: Path):
    gold = _write(gold_dir / "g.csv", "v\n1\n")
    r = compare_csv(gold, gold_dir / "nope.csv")
    assert r["score"] == 0.0
    assert r["error"] == "prediction missing"


def test_benchmark_gold_task_basic():
    """自建标注集 task_basic：正确查询应得满分（机制端到端）。"""
    from data_agent.adapters.duckdb import DuckDBDataSourceRegistry

    task_dir = BENCH / "tasks" / "task_basic"
    reg = DuckDBDataSourceRegistry()
    reg.register_directory(task_dir)
    df = reg.run_query('SELECT "规模(亿)" FROM df_funds')
    pred = GOLD_DIR.parent / "tmp_pred.csv"
    df.to_csv(pred, index=False)
    r = compare_csv(GOLD_DIR / "task_basic.csv", pred)
    assert r["score"] == 1.0
    pred.unlink(missing_ok=True)


def test_benchmark_gold_task_filter():
    """自建标注集 task_filter：实体去重查询（注册→过滤→去重）得满分。"""
    from data_agent.adapters.duckdb import DuckDBDataSourceRegistry

    task_dir = BENCH / "tasks" / "task_filter"
    reg = DuckDBDataSourceRegistry()
    reg.register_directory(task_dir)
    df = reg.run_query(
        'SELECT DISTINCT name FROM df_members WHERE team = \'A组\' AND status = \'正常\''
    )
    pred = GOLD_DIR.parent / "tmp_pred.csv"
    df.to_csv(pred, index=False)
    r = compare_csv(GOLD_DIR / "task_filter.csv", pred)
    assert r["score"] == 1.0
    pred.unlink(missing_ok=True)
