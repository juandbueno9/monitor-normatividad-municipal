"""
scraper.py
----------
Cliente para detectar nuevas publicaciones normativas del municipio de
Santa Rosa de Cabal (Risaralda) en https://www.santarosadecabal-risaralda.gov.co/tema/normatividad

El sitio es una SPA en Angular (no renderiza el listado en el HTML plano),
por lo que en lugar de parsear HTML de forma fragil, este modulo consume
directamente la API JSON interna que la propia pagina usa para pintar el
listado (plataforma "MiColombiaDigital", usada por muchas alcaldias
colombianas). Esto es mas estable ante cambios de maquetacion.

Si en el futuro esa API cambiase de forma, `parse_listing_html` queda como
respaldo basado en BeautifulSoup sobre el HTML de la pagina de detalle
(la cual si es renderizada por el navegador; se deja documentado el
selector usado en caso de que se quiera adaptar a scraping con Selenium/
Playwright).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("app-normatividad.scraper")

# ---------------------------------------------------------------------------
# Configuracion del sitio
# ---------------------------------------------------------------------------

# Host publico (el que ve el usuario) y host real de la API (backend de la
# plataforma MiColombiaDigital que sirve el contenido de esta alcaldia).
PUBLIC_BASE_URL = "https://www.santarosadecabal-risaralda.gov.co"
API_BASE_URL = "https://santarosadecabalrisaralda.micolombiadigital.gov.co"

# ID del "tag" (categoria) de Normatividad en este sitio. Se obtuvo
# inspeccionando las llamadas XHR que hace la pagina de normatividad.
NORMATIVIDAD_TAG_ID = 19

LIST_ENDPOINT = f"{API_BASE_URL}/api/v1/contents"
DETAIL_ENDPOINT = f"{API_BASE_URL}/api/v1/contents/{{friendly_name}}"
PUBLIC_DETAIL_URL = f"{PUBLIC_BASE_URL}/normatividad/{{friendly_name}}"

DEFAULT_TIMEOUT = 20  # segundos
DEFAULT_PAGE_SIZE = 30

USER_AGENT = (
    "AppNormatividadBot/1.0 (+alertas@empresa.com; "
    "monitor interno de normatividad municipal)"
)


@dataclass
class Norma:
    """Representa una norma/publicacion individual."""

    content_id: int
    titulo: str
    tipo: str
    descripcion: str
    fecha_expedicion: Optional[str]
    fecha_modificacion: Optional[str]
    slug: str
    url_detalle: str
    url_pdf: Optional[str] = None
    nombre_archivo: Optional[str] = None
    tamano_bytes: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_session() -> requests.Session:
    """Crea una sesion de requests con reintentos y timeouts razonables."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
    )
    return session


def _clean_html(raw_html: Optional[str]) -> str:
    """Limpia HTML a texto plano usando BeautifulSoup (para cuerpos/descr.)."""
    if not raw_html:
        return ""
    try:
        return BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    except Exception:  # nunca debe tumbar el flujo por un HTML mal formado
        logger.exception("No se pudo limpiar HTML de la descripcion")
        return raw_html


