# Alertas meteorológicas — sur de Chile (Los Lagos a Magallanes)

Sistema en producción que monitorea 68 centros de cultivo, evalúa umbrales
propios de viento, lluvia, oleaje y helada, y envía alertas por email
cuando corresponde. Incluye además una app web de monitoreo en vivo.

**En producción ahora mismo:**
- Backend (`main.py`) corriendo cada 30 min vía GitHub Actions.
- App de monitoreo (`app.html`) publicada en GitHub Pages:
  `https://elabbe-sso.github.io/alerta-meteorologica-centros/app.html`

## Qué está activo hoy

- **68 puntos monitoreados** (centros de cultivo), cada uno con coordenadas
  exactas y vinculado a una comuna real. No hay comunas "genéricas": todo se
  evalúa punto por punto.
- **Datos crudos**: se combina el **peor caso** entre varios modelos
  meteorológicos, para no perder una alerta si uno solo la subestima:
  - **Open-Meteo** (mezcla "best_match") — sin llave, activo en Python y en `app.html`.
  - **DWD ICON** (modelo alemán explícito, `&models=icon_seamless`) — sin llave, activo en ambos.
  - **ECMWF** (modelo europeo explícito, `&models=ecmwf_ifs025`) — sin llave, activo **solo en `app.html`** (ver nota más abajo sobre por qué no en Python).
  - **Open-Meteo Marine API** (altura de olas/oleaje) — endpoint separado, sin llave, activo en ambos.
- **Alertas por umbral propio** (no por alertas oficiales de ningún organismo — ver más abajo):
  - Viento sostenido ≥ 40 km/h (en el correo: el **peor pronosticado** hasta el próximo envío — ver detalle abajo)
  - Ráfagas ≥ 60 km/h (mismo criterio de pronóstico que el viento)
  - Lluvia ≥ 50 mm/24h
  - Oleaje ≥ 2.0 m
  - Helada: mínima pronosticada ≤ -3°C (Los Lagos y Aysén) o ≤ -5°C (Magallanes), mirando **solo las próximas 12 horas hacia adelante** (nunca horas ya pasadas — ver detalle abajo)
  - Tormenta eléctrica: código de clima 95/96/99 detectado ahora o en las próximas 6 horas (cuenta como una condición amarilla más — ver reglas de severidad abajo)
- **Notificación por email — reporte en horarios fijos**: el chequeo de datos
  corre cada vez que el workflow se dispara, pero el correo
  solo se **arma y envía a las 7:30, 14:00 y 19:00** (hora de Chile,
  configurable en `HORAS_ENVIO` de `main.py`). El disparo puntual usa dos
  vías redundantes: el cron interno de GitHub Actions (`.github/workflows/alertas.yml`,
  en minutos `:07`/`:37` para evitar la congestión de GitHub en `:00`/`:30`)
  y, como respaldo más confiable, 3 tareas programadas en cron-job.org que
  llaman directamente a la API de GitHub a la hora exacta de cada envío.
  Como ambos disparadores pueden caer dentro de la misma ventana de 15 min
  de un mismo horario, `estado.py` (SQLite) registra "ya se envió el
  reporte de las 14:00 de hoy" para que el segundo disparador no repita el
  envío — sin este chequeo, llegarían dos correos idénticos por horario.
  Cada envío es un **reporte completo del estado actual** — no solo lo
  "nuevo" — agrupado por severidad (rojas / amarillas / informativas). Si
  no hay ninguna alerta activa, igual se manda un correo confirmando que
  todo está normal, para que el silencio no se confunda con que el sistema
  dejó de funcionar. El correo es HTML (con una versión en texto plano de
  respaldo automático). El remitente puede mostrar un nombre visible (no
  solo el correo pelado) configurando el secret opcional `SMTP_FROM_NAME`.
- **`app.html`**: buscador, ícono de clima, temperatura actual, pronóstico de
  próximas 6h, chips de resumen clicables (filtran por color), enlace
  destacado a "Estados de Puerto" (ver abajo).

## Reglas de severidad (color) — idénticas en `app.html` y el correo

El color final de cada centro (roja/amarilla/informativa) se calcula igual
en ambos lados (`app.html` y `reporte.py`/`reglas.py`), no por el tipo
individual de cada condición sino por esta tabla:

| Situación | Color |
|---|---|
| Solo helada | Verde (informativa) |
| 1 sola condición amarilla (viento, ráfaga, lluvia, oleaje, o tormenta) | Amarilla |
| Helada + 1 amarilla | Amarilla (la helada nunca cuenta para el "2 o más") |
| 2 o más amarillas juntas (la tormenta cuenta como una más) | Roja |
| Cualquier condición al 30% o más sobre su umbral, aunque sea única | Roja |
| Nada activo | Verde (sin alerta) |

