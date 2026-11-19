# World X | Hardcore — actualización del mercado

Extensión integrada con el bot entregado en WorldX-Bot-Hosting.zip. Python 3.12, un proceso, SQLite compartida. El archivo de entrada es `main.py` (también admite `bot.py`). No es un cambio desplegado en tu servidor: el paquete debe instalarse y configurarse.

## 1. Arquitectura y datos

La interfaz Discord vive en `wx_market/ui.py`; las reglas, reservas y transacciones en `wx_market/domain.py`; el esquema incremental en `wx_market/schema.sql`. `bot.py` instala el sistema después de economía y restaura la vista persistente al iniciar.

Se reutilizan `eco_accounts`, `eco_transactions`, `eco_entries`, `eco_profiles` y los pagos de misiones del bot anterior. No se crea otra moneda ni otra cartera. Los salarios, aldeas, nacimientos y permisos de los módulos anteriores conservan su configuración.

| Tabla nueva | Función |
|---|---|
| market_items / market_tags | Catálogo, filtros e identidad estable del artículo |
| market_profiles | Ficha de requisitos verificada por otro miembro del staff |
| market_requests | Solicitud, consentimiento, precio congelado, evidencias y resolución |
| market_holds | Dinero reservado, todavía no descontado |
| market_inventory | Una unidad por adquisición, propietario, origen, fecha y bloqueo |
| market_loans | Principal, devolución acumulada y vencimiento |
| market_audit | Registro inmutable desde el bot y cola de publicación a Discord |
| market_versions | Versión del esquema incremental |

El esquema completo está en `wx_market/schema.sql`; el esquema económico anterior sigue en `economy_domain.py`. Hay índices para filtros, precios, etiquetas, inventarios, pendientes y movimientos contables. Las listas consultan ocho filas por página. La búsqueda de fragmentos de nombre usa `instr`; con este catálogo pequeño evita una dependencia FTS.

Una resolución usa `BEGIN IMMEDIATE`: revalida → libera su reserva → transfiere dinero → entrega/mueve el permiso → guarda resolución y auditoría → commit. Un error revierte todo. Un segundo clic no repite una compra resuelta. Todas las vías antiguas que debitan Ryo pasan por un control de reservas. SQLite usa WAL, caché de 2 MiB y timeout de bloqueo de 5 s. No ejecutar varias copias del bot con la misma base.

La publicación de mensajes ocurre después del commit. Si Discord falla, se reintenta cada 30 segundos. Puede repetirse un mensaje de log tras un cierre entre el envío y su confirmación, pero no su operación económica. Los botones conservan su funcionamiento después de reiniciar.

## 2. Catálogo y fidelidad al HTML

`config/market/catalog_html.json` contiene **612 filas** importadas del HTML V3 proporcionado. Cada artículo conserva `source_values`, el número de fila y SHA-256 del documento. Precios, nivel, stats, coste del juego y notas originales se pueden contrastar allí. La copia de referencia está en `docs/fuentes/catalogo_v3.html`.

`config/market/catalog_additions.json` incluye cuatro entradas de la lámina de Paper y 13 propuestas: cuatro permisos raciales y nueve afinidades de jinchūriki. Las propuestas raciales/jinchūriki están **desactivadas** (`enabled:false`): sus precios y progresión de boss no se presentan como valores existentes en el HTML. Para habilitarlas, fija sus requisitos y precios, cambia `enabled` y recarga. La afinidad sugerida de cada bijuu es una propuesta de rol, no una equivalencia oficial entre bloodlines.

Las 11 categorías están representadas. Kenjutsu/estilos se incluyen como permisos dentro de objetos; las invocaciones están dentro de subjutsus. El tipo distingue permiso y objeto físico. Todas las entradas tienen emoji; `image_url` acepta una URL HTTPS opcional. No se descargan imágenes del catálogo al servidor.

**Excepciones expresas al HTML:** Body Dodge cuesta 5.000.000 Ryo, o 500.000 para Kenichi/Ryuji Kenichi; las prohibiciones, exclusividades y descuentos indicados por el servidor se superponen a la fuente. `server_overrides` identifica los cambios de precio/prohibición. Los descuentos son excluyentes: se toma el menor precio elegible y se aplica ×3 una sola vez si corresponde. El permiso y los costes del juego siguen siendo independientes.

El ×3 **solo permite estar fuera del rango base**. No elimina nivel, stats, linaje, elemento, nombramiento jinchūriki, permiso racial ni desbloqueo nativo. Esto conserva la regla de las láminas anteriores. Un artículo prohibido no se vende a ningún precio. Para cambiar esa política de forma consciente, edita `policy` en el requisito correspondiente.

