# -*- coding: utf-8 -*-
"""Вариант Б (матрицы): фильтры мусора в извлечении «модель × параметр»."""
from app.extract import DEFAULT_UNITS, extract_deterministic
from app.loaders._base import LoadResult
from app.preset import Cell, Table


def _cell(text, row, col, x0, x1):
    return Cell(text=text, row=row, col=col, x0=x0, x1=x1)


def _row_fill(row, col, text, x0, x1):
    out = [Cell(text="", row=row, col=c, x0=None, x1=None) for c in range(6)]
    out[col] = _cell(text, row, col, x0, x1)
    return out


def _filter_table():
    rows = [
        # широкие «суперзаголовки»: Ду фильтра/компенсатора и размеры (Lф/Hф)
        [_cell("Марка насоса", 0, 0, 50, 130), _cell("Условный проход Dу, мм", 0, 1, 150, 240),
         _cell("Размеры фильтра, мм", 0, 3, 260, 350), _cell("Подача, м3/ч", 0, 5, 360, 400)],
        [_cell("Фильтр", 1, 1, 150, 190), _cell("Компенсатор", 1, 2, 200, 240),
         _cell("Lф", 1, 3, 260, 300), _cell("Hф", 1, 4, 310, 350)],
    ]
    models = [
        ("КММ40-25-160", 40, 40, 200, 150, "12,5"),
        ("КММ50-32-125", 50, 50, 230, 160, "25"),
        ("КММ65-50-125", 65, 65, 290, 180, "32"),
    ]
    for i, (m, du, komp, lf, hf, q) in enumerate(models, start=2):
        rows.append([
            _cell(m, i, 0, 50, 130), _cell(str(du), i, 1, 150, 190), _cell(str(komp), i, 2, 200, 240),
            _cell(str(lf), i, 3, 260, 300), _cell(str(hf), i, 4, 310, 350), _cell(q, i, 5, 360, 400),
        ])
    return Table(id="t-filter", rows=rows)


def test_matrix_skips_accessory_and_dimension_columns():
    preset = {"columns": {}, "labels": [], "units": DEFAULT_UNITS, "include_all": True}
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[_filter_table()])
    params, _w, _tid = extract_deterministic(lr, preset, "doc.pdf")
    names = [p.name for p in params]
    # «Условный проход» фильтра/компенсатора и буквенные «Размеры» (Lф/Hф) — мусор
    assert not any("Условный проход" in n for n in names)
    assert not any(n.startswith("Размер") for n in names)
    # настоящий параметр из той же таблицы остаётся
    подача = [n for n in names if n.startswith("Подача")]
    assert len(подача) == 3


def _table_ecv_models():
    """Формат 1: строки = модели ЭЦВ (подача в маркировке, колонка «Подача» не у всех)."""
    rows = [
        [_cell("ЭЦВ 5-6,5-50", 0, 0, 50, 130), _cell("50", 0, 1, 150, 190),
         _cell("6+0,5", 0, 2, 200, 240), _cell("37,5", 0, 3, 250, 290)],
        [_cell("ЭЦВ 5-6,5-65", 1, 0, 50, 130), _cell("65", 1, 1, 150, 190),
         _cell("6+0,8", 1, 2, 200, 240), _cell("39", 1, 3, 250, 290)],
        [_cell("ЭЦВ ЭЦВ 5-6,5-80 5-6,5-100", 2, 0, 50, 130), _cell("6,5", 2, 1, 150, 190),
         _cell("100 80", 2, 2, 200, 240), _cell("41 44", 2, 3, 250, 290)],
    ]
    return Table(id="t-ecv", rows=rows)


