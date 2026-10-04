"""漂移报告渲染：文本（人读）与 JSON（机读）。只负责呈现，不做校验与修复。"""
import json as _json

from . import FIELDS

KIND_CN = {
    "value_drift": "字段值漂移",
    "checksum_corrupt": "墙侧校验和缓存损坏",
    "missing_card": "墙侧投影缺失",
    "orphan_card": "墙侧投影多余（孤儿卡）",
}


def _summ(snap):
    if snap is None:
        return "—（无投影）—"
    return " / ".join(f"{k}={snap.get(k)!r}" for k in FIELDS)


def render_text(drifts: list[dict]) -> str:
    if not drifts:
        return "投影校验通过：墙列表与详情字段一致，无漂移。\n"
    lines = [f"检出 {len(drifts)} 条投影漂移："]
    for d in drifts:
        lines.append("")
        lines.append(f"wish_id={d['wish_id']}  [{KIND_CN.get(d['kind'], d['kind'])}]")
        if d["fields"]:
            lines.append(f"  不一致字段: {', '.join(d['fields'])}")
        lines.append(f"  详情侧摘要: {_summ(d['detail_snapshot'])}")
        lines.append(f"  墙侧摘要  : {_summ(d['wall_snapshot'])}")
        lines.append(f"  详情校验和: {d['detail_checksum']}")
        lines.append(f"  墙侧校验和: {d['wall_checksum']}")
    lines.append("")
    return "\n".join(lines)


def render_json(drifts: list[dict]) -> str:
    return _json.dumps(
        {"drift_count": len(drifts), "drifts": drifts},
        ensure_ascii=False, indent=2,
    )
