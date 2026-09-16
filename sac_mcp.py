"""Servidor MCP para el SAC (Foca Software, sección Usuario).

Se loguea contra el SAC por HTTP (sin navegador, sin sesión manual). La
mayoría de las herramientas son de solo lectura:

- consultar_incidencia: una incidencia puntual por número de ticket.
- incidencias_por_usuario: incidencias asignadas a un usuario del SAC.
- incidencias_por_cliente: incidencias de un cliente (por nombre o id).
- notas_incidencia: historial de notas y cambios de estado.

Tres son de escritura:

- agregar_nota_incidencia: agrega una nota/tarea a una incidencia. Ejecuta
  directo contra producción apenas se invoca - no hay confirmación de por
  medio en el servidor. Quien la use (el agente) tiene que confirmar con la
  persona el ticket y el texto antes de llamarla.
- reasignar_incidencia: reasigna una incidencia a otra persona/nivel/criticidad.
- crear_incidencia: crea una incidencia nueva para un cliente (botón "Nueva"
  del SAC). Ejecuta directo contra producción apenas se invoca - confirmar
  con la persona cliente, asunto, mensaje, canal, producto, software,
  diagnóstico y tipificación antes de llamarla.

  Nota sobre el campo "Nota al Cliente" / `nota_cliente` que se ve en
  `notas_incidencia`: acá siempre se manda `es_nota_visible="0"` (hace falta
  mandar ALGO para que la nota se guarde; sin el campo, el guardado falla en
  silencio). La columna NC del SAC Foca puede mostrar igual "SI" - es solo
  cómo se ve puertas adentro en esta interfaz, no refleja lo que el cliente
  ve. La visibilidad real para el cliente la controla otro sistema/visor
  aparte, que esta herramienta no toca.

Credenciales via variables de entorno (ver .env.example) - nunca hardcodear.
"""
import base64
import os

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

BASE_URL = os.environ.get("SAC_BASE_URL", "http://10.231.45.254:8080")
USERNAME = os.environ.get("SAC_USERNAME")
PASSWORD = os.environ.get("SAC_PASSWORD")

mcp = MCPServer("sac-foca")


def _login(session: requests.Session) -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Faltan credenciales. Definí SAC_USERNAME y SAC_PASSWORD en un "
            "archivo .env (ver .env.example)."
        )

    login_url = f"{BASE_URL}/usuario/entrar.php"
    payload = {
        "nick_usu": USERNAME,
        "contra_usu": PASSWORD,
        "enviar": "enviar",
    }
    resp = session.post(login_url, data=payload, timeout=15)
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("Login falló: el SAC no devolvió JSON. ¿Cambió el sitio?")

    if data.get("error"):
        raise RuntimeError(f"Login falló: {data['error']}")
    if data.get("result") is not True:
        raise RuntimeError("Login falló: revisá SAC_USERNAME/SAC_PASSWORD.")


def _texto_celda(td) -> str:
    return td.get_text(separator="\n", strip=True)


def _texto_sin_etiqueta(td) -> str:
    """Como _texto_celda, pero descarta la primera línea si es una etiqueta
    en negrita (ej. "Tipificación", "Fecha") en vez de contenido real."""
    partes = td.get_text(separator="\n", strip=True).split("\n", 1)
    primer_tag = td.find("b")
    if primer_tag is not None and len(partes) > 1 and partes[0] == primer_tag.get_text(strip=True):
        return partes[1]
    return "\n".join(partes)


def _filas(tabla) -> list:
    """Filas <tr> directas de una tabla. El HTML crudo del SAC no trae <tbody>
    (el navegador lo agrega solo en el DOM) - buscar ahí primero y si no,
    caer a los <tr> directos de <table>."""
    tbody = tabla.find("tbody")
    contenedor = tbody if tbody is not None else tabla
    return contenedor.find_all("tr", recursive=False)