Para que el correo pueda calcular el 30% extremo, `reglas.py` incluye el
`valor` y `umbral` numérico de cada alerta (no solo el mensaje ya armado).
`app.html` calcula esta misma tabla del lado del navegador (con los datos
que le sirve `api.py`, ver más abajo) — es la única otra parte del sistema
que colorea centros, y usa exactamente este mismo criterio.

Cuando una categoría (rojas/amarillas/informativas) llega a **10 o más
centros**, el correo la condensa en una lista de nombres en vez de una
tarjeta de detalle por cada uno — para que no se vuelva eterno de leer
(`UMBRAL_CONDENSAR` en `reporte.py`). Las rojas nunca se condensan.

## Viento y ráfagas en el correo — pronóstico dinámico, no el dato actual

A diferencia de `app.html` (que muestra la condición del momento, ideal
para un monitoreo en vivo), **el correo de reporte compara contra el peor
viento/ráfaga pronosticado desde ahora hasta el próximo envío programado**
(ver `HORAS_ENVIO`), no el dato instantáneo. Así, si a las 7:30 el viento
está tranquilo pero se pronostica fuerte para el mediodía, el reporte de
las 7:30 ya avisa — no espera a que el viento realmente suba.

La ventana se calcula sola en cada corrida (`horas_hasta_proximo_envio()`
en `main.py`): por ejemplo, a las 14:05 con horarios 7:30/14:00/19:00, la
ventana es de ~5 horas (hasta las 19:00). Esto usa el mismo mecanismo de
`reglas.py` que la helada (`usar_pronostico_viento=True`), controlado con
un parámetro para no afectar el dato en vivo que muestra `app.html`.

## Alerta de helada — "hacia adelante", no hacia atrás

En vez de mirar la temperatura de ahora mismo o el mínimo de todo el día, se
calcula la temperatura **más baja pronosticada entre este momento y las
próximas 12 horas**, mirando siempre hacia adelante:
- Si el frío **ya pasó** y no se pronostica que continúe, la alerta se cae
  sola en el siguiente ciclo.
- Si el frío **viene** más tarde, la alerta se enciende con anticipación.

El umbral en sí varía por región (`UMBRALES_POR_REGION` en `config.py`,
`UMBRAL_POR_REGION` en `app.html`): -3°C en Los Lagos y Aysén, -5°C en
Magallanes, donde las heladas moderadas son normales y no ameritan alerta.

Misma lógica en `fuentes.py` (`_extremo_prevista()`) y en `app.html`.

## Otros parámetros que se muestran (informativos, no disparan alerta)

- **Mín / Máx**: pronóstico de temperatura del día completo.
- **Humedad** y **sensación térmica**: dato puntual del momento actual.

Solo viento, ráfaga, lluvia, oleaje, helada y tormenta generan alertas.

## Por qué ECMWF reemplaza a yr.no en `app.html`, no en Python

yr.no (MET Norway) es una fuente real y de buena calidad, pero **no puede
llamarse desde el navegador**: exige un header `User-Agent` propio, y el
`fetch()` de JavaScript no permite que una página modifique ese header
(restricción de seguridad del navegador). Por eso `app.html` usa ECMWF en su
lugar — sin llave, mismo endpoint de Open-Meteo, sin ese problema. En la
práctica, el propio yr.no ya se apoya en datos de ECMWF fuera de la zona
nórdica, así que la calidad es equivalente.

## Estructura de archivos

```
config.py          -> los 68 puntos, sus comunas, y los umbrales
fuentes.py         -> recolectores de datos (Open-Meteo, DWD ICON, yr.no, Marine)
reglas.py          -> motor de reglas (umbrales -> alertas)
notificadores.py   -> envío por email (activo) y WhatsApp (implementado, sin configurar)
estado.py          -> registro en SQLite: evita correos duplicados entre los dos disparadores (ver abajo)
reporte.py         -> arma el correo (texto plano + HTML), agrupado por severidad
main.py            -> orquestador: junta todo, un ciclo por corrida
app.html           -> app de monitoreo en vivo (publicada en GitHub Pages), consulta api.py
api.py             -> servidor propio (desplegado en Render) que centraliza las consultas a
                       Open-Meteo para app.html — ver sección de abajo sobre por qué existe
.github/workflows/alertas.yml -> automatiza main.py en GitHub Actions
```

## Cómo correrlo / desplegarlo

```bash
pip install -r requirements.txt

export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="tu_correo@gmail.com"
export SMTP_PASS="tu_password_de_aplicacion"
export SMTP_FROM_NAME="Alertas Centros de Cultivo"   # opcional
export DESTINATARIOS_EMAIL="correo1@x.cl,correo2@x.cl"

python main.py
```

**En producción** esto no hace falta correrlo a mano: `.github/workflows/alertas.yml`
ya lo ejecuta automáticamente cada 30 minutos en GitHub Actions, leyendo las
credenciales desde repository secrets (mismo nombre que las variables de
entorno de arriba). Para agregar/cambiar destinatarios: Settings → Secrets
and variables → Actions → editar `DESTINATARIOS_EMAIL` (hay que reescribir
la lista completa, GitHub no muestra el valor anterior).

