"""Server-rendered inline SVG charts (single-series only).

Design rules: one hue per chart (identity is in the title, no legend),
2px lines, 4px-rounded bars anchored to the baseline, recessive grid,
per-mark <title> tooltips, text in text color — never the series color.
"""

from __future__ import annotations

from html import escape

ACCENT = "#2f6fed"
GRID = "#e3e6ea"
TEXT_MUTED = "#6b7686"

W, H, PAD = 640, 200, 30


def _scale(values: list[float], lo: float | None, hi: float | None) -> tuple[float, float]:
    vmin = lo if lo is not None else min(values)
    vmax = hi if hi is not None else max(values)
    if vmax <= vmin:
        vmax = vmin + 1
    return vmin, vmax


def _points(values: list[float], vmin: float, vmax: float) -> list[tuple[float, float]]:
    n = len(values)
    step = (W - 2 * PAD) / max(n - 1, 1)
    return [
        (PAD + i * step, H - PAD - (v - vmin) / (vmax - vmin) * (H - 2 * PAD))
        for i, v in enumerate(values)
    ]


def _x_labels(labels: list[str], n: int) -> str:
    if not labels:
        return ""
    every = max(1, (n + 4) // 5)
    step = (W - 2 * PAD) / max(n - 1, 1)
    parts = []
    for i, lab in enumerate(labels):
        if i % every:
            continue
        x = PAD + i * step
        parts.append(
            f'<text x="{x:.0f}" y="{H - 8}" font-size="11" fill="{TEXT_MUTED}"'
            f' text-anchor="middle">{escape(lab)}</text>'
        )
    return "".join(parts)


def empty_chart(message: str = "데이터가 아직 없습니다") -> str:
    return f'<div class="empty">{escape(message)}</div>'


def line_chart(
    points: list[dict],
    value_key: str,
    label_key: str = "week",
    *,
    vmin: float | None = None,
    vmax: float | None = None,
    fmt: str = "{:.0f}",
) -> str:
    rows = [p for p in points if p.get(value_key) is not None]
    if len(rows) < 2:
        return empty_chart()
    values = [float(p[value_key]) for p in rows]
    labels = [str(p.get(label_key, ""))[5:] for p in rows]  # MM-DD
    lo, hi = _scale(values, vmin, vmax)
    pts = _points(values, lo, hi)
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    grid_lines = "".join(
        f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>'
        for y in (PAD, (H) / 2, H - PAD)
    )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}">'
        f"<title>{escape(labels[i])}: {fmt.format(values[i])}</title></circle>"
        for i, (x, y) in enumerate(pts)
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" style="width:100%;height:auto">'
        f"{grid_lines}"
        f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="2"'
        f' stroke-linejoin="round"/>'
        f"{dots}{_x_labels(labels, len(rows))}</svg>"
    )


def bar_chart(
    points: list[dict],
    value_key: str,
    label_key: str = "week",
    *,
    fmt: str = "{:.0f}",
) -> str:
    rows = [p for p in points if p.get(value_key) is not None]
    if not rows:
        return empty_chart()
    values = [float(p[value_key]) for p in rows]
    labels = [str(p.get(label_key, ""))[5:] for p in rows]
    lo, hi = _scale(values, 0, None)
    n = len(rows)
    slot = (W - 2 * PAD) / n
    bw = max(6, min(28, slot - 4))
    bars = []
    for i, v in enumerate(values):
        x = PAD + i * slot + (slot - bw) / 2
        bh = (v - lo) / (hi - lo) * (H - 2 * PAD)
        y = H - PAD - bh
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(bh, 1):.1f}"'
            f' rx="4" fill="{ACCENT}">'
            f"<title>{escape(labels[i])}: {fmt.format(v)}</title></rect>"
        )
    baseline = (
        f'<line x1="{PAD}" y1="{H - PAD}" x2="{W - PAD}" y2="{H - PAD}"'
        f' stroke="{GRID}" stroke-width="1"/>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" style="width:100%;height:auto">'
        f"{baseline}{''.join(bars)}{_x_labels(labels, n)}</svg>"
    )
