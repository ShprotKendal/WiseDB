"""CLI: ``wisedb-viz`` или ``python -m wisedb.visualize``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wisedb.vector_db import VectorDB


def _node_label(node) -> str:
    pl = node.payload or {}
    t = pl.get("type", "")
    if t == "text" and pl.get("text"):
        s = str(pl["text"])
        return f"{node.id}\n{s[:40]}{'…' if len(s) > 40 else ''}"
    if "value" in pl:
        return f"{node.id}\n({pl.get('type', '?')}: {pl['value']})"
    if t:
        return f"{node.id}\n({t})"
    return node.id


def build_demo_db() -> VectorDB:
    db = VectorDB()
    db.upsert_node("A", 0.0, 0.0, {"type": "text", "text": "origin note"})
    db.upsert_node("B", 3.0, 4.0, {"type": "text", "text": "along (3,4)"})
    db.upsert_node("C", 1.0, 0.0, {"type": "number", "value": 42})
    db.upsert_node("D", 1.0, 1.0, {"type": "text", "text": "diagonal"})
    db.upsert_node("E", -1.0, 2.0, {"type": "text", "text": "left-up"})
    db.add_vector("v_AB", "A", "B", {"label": "AB"})
    db.add_vector("v_AC", "A", "C", {"label": "AC"})
    db.add_vector("v_AD", "A", "D", {"label": "AD"})
    db.add_vector("v_CE", "C", "E", {"label": "CE"})
    return db


def load_db(path: Path) -> VectorDB:
    return VectorDB.load(str(path))


def draw(db: VectorDB, title: str, save_path: Path | None) -> None:
    import matplotlib

    if save_path is not None:
        matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.6)

    for vid in db.vector_ids():
        vec = db.get_vector(vid)
        if vec is None:
            continue
        t = db.get_node(vec.tail_node_id)
        h = db.get_node(vec.head_node_id)
        if not t or not h:
            continue
        arr = FancyArrowPatch(
            (t.x, t.y),
            (h.x, h.y),
            arrowstyle="-|>",
            mutation_scale=14,
            linewidth=1.6,
            color="#2563eb",
            alpha=0.85,
            zorder=1,
        )
        ax.add_patch(arr)
        mx, my = (t.x + h.x) / 2, (t.y + h.y) / 2
        label = vec.metadata.get("label", vid)
        ax.annotate(
            str(label),
            (mx, my),
            fontsize=8,
            color="#1e3a8a",
            ha="center",
            va="center",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#93c5fd", alpha=0.9),
        )

    for nid in db.node_ids():
        n = db.get_node(nid)
        if not n:
            continue
        ax.scatter([n.x], [n.y], s=220, c="#f97316", edgecolors="#9a3412", linewidths=1.5, zorder=3)
        ax.annotate(
            _node_label(n),
            (n.x, n.y),
            textcoords="offset points",
            xytext=(10, 10),
            fontsize=9,
            ha="left",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#fff7ed", edgecolor="#fdba74", alpha=0.95),
            zorder=4,
        )

    xs = [db.get_node(i).x for i in db.node_ids() if db.get_node(i)]
    ys = [db.get_node(i).y for i in db.node_ids() if db.get_node(i)]
    if xs and ys:
        pad = max(0.5, (max(xs) - min(xs) + max(ys) - min(ys)) * 0.08 + 0.3)
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)

    fig.tight_layout()
    if save_path is not None:
        fig.savefig(save_path, dpi=150)
        print(f"Записано {save_path.resolve()}")
        plt.close(fig)
    else:
        plt.show()


def main() -> int:
    default_input = Path.cwd() / "data" / "vectors.json"
    p = argparse.ArgumentParser(description="Визуализация графа VectorDB (2D узлы и стрелки).")
    p.add_argument(
        "-i",
        "--input",
        type=Path,
        default=default_input,
        help=f"JSON (формат v2); по умолчанию: {default_input}",
    )
    p.add_argument("--demo", action="store_true", help="Не читать файл; встроенный демо-граф")
    p.add_argument("--save", type=Path, default=None, help="Сохранить PNG вместо окна")
    args = p.parse_args()

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("Установите extra viz:  pip install -e \".[viz]\"", file=sys.stderr)
        return 1

    if args.demo:
        db = build_demo_db()
        title = "Демо-граф WiseDB"
    else:
        if not args.input.exists():
            db = build_demo_db()
            title = f"Демо (нет файла {args.input})"
        else:
            try:
                db = load_db(args.input)
                title = f"WiseDB — {args.input.name}"
            except ValueError as e:
                print(f"{args.input}: {e}", file=sys.stderr)
                print("Откат к встроенному демо-графу.", file=sys.stderr)
                db = build_demo_db()
                title = "Демо-граф (legacy или неизвестный JSON)"

    draw(db, title=title, save_path=args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
