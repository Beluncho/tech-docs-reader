# vm-renderer-test — node-харнесс логики Visual Mapper (этап 3)

Проверяет TableRenderer/VisualMapper/table-ops/preview
(frontend/js/visual-mapper/) на реальных данных таблиц без браузера: fabric
подменяется заглушкой (fabric-stub.mjs), модули копируются с заменой импорта
(перед запуском!).

Проверки: сетка/anchorAt (merged → якорь), раскладка (монотонность offX/offY,
прямоугольники якорей без пересечений, покрытие сетки), render (число Rect =
числу якорей), zoom clamp, highlightCell, клик по продолжению → якорь, роли,
операции с ячейками (merge/split/transpose/удаление строк-колонок: чистые
функции на синтетике + раунд-трип merge→split→transpose→delRow с инвариантами
после каждой операции и undo-all → исходная таблица — на каждой таблице файла),
предпросмотр извлечения (buildPreview/toCsv: key-value / matrix-rows /
matrix-columns синтетика, реальная НС p6-t1 — 18 карточек × 7 параметров
из 45 value-ячеек, warnings, переопределения label/unit/group, section/
condition/ignore, CSV-экранирование, инвариант транспонирования), шаблоны
маппинга (getOps — спеки структурных правок из истории, согласованность с
undo/redo, applyOps на свежем клоне ≡ правленая таблица маппера,
setAnnotations с нормализацией к якорям и undo, no-op спеки).

Использование:
    node make-stubs.mjs              # подготовить копии модулей (после правок frontend)
    node run-test.mjs <tables.json>  # JSON одной таблицы или массив (--all)

Данные таблицы (TableOut JSON) выгружаются из backend:
    python export_table.py <pdf> <table-id|--merged N|--all> [out.json]

Сгенерированные JSON лежат в out-debug/vm_test_*.json (gitignored).
