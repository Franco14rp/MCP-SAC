# MCP-SAC — incidencias del SAC en el chat

Servidor MCP para el SAC (Foca Software, `http://10.231.45.254:8080/usuario/`). Se
loguea por HTTP directo (sin navegador, sin sesión manual) y expone herramientas para
consultar y — con cuidado — modificar incidencias desde el chat de un asistente de IA.

No es un dashboard: no guarda nada, no corre nada en segundo plano. Cada llamada es una
petición nueva al SAC.

**Requisito de red:** el servidor corre como un proceso local en tu máquina y necesita
llegar a `10.231.45.254` (IP privada) — o sea, tenés que estar en la red interna o VPN
de la empresa. Por eso solo sirve con clientes que ejecutan el proceso en tu PC (ver
sección 3); un chat 100% en la nube (por ejemplo la app de ChatGPT tal como funciona
hoy) no puede llegar a esa IP aunque lograras configurarlo.

## Herramientas expuestas

| Tool | Qué hace | Escribe |
|---|---|---|
| `consultar_incidencia` | Datos de un ticket puntual por número | no |
| `incidencias_por_usuario` | Incidencias asignadas a alguien (por nombre de usuario del SAC, ej. "lmonclus"), paginado | no |
| `incidencias_por_cliente` | Incidencias de un cliente por nombre o id, con filtro de abiertas/cerradas, paginado | no |
| `notas_incidencia` | Historial completo de un ticket: notas de técnicos + cambios de estado automáticos, paginado | no |
| `usuarios_por_nivel` | Personas que pertenecen a un nivel/cola del SAC (ej. "1º Nivel EESS"), con su id | no |
| `agregar_nota_incidencia` | Agrega una nota/tarea real a un ticket | **sí** |
| `reasignar_incidencia` | Reasigna un ticket a otra persona (y opcionalmente nivel/criticidad) | **sí** |
| `crear_incidencia` | Crea una incidencia nueva para un cliente (botón "Nueva") | **sí** |

## 1. Configuración

Copiá `.env.example` a `.env` y completá tu usuario y contraseña del SAC vos mismo
(Claude nunca escribe ni lee este archivo):

```bash
cp .env.example .env
```

```
SAC_BASE_URL=http://10.231.45.254:8080
SAC_USERNAME=tu_usuario
SAC_PASSWORD=tu_contraseña
```

## 2. Instalación

Cloná el repo donde quieras (la ruta la elige cada uno, no importa el nombre de la
carpeta) e instalá las dependencias en un entorno virtual propio:

