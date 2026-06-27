# =============================================================================
# data_manager.py
# Responsabilidad única: toda la lógica de persistencia de datos.
# Lee y escribe en 'finanzas.xlsx' usando pandas + openpyxl.
# =============================================================================

import openpyxl
import pandas as pd
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Constantes del dominio
# ---------------------------------------------------------------------------

ARCHIVO_EXCEL = "finanzas.xlsx"
HOJA_TRANSACCIONES = "Transacciones"
HOJA_PRESUPUESTOS = "Presupuestos"

CATEGORIAS_DEFAULT = [
    "Alimentación",
    "Transporte",
    "Ocio",
    "Hogar",
    "Facturas",
    "Salud",
    "Ingresos",
]

# Alias para compatibilidad con ocr_engine.py
CATEGORIAS = CATEGORIAS_DEFAULT

TIPOS = ["Gasto", "Ingreso"]

COLUMNAS_TX = ["Fecha", "Comercio", "Importe", "Tipo", "Categoría"]
COLUMNAS_PRESUPUESTO = ["Categoría", "Límite"]


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------

def _crear_excel_vacio() -> None:
    with pd.ExcelWriter(ARCHIVO_EXCEL, engine="openpyxl") as writer:
        pd.DataFrame(columns=COLUMNAS_TX).to_excel(
            writer, sheet_name=HOJA_TRANSACCIONES, index=False
        )
        pd.DataFrame({
            "Categoría": CATEGORIAS_DEFAULT,
            "Límite": [0.0] * len(CATEGORIAS_DEFAULT),
        }).to_excel(writer, sheet_name=HOJA_PRESUPUESTOS, index=False)


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def cargar_transacciones() -> pd.DataFrame:
    if not Path(ARCHIVO_EXCEL).exists():
        _crear_excel_vacio()
        return pd.DataFrame(columns=COLUMNAS_TX)

    df = pd.read_excel(ARCHIVO_EXCEL, sheet_name=HOJA_TRANSACCIONES, engine="openpyxl")

    for col in COLUMNAS_TX:
        if col not in df.columns:
            df[col] = None

    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.date

    return df[COLUMNAS_TX]


def cargar_presupuestos() -> pd.DataFrame:
    if not Path(ARCHIVO_EXCEL).exists():
        _crear_excel_vacio()

    # Manejar archivos creados con el nombre de hoja incorrecto (bug previo)
    wb = openpyxl.load_workbook(ARCHIVO_EXCEL)
    hojas = wb.sheetnames
    wb.close()

    if HOJA_PRESUPUESTOS not in hojas:
        if "Presupuesto" in hojas:
            wb = openpyxl.load_workbook(ARCHIVO_EXCEL)
            wb["Presupuesto"].title = HOJA_PRESUPUESTOS
            wb.save(ARCHIVO_EXCEL)
            wb.close()
        else:
            _crear_excel_vacio()

    df = pd.read_excel(ARCHIVO_EXCEL, sheet_name=HOJA_PRESUPUESTOS, engine="openpyxl")

    # Añadir categorías que falten con límite 0
    cats_existentes = set(df["Categoría"].tolist())
    filas_nuevas = [
        {"Categoría": cat, "Límite": 0.0}
        for cat in CATEGORIAS_DEFAULT
        if cat not in cats_existentes
    ]
    if filas_nuevas:
        df = pd.concat([df, pd.DataFrame(filas_nuevas)], ignore_index=True)

    return df[COLUMNAS_PRESUPUESTO]


def obtener_categorias() -> list[str]:
    """Devuelve la lista actual de categorías desde el Excel."""
    if not Path(ARCHIVO_EXCEL).exists():
        return CATEGORIAS_DEFAULT.copy()
    try:
        df = pd.read_excel(ARCHIVO_EXCEL, sheet_name=HOJA_PRESUPUESTOS, engine="openpyxl")
        return df["Categoría"].tolist()
    except Exception:
        return CATEGORIAS_DEFAULT.copy()


# ---------------------------------------------------------------------------
# Anti-duplicados
# ---------------------------------------------------------------------------