Cuando el HTML dice un intervalo, el extremo inferior se usa como mínimo; progresar más allá del extremo superior no impide comprar. `Jounin Sensei / Kage` admite cualquiera de esas alternativas (el umbral inferior). Los campos `NIN / CHI` exigen ambos valores; el texto original permanece disponible al revisor. `rank_level` es una jerarquía de acceso, no los puntos totales del rango: Academia 0, Genin 1, Chunin 2, Jounin 3, Jounin Sensei/Subkage/Sannin 4, Kage 5.

### Fichas y condiciones manuales

El ejemplo editable es `config/market/profile.example.json`. Un revisor diferente del jugador carga su ficha con `/mercado perfil usuario archivo`. No puede aprobar su propia ficha. Caduca en 30 días, configurable. Las claves de elementos y bloodlines deben coincidir exactamente con los IDs del catálogo.

Cada fila del HTML tiene una condición `unlock:<id-del-artículo>`. El staff añade esa etiqueta únicamente tras comprobar sus condiciones nativas, pergaminos, misiones y requisitos expresados en prosa. No se afirma que el bot lea automáticamente fotos o el estado de Roblox. Las condiciones estructuradas de rango, nivel, estadísticas, linaje y elemento se validan además mediante código. Antes de utilizar la tienda, prepara la ficha verificada; la solicitud posterior también exige pruebas para el revisor de la compra.

Gen 1 exige `jinchuuriki:designado` y la etiqueta del bijuu concreto (por ejemplo `jinchuuriki:kor`). Solo el staff que gestione los nombramientos debe incluirlas. Gen 2/3 y Diez Colas no están disponibles. El control de nombramientos entre aldeas es administrativo: no se ha inventado un reparto automático. Los permisos raciales vinculan shark con wanziame/hoshigaki, snake con cobra y puppet con minakami, más etiquetas de boss/autorización para las propuestas.

`unique:true` significa una unidad activa del permiso por jugador; no representa el cupo natural mundial de un bloodline. Los cupos únicos de nacimiento siguen en el sistema de asignación existente y no se venden ni se modifican aquí.

## 3. Comandos e interfaz

| Comando `/mercado …` | Uso |
|---|---|
| tienda | Menú de categorías, rango/tipo, selección de ítem y páginas |
| buscar | Nombre, categoría, tipo, raza, bloodline, elemento, rango y precio |
| comprar | Autocompletar un artículo y abrir su ficha con botón de compra |
| inventario | Permisos/objetos adquiridos y su procedencia |
| historial | Movimientos de Ryo y reservas; staff puede consultar a otro usuario |
| solicitudes / solicitud | Listar o abrir una solicitud y aceptar/cancelar ofertas |
| evidencia | Enlazar una captura PNG/JPG/WEBP al canal privado de revisión |
| tradeo | Vender/ceder un permiso u objeto propio por Ryo; precio 0 permite cesión |
| prestar / devolver / prestamos | Oferta de préstamo, devolución parcial/total y listado |
| ensenar | Enseñar una técnica propia con consentimiento del alumno y revisión |
| usar | Consumir una unidad de un consumible/lanzable |
| perfil / ver_perfil | Verificación de requisitos por staff / consulta personal |
| logs | Auditoría paginada, solo staff |
| recargar | Administración: validar JSON y recargar el catálogo |
| diagnostico | Administración: comprobar canales y permisos |

Compra: ver artículo → solicitar → reserva de saldo → aportar capturas → staff aprueba/rechaza con motivo. Solo la aprobación descuenta saldo y entrega el objeto. Rechazo, cancelación y caducidad liberan las reservas. Máximo cinco pendientes por solicitante y caducidad de 72 horas por defecto.

Tradeo: origen propietario y destinatario válido → reserva del objeto → destinatario acepta desde `/mercado solicitud` → reserva del dinero → ambos aportan evidencia → staff revisa en el canal de tradeos. Se transfiere la propiedad, no se duplica. El destinatario y el origen deben cumplir todos los requisitos sin usar ×3. Esta versión implementa permiso/objeto por Ryo, no trueque de dos objetos.

Enseñanza: ambos deben cumplir requisitos, el alumno acepta y aporta pruebas junto al maestro. La tarifa `teaching_fee`, limitada al precio personal elegible del alumno para conservar sus descuentos, se transfiere al maestro y este conserva su técnica. Solo artículos `teachable:true` lo permiten. La tarifa por defecto coincide con el precio base del permiso y se puede editar antes de habilitar el uso.

Préstamo: el prestamista reserva dinero; el destinatario acepta; staff verifica el acuerdo y las pruebas del prestamista. Es sin intereses, entre 1 y 90 días; no crea dinero. Se puede devolver parcialmente. El vencimiento queda registrado, sin cobros automáticos ni sanciones inventadas.

La procedencia muestra saldo conciliado con el libro contable, ingresos de misiones realmente pagadas y movimientos históricos. Salarios, transferencias, préstamos y ajustes administrativos registrados también pueden financiar compras. No se exige que el 100% del saldo proceda de misiones ni se atribuyen unidades fungibles de Ryo a una misión concreta. Un saldo modificado directamente sin asiento contable bloquea la compra para revisión.

