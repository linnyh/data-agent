"""领域模型单测：字段语义与 CONTEXT.md 术语一致。"""

from data_agent.domain.models import (
    AnalysisGoal,
    DataSourceFile,
    DataSourceKind,
    Dataset,
    DocumentFile,
    Result,
    Session,
    SessionStatus,
    TableData,
    VideoBriefing,
)


def test_session_defaults():
    s = Session(session_id="s1", user_id="u1")
    assert s.status is SessionStatus.CREATED
    assert s.dataset == Dataset()


def test_dataset_composition():
    ds = Dataset(
        sources=[DataSourceFile(name="a.csv", kind=DataSourceKind.CSV, path="/d/a.csv")],
        documents=[DocumentFile(name="b.md", path="/d/b.md")],
    )
    ds.video = VideoBriefing(path="/d/briefing.mp4")
    assert len(ds.sources) == 1
    assert ds.video.name == "briefing.mp4"


def test_followup_is_just_a_new_goal():
    """追问语义（ADR-0004）：用户新分析 = 新 AnalysisGoal，共享会话。"""
    g1 = AnalysisGoal(text="哪些基金符合条件")
    g2 = AnalysisGoal(text="换个口径重算")
    assert g1.created_at <= g2.created_at


def test_result_shape():
    r = Result(
        narration="共 3 只基金符合条件。",
        table=TableData(columns=["fund"], rows=[["A"], ["B"]], total_rows=2),
    )
    assert r.source_kind == "solver"
    assert r.table.total_rows == 2