def es_duplicado(df: pd.DataFrame, fecha: date, comercio: str, importe: float) -> bool:
    if df.empty:
        return False
    comercio_norm = comercio.strip().lower()
    mask = (
        (df["Fecha"] == fecha) &
        (df["Comercio"].str.strip().str.lower() == comercio_norm) &
        (df["Importe"].round(2) == round(importe, 2))
    )
    return mask.any()


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def _guardar_todo(df_tx: pd.DataFrame, df_pres: pd.DataFrame) -> None:
    with pd.ExcelWriter(ARCHIVO_EXCEL, engine="openpyxl") as writer:
        df_tx.to_excel(writer, sheet_name=HOJA_TRANSACCIONES, index=False)
        df_pres.to_excel(writer, sheet_name=HOJA_PRESUPUESTOS, index=False)


def añadir_transaccion(
    fecha: date, comercio: str, importe: float, tipo: str, categoria: str
) -> tuple[bool, str]:
    df_tx = cargar_transacciones()
    df_pres = cargar_presupuestos()

    if es_duplicado(df_tx, fecha, comercio, importe):
        return False, (
            f"⚠️ Duplicado detectado: ya existe un registro del {fecha} "
            f"en '{comercio}' por {importe:.2f} €. Operación cancelada."
        )

    nueva_fila = pd.DataFrame([{
        "Fecha": fecha,
        "Comercio": comercio.strip(),
        "Importe": round(importe, 2),
        "Tipo": tipo,
        "Categoría": categoria,
    }])
    df_tx = pd.concat([df_tx, nueva_fila], ignore_index=True)
    _guardar_todo(df_tx, df_pres)
    return True, f"✅ Registro añadido: {comercio} — {importe:.2f} € ({categoria})"


def actualizar_presupuestos(nuevos_limites: dict[str, float]) -> None:
    df_tx = cargar_transacciones()
    df_pres = cargar_presupuestos()
    for cat, limite in nuevos_limites.items():
        df_pres.loc[df_pres["Categoría"] == cat, "Límite"] = round(limite, 2)
    _guardar_todo(df_tx, df_pres)


def añadir_categoria(nombre: str) -> tuple[bool, str]:
    """Añade una nueva categoría personalizada al Excel."""
    nombre = nombre.strip().title()
    if not nombre:
        return False, "El nombre no puede estar vacío."

    df_tx = cargar_transacciones()
    df_pres = cargar_presupuestos()

    if nombre in df_pres["Categoría"].tolist():
        return False, f"La categoría '{nombre}' ya existe."

    nueva = pd.DataFrame([{"Categoría": nombre, "Límite": 0.0}])
    df_pres = pd.concat([df_pres, nueva], ignore_index=True)
    _guardar_todo(df_tx, df_pres)
    return True, f"✅ Categoría '{nombre}' añadida correctamente."


# ---------------------------------------------------------------------------
# Cálculos para el Dashboard
# ---------------------------------------------------------------------------

def calcular_gasto_por_categoria(df: pd.DataFrame, mes: int, año: int) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    mask = (
        (df["Tipo"] == "Gasto") &
        (pd.to_datetime(df["Fecha"]).dt.month == mes) &
        (pd.to_datetime(df["Fecha"]).dt.year == año)
    )
    return df[mask].groupby("Categoría")["Importe"].sum()


def calcular_resumen_mensual(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Mes", "Ingresos", "Gastos"])

    df_copia = df.copy()
    df_copia["Fecha"] = pd.to_datetime(df_copia["Fecha"])
    df_copia["Mes"] = df_copia["Fecha"].dt.to_period("M").astype(str)

    resumen = df_copia.groupby(["Mes", "Tipo"])["Importe"].sum().unstack(fill_value=0)

    for col in ["Gasto", "Ingreso"]:
        if col not in resumen.columns:
            resumen[col] = 0.0

    resumen = resumen.rename(columns={"Gasto": "Gastos", "Ingreso": "Ingresos"})
    return resumen.reset_index().sort_values("Mes")[["Mes", "Ingresos", "Gastos"]]
