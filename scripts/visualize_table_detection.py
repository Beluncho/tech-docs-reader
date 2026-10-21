"""Визуализация детекции таблиц на страницах PDF (отладка, подэтап 2.8).

Рендерит страницу PDF и рисует поверх рамки детектированных ячеек:
- красные   — merged cells (якоря rowspan/colspan; bbox якоря покрывает
              весь объединённый диапазон, клетки-продолжения не рисуются);
- синие     — строки шапки (header_rows);
- оранжевые — ось матрицы (модели/параметры в первой колонке данных);
- зелёные   — обычные ячейки;
- фиолетовые — граница таблицы целиком + подпись «id [source]».

Запуск:
  python scripts/visualize_table_detection.py "path/to/doc.pdf" --pages 6
  python scripts/visualize_table_detection.py doc.pdf --pages 6,9-11 --html
  python scripts/visualize_table_detection.py doc.pdf                # все страницы с таблицами
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import fitz

from app.detect import detect_tables_from_grid, detect_tables_on_page, merge_page_tables
from app.loaders.pdf_loader import _page_rects, _page_words
from app.preset import Cell, Table

# роли ячеек → цвет рамки (RGB 0..1)
ROLE_COLORS = {
    "merged": (1.0, 0.0, 0.0),
    "header": (0.0, 0.0, 1.0),
    "matrix_axis": (1.0, 0.55, 0.0),
    "normal": (0.0, 0.55, 0.0),
}
ROLE_LEGEND = {
    "merged": "красная — merged cell (rowspan/colspan)",
    "header": "синяя — шапка (header_rows)",
    "matrix_axis": "оранжевая — ось матрицы (модели/параметры)",
    "normal": "зелёная — обычная ячейка",
}
TABLE_OUTLINE_COLOR = (0.5, 0.0, 0.5)
CELL_LINE_WIDTH = 0.7
TABLE_LINE_WIDTH = 1.5

_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def cell_role(cell: Cell, table: Table) -> str | None:
    """Роль ячейки для раскраски; None — не рисовать (merged-продолжение
    покрыто bbox якоря; ячейки без координат отсеиваются при отрисовке)."""
    if cell.is_merged:
        return None
    if cell.rowspan > 1 or cell.colspan > 1:
        return "merged"
    if table.header_rows and cell.row < table.header_rows:
        return "header"
    if table.is_matrix and cell.col == 0 and cell.row >= (table.header_rows or 0):
        return "matrix_axis"
    return "normal"


def detect_page_tables(pdf_path: str, page_index: int) -> list[Table]:
    """Полный пайплайн детекции одной страницы (как в pdf_loader.load)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        words = _page_words(page)
        word = detect_tables_on_page(words, page_index)
        grid = detect_tables_from_grid(words, _page_rects(page), page_index)
        return merge_page_tables(page_index, grid, word)
    finally:
        doc.close()