def _get(session: requests.Session, path: str, params: dict) -> BeautifulSoup:
    resp = session.get(f"{BASE_URL}{path}", params=params, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "iso-8859-1"
    return BeautifulSoup(resp.text, "html.parser")


def _parsear_incidencia(soup: BeautifulSoup, ticket: str) -> dict:
    tabla = soup.find("table", class_="tabla")
    if tabla is None:
        raise RuntimeError(f"No se encontró la incidencia {ticket} (o la sesión no está autenticada).")

    filas = _filas(tabla)
    if len(filas) < 3:
        raise RuntimeError(f"No se encontró la incidencia {ticket}.")

    # filas[0] es el header; filas[1] los datos; filas[2] el reporte.
    datos = filas[1].find_all("td")
    if len(datos) < 12:
        raise RuntimeError(f"Estructura inesperada para la incidencia {ticket} (columnas: {len(datos)}).")

    resultado = {
        "ticket": _texto_celda(datos[0]),
        "asunto": _texto_celda(datos[1]),
        "creado": _texto_celda(datos[2]),
        "asignado": _texto_celda(datos[3]),
        "cliente": _texto_celda(datos[4]),
        "software": _texto_celda(datos[6]),
        "canal": _texto_celda(datos[7]),
        "diagnostico": _texto_celda(datos[8]),
        "categoria": _texto_celda(datos[9]),
        "estado": _texto_celda(datos[10]),
        "criticidad": _texto_celda(datos[11]),
    }

    if len(filas) >= 3:
        reporte_tds = filas[2].find_all("td")
        if len(reporte_tds) >= 6:
            resultado["reporte_cliente"] = _texto_celda(reporte_tds[1])
            resultado["tipificacion"] = _texto_sin_etiqueta(reporte_tds[2])
            resultado["fecha_creacion"] = _texto_sin_etiqueta(reporte_tds[4])
            resultado["fecha_actualizacion"] = _texto_celda(reporte_tds[5])

    return resultado


@mcp.tool()
def consultar_incidencia(ticket: str) -> dict:
    """Consulta una incidencia del SAC (Foca Software) por número de ticket.

    Devuelve un diccionario con: ticket, asunto, estado, criticidad, cliente,
    asignado, software, canal, diagnostico, categoria, fecha_creacion,
    fecha_actualizacion, reporte_cliente y tipificacion.
    """
    session = requests.Session()
    _login(session)
    soup = _get(session, "/usuario/informes/buscar_inc.php", {"mand_idd": ticket})
    return _parsear_incidencia(soup, ticket)


def _parsear_listado_usuario(soup: BeautifulSoup) -> list[dict]:
    tabla = soup.find("table", class_="tableshow")
    if tabla is None:
        return []

    filas = [
        tr for tr in _filas(tabla)
        if tr.find("td") is not None and tr.get("class") == ["tr"]
    ]

    incidencias = []
    for fila in filas:
        tds = fila.find_all("td", recursive=False)
        if len(tds) < 9:
            continue
        incidencias.append({
            "ticket": _texto_celda(tds[0]),
            "asunto": _texto_celda(tds[1]),
            "cliente": _texto_celda(tds[2]),
            "software": _texto_celda(tds[4]),
            "diagnostico_categoria": _texto_celda(tds[5]).replace("\n", " — "),
            "estado": _texto_celda(tds[6]),
            "criticidad": _texto_celda(tds[7]).strip(),
            "fecha_alta": _texto_celda(tds[8]),
        })
    return incidencias


def _info_paginas(soup: BeautifulSoup) -> str | None:
    texto = soup.get_text()
    import re
    m = re.search(r"Info\. Paginas:\s*([^\n]*)", texto)
    return m.group(1).strip() if m else None


@mcp.tool()
def incidencias_por_usuario(usuario: str, pagina: int = 1) -> dict:
    """Lista las incidencias asignadas a un usuario del SAC (ej. "lmonclus").

    Devuelve {"incidencias": [...], "info_paginas": "..."}. Cada incidencia
    trae: ticket, asunto, cliente, software, diagnostico_categoria, estado,
    criticidad, fecha_alta. Si hay más de una página, volvé a llamar con
    `pagina` incrementado (el texto de info_paginas indica el total).
    """
    session = requests.Session()
    _login(session)
    params = {"colaenv": usuario}
    if pagina > 1:
        params["_pagi_pg"] = pagina
    soup = _get(session, "/usuario/incidencias/listas/listaporusuarios.php", params)
    return {
        "incidencias": _parsear_listado_usuario(soup),
        "info_paginas": _info_paginas(soup),
    }


def _resolver_cliente_id(session: requests.Session, cliente: str) -> tuple[str, list[str]]:
    """Si `cliente` ya es numérico lo devuelve tal cual. Si es texto, busca
    coincidencias por nombre en el listado de clientes del SAC.

    Devuelve (id_o_vacio, lista_de_candidatos). Si hay una sola coincidencia,
    id_o_vacio trae el id y candidatos queda vacío. Si hay 0 o varias,
    id_o_vacio queda vacío y candidatos trae las opciones para desambiguar.
    """
    if cliente.strip().isdigit():
        return cliente.strip(), []

    soup = _get(session, "/usuario/informes/cliente/index.php", {})
    select = soup.find("select")
    if select is None:
        raise RuntimeError("No se pudo leer el listado de clientes del SAC.")

    objetivo = cliente.strip().lower()
    coincidencias = [
        (opt.get("value"), opt.get_text(strip=True))
        for opt in select.find_all("option")
        if opt.get("value") and objetivo in opt.get_text(strip=True).lower()
    ]

    if len(coincidencias) == 1:
        return coincidencias[0][0], []
    return "", [nombre for _id, nombre in coincidencias[:20]]


def _parsear_listado_cliente(soup: BeautifulSoup) -> list[dict]:
    tabla = soup.find("table", class_="tabla")
    if tabla is None:
        return []

    filas = _filas(tabla)
    incidencias = []
    for fila in filas[1:]:  # fila 0 es el header
        tds = fila.find_all("td", recursive=False)
        if len(tds) < 11:
            continue
        incidencias.append({
            "ticket": _texto_celda(tds[0]),
            "asunto": _texto_celda(tds[1]),
            "cliente": _texto_celda(tds[2]),
            "software": _texto_celda(tds[4]),
            "diagnostico": _texto_celda(tds[5]),
            "estado": _texto_celda(tds[6]),
            "criticidad": _texto_celda(tds[7]),
            "asignado": _texto_celda(tds[8]),
            "fecha_apertura": _texto_celda(tds[9]),
            "fecha_cierre": _texto_celda(tds[10]),
        })
    return incidencias


@mcp.tool()
def incidencias_por_cliente(cliente: str, pagina: int = 1, solo_abiertas: bool = True) -> dict:
    """Lista las incidencias de un cliente del SAC, por nombre o id.

    Si `cliente` es un nombre y hay varias coincidencias (o ninguna), devuelve
    {"candidatos": [...]} para que elijas cuál; volvé a llamar pasando el
    nombre exacto o el id numérico. `solo_abiertas=True` (default) filtra las
    incidencias con estado "Cerrada". Devuelve {"cliente_id", "incidencias",
    "info_paginas"}.
    """
    session = requests.Session()
    _login(session)

    cliente_id, candidatos = _resolver_cliente_id(session, cliente)
    if not cliente_id:
        if candidatos:
            return {"candidatos": candidatos}
        raise RuntimeError(f"No se encontró ningún cliente que coincida con \"{cliente}\".")

    params = {"mand_idd": "", "envcliente": cliente_id}
    if pagina > 1:
        params["_pagi_pg"] = pagina
    soup = _get(session, "/usuario/informes/buscar_inc.php", params)

    incidencias = _parsear_listado_cliente(soup)
    if solo_abiertas:
        incidencias = [i for i in incidencias if i["estado"].strip().lower() != "cerrada"]

    return {
        "cliente_id": cliente_id,
        "incidencias": incidencias,
        "info_paginas": _info_paginas(soup),
    }


def _parsear_notas(soup: BeautifulSoup) -> list[dict]:
    tablas = soup.find_all("table", class_="tabla")
    # tablas[0] es la barra de paginación de arriba; la tabla con las notas
    # es la primera que tiene más de una fila con 7 columnas.
    tabla_notas = None
    for t in tablas:
        filas = _filas(t)
        if len(filas) >= 2 and len(filas[1].find_all("td", recursive=False)) == 7:
            tabla_notas = t
            break
    if tabla_notas is None:
        return []

    filas = _filas(tabla_notas)
    notas = []
    for fila in filas[2:]:  # fila 0: título "Historial"; fila 1: header de columnas
        tds = fila.find_all("td", recursive=False)
        if len(tds) < 7:
            continue
        notas.append({
            "referencia": _texto_celda(tds[0]),
            "detalle": _texto_celda(tds[1]),
            "fecha": _texto_celda(tds[2]),
            "adjunto": _texto_celda(tds[3]),
            "nota_cliente": _texto_celda(tds[4]),
            "accion": _texto_celda(tds[5]),
            "realizado_por": _texto_celda(tds[6]),
        })
    return notas


@mcp.tool()
def notas_incidencia(ticket: str, pagina: int = 1) -> dict:
    """Historial completo de una incidencia del SAC: notas/tareas de los
    técnicos (comentarios internos, respuestas al cliente, cierres) Y los
    cambios de estado automáticos (ej. "de Abierta ---> a Detenida"), que es
    lo mismo que ves con el botón "Ver Historial Completo" en el SAC.

    Devuelve {"notas": [...], "info_paginas": "..."}. Cada nota trae:
    referencia (ej. "Comentario", "CERRADA:"), detalle (el texto de la nota o
    la descripción del cambio de estado), fecha, adjunto (Si/No), nota_cliente
    (si se le mostró al cliente en Autogestión), accion ("Nueva Tarea" o
    "Cambio de Estado") y realizado_por (usuario, o "mailbot" si fue
    automático). Las más recientes aparecen primero. Si hay más de una
    página, volvé a llamar con `pagina` incrementado (info_paginas indica el
    total).
    """
    session = requests.Session()
    _login(session)
    params = {"colaenv": "", "mand_idd": ticket, "clientes": "", "tarea": 7}
    if pagina > 1:
        params["_pagi_pg"] = pagina
    soup = _get(session, "/usuario/incidencias/listas/listacolas_hist.php", params)
    return {
        "notas": _parsear_notas(soup),
        "info_paginas": _info_paginas(soup),
    }


def _encode_latin1_base64(texto: str) -> str:
    """Replica exacta de encodeLatin1ToBase64() del JS del SAC: el botón real
    corre esto antes de enviar y el servidor guarda el cuerpo desde este
    campo (`text64`), no desde `msg` en texto plano - de ahí que mandar solo
    `msg` guarde la nota con el cuerpo vacío."""
    texto = texto.replace("–", "-")
    crudo = bytes(ord(c) if ord(c) <= 0xFF else ord("?") for c in texto)
    return base64.b64encode(crudo).decode("ascii")


def _campos_ocultos_nueva_tarea(session: requests.Session, ticket: str) -> dict:
    """Trae el formulario real de "Nueva Tarea" (el que carga por AJAX el
    botón del mismo nombre) y devuelve sus campos ocultos - son específicos
    de cada ticket (propietario, producto, software, canal, diagnóstico,
    etc.), no se pueden hardcodear."""
    soup = _get(
        session,
        "/usuario/incidencias/listas/listacolas_consult.php",
        {"colaenv": "2", "mand_idd": ticket, "tarea": "1", "subtarea": "0"},
    )
    form = soup.find("form", {"id": "formulario"})
    if form is None:
        raise RuntimeError(f"No se pudo abrir el formulario de Nueva Tarea para {ticket}.")

    campos = {
        inp.get("name"): inp.get("value", "")
        for inp in form.find_all("input", {"type": "hidden"})
        if inp.get("name")
    }
    if "mand_idd" not in campos:
        raise RuntimeError(f"El formulario de Nueva Tarea para {ticket} no trae los campos esperados.")
    return campos


@mcp.tool()
def agregar_nota_incidencia(
    ticket: str,
    tarea: str,
    referencia: str = "Comentario",
) -> dict:
    """Agrega una nota/tarea a una incidencia del SAC - EJECUTA EN PRODUCCIÓN
    apenas se llama, sin ningún paso de confirmación intermedio. Quien use
    esta herramienta tiene que confirmar con la persona el ticket y el texto
    antes de invocarla.

    No tiene forma de marcarla como "Nota al Cliente" (a pedido explícito de
    la persona dueña de este proyecto). El historial (notas_incidencia) puede
    igual mostrar esa nota con nota_cliente="SI" - es solo cómo se ve puertas
    adentro en el SAC Foca; no significa que el cliente la vea, eso lo
    controla otro sistema aparte que esta herramienta no toca.

    `tarea` es el texto de la nota (mínimo 5 caracteres, igual que exige el
    SAC). `referencia` es la etiqueta corta que se le pone (por defecto
    "Comentario", también mínimo 5 caracteres).

    Si la incidencia está Cerrada, esta función no la reabre sola: devuelve
    un error pidiendo confirmar explícitamente que se quiere reabrir (mismo
    comportamiento que el cartel de confirmación del SAC).

    Devuelve {"ok": True} si se guardó.
    """
    if len(referencia.strip()) < 5:
        raise RuntimeError("La referencia debe tener al menos 5 caracteres.")
    if len(tarea.strip()) < 5:
        raise RuntimeError("La tarea debe tener al menos 5 caracteres.")

    session = requests.Session()
    _login(session)

    estado_actual = _parsear_incidencia(
        _get(session, "/usuario/informes/buscar_inc.php", {"mand_idd": ticket}), ticket
    )["estado"]
    if estado_actual.strip().lower() == "cerrada":
        raise RuntimeError(
            f"La incidencia {ticket} está Cerrada. El SAC pediría confirmar la "
            "reapertura antes de agregar la nota - preguntale a la persona si "
            "corresponde reabrirla antes de reintentar."
        )

    campos = _campos_ocultos_nueva_tarea(session, ticket)
    payload = {
        **campos,
        "sbj": referencia,
        "msg": tarea,
        "text64": _encode_latin1_base64(tarea),
        "es_nota_visible": "0",  # nunca "Nota al Cliente" - a pedido explícito
        "postback": "Guardar Tarea",
    }

    resp = session.post(
        f"{BASE_URL}/usuario/incidencias/listas/listausuarios.php",
        data=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return {"ok": True}


# Niveles/colas del SAC - lista fija (no depende del ticket), extraída del
# <select name="paises"> que devuelve combos.php. Si el SAC agrega o saca un
# nivel, hay que volver a extraerla de ahí.
NIVELES = {
    "1º Nivel EESS": "2", "1º Nivel Retail": "25", "2N Analisis Mejora": "45",
    "2º Nivel EESS": "3", "2º Nivel Retail": "33", "3º Nivel EESS": "4",
    "3º Nivel Retail": "34", "4º Nivel EESS": "51", "4º Nivel Retail": "54",
    "5º Nivel EESS": "52", "5º Nivel Retail": "55", "6º Nivel EESS": "53",
    "6º Nivel Retail": "56", "ALR": "15", "ANALISIS": "68",
    "Act. URGENTES": "71", "Actualizacion ALR": "62", "Actualizacion Error": "5",
    "Actualizacion Retail": "39", "Actualización Mejora": "61",
    "Administracion": "72", "Capacitacion": "40", "Comercial": "48",
    "Cotizadas": "13", "DESARROLLO": "14", "Desarrollo (Comp)": "26",
    "Desarrollo (ERetail)": "28", "Desarrollo (EYPF)": "7",
    "Desarrollo (E_EESS)": "9", "Desarrollo (E_Open)": "8",
    "Desarrollo (MPart)": "29", "Desarrollo (MRetail)": "27",
    "Desarrollo (MYPF)": "10", "Desarrollo (M_EESS)": "12",
    "Desarrollo (M_Open)": "11", "Desarrollo PosWeb": "74",
    "Desarrollo(ERetPos)": "42", "Desarrollo(MRetPos)": "41",
    "Enviar mail": "80", "Envio de ventas": "81", "Implement Funciones": "67",
    "Implement Nueva": "76", "Implement Pago Elect": "66",
    "Implement Sistema": "23", "Implement TRX": "65", "Mantenimiento Open": "24",
    "Mejora Continua": "70", "Piloto Error": "36", "Piloto Mejora": "64",
    "Relacionada Error": "37", "Relacionada Mejora": "63",
    "Revision MP y apps": "73", "TESTING": "58", "Tareas internas": "57",
    "Testing (ERetail)": "30", "Testing (ERetailPos)": "43",
    "Testing (E_EESS)": "17", "Testing (E_Open)": "16", "Testing (MRetail)": "31",
    "Testing (MRetailPos)": "44", "Testing (M_EESS)": "19",
    "Testing (M_Open)": "18", "Testing a Liberar": "32",
}

# Criticidad - mapeo fijo visto en el <select name="nueva_prioriadad"> de combos.php.
CRITICIDADES = {"baja": "3", "media": "2", "alta": "1", "critico": "4", "crítico": "4"}

# Catálogos fijos del formulario "Nueva Incidencia" (incidencias/incidencias.php) -
# son radio buttons estáticos en el HTML, no dependen del cliente ni se cargan por
# AJAX. Si el SAC agrega o saca una opción, hay que volver a extraerla de ahí.
CANALES = {
    "Telefónico Avanzado": "1", "Telefónico Call Center": "2", "Email": "3",
    "Chat": "5", "Personalizado": "6", "Guardia": "7", "WhatsApp": "8",
    "Debo Autogestión": "9",
}

PRODUCTOS = {
    "DEBO ESTACIONES": "1", "DEBO RETAIL": "2", "NAVI": "3", "Otros": "4", "SAC": "5",
}

SOFTWARES = {
    "BO": "1", "HO": "2", "POS": "3", "Win Server": "4", "Win User": "5",
    "Antivirus": "6", "SQL": "7", "Otro": "8", "SAC": "9", "DEBO_CLOUD": "10",
}

# "Diagnóstico" - una sola lista plana (los encabezados Módulo/Hardware/
# Conectividad/Servicios son puramente visuales en el SAC, no un campo aparte).
DIAGNOSTICOS = {
    "Administración": "15", "Bancos": "16", "Contable": "17", "Proveedores": "18",
    "Tarjetas": "19", "Valores": "20", "Tienda": "21", "Playa": "22",
    "Parametros": "23", "Liq de Haberes": "24", "Restaurante": "25",
    "Impresion": "26", "Distribuidor": "27", "iDebo": "28", "Herramientas": "29",
    "Sueldos": "30", "Salón ventas": "54", "Tablet": "59", "SAC": "61",
    "Ventas": "63", "Reporteador Web Retail": "64", "Creditos Retail": "65",
    "Despachos": "66", "Vouchers": "68", "Informes": "70", "ClubHouse": "71",
    "App para Dispositivos moviles": "72", "DEBO_FE": "74", "APIS VARIAS": "78",
    "POS Web Tienda": "79", "POS Web Playa": "80", "PoS Retail": "81",
    "PoS Ibaceta": "82", "Power BI": "83", "POS Web Resto": "86",
    "POS Web Kiosco Digital": "87", "CLOVER": "88", "Debo Cloud": "89",
    "Servidor": "31", "Terminal": "32", "Impresora": "33", "UPS": "34",
    "Disco Rigido": "35", "Monitor": "37", "Otro": "38", "Fiscal": "53",
    "Hub": "39", "Cables": "40", "Placas": "41", "Switch": "42",
    "Implementación": "43", "Capacitación": "44", "Re Instalación": "45",
    "Licencia Adicional": "46", "Otros": "47", "Backup": "67", "YPF": "76",
    "Shell": "77", "Fidelidad Shell Box": "84",
}


def _resolver_o_candidatos(campo: str, valor: str, opciones: dict) -> tuple[str, dict | None]:
    """Wrapper de _resolver_por_nombre para los campos de crear_incidencia:
    devuelve (id, None) si resolvió, o ("", {"candidatos_<campo>": [...]})
    para devolver tal cual desde la tool en vez de adivinar."""
    id_, candidatos = _resolver_por_nombre(valor, opciones)
    if id_:
        return id_, None
    if candidatos:
        return "", {f"candidatos_{campo}": candidatos}
    raise RuntimeError(f"No se encontró ninguna opción de {campo} que coincida con \"{valor}\".")


def _cliente_habilitado(session: requests.Session, cliente_id: str) -> tuple[bool, str]:
    """Replica el chequeo que hace cliente() en incidencias/index.php antes de
    abrir el formulario de Nueva Incidencia: si el cliente tiene facturas
    impagas (habilitado=0 en Gestión Anexo Cliente), el SAC no deja crear la
    incidencia. Devuelve (habilitado, novedades)."""
    resp = session.post(
        f"{BASE_URL}/usuario/Gestion_Anexo_Cliente/Controller/ControllerGestorAnexoCliente.php",
        data={"FUNC": 1, "COD_CLI": cliente_id},
        timeout=15,
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"No se pudo verificar el estado del cliente {cliente_id} en el SAC.")
    anexo = (data.get("result") or {}).get("_Anexo") or {}
    habilitado = str(anexo.get("habilitado", "1")) != "0"
    return habilitado, anexo.get("novedades") or ""


def _campos_form_nueva_incidencia(session: requests.Session, cliente_id: str) -> tuple[dict, dict, str]:
    """Trae el formulario real de "Nueva Incidencia" para un cliente
    (incidencias/incidencias.php?p=<cliente_id>) y devuelve (campos_ocultos,
    tipificaciones, nombre_cliente). `tipificaciones` es {texto: id} - depende
    del catálogo vigente en el SAC, no se puede hardcodear como los otros
    campos del formulario."""
    soup = _get(session, "/usuario/incidencias/incidencias.php", {"p": cliente_id})
    form = soup.find("form", {"id": "formulario"})
    if form is None:
        raise RuntimeError(f"No se pudo abrir el formulario de Nueva Incidencia para el cliente {cliente_id}.")

    campos = {
        inp.get("name"): inp.get("value", "")
        for inp in form.find_all("input", {"type": "hidden"})
        if inp.get("name")
    }
    if "codigo_cli" not in campos:
        raise RuntimeError(f"El formulario de Nueva Incidencia para el cliente {cliente_id} no trae los campos esperados.")

    select_tip = form.find(id="tipificacion")
    tipificaciones = {
        opt.get_text(strip=True): opt.get("value")
        for opt in (select_tip.find_all("option") if select_tip is not None else [])
        if opt.get("value") and opt.get("value") != "0"
    }

    from_input = form.find("input", {"name": "from"})
    cliente_nombre = from_input.get("value", "") if from_input is not None else ""

    return campos, tipificaciones, cliente_nombre


def _tickets_abiertos_cliente(session: requests.Session, cliente_id: str) -> set:
    soup = _get(session, "/usuario/informes/buscar_inc.php", {"mand_idd": "", "envcliente": cliente_id})
    return {i["ticket"] for i in _parsear_listado_cliente(soup)}


@mcp.tool()
def crear_incidencia(
    cliente: str,
    asunto: str,
    mensaje: str,
    canal: str = "Personalizado",
    diagnostico: str = "Administración",
    producto: str = "Debo Estaciones",
    software: str = "BO",
    tipificacion: str = "indole administrativa",
) -> dict:
    """Crea una incidencia nueva en el SAC (Foca Software) para un cliente -
    es el botón "Nueva" de la sección Incidencias. EJECUTA EN PRODUCCIÓN
    apenas se llama, sin ningún paso de confirmación intermedio. A diferencia
    de una nota, esto genera un ticket nuevo real - según el `canal` elegido
    puede corresponder a un contacto real del cliente. Quien use esta
    herramienta tiene que confirmar con la persona los datos que sí varían
    caso a caso (cliente, asunto, mensaje) antes de invocarla; `canal`,
    `diagnostico`, `producto`, `software` y `tipificacion` tienen default y
    NO hace falta preguntarlos salvo que la persona aclare algo distinto de
    entrada.

    `cliente`: nombre o id (ver incidencias_por_cliente para resolverlo).
    `asunto`: 5 a 128 caracteres.
    `mensaje`: cuerpo del reporte, mínimo 5 caracteres.
    `canal`: nombre (o parte) o id - ver CANALES. Default "Personalizado" -
      no preguntar, usar el default salvo que se aclare otro de antemano
      (ej. "email", "whatsapp", "telefonico call center").
    `diagnostico`: nombre o id - ver DIAGNOSTICOS. Default "Administración" -
      no preguntar, usar el default salvo que se aclare otro de antemano
      (ej. "playa", "impresora").
    `producto`: nombre o id - ver PRODUCTOS. Default "Debo Estaciones" - no
      preguntar, usar el default salvo que se aclare otro de antemano (ej.
      "debo retail", "navi").
    `software`: nombre o id - ver SOFTWARES. Default "BO" - no preguntar,
      usar el default salvo que se aclare otro de antemano (ej. "pos", "sql").
    `tipificacion`: nombre (o parte) o id, del catálogo vigente en el SAC (se
      consulta en el momento, no es una lista fija). Default resuelve a
      "Incidencias de índole administrativa" - no preguntar, usar el default
      salvo que se aclare otra de antemano.

    Si `cliente`, `canal`, `producto`, `software`, `diagnostico` o
    `tipificacion` no matchea una única opción, devuelve
    {"candidatos_<campo>": [...]} en vez de adivinar - volvé a llamar con el
    nombre exacto o el id.

    Si el cliente tiene facturas impagas (deshabilitado en el SAC), no crea
    la incidencia: devuelve un error pidiendo confirmar explícitamente con la
    persona antes de insistir (mismo comportamiento que el cartel del SAC).

    Devuelve {"ok": True, "ticket": "...", "cliente": "..."} con el número
    del ticket creado. Si se guardó pero no se pudo confirmar el número
    automáticamente, devuelve {"ok": True, "ticket": None, "aviso": "..."} -
    revisar con incidencias_por_cliente.
    """
    asunto = asunto.strip()
    mensaje = mensaje.strip()
    if len(asunto) < 5 or len(asunto) > 128:
        raise RuntimeError("El asunto debe tener entre 5 y 128 caracteres.")
    if len(mensaje) < 5:
        raise RuntimeError("El mensaje debe tener al menos 5 caracteres.")

    canal_id, err = _resolver_o_candidatos("canal", canal, CANALES)
    if err:
        return err
    producto_id, err = _resolver_o_candidatos("producto", producto, PRODUCTOS)
    if err:
        return err
    software_id, err = _resolver_o_candidatos("software", software, SOFTWARES)
    if err:
        return err
    diagnostico_id, err = _resolver_o_candidatos("diagnostico", diagnostico, DIAGNOSTICOS)
    if err:
        return err

    session = requests.Session()
    _login(session)

    cliente_id, candidatos_cliente = _resolver_cliente_id(session, cliente)
    if not cliente_id:
        if candidatos_cliente:
            return {"candidatos_cliente": candidatos_cliente}
        raise RuntimeError(f"No se encontró ningún cliente que coincida con \"{cliente}\".")

    habilitado, novedades = _cliente_habilitado(session, cliente_id)
    if not habilitado:
        raise RuntimeError(
            f"El cliente {cliente_id} figura deshabilitado en el SAC (facturas "
            "impagas). El SAC pediría confirmar antes de crear la incidencia - "
            "preguntale a la persona si corresponde insistir." +
            (f" Novedades: {novedades}" if novedades else "")
        )

    campos, tipificaciones, cliente_nombre = _campos_form_nueva_incidencia(session, cliente_id)

    tip_id, candidatos_tip = _resolver_por_nombre(tipificacion, tipificaciones)
    if not tip_id:
        if candidatos_tip:
            return {"candidatos_tipificacion": candidatos_tip}
        raise RuntimeError(f"No se encontró ninguna tipificación que coincida con \"{tipificacion}\".")

    tickets_antes = _tickets_abiertos_cliente(session, cliente_id)

    payload = {
        **campos,
        "from": cliente_nombre,
        "sbj": asunto,
        "tipificacion": tip_id,
        "tipif": tip_id,
        "msg": mensaje,
        "text64": _encode_latin1_base64(mensaje),
        "canal": canal_id,
        "sist": producto_id,
        "soft": software_id,
        "selec": diagnostico_id,
        "postback": "Guardar Ticket",
    }

    resp = session.post(f"{BASE_URL}/usuario/incidencias/incidencias.php", data=payload, timeout=15)
    resp.raise_for_status()

    tickets_despues = _tickets_abiertos_cliente(session, cliente_id)
    nuevos = tickets_despues - tickets_antes
    if len(nuevos) == 1:
        return {"ok": True, "ticket": next(iter(nuevos)), "cliente": cliente_nombre}
    return {
        "ok": True,
        "ticket": None,
        "cliente": cliente_nombre,
        "aviso": (
            "Se envió la incidencia pero no se pudo confirmar el número de "
            "ticket automáticamente - revisar con incidencias_por_cliente."
        ),
    }


def _resolver_por_nombre(nombre_o_id: str, opciones: dict) -> tuple[str, list[str]]:
    """Busca `nombre_o_id` (id exacto, nombre exacto case-insensitive, o
    substring case-insensitive del nombre) en `opciones` ({nombre: id}).
    Devuelve (id_o_vacio, candidatos).

    Un nombre exacto gana siempre, aunque también matchee como substring de
    otro nombre (ej. "BO" es substring de "DEBO_CLOUD" - sin este chequeo,
    pasar "BO" devolvía {"candidatos": ["BO", "DEBO_CLOUD"]} en vez de
    resolver directo a BO)."""
    if nombre_o_id.strip() in opciones.values():
        return nombre_o_id.strip(), []

    objetivo = nombre_o_id.strip().lower()

    exactas = [(n, i) for n, i in opciones.items() if n.lower() == objetivo]
    if len(exactas) == 1:
        return exactas[0][1], []

    coincidencias = [(n, i) for n, i in opciones.items() if objetivo in n.lower()]
    if len(coincidencias) == 1:
        return coincidencias[0][1], []
    return "", [n for n, _i in coincidencias[:20]]


def _usuarios_de_nivel(session: requests.Session, nivel_id: str) -> list[dict]:
    soup = _get(
        session,
        "/usuario/incidencias/tareas/combo/select_dependientes_proceso.php",
        {"select": "estados", "opcion": nivel_id},
    )
    select = soup.find("select")
    if select is None:
        return []
    return [
        {"id": opt.get("value"), "nombre": opt.get_text(strip=True)}
        for opt in select.find_all("option")
        if opt.get("value") and opt.get("value") != "0"
    ]


@mcp.tool()
def usuarios_por_nivel(nivel: str) -> dict:
    """Lista las personas que pertenecen a un nivel/cola del SAC (ej. "1º Nivel
    EESS", "DESARROLLO"), con su id. Útil para saber a quién asignarle un
    ticket antes de usar reasignar_incidencia. No modifica nada.

    `nivel` puede ser el nombre (o una parte de él) o el id numérico. Si el
    nombre matchea varios niveles, devuelve {"candidatos": [...]} en vez de
    adivinar.

    Devuelve {"nivel": "...", "nivel_id": "...", "usuarios": [{"id","nombre"}]}.
    """
    nivel_id, candidatos = _resolver_por_nombre(nivel, NIVELES)
    if not nivel_id:
        if candidatos:
            return {"candidatos": candidatos}
        raise RuntimeError(f"No se encontró ningún nivel que coincida con \"{nivel}\".")

    session = requests.Session()
    _login(session)
    nombre_nivel = next((n for n, i in NIVELES.items() if i == nivel_id), nivel_id)
    return {
        "nivel": nombre_nivel,
        "nivel_id": nivel_id,
        "usuarios": _usuarios_de_nivel(session, nivel_id),
    }


def _campos_ocultos_asignar(session: requests.Session, ticket: str) -> dict:
    soup = _get(
        session,
        "/usuario/incidencias/tareas/combo/combos.php",
        {"mand_idd": ticket, "donde": "listacolas", "desde_consulta": "1"},
    )
    form = soup.find("form", {"id": "formulario"})
    if form is None:
        raise RuntimeError(f"No se pudo abrir el formulario de Asignar para {ticket}.")

    campos = {
        inp.get("name"): inp.get("value", "")
        for inp in form.find_all("input", {"type": "hidden"})
        if inp.get("name")
    }
    if "mand_idd" not in campos:
        raise RuntimeError(f"El formulario de Asignar para {ticket} no trae los campos esperados.")
    return campos


@mcp.tool()
def reasignar_incidencia(
    ticket: str,
    usuario: str,
    nivel: str | None = None,
    criticidad: str | None = None,
    referencia: str = "Cambio de Propietario (Asignar)",
) -> dict:
    """Reasigna una incidencia del SAC a otra persona - EJECUTA EN PRODUCCIÓN
    apenas se llama, sin confirmación intermedia. Quien use esta herramienta
    tiene que confirmar con la persona el ticket, el nivel y el usuario
    exactos antes de invocarla.

    `usuario` es obligatorio: nombre (o parte de él, ej. "lmonclus" o
    "Lisandro Monclus") o id numérico. `nivel` es opcional - si no se pasa,
    se mantiene el nivel/cola actual del ticket; si se pasa, tiene que ser un
    nivel válido (ver usuarios_por_nivel) y el usuario tiene que pertenecer a
    ESE nivel (la lista de personas depende del nivel elegido). `criticidad`
    opcional: "baja", "media", "alta" o "critico" - si no se pasa, se
    mantiene la actual.

    Si el nombre de usuario matchea varias personas del nivel resuelto,
    devuelve {"candidatos": [...]} en vez de adivinar.

    Devuelve {"ok": True, "nivel": ..., "usuario": ...} si se guardó.
    """
    session = requests.Session()
    _login(session)

    campos = _campos_ocultos_asignar(session, ticket)
    nivel_id = campos.get("id_cola", "")
    if nivel:
        nivel_id, candidatos_nivel = _resolver_por_nombre(nivel, NIVELES)
        if not nivel_id:
            if candidatos_nivel:
                return {"candidatos_nivel": candidatos_nivel}
            raise RuntimeError(f"No se encontró ningún nivel que coincida con \"{nivel}\".")

    usuarios_nivel = _usuarios_de_nivel(session, nivel_id)
    opciones_usuario = {u["nombre"]: u["id"] for u in usuarios_nivel}
    usuario_id, candidatos_usuario = _resolver_por_nombre(usuario, opciones_usuario)
    if not usuario_id:
        if candidatos_usuario:
            return {"candidatos": candidatos_usuario}
        raise RuntimeError(
            f"No se encontró ningún usuario que coincida con \"{usuario}\" "
            f"en el nivel resuelto ({nivel_id})."
        )

    criticidad_id = campos.get("prioridad", "3")
    if criticidad:
        criticidad_id = CRITICIDADES.get(criticidad.strip().lower(), criticidad_id)

    payload = {
        **campos,
        "paises": nivel_id,
        "estados": usuario_id,
        "nueva_prioriadad": criticidad_id,
        "sbj": referencia,
        "postback_tareas": "Guardar Tarea",
    }

    resp = session.post(
        f"{BASE_URL}/usuario/incidencias/tareas/combo/combos.php",
        data=payload,
        timeout=15,
    )
    resp.raise_for_status()

    nombre_nivel = next((n for n, i in NIVELES.items() if i == nivel_id), nivel_id)
    nombre_usuario = next((n for n, i in opciones_usuario.items() if i == usuario_id), usuario_id)
    return {"ok": True, "nivel": nombre_nivel, "usuario": nombre_usuario}


if __name__ == "__main__":
    mcp.run()
