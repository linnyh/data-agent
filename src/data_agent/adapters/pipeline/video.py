"""视频链 Adapter：预处理（抽帧/ASR/hiccup/交错）与答案预判（多轮投票）。

桥接模式与 doc_extract 一致：构造器注入模型工厂；
预处理链走 内置资产 video_parts_for_task（算法资产零改动）；
video_result 判定走 内置资产 run_video_result_voted（模型可注入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_agent.domain.pipeline import (
    VideoPartsResult,
    VideoResultAdvice,
)

# 内置资产入口
from data_agent.assets.video.build_video_input import video_parts_for_task
from data_agent.assets.agents_v2.video_result_agent import run_video_result_voted


class AssetVideoPreprocessor:
    """抽帧 + ASR + hiccup + 时间轴交错（video_parts_for_task 原样复用）。"""

    def preprocess(
        self,
        *,
        task_dir: Path,
        question: str = "",
        knowledge: str = "",
        log_dir: Path | None = None,
    ) -> VideoPartsResult:
        try:
            parts = video_parts_for_task(
                task_dir,
                question=question,
                knowledge=knowledge,
                log_dir=log_dir,
            )
        except Exception:
            return VideoPartsResult()
        return VideoPartsResult(parts=parts, has_video=bool(parts))


class AssetVideoResultJudge:
    """视频面板答案预判（EXTRACT→ALIGN→DECIDE 三步 + 多轮投票，模型可注入）。"""

    def __init__(self, model_factory=None) -> None:
        # 模型工厂返回 pydantic-ai 兼容模型（think 模型：识别+排诱饵是推理任务）
        self._factory = model_factory or self._default_factory

    @staticmethod
    def _default_factory() -> Any:
        from data_agent.assets.tools_v2.general_tools import build_model

        return build_model()

    async def judge(
        self,
        *,
        task_dir: Path,
        log_dir: Path | None = None,
        vote_rounds: int = 5,
    ) -> VideoResultAdvice:
        try:
            res = await run_video_result_voted(
                task_dir=task_dir,
                model=self._factory(),
                vote_rounds=vote_rounds,
                log_dir=log_dir,
            )
        except Exception as e:
            return VideoResultAdvice(error=f"{type(e).__name__}: {e}")

        return VideoResultAdvice(
            decision=res.decision,
            has_displayed_answer=res.has_displayed_answer,
            confidence=res.confidence,
            claimed_values=res.claimed_values,
            normalized_values=res.normalized_values,
            target_columns=list(res.target_columns or []),
            source_frames=list(res.source_frames or []),
            distractor_notes=res.distractor_notes or "",
            has_video=res.has_video,
            error=res.error or "",
        )
