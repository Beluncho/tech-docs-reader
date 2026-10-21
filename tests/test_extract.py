from app.extract import extract_deterministic, extract_keyvalue
from app.loaders._base import LoadResult
from app.preset import Cell, Table


def _table2col():
    rows = [
        [Cell(text="Подача", row=0, col=0), Cell(text="12,5 м3/ч", row=0, col=1)],
        [Cell(text="Напор", row=1, col=0), Cell(text="32 м", row=1, col=1)],
        [Cell(text="Мощность", row=2, col=0), Cell(text="7,5 кВт", row=2, col=1)],
    ]
    return Table(id="t1", rows=rows)


def test_extract_2col_with_units():
    preset = {"columns": {"count": 2, "roles": {"label": 0, "value": 1}}, "labels": ["подача", "напор", "мощность"], "units": ["м3/ч", "м", "кВт"]}
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[_table2col()])
    params, _w, tid = extract_deterministic(lr, preset, "doc.pdf")
    assert tid == "t1"
    assert len(params) == 3
    assert params[0].name == "Подача"
    assert params[0].value == "12,5"
    assert params[0].unit == "м3/ч"
    assert params[1].unit == "м"


def test_extract_keyvalue():
    preset = {"keyvalue": True, "labels": ["подача", "напор"], "units": ["м3/ч", "м"]}
    lines = ["Подача: 12,5 м3/ч", "Напор — 32 м", "прочая строка"]
    params = extract_keyvalue(lines, preset, "doc.txt")
    assert len(params) == 2
    assert params[0].value == "12,5"
    assert params[1].name == "Напор"


def test_extract_header_table():
    preset = {
        "columns": {
            "count": 3,
            "has_header": True,
            "roles": {"label": 0, "value": 1},
            "header": {"наименование": "label", "значение": "value", "ед. изм.": "unit"},
        },
        "labels": ["подача"],
    }
    rows = [
        [Cell(text="Наименование", row=0, col=0), Cell(text="Ед. изм.", row=0, col=1), Cell(text="Значение", row=0, col=2)],
        [Cell(text="Подача", row=1, col=0), Cell(text="м3/ч", row=1, col=1), Cell(text="12,5", row=1, col=2)],
    ]
    t = Table(id="t2", rows=rows)
    lr = LoadResult(fmt="xlsx", loader="xlsx", pages=1, tables=[t])
    params, _w, _tid = extract_deterministic(lr, preset, "doc.xlsx")
    assert len(params) == 1
    assert params[0].value == "12,5"
    assert params[0].unit == "м3/ч"


def _table2col_named(tid: str, rows_text):
    rows = [
        [Cell(text=a, row=i, col=0), Cell(text=b, row=i, col=1)]
        for i, (a, b) in enumerate(rows_text)
    ]
    return Table(id=tid, rows=rows)


def test_deterministic_scans_all_matching_tables_and_dedup():
    preset = {
        "columns": {"count": 2, "roles": {"label": 0, "value": 1}},
        "labels": ["подача", "напор"],
    }
    t1 = _table2col_named("t1", [("Подача", "12,5 м3/ч"), ("Мусор", "текст без цифр")])
    t2 = _table2col_named("t2", [("Подача", "12,5 м3/ч"), ("Напор", "32 м")])
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[t1, t2])
    params, _w, tid = extract_deterministic(lr, preset, "doc.pdf")
    assert tid == "t1"
    assert len(params) == 2  # дубль «Подача» схлопнулся, «Мусор» не попал
    names = sorted(p.name for p in params)
    assert names == ["Напор", "Подача"]


def test_deterministic_include_all_skips_non_value_rows():
    preset = {"columns": {"count": 2, "roles": {"label": 0, "value": 1}}, "labels": [], "include_all": True}
    t = _table2col_named(
        "t1",
        [
            ("Номер документа", "F-2606020"),
            ("Quick quote", "текст без цифр"),
            ("Подача", "12,5 м3/ч"),
            ("Температура", "40,0 °C"),
        ],
    )
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[t])
    params, _w, _tid = extract_deterministic(lr, preset, "doc.pdf")
    values = sorted(p.value for p in params)
    assert values == ["12,5", "40,0"]