def test_ecv_feed_poddacha_from_marking():
    preset = {"columns": {}, "labels": [], "units": DEFAULT_UNITS, "include_all": True}
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[_table_ecv_models()])
    params, _w, _tid = extract_deterministic(lr, preset, "doc.pdf")
    by_name = {p.name: p.value for p in params}
    assert by_name.get("Подача (ЭЦВ 5-6,5-50)") == "6,5"
    assert by_name.get("Подача (ЭЦВ 5-6,5-65)") == "6,5"
    # склеенная ячейка «ЭЦВ ЭЦВ 5-6,5-80 5-6,5-100» даёт обе модели
    assert by_name.get("Подача (ЭЦВ 5-6,5-80)") == "6,5"
    assert by_name.get("Подача (ЭЦВ 5-6,5-100)") == "6,5"


def _table_1k_transposed():
    """Формат 2: строки = показатели, колонки = модели 1К; последняя строка без значений."""
    rows = [
        [_cell("Наименование показателя", 0, 0, 76, 190), _cell("1К50-32-125", 0, 1, 229, 257),
         _cell("1К65-50-160", 0, 2, 274, 310), _cell("1К80-50-200", 0, 3, 320, 350),
         _cell("1К100-65-200", 0, 4, 380, 420)],
        [_cell("Подача, м3/ч", 1, 0, 76, 156), _cell("12,5", 1, 1, 231, 253),
         _cell("25", 1, 2, 282, 295), _cell("50", 1, 3, 344, 355),
         _cell("100", 1, 4, 380, 395)],
        [_cell("Максимальная мощ-", 2, 0, 76, 200), _cell("", 2, 1, 229, 257),
         _cell("", 2, 2, 274, 310), _cell("", 2, 3, 320, 350),
         _cell("", 2, 4, 380, 420)],
    ]
    t = Table(id="t-1k", rows=rows, page=0)
    for r, row in enumerate(rows):
        for c in row:
            c.y0 = 60 + r * 60
            c.y1 = 75 + r * 60
    return t


def _page_words_gap():
    """Слова «разрыва» под последней строкой: метка + значения мощности (как у 1К)."""
    def w(x0, y0, x1, y1, text):
        return (x0, y0, x1, y1, text)
    return [
        w(76.3, 224.7, 109.7, 237.7, "ность"),
        w(234.7, 224.7, 250.8, 237.7, "1,6"),
        w(280.1, 224.7, 296.2, 237.7, "4,3"),
        w(322.6, 224.7, 336.0, 237.7, "15"),
        w(361.0, 224.7, 377.1, 237.7, "7,0"),
        w(76.3, 239.6, 121.5, 252.6, "насоса,"),
        w(125.3, 239.6, 145.6, 252.6, "кВт"),
    ]


def test_gap_continuation_power_1k():
    preset = {"columns": {}, "labels": [], "units": DEFAULT_UNITS, "include_all": True}
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[_table_1k_transposed()],
                    page_words=[_page_words_gap()])
    params, _w, _tid = extract_deterministic(lr, preset, "doc.pdf")
    by_name = {p.name: p.value for p in params}
    assert by_name.get("Мощность (1К50-32-125)") == "1,6"
    assert by_name.get("Мощность (1К65-50-160)") == "4,3"
    assert by_name.get("Мощность (1К80-50-200)") == "15"
    assert by_name.get("Мощность (1К100-65-200)") == "7,0"


