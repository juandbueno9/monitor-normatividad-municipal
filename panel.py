"""
panel.py
--------
Panel visual de monitor-normatividad.

A diferencia de la primera versión, ahora este módulo levanta un pequeño
SERVIDOR LOCAL (con la librería estándar de Python, sin dependencias
nuevas) en http://127.0.0.1:8765/. Esto permite dos cosas que un archivo
HTML estático no puede hacer:

  1. Mantenerse actualizado solo: un hilo en segundo plano vuelve a
     consultar el sitio de la Alcaldía cada pocos minutos
     (PANEL_REFRESH_SECONDS, por defecto 300 = 5 min), y la página web
     recarga esos datos automáticamente cada ~20 segundos mientras la
     tengas abierta. Si aparece una norma nueva, la vas a ver sin tener
     que recargar nada a mano.
  2. Enviar una norma puntual por correo: cada tarjeta tiene un botón
     "Enviar por correo" que te deja escribir un destinatario y la manda
     en el momento, usando el mismo mecanismo de Microsoft Graph que ya
     usa main.py.

Nota sobre "Ver PDF": el servidor de archivos de la Alcaldía
(micolombiadigital.gov.co) bloquea que sus páginas/PDFs se muestren
incrustados dentro de otra página (encabezado X-Frame-Options) — por eso
la primera versión mostraba "ha rechazado la conexión" dentro del modal.
Por eso ahora "Ver PDF" abre el documento en una pestaña/ventana nueva del
navegador (sí funciona perfecto así; solo no se puede incrustar dentro de
esta página).

Uso:
    python panel.py

Esto abre tu navegador en http://127.0.0.1:8765/. Deja la terminal
abierta mientras lo estés usando; para apagarlo, vuelve a la terminal y
presiona Ctrl+C.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from dotenv import load_dotenv

from mailer import GraphMailer, GraphMailError
from plantillas import construir_html_normas
from scraper import Norma, obtener_normas_recientes
from storage import cargar_historial, cargar_vistos

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "panel.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("app-normatividad.panel")

HOST = "127.0.0.1"
PORT_INICIAL = int(os.getenv("PANEL_PORT", "8765"))
REFRESH_SECONDS = int(os.getenv("PANEL_REFRESH_SECONDS", "300"))  # 5 minutos
PAGE_SIZE = int(os.getenv("PANEL_PAGE_SIZE", os.getenv("SCRAPE_PAGE_SIZE", "60")))

MAIL_SENDER = os.getenv("MAIL_SENDER", "")
MAIL_RECIPIENTS_DEFAULT = os.getenv("MAIL_RECIPIENTS", "")
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")

# --- Colores del panel (cámbialos para usar los de tu organización) ---------
COLOR_TEAL_OSCURO = "#0f2a44"
COLOR_TEAL = "#1d4ed8"
COLOR_MAGENTA = "#0ea5e9"


# ---------------------------------------------------------------------------
# Cache de normas (se refresca en un hilo de fondo)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict = {"normas": [], "actualizado_en": None, "error": None, "actualizando": False}


def _actualizar_cache() -> None:
    with _cache_lock:
        _cache["actualizando"] = True
    try:
        logger.info("Consultando el sitio de normatividad (page_size=%d)...", PAGE_SIZE)
        normas = obtener_normas_recientes(page_size=PAGE_SIZE, incluir_detalle=True)
        with _cache_lock:
            _cache["normas"] = normas
            _cache["actualizado_en"] = datetime.now().isoformat(timespec="seconds")
            _cache["error"] = None
        logger.info("Cache actualizada: %d normas.", len(normas))
    except Exception as exc:
        logger.exception("No se pudo actualizar la cache de normas")
        with _cache_lock:
            _cache["error"] = str(exc)
    finally:
        with _cache_lock:
            _cache["actualizando"] = False


def _hilo_actualizacion_periodica() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        _actualizar_cache()


def _norma_a_dict(n: Norma, vistos_ids: set[int]) -> dict:
    d = n.to_dict()
    d["es_nueva"] = n.content_id not in vistos_ids
    return d


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silencia el log por defecto de http.server
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_text: str) -> None:
        body = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (nombre fijado por BaseHTTPRequestHandler)
        ruta = urlparse(self.path).path

        if ruta == "/":
            self._send_html(PAGINA_HTML)
            return

        if ruta == "/api/normas":
            with _cache_lock:
                vistos = set(cargar_vistos()["seen_ids"])
                normas = [_norma_a_dict(n, vistos) for n in _cache["normas"]]
                data = {
                    "normas": normas,
                    "actualizado_en": _cache["actualizado_en"],
                    "actualizando": _cache["actualizando"],
                    "error": _cache["error"],
                    "destinatario_por_defecto": MAIL_RECIPIENTS_DEFAULT,
                }
            self._send_json(data)
            return

        if ruta == "/api/historial":
            self._send_json({"historial": cargar_historial()})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        ruta = urlparse(self.path).path
        largo = int(self.headers.get("Content-Length", 0) or 0)
        crudo = self.rfile.read(largo) if largo else b""

        if ruta == "/api/actualizar":
            _actualizar_cache()
            self.do_GET_normas_directo()
            return

        if ruta == "/api/enviar":
            try:
                payload = json.loads(crudo or b"{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "Solicitud invalida (JSON mal formado)."}, 400)
                return
            self._manejar_envio(payload)
            return

        self.send_response(404)
        self.end_headers()

    def do_GET_normas_directo(self) -> None:
        """Reutilizado por /api/actualizar para devolver el estado fresco tras refrescar."""
        with _cache_lock:
            vistos = set(cargar_vistos()["seen_ids"])
            normas = [_norma_a_dict(n, vistos) for n in _cache["normas"]]
            data = {
                "normas": normas,
                "actualizado_en": _cache["actualizado_en"],
                "actualizando": _cache["actualizando"],
                "error": _cache["error"],
                "destinatario_por_defecto": MAIL_RECIPIENTS_DEFAULT,
            }
        self._send_json(data)

    def _manejar_envio(self, payload: dict) -> None:
        content_id = payload.get("content_id")
        destinatario = (payload.get("destinatario") or "").strip()

        if not destinatario:
            self._send_json({"ok": False, "error": "Falta el correo destinatario."}, 400)
            return

        with _cache_lock:
            norma = next((n for n in _cache["normas"] if n.content_id == content_id), None)

        if norma is None:
            self._send_json(
                {"ok": False, "error": "No se encontró esa norma (intenta actualizar el panel)."}, 404
            )
            return

        if not all([MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MAIL_SENDER]):
            self._send_json(
                {"ok": False, "error": "Faltan credenciales de Microsoft Graph en el archivo .env."}, 500
            )
            return

        try:
            mailer = GraphMailer(MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET)
            cuerpo_html = construir_html_normas(
                [norma],
                intro=(
                    f"Envío manual desde el panel de normatividad de la siguiente norma:"
                ),
            )
            mailer.enviar_correo(
                sender=MAIL_SENDER,
                destinatarios=[destinatario],
                asunto=f"[Normatividad] {norma.titulo}",
                cuerpo_html=cuerpo_html,
            )
            logger.info("Norma %s enviada manualmente a %s", norma.content_id, destinatario)
            self._send_json({"ok": True})
        except (GraphMailError, ValueError, Exception) as exc:
            logger.exception("Error enviando la norma %s manualmente", content_id)
            self._send_json({"ok": False, "error": str(exc)}, 500)


def _puerto_disponible(host: str, puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, puerto)) != 0


def _elegir_puerto() -> int:
    for candidato in range(PORT_INICIAL, PORT_INICIAL + 10):
        if _puerto_disponible(HOST, candidato):
            return candidato
    raise RuntimeError(
        f"No se encontró un puerto libre entre {PORT_INICIAL} y {PORT_INICIAL + 9}. "
        "Define PANEL_PORT en tu .env con otro valor."
    )


# ---------------------------------------------------------------------------
# Página (estática: los datos se cargan por JavaScript vía /api/normas)
# ---------------------------------------------------------------------------

PAGINA_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Panel de Normatividad</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --teal-oscuro: __TEAL_OSCURO__;
    --teal: __TEAL__;
    --magenta: __MAGENTA__;
    --fondo: #f4f7f8;
    --texto: #2c2c2c;
    --borde: #e1e8ea;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Arial, Helvetica, sans-serif; background: var(--fondo); color: var(--texto); }
  header.app {
    background: linear-gradient(135deg, var(--teal-oscuro), var(--teal));
    color: #fff; padding: 22px 28px; display: flex; align-items: center;
    justify-content: space-between; flex-wrap: wrap; gap: 12px;
  }
  header.app .marca { display: flex; flex-direction: column; gap: 2px; }
  header.app .empresa { font-size: 22px; font-weight: 800; letter-spacing: 0.5px; }
  header.app .subtitulo { font-size: 13px; color: #dbeafe; }
  header.app .estado { font-size: 12px; color: #dbeafe; text-align: right; }
  header.app .estado button {
    background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.4); color: #fff;
    padding: 5px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; margin-left: 8px;
  }
  header.app .estado button:hover { background: rgba(255,255,255,0.28); }
  #punto-vivo { display:inline-block; width:8px; height:8px; border-radius:50%; background:#5fd97a; margin-right:6px; }
  #punto-vivo.actualizando { background:#f2c94c; animation: parpadeo 1s infinite; }
  @keyframes parpadeo { 50% { opacity: 0.3; } }

  nav.tabs { display: flex; gap: 4px; padding: 0 28px; background: #fff; border-bottom: 1px solid var(--borde); }
  nav.tabs button { border: none; background: none; padding: 14px 18px; font-size: 14px; font-weight: 600; color: #6b7280; cursor: pointer; border-bottom: 3px solid transparent; }
  nav.tabs button.activo { color: var(--teal-oscuro); border-bottom-color: var(--magenta); }

  main { padding: 24px 28px 60px; max-width: 1200px; margin: 0 auto; }
  section.vista { display: none; }
  section.vista.activo { display: block; }

  #banner-nuevas {
    display: none; background: #eafff1; border: 1px solid #b7e8c4; color: #1a7f37;
    padding: 10px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; font-weight: 600;
  }
  #banner-nuevas.mostrar { display: block; }

  .barra-filtros { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
  .barra-filtros input[type="text"] { flex: 1; min-width: 220px; padding: 10px 14px; border: 1px solid var(--borde); border-radius: 8px; font-size: 14px; }
  .barra-filtros select { padding: 10px 14px; border: 1px solid var(--borde); border-radius: 8px; font-size: 14px; background: #fff; }

  .rejilla { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
  .tarjeta { background: #fff; border: 1px solid var(--borde); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; box-shadow: 0 1px 2px rgba(12,67,79,0.04); position: relative; }
  .tarjeta.nueva { border-color: var(--magenta); }
  .badge-nueva {
    position: absolute; top: -9px; right: 12px; background: var(--magenta); color: #fff;
    font-size: 10px; font-weight: 800; padding: 3px 8px; border-radius: 999px; letter-spacing: 0.4px;
  }
  .tarjeta-cab { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .pastilla { background: var(--teal-oscuro); color: #fff; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; padding: 3px 9px; border-radius: 999px; }
  .fecha { font-size: 12px; color: #8a8a8a; }
  .tarjeta-titulo { font-size: 15px; margin: 4px 0 6px; color: var(--teal-oscuro); line-height: 1.3; }
  .tarjeta-desc { font-size: 13px; color: #555; flex: 1; line-height: 1.4; margin: 0 0 12px; }
  .tarjeta-pie { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .btn { border: none; padding: 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; }
  .btn-pdf { background: var(--magenta); color: #fff; }
  .btn-pdf:hover { opacity: 0.9; }
  .btn-correo { background: #eff6ff; color: var(--teal-oscuro); border: 1px solid var(--borde); }
  .btn-correo:hover { background: #dbeafe; }
  .enlace-sitio { font-size: 12px; color: var(--teal); text-decoration: none; margin-left: auto; }
  .enlace-sitio:hover { text-decoration: underline; }
  .tam { font-size: 11px; color: #999; }

  .form-correo { display: none; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--borde); gap: 8px; flex-direction: column; }
  .form-correo.abierto { display: flex; }
  .form-correo .fila { display: flex; gap: 8px; }
  .form-correo input[type="email"] { flex: 1; padding: 7px 10px; border: 1px solid var(--borde); border-radius: 6px; font-size: 13px; }
  .form-correo .mensaje { font-size: 12px; }
  .form-correo .mensaje.ok { color: #1a7f37; }
  .form-correo .mensaje.error { color: var(--magenta); }

  table.historial { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--borde); border-radius: 12px; overflow: hidden; }
  table.historial th { text-align: left; font-size: 12px; text-transform: uppercase; color: #8a8a8a; background: #fafcfc; padding: 12px 14px; border-bottom: 1px solid var(--borde); }
  table.historial td { padding: 12px 14px; border-bottom: 1px solid var(--borde); font-size: 13px; vertical-align: top; }
  .col-detalle { color: #555; max-width: 480px; }
  .pastilla-estado { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
  .vacio { color: #8a8a8a; font-size: 13px; padding: 20px; text-align: center; }

  footer.app { text-align: center; font-size: 11px; color: #9aa5a8; padding: 20px; }
</style>
</head>
<body>

<header class="app">
  <div class="marca">
    <span class="empresa">MONITOR NORMATIVO</span>
    <span class="subtitulo">Panel de Normatividad municipal · Santa Rosa de Cabal</span>
  </div>
  <div class="estado">
    <span id="punto-vivo"></span><span id="texto-estado">Cargando…</span>
    <button onclick="actualizarAhora()">Actualizar ahora</button>
  </div>
</header>

<nav class="tabs">
  <button class="activo" id="tab-btn-normas" onclick="mostrarVista('normas', this)">Normas (<span id="conteo-normas">0</span>)</button>
  <button id="tab-btn-historial" onclick="mostrarVista('historial', this)">Historial (<span id="conteo-historial">0</span>)</button>
</nav>

<main>
  <section id="vista-normas" class="vista activo">
    <div id="banner-nuevas"></div>
    <div class="barra-filtros">
      <input type="text" id="buscador" placeholder="Buscar por título o descripción..." oninput="renderizar()">
      <select id="filtro-tipo" onchange="renderizar()">
        <option value="">Todas las categorías</option>
      </select>
    </div>
    <div class="rejilla" id="rejilla"></div>
  </section>

  <section id="vista-historial" class="vista">
    <table class="historial">
      <thead>
        <tr>
          <th>Fecha de ejecución</th>
          <th>Resultado</th>
          <th style="text-align:center;">Normas nuevas</th>
          <th>Detalle</th>
        </tr>
      </thead>
      <tbody id="cuerpo-historial"></tbody>
    </table>
  </section>
</main>

<footer class="app">monitor-normatividad · se actualiza solo cada pocos minutos mientras esta página esté abierta</footer>

<script>
let NORMAS = [];
let CONOCIDAS = new Set();       // content_id ya vistos en ESTA sesión del navegador
let PRIMERA_CARGA = true;
let DESTINATARIO_DEFECTO = '';

const BADGES_RESULTADO = {
  notificado: ['Correo enviado', '#1a7f37'],
  sin_novedades: ['Sin novedades', '#6b7280'],
  error_consulta: ['Error al consultar el sitio', '__MAGENTA__'],
  error_envio: ['Error al enviar correo', '__MAGENTA__'],
  error_configuracion: ['Falta configuración (.env)', '__MAGENTA__'],
};

function escaparHtml(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

function mostrarVista(id, boton) {
  document.querySelectorAll('.vista').forEach(v => v.classList.remove('activo'));
  document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('activo'));
  document.getElementById('vista-' + id).classList.add('activo');
  boton.classList.add('activo');
  if (id === 'historial') cargarHistorial();
}

function poblarFiltroTipos() {
  const select = document.getElementById('filtro-tipo');
  const actual = select.value;
  const tipos = [...new Set(NORMAS.map(n => n.tipo).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Todas las categorías</option>' +
    tipos.map(t => `<option value="${escaparHtml(t)}">${escaparHtml(t)}</option>`).join('');
  select.value = actual;
}

function tarjetaHtml(n) {
  const fecha = (n.fecha_expedicion || n.fecha_modificacion || '').slice(0, 10) || 'Sin fecha';
  const pdfUrl = n.url_pdf || n.url_detalle;
  const descripcion = (n.descripcion || '').slice(0, 220);
  const puntos = (n.descripcion || '').length > 220 ? '…' : '';
  const tam = n.tamano_bytes ? `<span class="tam">${Math.round(n.tamano_bytes / 1024)} KB</span>` : '';
  const esNueva = n.es_nueva ? 'nueva' : '';
  const badge = n.es_nueva ? '<span class="badge-nueva">NUEVA</span>' : '';

  return `
    <article class="tarjeta ${esNueva}" data-id="${n.content_id}" data-tipo="${escaparHtml(n.tipo)}" data-texto="${escaparHtml((n.titulo + ' ' + descripcion).toLowerCase())}">
      ${badge}
      <div class="tarjeta-cab">
        <span class="pastilla">${escaparHtml(n.tipo || 'Sin categoría')}</span>
        <span class="fecha">${fecha}</span>
      </div>
      <h3 class="tarjeta-titulo">${escaparHtml(n.titulo || '(Sin título)')}</h3>
      <p class="tarjeta-desc">${escaparHtml(descripcion)}${puntos}</p>
      <div class="tarjeta-pie">
        <button class="btn btn-pdf" onclick="window.open('${pdfUrl}', '_blank')">Ver PDF</button>
        <button class="btn btn-correo" onclick="toggleFormCorreo(${n.content_id})">Enviar por correo</button>
        <a class="enlace-sitio" href="${n.url_detalle}" target="_blank" rel="noopener">Ver en el sitio ↗</a>
        ${tam}
      </div>
      <div class="form-correo" id="form-correo-${n.content_id}">
        <div class="fila">
          <input type="email" id="correo-input-${n.content_id}" placeholder="correo@destino.com" value="${escaparHtml(DESTINATARIO_DEFECTO)}">
          <button class="btn btn-pdf" onclick="enviarCorreo(${n.content_id})">Enviar</button>
        </div>
        <div class="mensaje" id="correo-mensaje-${n.content_id}"></div>
      </div>
    </article>
  `;
}

function renderizar() {
  poblarFiltroTipos();
  const texto = document.getElementById('buscador').value.toLowerCase();
  const tipo = document.getElementById('filtro-tipo').value;
  const filtradas = NORMAS.filter(n => {
    const coincideTexto = !texto || (n.titulo + ' ' + (n.descripcion || '')).toLowerCase().includes(texto);
    const coincideTipo = !tipo || n.tipo === tipo;
    return coincideTexto && coincideTipo;
  });
  document.getElementById('rejilla').innerHTML = filtradas.map(tarjetaHtml).join('') ||
    '<p class="vacio">No se encontraron normas.</p>';
  document.getElementById('conteo-normas').textContent = NORMAS.length;
}

function toggleFormCorreo(id) {
  document.getElementById('form-correo-' + id).classList.toggle('abierto');
}

async function enviarCorreo(id) {
  const input = document.getElementById('correo-input-' + id);
  const mensaje = document.getElementById('correo-mensaje-' + id);
  const destinatario = input.value.trim();
  if (!destinatario) {
    mensaje.textContent = 'Escribe un correo destinatario.';
    mensaje.className = 'mensaje error';
    return;
  }
  mensaje.textContent = 'Enviando…';
  mensaje.className = 'mensaje';
  try {
    const resp = await fetch('/api/enviar', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content_id: id, destinatario: destinatario})
    });
    const data = await resp.json();
    if (data.ok) {
      mensaje.textContent = '✓ Enviado a ' + destinatario;
      mensaje.className = 'mensaje ok';
    } else {
      mensaje.textContent = '✗ ' + (data.error || 'No se pudo enviar.');
      mensaje.className = 'mensaje error';
    }
  } catch (e) {
    mensaje.textContent = '✗ Error de conexión con el panel.';
    mensaje.className = 'mensaje error';
  }
}

function actualizarEstadoCabecera(datos) {
  const punto = document.getElementById('punto-vivo');
  const texto = document.getElementById('texto-estado');
  punto.classList.toggle('actualizando', !!datos.actualizando);
  if (datos.error) {
    texto.textContent = 'Error al consultar el sitio (ver logs/panel.log)';
  } else if (datos.actualizado_en) {
    const f = new Date(datos.actualizado_en);
    texto.textContent = 'Actualizado ' + f.toLocaleTimeString();
  } else {
    texto.textContent = 'Cargando…';
  }
}

function mostrarBannerNuevas(cantidad) {
  const banner = document.getElementById('banner-nuevas');
  banner.textContent = `🆕 Se encontraron ${cantidad} norma(s) nueva(s).`;
  banner.classList.add('mostrar');
  setTimeout(() => banner.classList.remove('mostrar'), 8000);
}

async function cargarNormas() {
  try {
    const resp = await fetch('/api/normas');
    const datos = await resp.json();
    NORMAS = datos.normas || [];
    actualizarEstadoCabecera(datos);
    DESTINATARIO_DEFECTO = datos.destinatario_por_defecto || DESTINATARIO_DEFECTO;

    const idsActuales = new Set(NORMAS.map(n => n.content_id));
    if (!PRIMERA_CARGA) {
      const nuevasDesdeUltimoVistazo = [...idsActuales].filter(id => !CONOCIDAS.has(id));
      if (nuevasDesdeUltimoVistazo.length > 0) {
        mostrarBannerNuevas(nuevasDesdeUltimoVistazo.length);
        if (document.hidden) {
          document.title = `(${nuevasDesdeUltimoVistazo.length}) Panel de Normatividad`;
        }
      }
    }
    CONOCIDAS = idsActuales;
    PRIMERA_CARGA = false;
    renderizar();
  } catch (e) {
    document.getElementById('texto-estado').textContent = 'No se pudo conectar con el panel local.';
  }
}

async function actualizarAhora() {
  document.getElementById('punto-vivo').classList.add('actualizando');
  document.getElementById('texto-estado').textContent = 'Actualizando…';
  try {
    const resp = await fetch('/api/actualizar', {method: 'POST'});
    const datos = await resp.json();
    NORMAS = datos.normas || [];
    actualizarEstadoCabecera(datos);
    CONOCIDAS = new Set(NORMAS.map(n => n.content_id));
    renderizar();
  } catch (e) {
    document.getElementById('texto-estado').textContent = 'No se pudo actualizar.';
  }
}

function filaHistorial(e) {
  const fecha = (e.fecha || '').replace('T', ' ').slice(0, 19) || '—';
  const [texto, color] = BADGES_RESULTADO[e.resultado] || [e.resultado, '#6b7280'];
  const nuevas = e.normas_nuevas || 0;
  const detalle = e.error || (e.titulos || []).slice(0, 3).join(', ') || '—';
  return `
    <tr>
      <td>${fecha}</td>
      <td><span class="pastilla-estado" style="background:${color}22;color:${color};">${escaparHtml(texto)}</span></td>
      <td style="text-align:center;">${nuevas}</td>
      <td class="col-detalle">${escaparHtml(detalle)}</td>
    </tr>
  `;
}

async function cargarHistorial() {
  try {
    const resp = await fetch('/api/historial');
    const datos = await resp.json();
    const historial = (datos.historial || []).slice().sort((a, b) => (b.fecha || '').localeCompare(a.fecha || ''));
    document.getElementById('cuerpo-historial').innerHTML = historial.map(filaHistorial).join('') ||
      '<tr><td colspan="4" class="vacio">Todavía no hay ejecuciones registradas. Corre "python main.py" al menos una vez.</td></tr>';
    document.getElementById('conteo-historial').textContent = historial.length;
  } catch (e) {
    // silencioso: la pestaña de normas sigue funcionando igual
  }
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) document.title = 'Panel de Normatividad';
});

cargarNormas();
cargarHistorial();
setInterval(cargarNormas, 20000);
setInterval(cargarHistorial, 60000);
</script>

</body>
</html>
"""

PAGINA_HTML = (
    PAGINA_HTML.replace("__TEAL_OSCURO__", COLOR_TEAL_OSCURO)
    .replace("__TEAL__", COLOR_TEAL)
    .replace("__MAGENTA__", COLOR_MAGENTA)
)


def main() -> None:
    logger.info("Cargando datos iniciales (puede tardar unos segundos)...")
    _actualizar_cache()

    threading.Thread(target=_hilo_actualizacion_periodica, daemon=True).start()

    puerto = _elegir_puerto()
    servidor = ThreadingHTTPServer((HOST, puerto), PanelHandler)
    url = f"http://{HOST}:{puerto}/"

    logger.info("Panel disponible en %s (se actualiza solo cada %ds). Ctrl+C para detener.", url, REFRESH_SECONDS)
    webbrowser.open(url)

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        logger.info("Deteniendo el panel...")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
