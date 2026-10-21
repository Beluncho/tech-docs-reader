"""Pydantic-модели API и внутренние структуры."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """Доказательство значения: документ/страница/цитата (как в модуле certificates)."""

    document: str
    page: int | None = None
    quote: str = ""
    table: str | None = None
    row: int | None = None
    fragment: str | None = None


class Parameter(BaseModel):
    """Извлечённая техническая характеристика."""

    name: str
    value: str
    unit: str | None = None
    evidence: Evidence
    source: Literal["preset", "llm"] = "preset"
    verified: bool = True


class CellOut(BaseModel):
    text: str
    page: int | None = None
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    is_merged: bool = False
    merged_with: list[int] | None = None  # [row, col] якорной ячейки


class TableOut(BaseModel):
    id: str
    sheet: str | None = None
    page: int | None = None
    header_row: int | None = None
    n_rows: int
    n_cols: int
    bbox: list[float | None] = Field(default_factory=list)
    cells: list[CellOut] = Field(default_factory=list)
    # метаданные структуры (этап 2): шапка, merged cells, матрица
    header_rows: int = 0
    has_merged_cells: bool = False
    is_matrix: bool = False
    orientation: str | None = None  # "rows" (модели в строках) / "columns" (в столбцах)


class DocumentOut(BaseModel):
    id: str
    filename: str
    ext: str
    fmt: str
    size_bytes: int
    pages: int
    ocr: bool
    status: Literal["PARSED", "PARSE_FAILED", "OCR_FAILED"]
    error: str | None = None
    tables: list[TableOut] = Field(default_factory=list)
    auto_preset_id: str | None = None
    last_result: dict | None = None


class DocumentListItem(BaseModel):
    id: str
    filename: str
    fmt: str
    pages: int
    status: str
    created_at: str


class PageRender(BaseModel):
    page: int
    width: int
    height: int
    pdf_width: float
    pdf_height: float


class ExtractRequest(BaseModel):
    preset_id: str | None = None
    table_id: str | None = None
    use_llm: bool = False
    include_all: bool = False


class ExtractResult(BaseModel):
    document_id: str
    preset_id: str | None = None
    preset_name: str | None = None
    source: Literal["preset", "llm", "mixed"] = "preset"
    matched_score: float | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_model: str | None = None


class PresetOut(BaseModel):
    id: str
    name: str
    description: str = ""
    doc_type: str = "passport"
    builtin: bool = False
    columns: dict = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    section_keywords: list[str] = Field(default_factory=list)
    header_keywords: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    include_all: bool = False
    keyvalue: bool = False


class PresetCreate(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    doc_type: str = "passport"
    columns: dict = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    section_keywords: list[str] = Field(default_factory=list)
    header_keywords: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    include_all: bool = False
    keyvalue: bool = False


class PresetFromExtraction(BaseModel):
    document_id: str
    name: str | None = None
    preset_id: str | None = None
    table_id: str | None = None
    description: str = ""


class MappingAnnotation(BaseModel):
    row: int
    col: int
    role: str
    label: str | None = None
    unit: str | None = None
    group: str | None = None


class MappingOp(BaseModel):
    op: str  # merge / split / transpose / del_rows / del_cols
    rect: list[int] | None = None        # merge: [r0, c0, r1, c1]
    slots: list[list[int]] | None = None  # split: [[r, c], ...]
    rows: list[int] | None = None         # del_rows
    cols: list[int] | None = None         # del_cols


class MappingCreate(BaseModel):
    name: str
    description: str = ""
    document_id: str
    table_id: str
    ops: list[MappingOp] = Field(default_factory=list)
    annotations: list[MappingAnnotation]


class MappingTemplateOut(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str = ""
    source: dict = Field(default_factory=dict)
    table_fingerprint: str | None = None
    table_signature: dict = Field(default_factory=dict)
    ops: list[MappingOp] = Field(default_factory=list)
    annotations: list[MappingAnnotation] = Field(default_factory=list)


class MappingMatchOut(BaseModel):
    template: MappingTemplateOut
    score: float
    method: str  # exact / partial


class MappingTableMatchOut(BaseModel):
    """Лёгкий матч одной таблицы документа (без ops/аннотаций шаблона)."""

    table_id: str
    template_id: str | None = None
    template_name: str | None = None
    score: float = 0.0
    method: str | None = None  # exact / partial


class LLMSuggestContext(BaseModel):
    """Контекст документа для подсказки LLM (этап 4)."""

    doc_type: str | None = None
    keywords: list[str] = Field(default_factory=list)


class LLMSuggestRequest(BaseModel):
    document_id: str
    table_id: str
    context: LLMSuggestContext | None = None
    refresh: bool = False  # обойти кэш, принудительно сходить в LLM (4.7)


class LLMAlternativeOut(BaseModel):
    role: str
    confidence: float = 0.0


class LLMSuggestedAnnotation(BaseModel):
    """Аннотация, предложенная LLM: row/col якоря или range-зона (этап 4.1 контракт)."""

    role: str
    row: int | None = None
    col: int | None = None
    range: list[int] | None = None
    label: str | None = None
    unit: str | None = None
    group: str | None = None
    confidence: float = 0.5
    reasoning: str | None = None
    alternatives: list[LLMAlternativeOut] = Field(default_factory=list)


class LLMSuggestSummary(BaseModel):
    total: int = 0
    high_confidence: int = 0
    low_confidence: int = 0


class LLMSuggestOut(BaseModel):
    """Ответ POST /api/llm/suggest-mapping."""

    orientation: str | None = None
    structure: str | None = None
    complexity: str | None = None
    annotations: list[LLMSuggestedAnnotation] = Field(default_factory=list)
    notes: str | None = None
    summary: LLMSuggestSummary = Field(default_factory=LLMSuggestSummary)
    model: str | None = None
    processing_time_ms: int = 0
    cached: bool = False  # ответ из кэша по фингерпринту запроса (4.7)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMFeedbackCorrection(BaseModel):
    """Коррекция пользователя «LLM предложил → пользователь выбрал» (этап 4.6)."""

    row: int
    col: int
    text: str | None = None
    llm_role: str
    llm_confidence: float = 0.5
    alternatives: list[LLMAlternativeOut] = Field(default_factory=list)
    user_role: str | None = None  # None = снял роль
    via: str | None = None  # alternative / role-button / confirm


class LLMFeedbackRequest(BaseModel):
    document_id: str
    table_id: str
    model: str | None = None
    corrections: list[LLMFeedbackCorrection]


class LLMFeedbackOut(BaseModel):
    """Ответ POST /api/llm/feedback."""

    logged: int


class LLMMetricsOut(BaseModel):
    """Ответ GET /api/llm/metrics: счётчики запросов + точность по журналу коррекций (4.7)."""

    requests: int = 0
    cache_hits: int = 0
    failures: int = 0
    total_time_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # производные
    cache_hit_rate: float = 0.0
    avg_time_ms: float = 0.0
    # точность по журналу llm_feedback.jsonl (None — коррекций ещё не было)
    corrected_cells: int | None = None
    agreed: int | None = None
    accuracy: float | None = None


class MappingDocumentMatchOut(BaseModel):
    """Мачи всех таблиц документа — для индикаторов основного UI (3.9)."""

    document_id: str
    matches: list[MappingTableMatchOut] = Field(default_factory=list)


# --- применение маппинга на backend (этап 3.8) --------------------------------


class PreviewEntry(BaseModel):
    """Одна запись предпросмотра: значение параметра модели."""

    name: str | None = None
    value: str
    unit: str | None = None
    group: str | None = None
    row: int
    col: int


class PreviewCard(BaseModel):
    """Карточка предпросмотра: модель (или «без модели») с параметрами."""

    model: str | None = None
    parameters: list[PreviewEntry] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class PreviewStats(BaseModel):
    models: int = 0
    params: int = 0
    values: int = 0


class MappingValidateRequest(BaseModel):
    """Маппинг для проверки: ops-рецепт + роли в финальных координатах."""

    document_id: str
    table_id: str
    ops: list[MappingOp] = Field(default_factory=list)
    annotations: list[MappingAnnotation]


class MappingValidateOut(BaseModel):
    document_id: str
    table_id: str
    cards: list[PreviewCard] = Field(default_factory=list)
    stats: PreviewStats = Field(default_factory=PreviewStats)
    warnings: list[str] = Field(default_factory=list)


class MappingApplyRequest(BaseModel):
    document_id: str
    table_id: str
    template_id: str


class MappingApplyOut(BaseModel):
    """Результат применения шаблона: правленая таблица + нормализованные роли."""

    template_id: str
    table: TableOut
    annotations: list[MappingAnnotation] = Field(default_factory=list)
    skipped_annotations: int = 0  # слоты не совпали со структурой / неизвестные роли
    applied_ops: int = 0
