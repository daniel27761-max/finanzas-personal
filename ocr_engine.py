# =============================================================================
# ocr_engine.py
# Responsabilidad única: extraer datos estructurados de imágenes de tickets
# usando Mistral OCR (mistral-ocr-latest) — 100% gratuito hasta 1.000
# páginas/mes en el tier free de Mistral AI.
#
# ¿Por qué Mistral OCR y no Tesseract/EasyOCR?
#   - Tesseract y EasyOCR requieren binarios del sistema que Streamlit
#     Community Cloud no permite instalar.
#   - Mistral OCR es una API REST pura: no necesita nada instalado en el
#     servidor, funciona perfectamente en Streamlit Cloud.
#   - A diferencia de Tesseract (solo texto plano), Mistral OCR entiende
#     la estructura del ticket y devuelve markdown que luego analizamos
#     con el modelo de chat para extraer los campos de forma fiable.
#   - Coste: 0 € para uso personal (<1.000 páginas/mes).
#
# Flujo de dos pasos:
#   1. mistral-ocr-latest  → convierte la imagen en texto/markdown rico
#   2. mistral-small-latest → analiza ese texto y devuelve JSON estructurado
#
# Ambos modelos están disponibles en el tier gratuito de Mistral AI.
# =============================================================================

import base64
import json
import re
from datetime import date, datetime
from io import BytesIO

from mistralai import Mistral
from PIL import Image

# ---------------------------------------------------------------------------
# Prompt para la extracción estructurada (paso 2)
# ---------------------------------------------------------------------------

_PROMPT_EXTRACCION = """
Eres un asistente especializado en leer tickets de compra.
A continuación te proporciono el texto extraído de un ticket mediante OCR.
Analízalo y devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional,
sin bloques de código markdown, sin explicaciones. Usa exactamente estas claves:

{
  "fecha": "YYYY-MM-DD",
  "comercio": "string",
  "importe": number,
  "categoria_sugerida": "string"
}

Reglas:
- fecha: Fecha del ticket en formato ISO 8601. null si no está visible.
- comercio: Nombre del establecimiento. null si no visible.
- importe: Total final del ticket en euros como número (con IVA). null si no visible.
- categoria_sugerida: Elige UNA de: Alimentación, Transporte, Ocio, Hogar, Facturas, Salud, Ingresos.
- Si no puedes leer un campo con seguridad, pon null. Nunca inventes valores.
"""

# ---------------------------------------------------------------------------
# Utilidad: convertir imagen a base64
# ---------------------------------------------------------------------------

def _imagen_a_base64(imagen_bytes: bytes) -> tuple[str, str]:
    """
    Convierte bytes de imagen a base64 y detecta el media type.

    Redimensiona a 1500px máximo para equilibrar calidad OCR y velocidad.
    Fuerza JPEG como formato de salida para reducir tamaño del payload.

    Args:
        imagen_bytes: Bytes crudos del archivo subido por el usuario.

    Returns:
        Tupla (base64_string, media_type).
    """
    img = Image.open(BytesIO(imagen_bytes))

    # Normalizar modo de color (PNG con alfa, paletas, etc.)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Redimensionar si es demasiado grande (mejora velocidad sin perder calidad OCR)
    max_px = 1500
    if max(img.width, img.height) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return b64, "image/jpeg"


# ---------------------------------------------------------------------------
# Paso 1: OCR — imagen → texto markdown
# ---------------------------------------------------------------------------

def _ocr_imagen(client: Mistral, b64: str, media_type: str) -> tuple[bool, str]:
    """
    Llama a mistral-ocr-latest para convertir la imagen en texto markdown.

    El modelo OCR de Mistral es específico para extracción de texto de
    documentos e imágenes; es más preciso que usar el modelo de chat
    directamente con la imagen.

    Args:
        client:     Instancia del cliente Mistral ya autenticada.
        b64:        Imagen codificada en base64.
        media_type: MIME type de la imagen ("image/jpeg", etc.).

    Returns:
        Tupla (éxito: bool, texto_extraído | mensaje_error).
    """
    try:
        respuesta = client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "image_url",
                "image_url": f"data:{media_type};base64,{b64}",
            },
        )
        # La respuesta contiene una lista de páginas; tomamos la primera
        texto = respuesta.pages[0].markdown if respuesta.pages else ""
        if not texto.strip():
            return False, "El OCR no pudo extraer texto de la imagen. Comprueba la calidad de la foto."
        return True, texto
    except Exception as e:
        return False, f"Error en el paso OCR: {e}"


