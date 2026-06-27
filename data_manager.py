# =============================================================================
# data_manager.py
# Persistencia de datos en Google Sheets usando gspread.
# Cada usuario tiene dos hojas propias: "Transacciones_<user>" y "Presupuestos_<user>"
# =============================================================================

import gspread
import pandas as pd
import streamlit as st
from datetime import date
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CATEGORIAS_DEFAULT = [
    "Alimentación", "Transporte", "Ocio", "Hogar",
    "Facturas", "Salud", "Ingresos",
]
CATEGORIAS = CATEGORIAS_DEFAULT
TIPOS = ["Gasto", "Ingreso"]
COLUMNAS_TX = ["Fecha", "Comercio", "Importe", "Tipo", "Categoría"]
COLUMNAS_PRESUPUESTO = ["Categoría", "Límite"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ---------------------------------------------------------------------------
# Conexión a Google Sheets (cacheada para no reconectar en cada rerun)
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)


def _get_spreadsheet() -> gspread.Spreadsheet:
    client = _get_client()
    return client.open_by_key(st.secrets["SPREADSHEET_ID"])


def _get_or_create_hoja(nombre: str, cabeceras: list[str]) -> gspread.Worksheet:
    """Devuelve la hoja con ese nombre, creándola si no existe."""
    ss = _get_spreadsheet()
    try:
        return ss.worksheet(nombre)
    except gspread.WorksheetNotFound:
        hoja = ss.add_worksheet(title=nombre, rows=1000, cols=len(cabeceras))
        hoja.append_row(cabeceras)
        return hoja


def _nombre_tx(usuario: str) -> str:
    return f"Transacciones_{usuario}"


def _nombre_pres(usuario: str) -> str:
    return f"Presupuestos_{usuario}"


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def cargar_transacciones(usuario: str) -> pd.DataFrame:
    hoja = _get_or_create_hoja(_nombre_tx(usuario), COLUMNAS_TX)
    datos = hoja.get_all_records()
    if not datos:
        return pd.DataFrame(columns=COLUMNAS_TX)
    df = pd.DataFrame(datos)
    for col in COLUMNAS_TX:
        if col not in df.columns:
            df[col] = None
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.date
    df["Importe"] = pd.to_numeric(df["Importe"], errors="coerce")
    return df[COLUMNAS_TX]


def cargar_presupuestos(usuario: str) -> pd.DataFrame:
    hoja = _get_or_create_hoja(_nombre_pres(usuario), COLUMNAS_PRESUPUESTO)
    datos = hoja.get_all_records()
    if not datos:
        # Inicializar con categorías por defecto
        df = pd.DataFrame({
            "Categoría": CATEGORIAS_DEFAULT,
            "Límite": [0.0] * len(CATEGORIAS_DEFAULT),
        })
        hoja.append_rows(df.values.tolist())
        return df
    df = pd.DataFrame(datos)
    # Añadir categorías nuevas que falten
    cats_existentes = set(df["Categoría"].tolist())
    nuevas = [{"Categoría": c, "Límite": 0.0} for c in CATEGORIAS_DEFAULT if c not in cats_existentes]
    if nuevas:
        hoja.append_rows([[r["Categoría"], r["Límite"]] for r in nuevas])
        df = pd.concat([df, pd.DataFrame(nuevas)], ignore_index=True)
    df["Límite"] = pd.to_numeric(df["Límite"], errors="coerce").fillna(0.0)
    return df[COLUMNAS_PRESUPUESTO]


def obtener_categorias(usuario: str) -> list[str]:
    try:
        df = cargar_presupuestos(usuario)
        return df["Categoría"].tolist()
    except Exception:
        return CATEGORIAS_DEFAULT.copy()


# ---------------------------------------------------------------------------
# Anti-duplicados
# ---------------------------------------------------------------------------

def es_duplicado(df: pd.DataFrame, fecha: date, comercio: str, importe: float) -> bool:
    if df.empty:
        return False
    mask = (
        (df["Fecha"] == fecha) &
        (df["Comercio"].str.strip().str.lower() == comercio.strip().lower()) &
        (pd.to_numeric(df["Importe"], errors="coerce").round(2) == round(importe, 2))
    )
    return mask.any()


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def añadir_transaccion(
    usuario: str, fecha: date, comercio: str, importe: float, tipo: str, categoria: str
) -> tuple[bool, str]:
    df_tx = cargar_transacciones(usuario)

    if es_duplicado(df_tx, fecha, comercio, importe):
        return False, (
            f"⚠️ Duplicado detectado: ya existe un registro del {fecha} "
            f"en '{comercio}' por {importe:.2f} €. Operación cancelada."
        )

    hoja = _get_or_create_hoja(_nombre_tx(usuario), COLUMNAS_TX)
    hoja.append_row([str(fecha), comercio.strip(), round(importe, 2), tipo, categoria])
    return True, f"✅ Registro añadido: {comercio} — {importe:.2f} € ({categoria})"


def actualizar_presupuestos(usuario: str, nuevos_limites: dict[str, float]) -> None:
    hoja = _get_or_create_hoja(_nombre_pres(usuario), COLUMNAS_PRESUPUESTO)
    datos = hoja.get_all_records()
    for i, fila in enumerate(datos, start=2):  # fila 1 = cabecera
        cat = fila["Categoría"]
        if cat in nuevos_limites:
            hoja.update_cell(i, 2, round(nuevos_limites[cat], 2))


def añadir_categoria(usuario: str, nombre: str) -> tuple[bool, str]:
    nombre = nombre.strip().title()
    if not nombre:
        return False, "El nombre no puede estar vacío."
    df_pres = cargar_presupuestos(usuario)
    if nombre in df_pres["Categoría"].tolist():
        return False, f"La categoría '{nombre}' ya existe."
    hoja = _get_or_create_hoja(_nombre_pres(usuario), COLUMNAS_PRESUPUESTO)
    hoja.append_row([nombre, 0.0])
    return True, f"✅ Categoría '{nombre}' añadida correctamente."


# ---------------------------------------------------------------------------
# Cálculos para el Dashboard
# ---------------------------------------------------------------------------

def calcular_gasto_por_categoria(df: pd.DataFrame, mes: int, año: int) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    mask = (
        (df["Tipo"] == "Gasto") &
        (df["Fecha"].dt.month == mes) &
        (df["Fecha"].dt.year == año)
    )
    return df[mask].groupby("Categoría")["Importe"].sum()


def calcular_resumen_mensual(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Mes", "Ingresos", "Gastos"])
    df = df.copy()
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Mes"] = df["Fecha"].dt.to_period("M").astype(str)
    resumen = df.groupby(["Mes", "Tipo"])["Importe"].sum().unstack(fill_value=0)
    for col in ["Gasto", "Ingreso"]:
        if col not in resumen.columns:
            resumen[col] = 0.0
    resumen = resumen.rename(columns={"Gasto": "Gastos", "Ingreso": "Ingresos"})
    return resumen.reset_index().sort_values("Mes")[["Mes", "Ingresos", "Gastos"]]