```bash
git clone <url-del-repo> MCP-SAC
cd MCP-SAC
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Después necesitás la ruta absoluta del proyecto para el paso 3:

```bash
pwd
```

En todo lo que sigue, `<RUTA_PROYECTO>` es el resultado de ese `pwd` (ej.
`/home/tu_usuario/MCP-SAC` o `C:\Users\tu_usuario\MCP-SAC`).

## 3. Registrar el servidor

Es un servidor MCP estándar por stdio (`python sac_mcp.py`), así que cualquier cliente
que soporte MCP local lo puede levantar. Los pasos concretos varían según el cliente:

**Claude Code:**

```bash
claude mcp add sac-foca -- <RUTA_PROYECTO>/.venv/bin/python <RUTA_PROYECTO>/sac_mcp.py
```

(parado dentro de la carpeta del proyecto podés usar `"$(pwd)/.venv/bin/python" "$(pwd)/sac_mcp.py"`
en vez de escribir la ruta a mano).

**Claude Desktop (app de escritorio):** agregar en
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) o
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), dentro de `mcpServers`:

```json
"sac-foca": {
  "command": "<RUTA_PROYECTO>/.venv/bin/python",
  "args": ["<RUTA_PROYECTO>/sac_mcp.py"]
}
```

(en Windows, `command` sería `<RUTA_PROYECTO>\.venv\Scripts\python.exe`).

**Gemini CLI:** mismo patrón, en `~/.gemini/settings.json` (o `.gemini/settings.json`
del proyecto) dentro de `mcpServers`, con la misma estructura `command`/`args` que
Claude Desktop. La clave exacta puede variar según la versión — confirmar contra la
[documentación oficial de Gemini CLI](https://github.com/google-gemini/gemini-cli) si
no toma el servidor.

**ChatGPT:** al momento de escribir esto, la app de ChatGPT conecta "conectores" MCP
remotos (HTTP/SSE con OAuth), no procesos locales por stdio como este script — así que
no hay forma directa de engancharlo ahí. Si OpenAI habilita ejecutar MCP local desde
ChatGPT en el futuro, igual haría falta que esa app corra en una máquina con acceso a
la red interna (ver el requisito de red más arriba). Antes de darlo por imposible,
confirmar el estado actual en la documentación de OpenAI, porque este soporte cambia
seguido.

En todos los casos hay que reiniciar (la sesión, o la app entera) para que el cliente
tome el servidor — y cada vez que se agregue o cambie una herramienta en `sac_mcp.py`,
también hace falta reiniciar para que el proceso la vuelva a cargar.

## Herramientas de escritura — leer antes de usar

`agregar_nota_incidencia` y `reasignar_incidencia` **ejecutan directo contra
producción apenas se llaman** — no hay ningún paso de confirmación intermedio en el
servidor, a diferencia de las de lectura. La responsabilidad de confirmar con la persona
el ticket, el texto y/o el usuario destino, antes de invocarlas, es de quien las usa
(el agente).

- `agregar_nota_incidencia` siempre guarda la nota como interna. No existe forma de
  pedirle que la marque "Nota al Cliente" — se sacó del todo de la función a pedido
  explícito. El historial puede igual mostrar `nota_cliente: "SI"` para esa nota: es
  solo cómo se ve puertas adentro en el SAC Foca, no significa que el cliente la vea (esa
  visibilidad real la controla otro sistema aparte, que esta herramienta no toca).
- `reasignar_incidencia` no reabre un ticket cerrado ni cambia nada que no se le pida
  explícitamente: si no se pasa `nivel`, mantiene el actual; si no se pasa `criticidad`,
  mantiene la actual.
- `usuario` en `reasignar_incidencia` se busca por **nombre y apellido real** (ej.
  "Franco Strappazzon"), no por el usuario de login del SAC (ej. "fstrappazzon" no
  matchea nada).
- Ambas se probaron contra producción con cambios reales (no simulados) antes de darlas
  por terminadas — quedó un rastro esperable: el ticket 839080 tiene 3 notas de prueba
  permanentes (no hay forma de borrarlas), y el ticket 840497 tiene en su historial un
  ida y vuelta de reasignación (fstrappazzon → lmonclus → fstrappazzon).
- `crear_incidencia` crea un ticket real y visible para el cliente (según el `canal`
  elegido puede incluso corresponder a un contacto real) — no probada todavía contra
  producción con un ticket real de prueba; probarla a propósito antes de confiar en ella
  para un caso real. `canal`, `producto`, `software` y `diagnostico` son catálogos fijos
  extraídos del HTML del formulario (`CANALES`, `PRODUCTOS`, `SOFTWARES`, `DIAGNOSTICOS`
  en `sac_mcp.py`); `tipificacion` en cambio se consulta en el momento porque depende del
  catálogo vigente en el SAC. Si el cliente tiene facturas impagas (deshabilitado), no
  crea el ticket sin confirmación explícita. El número de ticket devuelto se infiere
  comparando la lista de incidencias abiertas del cliente antes y después de crear -
  puede fallar si hay carga concurrente sobre el mismo cliente.

## Cómo está armado / cómo agregar una herramienta

Todo vive en `sac_mcp.py`, sin paquetes ni módulos separados:

- Cada herramienta expuesta es una función con `@mcp.tool()` arriba (buscá `usuarios_por_nivel`,
  `consultar_incidencia`, etc. como ejemplo corto y largo respectivamente).
- Las funciones sin ese decorador (`_login`, `_get`, `_parsear_*`, `_resolver_*`) son
  helpers internos: manejan la sesión HTTP, el parseo de HTML con BeautifulSoup y la
  resolución de nombres a ids de los catálogos del SAC. No se exponen al chat.
- Para agregar una herramienta nueva: definí la función, decorala con `@mcp.tool()`,
  tipá bien los parámetros y el diccionario de retorno (eso es lo que el modelo lee
  para saber cómo usarla), y si necesita traer una página nueva del SAC agregale su
  propio `_parsear_...`. Reiniciá el cliente MCP para que la recoja.
- Si vas a tocar una herramienta de escritura (`agregar_nota_incidencia`,
  `reasignar_incidencia`, `crear_incidencia`), fijate primero la sección de arriba: no
  hay ambiente de pruebas, así que un cambio mal probado pega directo en producción.

## Notas técnicas

- Los parsers dependen de la estructura HTML de cada página del SAC (tablas `class="tabla"`
  o `class="tableshow"`, sin `<tbody>` en el HTML crudo — eso lo agrega el navegador solo
  al renderizar, hay que buscar los `<tr>` directos de `<table>`). Si el SAC cambia alguna
  de estas páginas, hay que ajustar el parser correspondiente en `sac_mcp.py`.
- `agregar_nota_incidencia` necesita mandar el texto codificado en Base64 (Latin-1) en un
  campo separado (`text64`), replicando el JS del sitio — mandar solo el texto plano en
  `msg` guarda la nota con el cuerpo vacío.
- Algunos endpoints del SAC mandan `Content-Type: charset=UTF-8` real en el header aunque
  el HTML diga `iso-8859-1` en el meta tag (y viceversa) — el helper `_get()` respeta el
  encoding que detecta la librería `requests` antes de asumir `iso-8859-1`.
- El campo "Producto" se excluye del resultado de `consultar_incidencia` a pedido
  explícito.

## Archivos

| Archivo | Qué hace |
|---|---|
| `sac_mcp.py` | El servidor MCP con las 8 herramientas |
| `.env` / `.env.example` | Credenciales del SAC (`.env` fuera de git) |
| `requirements.txt` | Dependencias (`mcp`, `requests`, `beautifulsoup4`, `python-dotenv`) |
| `.venv/` | Entorno virtual de Python |
