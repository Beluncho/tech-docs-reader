from app.detect import detect_tables_on_page


def _word(x0, y0, text):
    return (x0, y0, x0 + len(text) * 5, y0 + 10, text)


def test_detect_two_column_table():
    words = []
    rows = [
        ("Подача", "12,5 м3/ч"),
        ("Напор", "32 м"),
        ("Мощность", "7,5 кВт"),
        ("Частота вращения", "1450 об/мин"),
    ]
    for i, (a, b) in enumerate(rows):
        y = 50 + i * 14
        words.append(_word(20, y, a))
        words.append(_word(120, y, b))
    tables = detect_tables_on_page(words, 0)
    assert len(tables) == 1
    t = tables[0]
    assert t.n_cols == 2
    assert len(t.rows) >= 4
    assert t.rows[0][0].text == "Подача"
    assert t.rows[0][1].text == "12,5 м3/ч"