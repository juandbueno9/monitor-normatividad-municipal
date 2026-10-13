<div align="center">

# 📜 Monitor de normatividad municipal

**Vigila cada hora el portal de normatividad de una alcaldía, avisa por correo cuando sale un decreto, acuerdo o resolución nuevo e incluye un panel web local para consultarlos.**

[![CI](https://github.com/juandbueno9/monitor-normatividad-municipal/actions/workflows/ci.yml/badge.svg)](https://github.com/juandbueno9/monitor-normatividad-municipal/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Microsoft Graph](https://img.shields.io/badge/Microsoft_Graph-OAuth2-0078D4?logo=microsoft&logoColor=white)
![Sin dependencias web](https://img.shields.io/badge/panel-stdlib_http.server-555)
![License](https://img.shields.io/badge/licencia-MIT-22d3ee)

<img src="docs/panel.jpg" alt="Panel web" width="100%" />

</div>

---

## 🎯 Problema

El área jurídica tenía que entrar todos los días al portal de la alcaldía para ver si había normas nuevas que afectaran a la organización (impuestos, horarios, movilidad). Era fácil pasar alguna por alto.

## ✅ Solución

- **Monitor programado** (cron/launchd, cada hora) que detecta publicaciones nuevas y envía **un correo HTML** con título, categoría, fecha y enlace al PDF.
- **Panel web local** para buscar, filtrar por categoría, ver el historial de ejecuciones y **reenviar una norma puntual** por correo.

<div align="center">
<img src="docs/correo-ejemplo.jpg" alt="Correo de ejemplo" width="640" />
<br/><sub>Correo de alerta (datos de ejemplo)</sub>
</div>

---

## 🏗️ Arquitectura

```mermaid
flowchart LR
    CRON["cron / launchd<br/>cada hora"] --> MAIN[main.py]
    MAIN --> SC[scraper.py] -->|API JSON interna| PORTAL[(Portal MiColombiaDigital)]
    MAIN --> ST[storage.py<br/>seen_norms.json]
    ST --> N{¿Normas nuevas?}
    N -->|sí| PL[plantillas.py<br/>HTML] --> ML[mailer.py<br/>MSAL + Graph]
    ML -->|ok| SAVE[Marca como notificadas]
    ML -->|falla| RETRY[No marca: se reintenta<br/>en la próxima corrida]
    PANEL["panel.py<br/>http://127.0.0.1:8765"] --> SC
    PANEL --> ML
```

### Decisiones técnicas

| Decisión | Por qué |
|---|---|
| **API JSON interna en vez de scraping HTML** | El portal es una SPA en Angular que no trae el listado en el HTML. Consumir la API que usa la propia página es más rápido, estable y no necesita navegador headless |
| **Solo se marca como notificada si el correo salió** | Un fallo temporal de red o de Graph no hace perder alertas |
| **Escritura atómica con respaldo** (`tempfile` + `os.replace` + `.bak`) | Un corte de luz a mitad de la escritura no corrompe el registro |
| **Reintentos con backoff** (`urllib3.Retry`) | Tolera caídas momentáneas del portal |
| **Panel con `http.server` de la librería estándar** | Cero dependencias web extra; escucha solo en `127.0.0.1` |
| **Logs con rotación** (`RotatingFileHandler`) | No llena el disco |

---

## 🚀 Instalación

```bash
git clone https://github.com/juandbueno9/monitor-normatividad-municipal.git
cd monitor-normatividad-municipal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Variables de entorno

| Variable | Descripción |
|---|---|
| `MS_TENANT_ID` / `MS_CLIENT_ID` / `MS_CLIENT_SECRET` | App de Entra ID con `Mail.Send` de aplicación |
| `MAIL_SENDER` | Buzón remitente |
| `MAIL_RECIPIENTS` | Destinatarios separados por coma |
| `SCRAPE_PAGE_SIZE` | Publicaciones consultadas por ejecución (por defecto 30) |
| `PANEL_REFRESH_SECONDS` | Cada cuánto se actualiza el panel (por defecto 300) |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` o `ERROR` |

## 🖥️ Uso

```bash
python main.py          # una pasada (para cron / launchd)
python main.py --loop   # en primer plano, repite cada hora
python panel.py         # panel web en http://127.0.0.1:8765
```

**Programación:**

```cron
# Linux
0 * * * * cd /opt/monitor-normatividad && .venv/bin/python main.py
```

En macOS ajusta las rutas en `com.empresa.normatividad.plist` y cárgalo con `launchctl load`.

**Adaptarlo a otra alcaldía:** muchas alcaldías de Colombia usan la plataforma MiColombiaDigital. Basta con cambiar `PUBLIC_BASE_URL` y `API_BASE_URL` en `scraper.py`.

---

## 📁 Estructura

```text
monitor-normatividad-municipal/
├── main.py          # Orquestador (una pasada o --loop)
├── scraper.py       # Cliente de la API del portal (reintentos, dataclass Norma)
├── storage.py       # Registro de vistos e historial con escritura atómica
├── plantillas.py    # Correo HTML
├── mailer.py        # Microsoft Graph con MSAL (client credentials)
├── panel.py         # Panel web local (stdlib) con búsqueda, filtros y reenvío
├── com.empresa.normatividad.plist
└── docs/            # Capturas
```

---

**Autor:** [Juan Bueno](https://github.com/juandbueno9) · Licencia [MIT](LICENSE)
