"""HTTP-маршруты стенда."""
from __future__ import annotations

import copy
import datetime
import json
import logging
import time

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app import ocr as ocr_mod
from app.api.deps import settings, storage
from app.extract import extract_deterministic
from app.llm import LLMError, extract_with_llm
from app.loaders import load_document_cached
from app.mapping_apply import apply_ops, build_preview, normalize_annotations
from app.mapping_template import (
    match_templates,
    table_signature,
    template_from_mapping,
)
from app.models import (
    CellOut,
    DocumentListItem,
    DocumentOut,
    ExtractRequest,
    ExtractResult,
    MappingAnnotation,
    MappingApplyOut,
    MappingApplyRequest,
    MappingCreate,
    MappingDocumentMatchOut,
    MappingMatchOut,
    MappingTableMatchOut,
    MappingTemplateOut,
    MappingValidateOut,
    MappingValidateRequest,
    LLMSuggestOut,
    LLMSuggestRequest,
    LLMMetricsOut,
    LLMFeedbackOut,
    LLMFeedbackRequest,
    PageRender,
    Parameter,
    PresetCreate,
    PresetFromExtraction,
    PresetOut,
    TableOut,
)
from app.llm_mapping import LLMMapperService, ROLES, compute_accuracy
from app.normalize import normalize_header
from app.preset import Table, candidate_header_row, document_signature, first_col_labels, match_presets

router = APIRouter(prefix="/api")

# События мониторинга (5.7): upload/extract/llm_suggest с doc_id и таймингами.
log = logging.getLogger("app.api")

ALLOWED_EXTS = {".xlsx", ".xls", ".docx", ".csv", ".txt", ".pdf"}


def _fmt_from_ext(ext: str) -> str:
    ext = ext.lstrip(".")
    return {"xlsx": "xlsx", "xls": "xlsx", "docx": "docx", "csv": "csv", "txt": "txt", "pdf": "pdf"}[ext]


def _table_out(t: Table) -> TableOut:
    cells = [
        CellOut(
            text=c.text,
            page=c.page,
            x0=c.x0,
            y0=c.y0,
            x1=c.x1,
            y1=c.y1,
            row=c.row,
            col=c.col,
            rowspan=c.rowspan,
            colspan=c.colspan,
            is_merged=c.is_merged,
            merged_with=list(c.merged_with) if c.merged_with else None,
        )
        for row in t.rows
        for c in row
    ]
    return TableOut(
        id=t.id,
        sheet=t.sheet,
        page=t.page,
        header_row=t.header_row,
        n_rows=len(t.rows),
        n_cols=t.n_cols,
        bbox=t.bbox or [],
        cells=cells,
        header_rows=t.header_rows,
        has_merged_cells=t.has_merged_cells,
        is_matrix=t.is_matrix,
        orientation=t.orientation,
    )


def _load_result_from_meta(meta: dict):
    from app.loaders._base import LoadResult

    tables = [Table.from_dict(d) for d in meta.get("tables", [])]
    return LoadResult(
        fmt=meta.get("fmt", ""),
        loader=meta.get("loader", ""),
        pages=meta.get("pages", 0),
        tables=tables,
        text_lines=meta.get("text_lines", []),
    )


def _document_out(meta: dict) -> DocumentOut:
    tables = [_table_out(Table.from_dict(d)) for d in meta.get("tables", [])]
    return DocumentOut(
        id=meta["id"],
        filename=meta.get("filename", ""),
        ext=meta.get("ext", ""),
        fmt=meta.get("fmt", ""),
        size_bytes=meta.get("size_bytes", 0),
        pages=meta.get("pages", 0),
        ocr=meta.get("ocr", False),
        status=meta.get("status", "PARSE_FAILED"),
        error=meta.get("error"),
        tables=tables,
        auto_preset_id=meta.get("auto_preset_id"),
        last_result=meta.get("last_result"),
    )


@router.get("/health")
def health():
    return {"status": "ok", "llm_enabled": settings.llm_enabled, "version": "0.1.0"}