# ---------------------------------------------------------------------------
# Paso 2: Análisis — texto → JSON estructurado
# ---------------------------------------------------------------------------

def _parsear_texto_ticket(client: Mistral, texto_ocr: str) -> tuple[bool, dict | str]:
    """
    Envía el texto OCR a mistral-small-latest para extraer campos estructurados.

    Usar un modelo de chat en el paso 2 (en lugar de reglas regex) permite
    manejar tickets con formatos muy variados sin mantenimiento de patrones.

    Args:
        client:    Instancia del cliente Mistral.
        texto_ocr: Texto markdown devuelto por el paso OCR.

    Returns:
        Tupla (éxito: bool, dict_campos | mensaje_error).
    """
    try:
        respuesta = client.chat.complete(
            model="mistral-small-latest",
            temperature=0,      # Máximo determinismo para extracción de datos
            max_tokens=300,
            messages=[
                {"role": "system", "content": _PROMPT_EXTRACCION},
                {"role": "user", "content": f"Texto del ticket:\n\n{texto_ocr}"},
            ],
        )
    except Exception as e:
        return False, f"Error en el análisis del texto: {e}"

    texto_respuesta = respuesta.choices[0].message.content.strip()

    # Limpiar posibles bloques ```json ... ``` que el modelo podría añadir
    texto_limpio = re.sub(r"```(?:json)?|```", "", texto_respuesta).strip()

    try:
        datos = json.loads(texto_limpio)
    except json.JSONDecodeError:
        return False, (
            f"El modelo no devolvió JSON válido.\nRespuesta recibida:\n{texto_respuesta}"
        )

    return True, datos


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def extraer_datos_ticket(
    imagen_bytes: bytes,
    api_key: str,
) -> tuple[bool, dict | str]:
    """
    Orquesta el pipeline completo imagen → datos estructurados.

    Pasos internos:
        1. Convierte la imagen a base64.
        2. Llama a mistral-ocr-latest para obtener el texto del ticket.
        3. Llama a mistral-small-latest para estructurar el texto en JSON.
        4. Valida y normaliza los tipos de datos del resultado.

    Args:
        imagen_bytes: Contenido binario del archivo subido (JPEG, PNG, WEBP…).
        api_key:      Clave de API de Mistral (leída desde st.secrets en app.py).

    Returns:
        Tupla (éxito: bool, resultado).
        - éxito=True:  resultado es dict con claves:
                       fecha (date|None), comercio (str|None),
                       importe (float|None), categoria_sugerida (str|None)
        - éxito=False: resultado es str con el mensaje de error legible.
    """
    # --- Preparar imagen ---
    try:
        b64, media_type = _imagen_a_base64(imagen_bytes)
    except Exception as e:
        return False, f"No se pudo procesar la imagen: {e}"

    # --- Inicializar cliente Mistral ---
    client = Mistral(api_key=api_key)

    # --- Paso 1: OCR ---
    ok, texto_o_error = _ocr_imagen(client, b64, media_type)
    if not ok:
        return False, texto_o_error
    texto_ocr = texto_o_error

    # --- Paso 2: Extracción estructurada ---
    ok, datos_o_error = _parsear_texto_ticket(client, texto_ocr)
    if not ok:
        return False, datos_o_error
    datos = datos_o_error

    # --- Paso 3: Validar y normalizar tipos ---
    resultado = {}

    # Fecha → objeto date
    fecha_raw = datos.get("fecha")
    if fecha_raw:
        try:
            resultado["fecha"] = datetime.strptime(str(fecha_raw), "%Y-%m-%d").date()
        except ValueError:
            try:
                from dateutil import parser as dp
                resultado["fecha"] = dp.parse(str(fecha_raw)).date()
            except Exception:
                resultado["fecha"] = None
    else:
        resultado["fecha"] = None

    # Comercio → string limpio
    comercio_raw = datos.get("comercio")
    resultado["comercio"] = str(comercio_raw).strip() if comercio_raw else None

    # Importe → float positivo
    importe_raw = datos.get("importe")
    try:
        resultado["importe"] = abs(float(importe_raw))
    except (TypeError, ValueError):
        resultado["importe"] = None

    # Categoría → validar contra el catálogo oficial
    from data_manager import CATEGORIAS  # Importación local para evitar import circular
    cat_raw = datos.get("categoria_sugerida")
    resultado["categoria_sugerida"] = cat_raw if cat_raw in CATEGORIAS else None

    return True, resultado