def visualize_page(pdf_path: str, page_index: int, tables: list[Table],
                   out_path: str, dpi: int = 150, labels: bool = True) -> None:
    """Нарисовать рамки ячеек поверх страницы и сохранить PNG."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        for t in tables:
            if t.bbox and any(t.bbox):
                rect = fitz.Rect(*t.bbox)
                page.draw_rect(rect, color=TABLE_OUTLINE_COLOR, width=TABLE_LINE_WIDTH)
                if labels:
                    page.insert_text(
                        (rect.x0, max(rect.y0 - 3, 8)),
                        f"{t.id} [{t.source}]",
                        fontsize=7, color=TABLE_OUTLINE_COLOR,
                    )
            for row in t.rows:
                for c in row:
                    role = cell_role(c, t)
                    if role is None or c.x0 is None or c.y0 is None:
                        continue
                    page.draw_rect(
                        fitz.Rect(c.x0, c.y0, c.x1, c.y1),
                        color=ROLE_COLORS[role], width=CELL_LINE_WIDTH,
                    )
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        pix.save(out_path)
    finally:
        doc.close()


def parse_pages(spec: str, max_page: int) -> list[int]:
    """Спецификация страниц «6», «6,9-11» (1-based) → отсортированный список.

    Диапазон с обратным порядком («11-9») понимается как «9-11».
    """
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = _RANGE_RE.fullmatch(part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.update(range(a, b + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"не понимаю спецификацию страниц: {part!r}")
    bad = [p for p in out if not 1 <= p <= max_page]
    if bad:
        raise ValueError(f"страницы вне документа (1..{max_page}): {sorted(bad)}")
    return sorted(out)


def table_summary(t: Table) -> dict:
    return {
        "id": t.id,
        "source": t.source or "-",
        "size": f"{t.grid_rows}x{t.grid_cols}",
        "header_rows": t.header_rows,
        "merged": "да" if t.has_merged_cells else "-",
        "matrix": (f"{t.orientation}" if t.is_matrix and t.orientation else
                   ("да" if t.is_matrix else "-")),
    }


def build_html(page_results: list[dict], title: str = "Детекция таблиц") -> str:
    """HTML-отчёт: PNG страниц (base64) + сводка по таблицам."""
    lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>{title}</title>",
        "<style>",
        "body{font-family:sans-serif;margin:20px;background:#fafafa}",
        "img{max-width:100%;border:1px solid #ccc;margin:8px 0}",
        "table{border-collapse:collapse;font-size:14px;margin:8px 0 24px}",
        "th,td{border:1px solid #999;padding:3px 10px;text-align:left}",
        "th{background:#eee}",
        ".legend li{margin:2px 0}",
        "</style></head><body>",
        f"<h1>{title}</h1>",
        "<ul class='legend'>",
        *(f"<li>{text}</li>" for text in ROLE_LEGEND.values()),
        "<li>фиолетовая — граница таблицы и подпись «id [source]»</li>",
        "</ul>",
    ]
    for pr in page_results:
        lines.append(f"<h2>Страница {pr['page']}</h2>")
        if pr["tables"]:
            with open(pr["png"], "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            lines.append(f"<img src='data:image/png;base64,{b64}' alt='страница {pr['page']}'>")
            lines.append("<table><tr>"
                         "<th>id</th><th>источник</th><th>размер</th>"
                         "<th>строк шапки</th><th>merged</th><th>матрица</th></tr>")
            for s in (table_summary(t) for t in pr["tables"]):
                lines.append(
                    f"<tr><td>{s['id']}</td><td>{s['source']}</td><td>{s['size']}</td>"
                    f"<td>{s['header_rows']}</td><td>{s['merged']}</td><td>{s['matrix']}</td></tr>"
                )
            lines.append("</table>")
        else:
            lines.append("<p>Таблицы не детектированы.</p>")
    lines.append("</body></html>")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Визуализация детекции таблиц на страницах PDF")
    ap.add_argument("pdf", help="путь к PDF-документу")
    ap.add_argument("--pages", default="all",
                    help="номера страниц (1-based): «6», «6,9-11», «all» — все страницы с таблицами")
    ap.add_argument("--out", default=None,
                    help="каталог для PNG/HTML (по умолчанию <имя_pdf>_detection)")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--html", action="store_true",
                    help="дополнительно собрать HTML-отчёт (PNG + сводка таблиц)")
    args = ap.parse_args(argv)

    # кириллица в консоли Windows при перенаправлении вывода
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    doc = fitz.open(args.pdf)
    page_count = doc.page_count
    doc.close()

    if args.pages.strip().lower() == "all":
        candidates = list(range(1, page_count + 1))
    else:
        try:
            candidates = parse_pages(args.pages, page_count)
        except ValueError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 2

    out_dir = args.out or os.path.splitext(os.path.basename(args.pdf))[0] + "_detection"
    os.makedirs(out_dir, exist_ok=True)

    page_results: list[dict] = []
    empty_pages: list[int] = []
    for pno in candidates:
        tables = detect_page_tables(args.pdf, pno - 1)
        if not tables:
            empty_pages.append(pno)
            if args.pages.strip().lower() == "all":
                continue  # в режиме «all» рендерим только страницы с таблицами
        png = os.path.join(out_dir, f"page-{pno:03d}.png")
        visualize_page(args.pdf, pno - 1, tables, png, dpi=args.dpi)
        page_results.append({"page": pno, "png": png, "tables": tables})
        print(f"страница {pno}: {len(tables)} таблиц -> {os.path.basename(png)}")

    if args.html:
        html_path = os.path.join(out_dir, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(build_html(page_results, title=f"Детекция таблиц: {os.path.basename(args.pdf)}"))
        print(f"HTML-отчёт: {html_path}")
    if empty_pages:
        print(f"без таблиц: страницы {', '.join(map(str, empty_pages))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