@router.post("/documents", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    ocr: bool = Form(False),
):
    t0 = time.perf_counter()
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "FILE_TOO_LARGE")
    filename = file.filename or "doc"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        raise HTTPException(415, f"UNSUPPORTED_FILE_TYPE: {ext}")
    fmt = _fmt_from_ext(ext)

    doc_id = storage.new_document_id()
    path = storage.save_original(doc_id, filename, data)
    meta = {
        "id": doc_id,
        "filename": filename,
        "ext": ext,
        "fmt": fmt,
        "size_bytes": len(data),
        "pages": 1,
        "ocr": bool(ocr),
        "status": "PARSED",
        "error": None,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tables": [],
        "text_lines": [],
        "loader": "",
        "auto_preset_id": None,
        "auto_preset_score": None,
    }
    try:
        ocr_words = None
        if fmt == "pdf" and ocr:
            ocr_words = ocr_mod.ocr_pdf(str(path), settings.ocr_service_url)
            meta["ocr"] = True
        load_result = load_document_cached(str(path), fmt, ocr_words=ocr_words)
        meta["loader"] = load_result.loader
        meta["pages"] = load_result.pages
        meta["tables"] = [t.to_dict() for t in load_result.tables]
        meta["text_lines"] = load_result.text_lines[:2000]
        signature = document_signature(load_result.loader, load_result.tables)
        preset, score, method, table_id = match_presets(storage.list_presets(), load_result.loader, signature)
        if preset and (method == "exact" or score >= 0.45):
            meta["auto_preset_id"] = preset["id"]
            meta["auto_preset_score"] = round(score, 3)
            meta["auto_preset_method"] = method
            meta["auto_table_id"] = table_id
    except ocr_mod.OCRNotConfigured as exc:
        log.warning("event=upload doc_id=%s file=%r status=OCR_FAILED error=%s", doc_id, filename, exc)
        meta["status"] = "OCR_FAILED"
        meta["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        # полный traceback в лог: причина PARSE_FAILED не должна теряться молча (5.7)
        log.exception("event=upload doc_id=%s file=%r status=PARSE_FAILED", doc_id, filename)
        meta["status"] = "PARSE_FAILED"
        meta["error"] = str(exc)[:500]
    storage.save_document_meta(doc_id, meta)
    log.info(
        "event=upload doc_id=%s file=%r fmt=%s pages=%s tables=%d status=%s ocr=%s ms=%.0f",
        doc_id, filename, fmt, meta["pages"], len(meta["tables"]), meta["status"],
        meta["ocr"], (time.perf_counter() - t0) * 1000,
    )
    return _document_out(meta)


@router.get("/documents")
def list_documents():
    items = []
    for meta in storage.list_documents():
        items.append(
            DocumentListItem(
                id=meta["id"],
                filename=meta.get("filename", ""),
                fmt=meta.get("fmt", ""),
                pages=meta.get("pages", 0),
                status=meta.get("status", "?"),
                created_at=meta.get("created_at", ""),
            )
        )
    return items


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def document_detail(doc_id: str):
    meta = storage.load_document_meta(doc_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    meta["id"] = doc_id
    return _document_out(meta)


@router.get("/documents/{doc_id}/pageinfo/{page}")
def page_info(doc_id: str, page: int):
    meta = storage.load_document_meta(doc_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if meta.get("fmt") != "pdf":
        raise HTTPException(400, "NOT_PDF")
    path = storage.original_path(doc_id)
    from app.render import render_page

    _data, w, h, pw, ph = render_page(str(path), page, settings.page_render_zoom)
    return PageRender(page=page, width=w, height=h, pdf_width=pw, pdf_height=ph)


@router.get("/documents/{doc_id}/page/{page}")
def page_image(doc_id: str, page: int):
    meta = storage.load_document_meta(doc_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if meta.get("fmt") != "pdf":
        raise HTTPException(400, "NOT_PDF")
    if page < 0 or page >= meta.get("pages", 0):
        raise HTTPException(404, "PAGE_NOT_FOUND")
    path = storage.original_path(doc_id)
    data, _info = storage.render_page_cached(doc_id, page, path, settings.page_render_zoom)
    return Response(content=data, media_type="image/png")


@router.post("/documents/{doc_id}/extract", response_model=ExtractResult)
def extract_document(doc_id: str, body: ExtractRequest):
    t0 = time.perf_counter()
    meta = storage.load_document_meta(doc_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if meta.get("status") == "PARSE_FAILED":
        raise HTTPException(409, "DOCUMENT_PARSE_FAILED")
    if meta.get("status") == "OCR_FAILED":
        raise HTTPException(409, "OCR_FAILED")

    load_result = _load_result_from_meta(meta)
    if meta.get("fmt") == "pdf" and not meta.get("ocr"):
        try:
            # кэш: upload уже разбирал этот файл — повторный extract не парсит PDF заново
            load_result = load_document_cached(str(storage.original_path(doc_id)), "pdf")
        except Exception:
            pass
    preset = None
    if body.preset_id:
        preset = storage.get_preset(body.preset_id)
        if preset is None:
            raise HTTPException(404, "PRESET_NOT_FOUND")
    else:
        auto_id = meta.get("auto_preset_id")
        if auto_id:
            preset = storage.get_preset(auto_id)

    warnings: list[str] = []
    source = "preset"
    llm_used = False
    llm_model = None
    params = []
    llm_table_id = body.table_id or meta.get("auto_table_id")
    det_table_id = body.table_id or None

    if body.use_llm:
        try:
            params, llm_model = extract_with_llm(
                load_result,
                settings,
                meta.get("filename", doc_id),
                preset=preset,
                table_id=llm_table_id,
            )
            source = "llm"
            llm_used = True
        except LLMError as exc:
            warnings.append(f"LLM: {exc}")
            if preset is None:
                raise HTTPException(502, f"LLM_FAILED: {exc}")
            source = "preset"

    if not params:
        if preset is None:
            raise HTTPException(
                409,
                "NO_PRESET_MATCHED — настройте маппинг вручную или включите use_llm=true",
            )
        params, det_warnings, table_id = extract_deterministic(
            load_result, preset, meta.get("filename", doc_id), include_all=body.include_all, table_id=det_table_id
        )
        warnings.extend(det_warnings)
        source = "mixed" if llm_used else "preset"

    result = ExtractResult(
        document_id=doc_id,
        preset_id=preset["id"] if preset else None,
        preset_name=preset.get("name") if preset else None,
        source=source,
        matched_score=meta.get("auto_preset_score"),
        parameters=params,
        warnings=warnings,
        llm_used=llm_used,
        llm_model=llm_model,
    )
    prev = meta.get("last_result") or {}
    prev_params = prev.get("parameters") or []
    if not params and prev_params:
        warnings.append(
            f"Прогон не дал результатов — показан предыдущий результат "
            f"({len(prev_params)} поз., источник: {prev.get('source', 'preset')})."
        )
        try:
            result = ExtractResult(
                document_id=doc_id,
                preset_id=prev.get("preset_id"),
                preset_name=prev.get("preset_name"),
                source=prev.get("source", "preset"),
                matched_score=prev.get("matched_score"),
                parameters=[Parameter(**p) for p in prev_params],
                warnings=warnings,
                llm_used=bool(prev.get("llm_used")),
                llm_model=prev.get("llm_model"),
            )
        except Exception:  # noqa: BLE001 — повреждённый прошлый результат не должен ломать прогон
            result = result.model_copy(update={"warnings": warnings})
    meta["last_result"] = json.loads(result.model_dump_json())
    meta["last_result_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    storage.save_document_meta(doc_id, meta)
    log.info(
        "event=extract doc_id=%s preset=%s source=%s params=%d llm=%s ms=%.0f",
        doc_id, result.preset_id, source, len(result.parameters), llm_used,
        (time.perf_counter() - t0) * 1000,
    )
    return result


@router.post("/documents/{doc_id}/reprocess", response_model=ExtractResult)
def reprocess(doc_id: str, body: ExtractRequest):
    return extract_document(doc_id, body)


@router.get("/presets", response_model=list[PresetOut])
def list_presets():
    return [PresetOut(**{**p, "builtin": p.get("builtin", False)}) for p in storage.list_presets()]


@router.post("/presets", response_model=PresetOut)
def create_preset(body: PresetCreate):
    data = body.model_dump()
    data.pop("id", None)
    preset_id = storage.save_preset(data)
    out = storage.get_preset(preset_id)
    return PresetOut(**out)


@router.put("/presets/{preset_id}", response_model=PresetOut)
def update_preset(preset_id: str, body: PresetCreate):
    existing = storage.get_preset(preset_id)
    if existing is None or existing.get("builtin"):
        raise HTTPException(404, "PRESET_NOT_FOUND_OR_BUILTIN")
    data = body.model_dump()
    data.pop("id", None)
    data["id"] = preset_id
    storage.save_preset(data)
    return PresetOut(**storage.get_preset(preset_id))


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str):
    ok = storage.delete_preset(preset_id)
    if not ok:
        raise HTTPException(404, "PRESET_NOT_FOUND")
    return {"deleted": preset_id}


@router.post("/presets/from-extraction", response_model=PresetOut)
def preset_from_extraction(body: PresetFromExtraction):
    """Сохранить набор из удачного прогона: структура документа + метки параметров."""
    meta = storage.load_document_meta(body.document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    tables = [Table.from_dict(d) for d in meta.get("tables", [])]
    if not tables:
        raise HTTPException(409, "NO_TABLES")
    last_result = meta.get("last_result") or {}

    # Целевая таблица: явная → авто-подобранная → которую реально читал LLM → первая
    target = None
    if body.table_id:
        target = next((t for t in tables if t.id == body.table_id), None)
    if target is None and meta.get("auto_table_id"):
        target = next((t for t in tables if t.id == meta["auto_table_id"]), None)
    if target is None:
        counts: dict[str, int] = {}
        for p in last_result.get("parameters", []):
            frag = (p.get("evidence") or {}).get("fragment")
            if frag and any(t.id == frag for t in tables):
                counts[frag] = counts.get(frag, 0) + 1
        if counts:
            best = max(counts, key=counts.get)
            target = next((t for t in tables if t.id == best), None)
    target = target or tables[0]

    src = storage.get_preset(body.preset_id) if body.preset_id else None
    if src:
        columns = copy.deepcopy(src.get("columns") or {})
        labels = [normalize_header(l) for l in src.get("labels", []) if l.strip()]
        doc_type = src.get("doc_type") or "passport"
        units = list(src.get("units") or [])
        section_keywords = list(src.get("section_keywords") or [])
        include_all = bool(src.get("include_all"))
        keyvalue = bool(src.get("keyvalue"))
    else:
        hr = candidate_header_row(target)
        columns = {
            "count": target.n_cols,
            "has_header": hr is not None,
            "roles": {"label": 0, "value": 1},
        }
        if hr is not None:
            columns["header"] = {
                c.text: ("label" if j == 0 else "value")
                for j, c in enumerate(target.rows[hr])
                if c.text.strip()
            }
        labels = [normalize_header(l) for l in first_col_labels(target)]
        doc_type = "passport"
        units = []
        section_keywords = []
        include_all = False
        keyvalue = False

    for p in last_result.get("parameters", []):
        name = normalize_header(p.get("name", ""))
        if name and name not in labels:
            labels.append(name)

    source_tag = last_result.get("source") or "preset"
    signature = document_signature(meta.get("loader", "pdf"), tables)
    preset = {
        "id": None,
        "name": body.name or f"Набор от {datetime.date.today()} ({meta.get('filename', 'doc')})",
        "description": body.description or f"Создан из успешного извлечения ({source_tag})",
        "doc_type": doc_type,
        "columns": columns,
        "labels": labels[:200],
        "section_keywords": section_keywords,
        "header_keywords": [],
        "units": units,
        "include_all": include_all,
        "keyvalue": keyvalue,
        "fingerprint": None,
    }
    try:
        from app.preset import compute_exact_fingerprint

        preset["fingerprint"] = compute_exact_fingerprint(meta.get("loader", "pdf"), signature)
    except Exception:
        preset["fingerprint"] = None
    preset_id = storage.save_preset(preset)
    return PresetOut(**storage.get_preset(preset_id))


# --- шаблоны маппинга из Visual Mapper (этап 3.7) ---------------------------


def _table_from_meta(meta: dict, table_id: str) -> Table | None:
    for d in meta.get("tables", []):
        if d.get("id") == table_id:
            return Table.from_dict(d)
    return None


@router.post("/presets/save-from-mapping", response_model=MappingTemplateOut)
def save_mapping(body: MappingCreate):
    """Сохранить маппинг из Visual Mapper (роли + структурные правки) как шаблон."""
    meta = storage.load_document_meta(body.document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    table = _table_from_meta(meta, body.table_id)
    if table is None:
        raise HTTPException(404, "TABLE_NOT_FOUND")
    if not body.annotations:
        raise HTTPException(422, "EMPTY_MAPPING — назначьте роли перед сохранением")

    template = template_from_mapping(
        name=body.name.strip() or f"Шаблон {body.table_id}",
        description=body.description,
        document_id=body.document_id,
        table=table,
        source={
            "document_id": body.document_id,
            "table_id": body.table_id,
            "filename": meta.get("filename", ""),
        },
        ops=[op.model_dump(exclude_none=True) for op in body.ops],
        annotations=[a.model_dump(exclude_none=True) for a in body.annotations],
    )
    mapping_id = storage.save_mapping(template)
    return MappingTemplateOut(**storage.get_mapping(mapping_id))


@router.get("/mapping/templates", response_model=list[MappingTemplateOut])
def list_mappings():
    return [MappingTemplateOut(**m) for m in storage.list_mappings()]


@router.delete("/mapping/templates/{mapping_id}")
def delete_mapping(mapping_id: str):
    if not storage.delete_mapping(mapping_id):
        raise HTTPException(404, "MAPPING_NOT_FOUND")
    return {"deleted": mapping_id}


@router.get("/mapping/match", response_model=MappingMatchOut | None)
def match_mapping(document_id: str, table_id: str):
    """Лучший шаблон маппинга для таблицы документа (по сигнатуре структуры)."""
    meta = storage.load_document_meta(document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    table = _table_from_meta(meta, table_id)
    if table is None:
        raise HTTPException(404, "TABLE_NOT_FOUND")
    matches = match_templates(storage.list_mappings(), table_signature(table))
    if not matches:
        return None
    best = matches[0]
    return MappingMatchOut(
        template=MappingTemplateOut(**best["template"]),
        score=best["score"],
        method=best["method"],
    )


@router.get("/mapping/match-document", response_model=MappingDocumentMatchOut)
def match_mapping_document(document_id: str):
    """Лучший шаблон для каждой таблицы документа — одним запросом.

    Лёгкая версия /mapping/match (id и имя шаблона, без ops/аннотаций):
    основной UI показывает индикатор шаблонов, маппер помечает таблицы
    в селекте. Без сохранённых шаблонов — пустой список.
    """
    meta = storage.load_document_meta(document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    mappings = storage.list_mappings()
    out = []
    for d in meta.get("tables", []):
        table = Table.from_dict(d)
        m = MappingTableMatchOut(table_id=table.id)
        if mappings:
            found = match_templates(mappings, table_signature(table))
            if found:
                best = found[0]
                m = MappingTableMatchOut(
                    table_id=table.id,
                    template_id=best["template"]["id"],
                    template_name=best["template"]["name"],
                    score=best["score"],
                    method=best["method"],
                )
        out.append(m)
    return MappingDocumentMatchOut(document_id=document_id, matches=out)


@router.post("/mapping/validate", response_model=MappingValidateOut)
def validate_mapping(body: MappingValidateRequest):
    """Применить маппинг (ops + роли) к таблице и вернуть предпросмотр извлечения.

    Серверный аналог предпросмотра Visual Mapper: та же семантика привязки
    значений к параметрам/моделям (паритет закреплён тестами на синтетических таблицах).
    """
    meta = storage.load_document_meta(body.document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    table = _table_from_meta(meta, body.table_id)
    if table is None:
        raise HTTPException(404, "TABLE_NOT_FOUND")
    if not body.annotations:
        raise HTTPException(422, "EMPTY_MAPPING — назначьте роли перед проверкой")

    apply_ops(table, [op.model_dump(exclude_none=True) for op in body.ops])
    anns = normalize_annotations(table, [a.model_dump(exclude_none=True) for a in body.annotations])
    preview = build_preview(table, anns)
    return MappingValidateOut(
        document_id=body.document_id,
        table_id=body.table_id,
        cards=preview["cards"],
        stats=preview["stats"],
        warnings=preview["warnings"],
    )


@router.post("/mapping/apply-template", response_model=MappingApplyOut)
def apply_mapping_template(body: MappingApplyRequest):
    """Применить сохранённый шаблон к таблице документа.

    Возвращает правленую таблицу (после ops-рецепта) и нормализованные
    аннотации — готовые к загрузке в Visual Mapper или серверному извлечению.
    Несовпавшая структура не ошибка: битые слоты/спеки пропускаются
    и отражаются в skipped_annotations.
    """
    meta = storage.load_document_meta(body.document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    table = _table_from_meta(meta, body.table_id)
    if table is None:
        raise HTTPException(404, "TABLE_NOT_FOUND")
    tpl = storage.get_mapping(body.template_id)
    if tpl is None:
        raise HTTPException(404, "MAPPING_NOT_FOUND")

    ops = tpl.get("ops") or []
    raw_anns = tpl.get("annotations") or []
    apply_ops(table, ops)
    anns = normalize_annotations(table, raw_anns)
    return MappingApplyOut(
        template_id=tpl["id"],
        table=_table_out(table),
        annotations=[MappingAnnotation(**a) for a in anns],
        skipped_annotations=len(raw_anns) - len(anns),
        applied_ops=len(ops),
    )


@router.post("/llm/suggest-mapping", response_model=LLMSuggestOut)
def suggest_mapping(body: LLMSuggestRequest):
    """Предложить маппинг таблицы через LLM (этап 4).

    Аннотации — в координатах исходной таблицы (row/col якоря или range-зона),
    с confidence по каждой. Роли — те же, что в Visual Mapper. Применение
    предложения — на стороне пользователя (4.5), автоприменения нет.
    Ответ кэшируется по фингерпринту запроса (4.7): повторное предложение той же
    таблицы не ходит в облако (cached: true); refresh — принудительный обход.
    """
    meta = storage.load_document_meta(body.document_id)
    if not meta:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    table = _table_from_meta(meta, body.table_id)
    if table is None:
        raise HTTPException(404, "TABLE_NOT_FOUND")
    if not settings.llm_enabled:
        raise HTTPException(503, "LLM_NOT_CONFIGURED")

    context = body.context.model_dump() if body.context else None
    t0 = time.perf_counter()
    try:
        result = LLMMapperService(settings, storage=storage).suggest_mapping(
            table, doc_name=meta.get("name", ""), context=context, refresh=body.refresh
        )
    except LLMError as exc:
        log.warning("event=llm_suggest doc=%s table=%s status=failed error=%s", body.document_id, body.table_id, exc)
        raise HTTPException(502, f"LLM_FAILED: {exc}") from exc
    log.info(
        "event=llm_suggest doc=%s table=%s cached=%s model=%s annotations=%d ms=%.0f",
        body.document_id, body.table_id, result.get("cached"), result.get("model"),
        len(result.get("annotations") or []), (time.perf_counter() - t0) * 1000,
    )
    return LLMSuggestOut(**result)


@router.get("/llm/metrics", response_model=LLMMetricsOut)
def llm_metrics():
    """Метрики LLM-маппинга (4.7): счётчики запросов/кэша/токенов из data/llm_stats.json
    + точность по журналу коррекций llm_feedback.jsonl (сколько предложений LLM
    пользователь подтвердил как верные)."""
    stats = storage.load_llm_stats()
    requests = stats.get("requests", 0)
    accuracy = compute_accuracy(storage.load_llm_feedback())
    out = LLMMetricsOut(
        requests=requests,
        cache_hits=stats.get("cache_hits", 0),
        failures=stats.get("failures", 0),
        total_time_ms=stats.get("total_time_ms", 0),
        prompt_tokens=stats.get("prompt_tokens", 0),
        completion_tokens=stats.get("completion_tokens", 0),
    )
    if requests:
        out.cache_hit_rate = round(out.cache_hits / requests, 4)
        out.avg_time_ms = round(out.total_time_ms / requests, 1)
    if accuracy:
        out.corrected_cells = accuracy["corrected_cells"]
        out.agreed = accuracy["agreed"]
        out.accuracy = accuracy["accuracy"]
    return out


@router.post("/llm/feedback", response_model=LLMFeedbackOut)
def log_llm_feedback(body: LLMFeedbackRequest):
    """Записать коррекции пользователя «LLM предложил → пользователь выбрал»
    (этап 4.6). Журнал — data/llm_feedback.jsonl, анализ для тюнинга промптов (4.8).
    Fire-and-forget на стороне UI; документ мог быть уже удалён — не проверяем.
    """
    if not body.corrections:
        raise HTTPException(422, "NO_CORRECTIONS")
    for c in body.corrections:
        if c.llm_role not in ROLES or (c.user_role is not None and c.user_role not in ROLES):
            raise HTTPException(422, f"INVALID_ROLE: {c.llm_role or c.user_role}")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entries = [
        {
            "timestamp": ts,
            "document_id": body.document_id,
            "table_id": body.table_id,
            "model": body.model,
            **c.model_dump(),
        }
        for c in body.corrections
    ]
    return LLMFeedbackOut(logged=storage.log_llm_feedback(entries))