def _page_words_cns_rowspan():
    """Слова страницы «Таблицы 1» НС: пара моделей в строке, значения на под-строках."""
    def w(text, x0, x1, y0):
        return (x0, y0, x1, y0 + 8.9, text)

    words = [
        # шапка «Обозначение насосов / Число ступеней / Подача / Напор / Частота ...»
        w("Обозначение", 51.9, 96.4, 44.4), w("Число", 116.8, 137.8, 44.4),
        w("Подача,", 144.1, 171.1, 44.4), w("Напор,", 175.7, 199.4, 44.4),
        w("Частота", 207.9, 234.9, 44.4), w("Допускаемый", 243.8, 290.9, 44.4),
        w("Мощность", 304.1, 340.3, 44.4), w("Масса,", 351.1, 374.5, 44.4),
        w("кг,", 376.4, 385.6, 44.4),
        w("насосов", 60.7, 87.5, 53.2), w("ступеней,", 110.8, 143.8, 53.2),
        w("м3/с", 150.9, 164.3, 53.2), w("м", 185.1, 190.2, 53.2),
        w("вращения", 204.7, 238.0, 53.2), w("кавитацион-", 246.3, 288.5, 53.2),
        w("электродвигате-", 294.7, 349.8, 53.2), w("не", 355.9, 363.8, 53.2),
        w("более", 365.8, 385.0, 53.2),
        w("шт", 122.4, 132.2, 62.3), w("(м3/ч)", 148.0, 167.3, 62.3),
        w("ротора,", 208.8, 233.9, 62.3), w("ный", 245.7, 259.7, 62.3),
        w("запас,", 261.7, 281.8, 62.3), w("м", 283.8, 288.9, 62.3),
        w("ля,", 302.0, 311.7, 62.3), w("кВт", 313.8, 326.5, 62.3),
        w("(Рн)", 328.5, 342.6, 62.3),
        w("(об/мин)", 206.7, 236.1, 71.5),
        # НС 13-280 / НСГ 13-280 (масса 311)
        w("НС", 38.7, 55.5, 87.4), w("13-280", 57.6, 80.2, 87.4),
        w("НСГ", 38.7, 60.2, 96.7), w("13-280", 62.2, 84.9, 96.7),
        w("311", 363.2, 375.3, 96.7),
        # мощность 30 — между базовой и PP-парой (общая для них)
        w("30", 318.2, 326.3, 112.4),
        # НС 13-280-РР / НСГ 13-280-РР (ступени 8, напор 280, масса 280)
        w("НС", 38.7, 55.5, 117.3), w("13-280-РР", 57.6, 91.9, 117.3),
        w("8", 125.3, 129.4, 121.9), w("280", 181.6, 193.7, 121.9),
        w("НСГ", 38.7, 60.2, 126.4), w("13-280-РР", 62.2, 96.5, 126.4),
        w("280", 363.2, 375.3, 126.4),
        # НСМ 13-280 / НСн 13-280 (мощность 45, масса 311)
        w("НСМ", 38.7, 62.7, 145.4), w("13-280", 64.7, 87.4, 145.4),
        w("45", 318.2, 326.3, 149.9), w("311", 363.2, 375.3, 149.9),
        w("НСн", 38.7, 59.9, 154.5), w("13-280", 61.8, 84.6, 154.5),
        # серийные значения серии 13 (печатаются один раз)
        w("0,0036", 148.2, 170.3, 160.0), w("(2950)", 212.4, 233.8, 160.0),
        w("3,0", 264.1, 274.1, 160.0),
    ]
    return words


def test_cns_table1_rowspan_words():
    """НС «Таблица 1»: rowspan-строки собираются по словам страницы (без ячеек)."""
    from app.matrix import extract_matrices

    page_words = [_page_words_cns_rowspan()]
    params, _seen = [], set()
    params = extract_matrices([], "doc.pdf", DEFAULT_UNITS, page_words=page_words)
    by_name = {p.name: (p.value, p.unit) for p in params}
    # парные значения: ступени/напор/масса/мощность
    assert by_name.get("Число ступеней (НС 13-280)") == ("8", "шт")
    assert by_name.get("Число ступеней (НСМ 13-280)") == ("8", "шт")
    assert by_name.get("Напор (НСн 13-280)") == ("280", "м")
    assert by_name.get("Масса (НС 13-280)") == ("311", "кг")
    assert by_name.get("Масса (НСГ 13-280-РР)") == ("280", "кг")
    # мощность у базовой пары наследуется от «-РР» (общая ячейка печати)
    assert by_name.get("Мощность (НС 13-280)") == ("30", "кВт")
    assert by_name.get("Мощность (НСМ 13-280)") == ("45", "кВт")
    # серийные значения раздаются всем моделям серии
    assert by_name.get("Подача (НС 13-280)") == ("0,0036", "м3/с")
    assert by_name.get("Частота вращения (НСГ 13-280-РР)") == ("2950", "об/мин")
    assert by_name.get("Кавитационный запас (НСн 13-280)") == ("3,0", "м")
    # полный набор: 6 моделей x 7 параметров
    assert len(params) == 42


