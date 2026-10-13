# Monitor de normatividad municipal

Vigila la sección de **Normatividad** del portal de una alcaldía (plataforma MiColombiaDigital) y envía un correo cuando se publican decretos, acuerdos o resoluciones nuevos. Incluye un panel web local para consultar y reenviar normas.

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![Microsoft Graph](https://img.shields.io/badge/Microsoft_Graph-0078D4?logo=microsoft&logoColor=white)

## Cómo funciona

```
launchd / cron (cada hora)
   └─ main.py
        ├─ scraper.py   ──► API JSON interna del portal (no scraping de HTML)
        ├─ storage.py   ──► seen_norms.json: IDs ya notificados (escritura atómica)
        ├─ plantillas.py──► correo HTML con título, categoría, fecha y enlace al PDF
        └─ mailer.py    ──► Microsoft Graph (OAuth2 client credentials con MSAL)
```

**Decisiones técnicas**
- El portal es una app Angular que no trae el listado en el HTML. En vez de usar un navegador headless, el scraper consume directamente la **API JSON** que usa la página: es más rápido y no se rompe si cambian el diseño.
- Una norma solo se marca como notificada **si el correo salió bien**, así no se pierden alertas por un fallo temporal.
- Reintentos automáticos con backoff (`urllib3.Retry`) y logs con rotación.

## Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # credenciales de la app de Entra ID y destinatarios
```

## Uso

```bash
python main.py          # una pasada (para cron/launchd)
python main.py --loop   # en primer plano, cada hora
python panel.py         # panel web en http://127.0.0.1:8765
```

**Programarlo:**
- **macOS**: ajusta las rutas en `com.empresa.normatividad.plist` y cárgalo con `launchctl load`.
- **Linux**: `0 * * * * cd /ruta/al/proyecto && .venv/bin/python main.py`

## Seguridad
- Secretos solo en `.env`, que está en `.gitignore`.
- El panel escucha únicamente en `127.0.0.1`.
