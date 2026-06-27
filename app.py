# =============================================================================
# app.py
# Interfaz visual con Streamlit para la gestión de finanzas personales.
# Mobile-first: diseñado para usarse desde el móvil en Streamlit Cloud.
# =============================================================================

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, datetime

import data_manager as dm
import ocr_engine as ocr

# ---------------------------------------------------------------------------
# Configuración de la página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="💰 Mis Finanzas",
    page_icon="💰",
    layout="centered",       # Mejor para móvil que "wide"
    initial_sidebar_state="collapsed",
)

# CSS mínimo para mejorar la experiencia móvil
st.markdown("""
<style>
    /* Botones más grandes y fáciles de pulsar en pantalla táctil */
    .stButton > button {
        width: 100%;
        padding: 0.6rem;
        font-size: 1rem;
    }
    /* Reducir padding lateral en móvil */
    .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
    }
    /* Barra de progreso roja personalizada */
    .barra-roja > div > div {
        background-color: #e74c3c !important;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Leer API key desde st.secrets (configurada en Streamlit Cloud)
# ---------------------------------------------------------------------------

def _get_api_key() -> str | None:
    """Devuelve la API key de Mistral o None si no está configurada."""
    try:
        return st.secrets["MISTRAL_API_KEY"]
    except (KeyError, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------

def _mostrar_barra_presupuesto(categoria: str, gastado: float, limite: float) -> None:
    """
    Renderiza una barra de progreso para una categoría.
    Se vuelve roja si el gasto supera el límite.
    """
    if limite <= 0:
        return

    porcentaje = min(gastado / limite, 1.0)
    excedido = gastado > limite

    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption(f"{categoria}")
        if excedido:
            # Barra roja con HTML cuando se supera el límite
            st.markdown(
                f"""
                <div style="background:#e74c3c;border-radius:4px;height:16px;width:100%;">
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption(f"🔴 {gastado:.2f} € / {limite:.2f} € — **¡Límite superado!**")
        else:
            st.progress(porcentaje)
            st.caption(f"🟢 {gastado:.2f} € / {limite:.2f} €")
    with col2:
        st.metric("", f"{porcentaje*100:.0f}%")


# ---------------------------------------------------------------------------
# Secciones de la app
# ---------------------------------------------------------------------------

def seccion_añadir_transaccion() -> None:
    """Formulario manual + OCR por imagen para añadir transacciones."""

    st.header("➕ Añadir transacción")

    api_key = _get_api_key()

    # --- Subida de imagen (cámara o galería) ---
    st.subheader("📷 Escanear ticket")
    if not api_key:
        st.warning(
            "⚠️ API key de Mistral no configurada. "
            "Ve a Settings → Secrets y añade `MISTRAL_API_KEY`."
        )
    else:
        # file_uploader con accept_multiple_files=False y tipos de imagen
        # En móvil Android, esto abre el selector con opción de cámara
        imagen = st.file_uploader(
            "Sube una foto del ticket o usa la cámara",
            type=["jpg", "jpeg", "png", "webp", "heic"],
            accept_multiple_files=False,
            help="En el móvil podrás elegir entre galería o cámara directamente.",
        )

        if imagen:
            st.image(imagen, caption="Ticket subido", use_column_width=True)

            if st.button("🔍 Extraer datos del ticket", use_container_width=True):
                with st.spinner("Analizando ticket con Mistral OCR..."):
                    ok, resultado = ocr.extraer_datos_ticket(
                        imagen_bytes=imagen.read(),
                        api_key=api_key,
                    )

                if not ok:
                    st.error(f"Error en el OCR: {resultado}")
                else:
                    # Guardar en session_state para pre-rellenar el formulario
                    st.session_state["ocr_fecha"] = resultado.get("fecha") or date.today()
                    st.session_state["ocr_comercio"] = resultado.get("comercio") or ""
                    st.session_state["ocr_importe"] = resultado.get("importe") or 0.0
                    st.session_state["ocr_categoria"] = resultado.get("categoria_sugerida") or dm.CATEGORIAS[0]
                    st.success("✅ Datos extraídos. Revisa y confirma el formulario.")

    st.divider()

    # --- Formulario manual (pre-rellenado si viene del OCR) ---
    st.subheader("✏️ Formulario")

    with st.form("form_transaccion", clear_on_submit=True):
        fecha = st.date_input(
            "Fecha",
            value=st.session_state.get("ocr_fecha", date.today()),
            max_value=date.today(),
        )
        comercio = st.text_input(
            "Comercio / Concepto",
            value=st.session_state.get("ocr_comercio", ""),
            placeholder="Ej: Mercadona, Nómina, Renfe...",
        )
        importe = st.number_input(
            "Importe (€)",
            min_value=0.01,
            value=float(st.session_state.get("ocr_importe", 0.01)),
            step=0.01,
            format="%.2f",
        )
        tipo = st.selectbox("Tipo", dm.TIPOS)

        # Índice por defecto basado en sugerencia OCR
        cats_dinamicas = dm.obtener_categorias()
        cat_sugerida = st.session_state.get("ocr_categoria", cats_dinamicas[0])
        idx_cat = cats_dinamicas.index(cat_sugerida) if cat_sugerida in cats_dinamicas else 0
        categoria = st.selectbox("Categoría", cats_dinamicas, index=idx_cat)

        enviado = st.form_submit_button("💾 Guardar registro", use_container_width=True)

    if enviado:
        if not comercio.strip():
            st.error("El campo Comercio / Concepto no puede estar vacío.")
        else:
            ok, mensaje = dm.añadir_transaccion(fecha, comercio, importe, tipo, categoria)
            if ok:
                st.success(mensaje)
                # Limpiar datos OCR del session_state tras guardar
                for k in ["ocr_fecha", "ocr_comercio", "ocr_importe", "ocr_categoria"]:
                    st.session_state.pop(k, None)
            else:
                st.warning(mensaje)


def seccion_presupuestos() -> None:
    """Permite definir límites mensuales y muestra barras de progreso."""

    st.header("🎯 Presupuestos mensuales")

    df_pres = dm.cargar_presupuestos()
    df_tx = dm.cargar_transacciones()

    # Mes y año actual para calcular el gasto real
    hoy = date.today()
    gastos_mes = dm.calcular_gasto_por_categoria(df_tx, hoy.month, hoy.year)

    st.subheader(f"📊 Progreso — {hoy.strftime('%B %Y')}")

    # Mostrar barras de progreso por categoría (solo gastos, no ingresos)
    cats_gasto = [c for c in categorias_actuales if c != "Ingresos"]
    hay_presupuesto = False
    for _, fila in df_pres[df_pres["Categoría"].isin(cats_gasto)].iterrows():
        cat = fila["Categoría"]
        limite = float(fila["Límite"])
        gastado = float(gastos_mes.get(cat, 0.0))
        if limite > 0:
            hay_presupuesto = True
            _mostrar_barra_presupuesto(cat, gastado, limite)
            st.write("")

    if not hay_presupuesto:
        st.info("Aún no has definido ningún presupuesto. Configúralos abajo.")

    st.divider()
    st.subheader("⚙️ Configurar límites")

    # Formulario para actualizar límites
    with st.form("form_presupuestos"):
        nuevos = {}
        for _, fila in df_pres[df_pres["Categoría"].isin(cats_gasto)].iterrows():
            cat = fila["Categoría"]
            nuevos[cat] = st.number_input(
                f"{cat} (€/mes)",
                min_value=0.0,
                value=float(fila["Límite"]),
                step=10.0,
                format="%.2f",
                key=f"pres_{cat}",
            )
        guardar = st.form_submit_button("💾 Guardar presupuestos", use_container_width=True)

    if guardar:
        dm.actualizar_presupuestos(nuevos)
        st.success("✅ Presupuestos actualizados.")
        st.rerun()


def seccion_dashboard() -> None:
    """Panel de control con gráficos interactivos."""

    st.header("📈 Dashboard")

    df_tx = dm.cargar_transacciones()

    if df_tx.empty:
        st.info("Aún no hay transacciones registradas. ¡Añade tu primera!")
        return

    # Selector de mes para el gráfico de tarta
    df_tx["Fecha"] = pd.to_datetime(df_tx["Fecha"])
    meses_disp = sorted(df_tx["Fecha"].dt.to_period("M").astype(str).unique(), reverse=True)
    mes_sel = st.selectbox("Mes a analizar", meses_disp)
    año_sel = int(mes_sel.split("-")[0])
    mes_num = int(mes_sel.split("-")[1])

    gastos_cat = dm.calcular_gasto_por_categoria(df_tx, mes_num, año_sel)

    # --- Gráfico de tarta: distribución de gastos ---
    st.subheader("🥧 Distribución de gastos")
    if gastos_cat.empty:
        st.info("Sin gastos registrados en este mes.")
    else:
        fig_pie = px.pie(
            values=gastos_cat.values,
            names=gastos_cat.index,
            title=f"Gastos por categoría — {mes_sel}",
            hole=0.35,   # Donut chart, más moderno
            color_discrete_sequence=px.colors.qualitative.Set3,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(
            showlegend=False,
            margin=dict(t=40, b=0, l=0, r=0),
            height=350,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    st.divider()

    # --- Gráfico de barras: Ingresos vs Gastos por mes ---
    st.subheader("📊 Ingresos vs. Gastos mensuales")
    resumen = dm.calcular_resumen_mensual(df_tx)

    if resumen.empty:
        st.info("Sin datos suficientes para el gráfico mensual.")
    else:
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=resumen["Mes"],
            y=resumen["Ingresos"],
            name="Ingresos",
            marker_color="#2ecc71",
        ))
        fig_bar.add_trace(go.Bar(
            x=resumen["Mes"],
            y=resumen["Gastos"],
            name="Gastos",
            marker_color="#e74c3c",
        ))
        fig_bar.update_layout(
            barmode="group",
            xaxis_title="Mes",
            yaxis_title="Euros (€)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=20, b=0, l=0, r=0),
            height=350,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    # --- Tabla de transacciones recientes ---
    st.subheader("🗒️ Últimas transacciones")
    df_reciente = (
        df_tx.sort_values("Fecha", ascending=False)
        .head(20)
        .reset_index(drop=True)
    )
    df_reciente["Fecha"] = df_reciente["Fecha"].dt.strftime("%d/%m/%Y")
    df_reciente["Importe"] = df_reciente["Importe"].map(lambda x: f"{x:.2f} €")
    st.dataframe(df_reciente, use_container_width=True, hide_index=True)


def seccion_historial() -> None:
    """Vista completa de transacciones con filtros."""

    st.header("🗂️ Historial completo")

    df_tx = dm.cargar_transacciones()

    if df_tx.empty:
        st.info("No hay transacciones registradas todavía.")
        return

    df_tx["Fecha"] = pd.to_datetime(df_tx["Fecha"])

    # Filtros
    col1, col2 = st.columns(2)
    with col1:
        cats_sel = st.multiselect(
            "Categoría", dm.CATEGORIAS, default=dm.CATEGORIAS
        )
    with col2:
        tipos_sel = st.multiselect("Tipo", dm.TIPOS, default=dm.TIPOS)

    df_filtrado = df_tx[
        df_tx["Categoría"].isin(cats_sel) & df_tx["Tipo"].isin(tipos_sel)
    ].sort_values("Fecha", ascending=False)

    # Totales rápidos
    total_ingresos = df_filtrado[df_filtrado["Tipo"] == "Ingreso"]["Importe"].sum()
    total_gastos = df_filtrado[df_filtrado["Tipo"] == "Gasto"]["Importe"].sum()
    balance = total_ingresos - total_gastos

    m1, m2, m3 = st.columns(3)
    m1.metric("💚 Ingresos", f"{total_ingresos:.2f} €")
    m2.metric("🔴 Gastos", f"{total_gastos:.2f} €")
    m3.metric("⚖️ Balance", f"{balance:.2f} €", delta=f"{balance:.2f} €")

    # Tabla
    df_mostrar = df_filtrado.copy()
    df_mostrar["Fecha"] = df_mostrar["Fecha"].dt.strftime("%d/%m/%Y")
    df_mostrar["Importe"] = df_mostrar["Importe"].map(lambda x: f"{x:.2f} €")
    st.dataframe(df_mostrar.reset_index(drop=True), use_container_width=True, hide_index=True)

    # Descarga del Excel
    with open(dm.ARCHIVO_EXCEL, "rb") as f:
        st.download_button(
            label="⬇️ Descargar finanzas.xlsx",
            data=f,
            file_name="finanzas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Navegación principal
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("💰 Mis Finanzas")

    # Menú de navegación adaptado a móvil (botones en fila)
    PAGINAS = {
        "➕ Añadir": seccion_añadir_transaccion,
        "🎯 Presupuestos": seccion_presupuestos,
        "📈 Dashboard": seccion_dashboard,
        "🗂️ Historial": seccion_historial,
    }

    if "pagina" not in st.session_state:
        st.session_state["pagina"] = "➕ Añadir"

    # Botones de navegación en columnas (táctil-friendly)
    cols = st.columns(len(PAGINAS))
    for col, nombre in zip(cols, PAGINAS):
        with col:
            if st.button(nombre, use_container_width=True):
                st.session_state["pagina"] = nombre

    st.divider()

    # Renderizar la sección activa
    PAGINAS[st.session_state["pagina"]]()


if __name__ == "__main__":
    main()