`app.html` no necesita desplegarse aparte de GitHub Pages — ya está
publicada y se actualiza sola cada vez que se sube un cambio al repositorio.

⚠️ Si cambias tu nombre de usuario de GitHub, el link de GitHub Pages
**cambia y no redirige automáticamente** (a diferencia de los repositorios).
Hay que volver a compartir el link nuevo.

## `api.py` — servidor propio para `app.html` (desplegado en Render)

`app.html` ya no consulta Open-Meteo directo desde el navegador de cada
persona. Se cambió por esto: en una red corporativa, todos los empleados
salen a internet con la misma IP pública (por el NAT de la empresa) — si
varias personas abren la app a la vez, el volumen combinado puede superar
el límite de Open-Meteo (600 llamadas/min, 5.000/hora, 10.000/día **por
IP**) y a todos les empieza a fallar con error 429, no solo a quien lo
satura.

`api.py` resuelve esto: consulta Open-Meteo (vía `fetch_datos_consenso()`
de `fuentes.py`, los 68 puntos en paralelo) **una sola vez cada 15
minutos**, con la IP del servidor, y guarda el resultado en memoria. Sin
importar si son 5 o 500 personas viendo `app.html` a la vez, Open-Meteo
solo ve las llamadas de este servidor. Expone un único endpoint,
`GET /api/datos`, que devuelve todo lo que `app.html` necesita mostrar
(temperatura, humedad, viento, ráfaga, oleaje, pronóstico por horas, etc.)
para los 68 centros.

Al arrancar, el proceso precalienta el caché en un hilo de fondo (para que
la primera consulta real no tenga que esperar el refresco completo, que
podría chocar con el límite de tiempo por defecto de Gunicorn).

**Desplegado en Render.com** (plan gratis):
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn api:app --timeout 120` (120s en vez del
  default de 30s, para dar margen a la primera consulta lenta)
- El plan gratis "duerme" el servicio tras un rato sin uso — la primera
  consulta después de eso puede tardar hasta ~50 segundos en responder.

`app.html` apunta a la URL de Render vía la constante `API_BASE` (al
principio del `<script>`) — si vuelves a desplegar el servicio con otra
URL, hay que actualizar esa constante y volver a subir `app.html`.

`dashboard.html` (el mapa de Windy) se descartó — ya no se mantiene ni se
usa; `api.py` ya no lo sirve.

## Ajustar los 68 puntos, comunas y umbrales

Todo vive en `config.py`:
- `PUNTOS_ESPECIFICOS`: lista ordenada alfabéticamente de `(nombre, lat, lon, comuna, región)`. Agregar uno nuevo es una línea.
- `COMUNAS_POR_REGION`: las comunas válidas, agrupadas por región. `validar_comunas_de_puntos()` avisa si algún punto quedó con una comuna que no existe en esta lista.
- `UMBRALES_DEFAULT`: los umbrales globales (arriba). `UMBRALES_POR_REGION` permite un override para toda una región (ej. helada en Magallanes). `UMBRALES_POR_COMUNA` permite un override por nombre exacto de punto (se aplica después, y pisa al de región si ambos aplican).

Al editar `config.py`, hay que replicar el mismo cambio en las listas
equivalentes dentro de `app.html` (`PUNTOS_ESPECIFICOS` y
`COMUNAS_POR_REGION`) para que todo quede sincronizado. `api.py` no
necesita este paso — lee `config.py` directamente.

## Lo que existe en el código pero NO está activo

Estas piezas están implementadas y funcionan si se activan, pero
actualmente no influyen en ninguna alerta ni notificación:

- **Alertas oficiales de SENAPRED**: el código para consultar sus tres
  capas reales (verde/amarilla/roja) sigue en `fuentes.py` y funciona, pero
  la llamada está **comentada** en `main.py` y `app.html` — a pedido
  explícito, el sistema solo notifica por umbral propio, no por alertas
  oficiales de ningún organismo.
- **yr.no en el backend Python**: el código existe en `fuentes.py`, pero
  requiere editar `YR_USER_AGENT` con el nombre real de tu app y un
  contacto válido antes de que MET Norway responda datos reales — sin ese
  paso, la llamada falla silenciosamente y el consenso sigue funcionando
  igual con las demás fuentes.
- **WhatsApp**: implementado en `notificadores.py` (Meta Cloud API o
  Twilio), pero aún no se configuraron credenciales reales
  (`WA_TOKEN`, `WHATSAPP_PROVIDER`, etc.) — hoy solo se envía por email.
- **Estados de Puerto (DIRECTEMAR)**: no se automatiza porque SITPORT y
  SVIP bloquean explícitamente el acceso automatizado (`robots.txt`). En su
  lugar, `app.html` tiene un botón "⚓ Estados de Puerto" que enlaza
  directamente al sitio oficial para revisarlo manualmente.
