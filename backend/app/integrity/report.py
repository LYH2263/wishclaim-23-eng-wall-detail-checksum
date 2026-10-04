"""报告：漂移报告的模型与渲染（JSON / 文本）。

每条漂移记录都带 wish_id、墙侧摘要、详情侧摘要，以及字段级对照。
"""
import json
from dataclasses import dataclass, field

from app.integrity.check import Drift

GATE_NAME = "wall-detail-projection"


@dataclass
class Report:
    checked: int
    entries: list[Drift] = field(default_factory=list)
    fix_applied: bool = False
    repaired: int = 0

    @property
    def ok(self) -> bool:
        return not self.entries

    def to_dict(self) -> dict:
        return {
            "gate": GATE_NAME,
            "ok": self.ok,
            "checked": self.checked,
            "drifted": len(self.entries),
            "fix_applied": self.fix_applied,
            "repaired": self.repaired,
            "entries": [
                {
                    "wish_id": d.wish_id,
                    "kind": d.kind,
                    "wall_digest": d.wall_digest,
                    "detail_digest": d.detail_digest,
                    "fields": d.fields,
                    "fixed": d.fixed,
                }
                for d in self.entries
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def render_text(self) -> str:
        if self.ok:
            tail = f" · repaired {self.repaired}" if self.fix_applied else ""
            return f"[{GATE_NAME}] OK · {self.checked} wishes · no drift{tail}"
        lines = [f"[{GATE_NAME}] DRIFT · {len(self.entries)}/{self.checked} wishes"]
        for d in self.entries:
            lines.append(
                f"  wish {d.wish_id} · {d.kind}"
                f" · wall={_short(d.wall_digest)} detail={_short(d.detail_digest)}"
            )
            for f, sides in d.fields.items():
                lines.append(
                    f"    {f}: wall={sides.get('wall')!r}"
                    f" detail={sides.get('detail')!r} source={sides.get('source')!r}"
                )
        return "\n".join(lines)


def _short(digest: str | None) -> str:
    return (digest or "—")[:12]
