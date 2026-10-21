"""Выгрузить таблицу (TableOut JSON) из документа для vm-renderer-test.

Использование:
    python export_table.py <pdf-путь> <table-id> [out.json]
    python export_table.py <pdf-путь> --merged <N> [out.json]  # N-я таблица с merged cells
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.loaders import load_document  # noqa: E402
from app.api.routes import _table_out  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        raise SystemExit(2)
    pdf, sel = args[0], args[1]
    out = args[2] if len(args) > 2 else "table.json"

    doc = load_document(pdf, "pdf")
    tables = [_table_out(t) for t in doc.tables]
    if sel == "--all":
        out = args[2] if len(args) > 2 else "tables.json"
        Path(out).write_text(json.dumps([t.model_dump() for t in tables], ensure_ascii=False), encoding="utf-8")
        print(f"{len(tables)} таблиц -> {out}")
        return
    if sel == "--merged":
        merged = [t for t in tables if t.has_merged_cells]
        t = merged[int(args[2])] if len(args) > 2 else merged[0]
        out = args[3] if len(args) > 3 else out
    else:
        t = next((x for x in tables if x.id == sel), None)
        if t is None:
            raise SystemExit(f"таблица {sel!r} не найдена; есть: {[x.id for x in tables]}")
    Path(out).write_text(t.model_dump_json(), encoding="utf-8")
    print(f"{t.id}: {t.n_rows}x{t.n_cols} hr={t.header_rows} merged={t.has_merged_cells} "
          f"matrix={t.is_matrix}({t.orientation}) -> {out}")


if __name__ == "__main__":
    main()