def fetch_listado(
    session: Optional[requests.Session] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """
    Consulta la API de listado y devuelve los ultimos `page_size` items
    de la categoria Normatividad, ordenados por mas recientes primero.

    Lanza requests.RequestException si la peticion falla tras los reintentos.
    """
    session = session or _build_session()
    params = {
        "rangeType": "",
        "page": 0,
        "hasNextPage": "false",
        "keyword": "",
        "isMobile": "false",
        "isFull": "false",
        "pageSize": page_size,
        "includeTypes": "true",
        "includeLabels": "true",
        "orderBy": "recent",
        "includeTags": "true",
        "tags": NORMATIVIDAD_TAG_ID,
    }

    logger.debug("Consultando listado: %s params=%s", LIST_ENDPOINT, params)
    resp = session.get(LIST_ENDPOINT, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    resultados = data.get("results", [])
    logger.info(
        "Listado obtenido: %d items (totalCount reportado=%s)",
        len(resultados),
        data.get("meta", {}).get("totalCount"),
    )
    return resultados


def fetch_detalle(
    friendly_name: str, session: Optional[requests.Session] = None
) -> dict[str, Any]:
    """Consulta el detalle de una norma (incluye el/los archivo(s) PDF)."""
    session = session or _build_session()
    url = DETAIL_ENDPOINT.format(friendly_name=friendly_name)
    logger.debug("Consultando detalle: %s", url)
    resp = session.get(url, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _tipo_desde_item(item: dict[str, Any]) -> str:
    labels = item.get("labels") or []
    if labels:
        # Puede venir como lista de strings (listado) o de dicts (detalle)
        primero = labels[0]
        if isinstance(primero, dict):
            return primero.get("name", "Sin categoria")
        return str(primero)
    return "Sin categoria"


def obtener_normas_recientes(
    page_size: int = DEFAULT_PAGE_SIZE,
    incluir_detalle: bool = True,
) -> list[Norma]:
    """
    Funcion principal del modulo: obtiene las normas mas recientes con toda
    la informacion necesaria para generar la alerta (incluye enlace directo
    al PDF, que solo viene en el endpoint de detalle).
    """
    session = _build_session()
    items = fetch_listado(session=session, page_size=page_size)

    normas: list[Norma] = []
    for item in items:
        friendly_name = item.get("friendlyName", "")
        content_id = item.get("contentID")
        if content_id is None or not friendly_name:
            logger.warning("Item de listado sin contentID/friendlyName, se omite: %s", item)
            continue

        norma = Norma(
            content_id=content_id,
            titulo=(item.get("name") or "").strip(),
            tipo=_tipo_desde_item(item),
            descripcion=_clean_html(item.get("metaDescription")),
            fecha_expedicion=item.get("startingDate"),
            fecha_modificacion=item.get("modifiedDate"),
            slug=friendly_name,
            url_detalle=PUBLIC_DETAIL_URL.format(friendly_name=friendly_name),
        )

        if incluir_detalle:
            try:
                detalle = fetch_detalle(friendly_name, session=session)
                archivos = detalle.get("files") or []
                if archivos:
                    primer_archivo = archivos[0]
                    norma.url_pdf = primer_archivo.get("filePath")
                    norma.nombre_archivo = primer_archivo.get("name")
                    norma.tamano_bytes = primer_archivo.get("size")
            except requests.RequestException:
                logger.exception(
                    "No se pudo obtener el detalle de '%s' (contentID=%s); "
                    "se continua sin enlace directo al PDF",
                    friendly_name,
                    content_id,
                )

        normas.append(norma)

    return normas


def parse_listing_html(html: str) -> list[dict[str, str]]:
    """
    Respaldo: extrae tarjetas de norma (`.norm-row`) desde un HTML ya
    renderizado (por ejemplo, capturado con un navegador headless) usando
    BeautifulSoup. Se conserva por si la API JSON cambiara de contrato.

    Estructura esperada por tarjeta (observada en el sitio):
        <div class="norm-row">
          <a class="norm-wImg" href="/normatividad/<slug>">...</a>
          <div class="norm-wTxt">
            <a class="text-upper">...tipo...</a>
            <a class="norm__name">...titulo...</a>
            <a class="norm__description">...descripcion...</a>
            <a>...fechas...</a>
          </div>
        </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select(".norm-row"):
        link = row.select_one("a.norm-wImg, a.norm__name")
        titulo_tag = row.select_one("a.norm__name")
        tipo_tag = row.select_one("a.text-upper")
        desc_tag = row.select_one("a.norm__description")
        items.append(
            {
                "titulo": titulo_tag.get_text(strip=True) if titulo_tag else "",
                "tipo": tipo_tag.get_text(strip=True) if tipo_tag else "",
                "descripcion": desc_tag.get_text(strip=True) if desc_tag else "",
                "url_detalle": (
                    PUBLIC_BASE_URL + link["href"]
                    if link and link.get("href", "").startswith("/")
                    else (link["href"] if link else "")
                ),
            }
        )
    return items