def _gcell(text, row, col, x0, x1, y0, y1):
    return Cell(text=text, row=row, col=col, x0=x0, x1=x1, y0=y0, y1=y1)


def _grid_table_1ks():
    """Транспонированная grid-таблица (как Таблица 1 1Кs): модели в шапке."""
    y = [(100, 115), (116, 131), (132, 157), (158, 183), (184, 199), (200, 215)]
    rows = [
        [_gcell("Наименование показателя", 0, 0, 60, 200, *y[0]),
         _gcell("Типоразмер насоса (агрегата) 1Кс", 0, 1, 210, 360, *y[0])],
        [_gcell("показателя", 1, 0, 60, 200, *y[1]),
         _gcell("20-50", 1, 1, 210, 245, *y[1]),
         _gcell("20-110", 1, 2, 250, 290, *y[1]),
         _gcell("32-150", 1, 3, 295, 360, *y[1])],
    ]
    labels = ["Подача, м3/ч", "Давление на входе, МПа",
              "Мощность, кВт", "Частота, Гц"]
    xs = [(210, 245), (250, 290), (295, 360)]
    for i, lab in enumerate(labels, start=2):
        row = [_gcell(lab, i, 0, 60, 200, *y[i])]
        for c, (x0, x1) in enumerate(xs, start=1):
            row.append(_gcell("", i, c, x0, x1, *y[i]))
        rows.append(row)
    return Table(id="p6-t2", rows=rows, page=0,
                 bbox=[60.0, 100.0, 360.0, 215.0], source="grid")


def _page_words_grid_1ks():
    def w(x0, y0, x1, y1, text):
        return (x0, y0, x1, y1, text)
    return [
        # Подача: значения по моделям
        w(212, 140, 222, 150, "20"), w(258, 140, 268, 150, "20"),
        w(300, 140, 310, 150, "32"),
        # Давление: одно значение на сегмент
        w(250, 165, 290, 175, "0,98(10)"),
        # Мощность: значения по моделям
        w(212, 186, 222, 196, "5,7"), w(258, 186, 268, 196, "13,5"),
        w(300, 186, 310, 196, "25,0"),
        # Частота: одно значение на сегмент
        w(250, 201, 290, 211, "49(2950)"),
    ]


def test_grid_transposed_pairs_1ks_common_values():
    """Grid «модели в шапке»: значения по колонкам; одно значение строки
    раздаётся всем моделям сегмента (как «общие» значения НС)."""
    from app.matrix import _grid_transposed_pairs

    params = _grid_transposed_pairs([_grid_table_1ks()], "doc.pdf", set(),
                                    [_page_words_grid_1ks()])
    got = {(p.name, p.value, p.unit) for p in params}
    # значения по моделям из шапки (префикс «1Кс» берётся из заголовка над кодами)
    assert ("Подача (1Кс 20-50)", "20", "м3/ч") in got
    assert ("Подача (1Кс 20-110)", "20", "м3/ч") in got
    assert ("Подача (1Кс 32-150)", "32", "м3/ч") in got
    assert ("Мощность (1Кс 20-110)", "13,5", "кВт") in got
    assert ("Мощность (1Кс 32-150)", "25,0", "кВт") in got
    # одно значение строки -> каждой модели сегмента
    pressure = sorted(p.name for p in params if p.name.startswith("Давление"))
    assert len(pressure) == 3
    assert all(p.name.startswith("Давление") and p.value == "0,98(10)"
               and p.unit == "МПа" for p in params if p.name.startswith("Давление"))
    assert sum(1 for p in params if p.name.startswith("Частота")) == 3
    assert sum(1 for p in params if p.name.startswith("Мощность")) == 3
    assert len(params) == 12