Las capturas se enlazan a mensajes privados de revisión mediante URLs; no se guardan bytes de imagen en este módulo. Las URLs de adjuntos de Discord pueden caducar: conserva el original y vuelve a adjuntarlo si deja de verse antes de resolver. El registro conserva quién, cuándo y qué mensaje aportó la prueba, pero esto no constituye un archivo permanente de imágenes. La revisión visual es humana.

## 4. Actualizar el alojamiento existente

1. Detén el bot. Descarga una copia de `data/`, tus JSON de configuración y las variables de entorno. Usa `python tools/backup_market.py` para una copia SQLite coherente si ya instalaste esta herramienta. No borres ni sustituyas `data/worldx-v2.sqlite3`.
2. Para **actualizar**, usa el ZIP `WorldX-Mercado-Actualizacion.zip`: contiene solo los archivos nuevos y `bot.py`, sin base de datos ni configuración histórica. No despliegues una segunda instancia que apunte al mismo bot.
3. Si modificaste `bot.py` después del paquete base, integra únicamente la importación/instancia de `MarketSystem` al final de los sistemas y `MARKET.restore()` en `setup_hook`, conservando tus otros cambios. El paquete completo es una alternativa para instalaciones nuevas o recuperación, no para sobrescribir sin revisar una instalación más reciente.
4. Mantén Python **3.12**. Archivo de entrada `main.py`. Instala las dependencias con `pip install -r requirements.txt`; el hosting puede hacerlo con su comando automático existente. El paquete usa las versiones fijadas del proyecto base.
5. Conserva las variables existentes: token, servidor, registro y roles. Añade las variables de `.env.market.example` con IDs reales. Nunca compartas el token en capturas.
6. Crea canales privados separados: compras, tradeos, enseñanza, préstamos y auditoría. Deniega **Ver canal** a `@everyone`. Permite al rol revisor y al bot ver, enviar, insertar enlaces y leer historial. Los jugadores aportan pruebas con el comando; no necesitan entrar al canal del staff.
7. `MARKET_REVIEW_ROLE_IDS` contiene los roles que pueden resolver/verificar perfiles; `MARKET_ADMIN_ROLE_IDS` los que recargan catálogo/diagnostican. El propietario configurado en `BOT_OWNER_ID` también tiene acceso. No se permite autoaprobar una operación ni la propia ficha, ni siquiera al propietario. Dos revisores permiten revisar las compras del staff entre sí.
8. Inicia, ejecuta `/mercado diagnostico`, verifica perfiles y realiza una compra de prueba con dos cuentas. Comprueba aprobación y rechazo, reserva, inventario y reinicio con una solicitud pendiente antes de abrirlo a jugadores. La economía previa debe estar configurada y habilitada; `/economia acreditar` sigue siendo necesario para los perfiles económicos.

La migración añade tablas automáticamente sin reiniciar saldos. El catálogo nuevo tiene IDs independientes: los inventarios anteriores permanecen consultables en su interfaz original; no se importan automáticamente porque sus IDs y requisitos pueden ser distintos. El antiguo `/mercado` se conserva como `/mercado_anterior` para consultar/cancelar ofertas históricas. Sus vías de compra, entrega y enseñanza están bloqueadas; usa `/mercado`. La tienda antigua puede mostrar datos históricos, pero no vender. No elimines esos registros.

Una reversión de versión exige rechazar/cancelar primero todas las solicitudes activas nuevas y conservar un respaldo: el código antiguo no conoce las reservas nuevas. Restaurar un respaldo también deshace todas las operaciones posteriores; no hacerlo sobre un bot activo.

## 5. Operación y pruebas

`python -m unittest discover -s tests -v` ejecuta las pruebas del mercado con una base temporal y el motor contable real. Se cubren reservas en transferencias antiguas, rollback después del débito, doble aprobación, x3, requisitos duros, datos alterados, pruebas faltantes, autoaprobación, consentimiento, tradeo, enseñanza, devolución, caducidad, reinicio, integridad de precios y filtros.

`python tools/backup_market.py --keep 3` guarda copias consistentes con comprobación de integridad. Elimina solo copias antiguas con el patrón propio en `backups/`. Programa copias fuera del bot con tu alojamiento si lo permite y descarga al menos una fuera de él. El catálogo/auditoría crece con el uso: monitoriza el espacio del hosting; esta entrega no borra historial de compras para ahorrar disco.

Se probó carga de los comandos y vista persistente sin conectar a Discord. La configuración de roles/canales, envío real de capturas y prueba de interacción en tu servidor deben verificarse al instalar. No se incluye ninguna credencial, imagen privada ni base de datos de jugadores.
