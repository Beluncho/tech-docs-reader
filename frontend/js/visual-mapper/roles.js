/* Роли ячеек для визуального маппинга (этап 3.4 — назначение; здесь константы). */
"use strict";

export const CELL_ROLES = {
  MODEL: {
    id: "model",
    label: "🏷️ Модель",
    color: "#e3f2fd",
    description: "Название модели изделия",
  },
  PARAM: {
    id: "param",
    label: "📊 Параметр",
    color: "#fff3e0",
    description: "Название параметра",
  },
  VALUE: {
    id: "value",
    label: "🔢 Значение",
    color: "#e8f5e9",
    description: "Значение параметра",
  },
  UNIT: {
    id: "unit",
    label: "📏 Единица",
    color: "#fce4ec",
    description: "Единица измерения",
  },
  SECTION: {
    id: "section",
    label: "📂 Секция",
    color: "#f3e5f5",
    description: "Заголовок секции",
  },
  CONDITION: {
    id: "condition",
    label: "💬 Условие",
    color: "#fff9c4",
    description: "Примечание/условие",
  },
  IGNORE: {
    id: "ignore",
    label: "🚫 Игнорировать",
    color: "#f5f5f5",
    description: "Игнорировать ячейку",
  },
};

export const ROLES_BY_ID = Object.fromEntries(Object.values(CELL_ROLES).map((r) => [r.id, r]));
