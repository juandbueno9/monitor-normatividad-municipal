"""
plantillas.py
--------------
Plantilla HTML del correo de alerta, compartida entre:
  - main.py  (envío automático cuando aparecen normas nuevas)
  - panel.py (envío manual de una norma puntual desde el panel visual)
"""

from __future__ import annotations

from datetime import datetime

from scraper import Norma


def _fila_norma_html(n: Norma) -> str:
    fecha = n.fecha_expedicion or n.fecha_modificacion or "Sin fecha"
    pdf_html = (
        f'<a href="{n.url_pdf}" style="color:#0943b5;">Descargar PDF</a>'
        if n.url_pdf
        else f'<a href="{n.url_detalle}" style="color:#0943b5;">Ver publicación</a>'
    )
    return f"""
    <tr>
      <td style="padding:10px;border-bottom:1px solid #e5e5e5;">
        <div style="font-weight:bold;color:#004884;">{n.titulo}</div>
        <div style="font-size:12px;color:#5c5c5c;margin:4px 0;">
          Categoría: {n.tipo} &nbsp;|&nbsp; Fecha de expedición: {fecha}
        </div>
        <div style="font-size:13px;color:#333;margin:6px 0;">{n.descripcion}</div>
        <div style="margin-top:6px;">{pdf_html}
          &nbsp;·&nbsp;
          <a href="{n.url_detalle}" style="color:#0943b5;">Ver en el sitio</a>
        </div>
      </td>
    </tr>
    """


def construir_html_normas(normas: list[Norma], intro: str | None = None) -> str:
    """
    Arma el cuerpo HTML de un correo con una o varias normas.

    `intro` permite personalizar el párrafo inicial (por ejemplo, para
    distinguir un envío automático de uno manual desde el panel). Si no se
    da, se usa un texto genérico.
    """
    filas = "\n".join(_fila_norma_html(n) for n in normas)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if intro is None:
        intro = (
            f"Se detectaron <strong>{len(normas)}</strong> norma(s) nueva(s) en el "
            "portal de la Alcaldía de Santa Rosa de Cabal (Risaralda)."
        )

    return f"""
    <html>
      <body style="font-family:Verdana,Geneva,sans-serif;color:#333;">
        <h2 style="color:#004884;">Normatividad Santa Rosa de Cabal</h2>
        <p>{intro}</p>
        <table style="width:100%;border-collapse:collapse;">
          {filas}
        </table>
        <p style="font-size:11px;color:#999;margin-top:20px;">
          Generado por monitor-normatividad el {ahora}.
        </p>
      </body>
    </html>
    """
