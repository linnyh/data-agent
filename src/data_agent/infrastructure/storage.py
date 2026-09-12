"""会话存储：上传文件分类落盘 + 会话目录隔离（共识 #16：目录即隔离边界）。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

_STRUCTURED_EXTS = {".csv": "csv", ".json": "json", ".db": "db", ".sqlite": "db", ".sqlite3": "db"}
_DOC_EXTS = {".md": "doc", ".markdown": "doc", ".txt": "doc", ".pdf": "doc"}
_VIDEO_EXTS = {".mp4": "video"}


def classify_file(name: str) -> str | None:
    """按扩展名决定落盘子目录（context 下的分类）；不识别返回 None。"""
    ext = Path(name).suffix.lower()
    if ext in _STRUCTURED_EXTS:
        return _STRUCTURED_EXTS[ext]
    if ext in _DOC_EXTS:
        return _DOC_EXTS[ext]
    if ext in _VIDEO_EXTS:
        return _VIDEO_EXTS[ext]
    return None


class SessionStorage:
    """管理会话专属目录：上传落盘、任务目录布局、清理。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def session_dir(self, session_id: str) -> Path:
        d = self._root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_upload(self, session_id: str, filename: str, data: bytes) -> Path:
        """按扩展名分类落盘到 <session>/context/<分类>/。返回相对会话目录的路径。"""
        category = classify_file(filename)
        if category is None:
            raise ValueError(f"不支持的文件类型: {filename}")
        sdir = self.session_dir(session_id)
        ctx = sdir / "context"
        target_dir = ctx / (category if category != "video" else "video")
        if category == "video" and filename.lower() != "briefing.mp4":
            filename = "briefing.mp4"  # 约定：视频统一命名
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / filename
        dest.write_bytes(data)
        return dest

    def list_uploads(self, session_id: str) -> list[dict]:
        """列出已上传文件：context 分类子目录下的所有文件，路径相对会话目录。"""
        sdir = self.session_dir(session_id)
        ctx = sdir / "context"
        items: list[dict] = []
        for category in ("csv", "json", "db", "doc", "video"):
            d = ctx / category
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file():
                    items.append({"filename": f.name, "path": str(f.relative_to(sdir))})
        return items

    def delete_upload(self, session_id: str, path: str) -> None:
        """按相对路径删除已上传文件。路径解析后必须落在会话 context 内。"""
        sdir = self.session_dir(session_id).resolve()
        ctx = (sdir / "context").resolve()
        dest = (sdir / Path(path)).resolve()
        if not dest.is_relative_to(ctx):
            raise ValueError("非法路径")
        dest.unlink(missing_ok=True)

    def ensure_task_layout(self, session_id: str) -> Path:
        """确保任务目录布局（context/ + workdir/ + task.json）。返回会话目录。"""
        sdir = self.session_dir(session_id)
        (sdir / "context").mkdir(exist_ok=True)
        (sdir / "workdir").mkdir(exist_ok=True)
        tj = sdir / "task.json"
        if not tj.exists():
            tj.write_text('{"question": ""}', encoding="utf-8")
        return sdir

    def set_first_question(self, session_id: str, question: str) -> None:
        """记录首条分析目标（会话标题源）；仅首次写入，追问不覆盖。"""
        import json

        tj = self.session_dir(session_id) / "task.json"
        try:
            data = json.loads(tj.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not data.get("question"):
            data["question"] = question
            tj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def first_question(self, session_id: str) -> str:
        import json

        tj = self.session_dir(session_id) / "task.json"
        try:
            data = json.loads(tj.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return (data.get("question") or "").strip()

    def save_trace(self, session_id: str, entries: list) -> None:
        """保存一轮对话的执行轨迹（节点/工具详情，debug 开关开启时收集）。"""
        import json
        import time

        if not entries:
            return
        tdir = self.session_dir(session_id) / "traces"
        tdir.mkdir(exist_ok=True)
        path = tdir / f"{int(time.time() * 1000)}.json"
        path.write_text(
            json.dumps(entries, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def list_traces(self, session_id: str) -> list[list]:
        """按时间正序返回该会话全部执行轨迹（每轮一条）。"""
        import json

        tdir = self.session_dir(session_id) / "traces"
        if not tdir.is_dir():
            return []
        out: list[list] = []
        for f in sorted(tdir.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def cleanup_expired(self, ttl_seconds: float) -> int:
        """清理超过 TTL 未活动的会话目录；返回清理数。"""
        removed = 0
        if not self._root.is_dir():
            return 0
        cutoff = time.time() - ttl_seconds
        for d in self._root.iterdir():
            if not d.is_dir():
                continue
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d)
                    removed += 1
            except OSError:
                continue
        return removed
