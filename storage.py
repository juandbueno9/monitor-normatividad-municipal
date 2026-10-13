"""
storage.py
----------
Persistencia local muy simple (JSON) para:

  1. Recordar que normas ya se notificaron por correo (evitar duplicados).
  2. Llevar un historial/bitacora de cada ejecucion (para el panel visual,
     pestaña "Historial"): cuando corrio, cuantas normas nuevas encontro,
     si el correo se envio bien o hubo un error.

Se usan archivos JSON en vez de una base de datos porque el volumen de
datos es pequeno y asi el proyecto no depende de un motor de base de datos
adicional. Toda escritura es atomica (se escribe a un archivo temporal y se
reemplaza) para evitar corromper el archivo si el proceso se interrumpe a
mitad de escritura.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger("app-normatividad.storage")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "seen_norms.json")
HISTORY_PATH = os.path.join(BASE_DIR, "run_history.json")
MAX_HISTORY_ENTRIES = 300


def _guardar_json_atomico(data: Any, path: str) -> None:
    """Escribe `data` como JSON de forma atomica (archivo temporal + replace)."""
    directorio = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directorio, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _cargar_json_con_respaldo(path: str, valor_por_defecto: Any) -> Any:
    """Carga un JSON; si esta corrupto, lo respalda con sufijo .bak y devuelve el default."""
    if not os.path.exists(path):
        return valor_por_defecto
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception(
            "No se pudo leer %s (posiblemente corrupto); se usara un valor vacio. "
            "El archivo danado se conserva con sufijo .bak para inspeccion manual.",
            path,
        )
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
        return valor_por_defecto


# ---------------------------------------------------------------------------
# Registro de normas ya notificadas (deduplicacion)
# ---------------------------------------------------------------------------


def cargar_vistos(path: str = DEFAULT_PATH) -> dict[str, Any]:
    """
    Carga el registro de normas ya notificadas.

    Estructura del archivo:
        {
          "seen_ids": [64645, 64644, ...],
          "detalle": {"64645": {"titulo": "...", "notificado_en": "..."}}
        }
    """
    if not os.path.exists(path):
        logger.info("No existe %s todavia; se iniciara un registro vacio", path)
        return {"seen_ids": [], "detalle": {}}

    data = _cargar_json_con_respaldo(path, {"seen_ids": [], "detalle": {}})
    data.setdefault("seen_ids", [])
    data.setdefault("detalle", {})
    return data


def guardar_vistos(data: dict[str, Any], path: str = DEFAULT_PATH) -> None:
    """Guarda el registro de vistos de forma atomica."""
    _guardar_json_atomico(data, path)


# ---------------------------------------------------------------------------
# Historial de ejecuciones (para la pestaña "Historial" del panel)
# ---------------------------------------------------------------------------


def cargar_historial(path: str = HISTORY_PATH) -> list[dict[str, Any]]:
    """Carga la lista de ejecuciones registradas, mas reciente al final."""
    historial = _cargar_json_con_respaldo(path, [])
    if not isinstance(historial, list):
        logger.warning("Formato inesperado en %s; se ignora y se reinicia.", path)
        return []
    return historial


def registrar_ejecucion(entrada: dict[str, Any], path: str = HISTORY_PATH) -> None:
    """
    Agrega una entrada al historial de ejecuciones y trunca a las ultimas
    `MAX_HISTORY_ENTRIES` para que el archivo no crezca indefinidamente.

    `entrada` debe incluir al menos: fecha (ISO 8601), resultado, y
    opcionalmente: normas_nuevas (int), detalle (str/list), error (str).
    """
    historial = cargar_historial(path)
    historial.append(entrada)
    if len(historial) > MAX_HISTORY_ENTRIES:
        historial = historial[-MAX_HISTORY_ENTRIES:]
    _guardar_json_atomico(historial, path)
