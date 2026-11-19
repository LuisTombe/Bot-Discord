# World X — reportes con verificación de evidencia

Actualización **Python / discord.py**, integrada al bot existente. No cambia salarios, pagos, tienda, registro, probabilidades ni bases de datos de jugadores. Mantiene las sesiones/autorizaciones de Open World y el flujo de pago independiente de misiones.

## Qué se instala

- `missions.py`: interfaz original mejorada; comprobación del rol Shinobi, instrucciones según objetivo, carga validada de imágenes y botón persistente «Verificar pruebas».
- `v7_missions.py`: incorpora limpieza/grafitis D y consulta indexada de duplicados.
- `v7_ui.py`: conecta la revisión nueva y aplica el control de Shinobi también a `/reportes azul`.
- `mission_evidence_policy.py`: validación de archivos, fechas, comprobaciones por imagen y bloqueo de aprobación sin revisión.
- `mission_review_ui.py`: `/reporte`, `/revision_mision` y `/catalogo_misiones`.
- `config/missions/evidence.json`: catálogo de evidencia y límites editables.
- `tests/test_mission_evidence.py`: pruebas funcionales del motor.

No sustituye `bot.py`, `misiones_config.json` ni `data/`. Necesita la versión anterior que ya contiene `v7_ui.py`, `missions.py`, `payment_evidence.py` y `discord.py==2.7.1`. El ZIP completo alternativo contiene también el mercado entregado antes; para actualizar un alojamiento existente, utiliza el ZIP pequeño de misiones.

## Instalación en Bot-Hosting

1. Detén el bot y descarga un respaldo completo, incluida la carpeta `data/` con capturas y SQLite. Conserva tus variables del entorno.
2. Sube `WorldX-Misiones-Actualizacion.zip` en **Archivos**, junto a `bot.py`, y descomprímelo reemplazando los archivos incluidos. Si modificaste por tu cuenta esos tres módulos, compara e integra los cambios antes de sustituirlos.
3. En **Entorno**, añade `SHINOBI_ROLE_ID` con el ID real del rol **Shinobi**. Es obligatorio: si está vacío/0, se bloquean los reportes con un mensaje de configuración. No se usa el nombre del rol ni se permite que administración evite este requisito para reportar.
4. Conserva Python 3.12, tu archivo de entrada y las variables existentes. Esta actualización no requiere instalar JavaScript ni cambiar el comando de inicio.
5. Crea o conserva los canales de autorizaciones y misiones. Ocúltalos a `@everyone`. Concede acceso al rol `REVIEWER_ROLE_ID`, a administración y al bot. El bot necesita Ver canal, Enviar mensajes, Insertar enlaces, Adjuntar archivos y Leer historial. Se bloquea la entrega de pruebas si otro rol/usuario tiene un permiso explícito de acceso sin pertenecer al staff.
6. Inicia el bot. Ejecuta `/misiones configurar autorizaciones:… misiones:…`. Los reportes publicados previamente conservan su canal original; también debe ser privado.
7. Prueba `/reporte` con una cuenta con Shinobi y con otra sin él. La segunda debe recibir una denegación. Los usuarios siguen necesitando ficha aprobada y los requisitos económicos previos donde corresponda.
8. Prueba un reporte y su rechazo/aprobación con otro moderador. No se permite autoaprobar ni verificar un reporte propio, tampoco al propietario en modo de pruebas.

No borres ni reemplaces `data/worldx-v2.sqlite3`. Las tablas nuevas se crean automáticamente. Los informes y créditos anteriores permanecen; los pendientes requerirán la nueva verificación al aprobarlos.

## Flujo del jugador

1. `/reporte`: selecciona **RPG/normal**, **Open World** o **World X**, y el objetivo.
2. Open World/World X mantienen la sesión abierta por el líder y la autorización personal previa. RPG D se reporta individualmente sin sesión.
3. Completa inicio y fin en **hora de Colombia (UTC−5)**, objetivo, enemigos y resultado. Añade los integrantes del squad si aplica; también deben ser Shinobi y tener autorización.
4. Pulsa **Captura e instrucciones → Subir captura**. El modal permite adjuntar imágenes directamente, sin escribir URLs y sin habilitar Message Content Intent para esta función.
5. Adjunta de una a cuatro imágenes. Se conserva la regla previa de **dos capturas para ejecución**: objetivo abatido y jugador a punto de ejecutarlo, más evidencia del desenlace.
6. Envía al staff y consulta `/misiones ver` o `/misiones mis_reportes` para conocer el estado. Un reporte representa una misión, no una misión por imagen.

Los comandos previos `/misiones nuevo`, paneles y `/reportes azul` siguen funcionando y también exigen el rol Shinobi. Cambiar un dato o reemplazar una captura invalida las verificaciones anteriores.

## Qué debe verse en las pruebas

Para las autorizaciones previas se comprueban fecha/hora, pantalla completa, identidad, TAB y menú M; no se exige un panel de misión que todavía no ha comenzado.

En **todas** las imágenes de misión: pantalla completa sin recorte, fecha y hora del dispositivo legibles, panel de misión, identidad coherente con la ficha y squad cuando corresponda. El temporizador del juego no sustituye al reloj/calendario de Windows. No agregues una fecha mediante un editor de imágenes.

| Objetivo | Evidencia específica |
|---|---|
| Protección de cargamento | Carga visible y panel de protección con progreso cercano al cierre o resultado |
| Destrucción de cargamento | Shipment visible a punto de destruirse; vida/progreso y panel legibles |
| Ejecución | Enemigo abatido, jugador a punto de ejecutarlo y segunda captura del desenlace |
| Captura/secuestro | Objetivo capturado e identificable; entrega si el encargo la exige |
| Recuperar/robar pergamino | Pergamino y progreso/resultado del objetivo |
| Escolta/protección de persona | Persona protegida y avance/llegada al destino |
| Entrega/robo de carga | Carga y entrega o avance del objetivo |
| Defensa de posición | Objetivo defendido y progreso/resultado |
| Encargos civiles | Tarea seleccionada y progreso/resultado del panel |

La imagen de referencia aportada muestra «Protect the shipment at the objective point», squad y la barra de Windows con 01:38, 13/11/2026. Sirve para explicar la composición. No se presupone que su contenido demuestre por sí solo la finalización o la proximidad al cierre: eso se contrasta en la revisión.

## Revisión del moderador

En el mensaje privado del reporte pulsa **Verificar pruebas**, o usa `/revision_mision reporte:ID` en ese mismo canal.

Se muestra cada imagen con la lista de condiciones. Para cada captura:

- Inspecciona imagen completa, panel, fecha, hora, identidad, squad, objetivo y peligrosidad declarada.
- Si falta fecha/hora, pulsa **Rechazar: falta fecha/hora**. El motivo queda registrado y no se conceden créditos.
- Si cumple, pulsa **Verifiqué todos los puntos**. Transcribe la fecha y hora visible, el desfase UTC del dispositivo, lo observado y escribe `CONFIRMO`.
- Repite para todas las imágenes y pulsa **Aprobar reporte verificado**. El moderador que aprueba debe haber verificado personalmente todas las imágenes de esa versión.

El desfase permite aceptar capturas de otros países y convertirlas al mismo instante. Se contrasta con el intervalo declarado; la tolerancia predeterminada es de cinco minutos, editable. Una fecha futura fuera de esa tolerancia se bloquea. Es un control de coherencia, no una certificación de que el reloj del equipo no fue modificado.

Los botones anteriores y `/misiones aprobar` o `/reportes resolver` no eluden esta condición: el motor rechaza una aprobación sin las verificaciones necesarias. Rechazar o devolver para corregir sigue disponible. Al aprobar se suma una misión por participante; **no se emite dinero automáticamente**. Se mantiene `/pagos mision` y su proceso anterior.

## Catálogo y peligrosidad

La clasificación siguiente es **de rol de World X**, no un rango ni recompensa oficial de Shindo. Open World y World X comparten tipos por tu instrucción. Se conservaron los diez tipos que ya estaban configurados y siete encargos civiles RPG. Los rangos personalizados que tengas en `misiones_config.json` se respetan; el código no los reemplaza.

| Base | Objetivos |
|---|---|
| D — RPG/normal | Hierba/pasto; recuperar gato; limpiar oficina del Kage; compras/mercado/mandados; carta/mensaje; quitar grafitis; limpieza general |
| D — Open World/World X | Recuperar pergamino; recoger y entregar paquete/cargamento |
| C — Open World/World X | Robar pergamino; proteger carga; destruir carga; escoltar/proteger persona; robar carga; defender objetivo/posición |
| B — Open World/World X | Ejecutar un objetivo; capturar/secuestrar un objetivo |
| A — peligro elevado | Los objetivos anteriores cuando el enemigo más fuerte corresponde a Jounin, ANBU o Jounin Sensei según tu configuración |
| S — peligro extremo | Los objetivos anteriores contra Subkage, Sannin, Kage o Akatsuki según tu configuración |

Para Open World, se utiliza el mayor entre rango base y peligrosidad del enemigo. Los civiles RPG se mantienen siempre D. El staff puede clasificar con `/reportes clasificar`, justificando la evidencia; el cambio de versión exige revisar las capturas nuevamente. Declarar un enemigo Kage no acredita por sí solo el rango S: el moderador debe comprobarlo.

**Límite de verificación del catálogo:** no se encontró una lista pública exhaustiva y actual de todas las misiones de Open World/World X. Por ello no se inventaron tipos «oficiales» para completar A/S. El catálogo es completo respecto a los objetivos configurados del servidor y los civiles solicitados, y permite ampliaciones. Grafitis/limpieza general se incluyen por tu instrucción, sin afirmar que sean nombres exactos del menú actual.

La wiki describe encargos civiles de estrellas azules y rojas (hierba/gatos, compras/mensajes). El servidor los agrupa como D aunque no todos compartan el mismo icono en el juego. Fuentes consultadas: [Missions, Shindo Life Wiki](https://shindo-life-rell.fandom.com/wiki/Missions) y [presentación Open World de RELLGames](https://www.youtube.com/watch?v=BSudimQH9ok). Ninguna se utiliza para afirmar una enumeración exhaustiva actual.

## Almacenamiento, trazabilidad y límites

Las capturas originales se conservan en `data/capturas/reportes/` y se adjuntan al canal de revisión, evitando depender de URLs firmadas que caduquen. Se procesa una imagen a la vez, con máximo 8 MiB por archivo. Se comprueban la firma del archivo y su estructura con Pillow; una extensión `.png` no basta. No se admiten animaciones.

El límite local de las pruebas es 160 MiB y se reservan 32 MiB libres en disco (editables en `config/missions/evidence.json`). Al llegar al límite se bloquean nuevas cargas: no se borran evidencias históricas en silencio. Descarga respaldos y ajusta capacidad/retención con el staff. Las demás capturas del registro también ocupan espacio; el control de espacio libre considera el disco completo.

Las tablas adicionales, en la SQLite existente, son `mission_evidence_checks` (verificación por reporte/versión/revisor/imagen) y `mission_proof_index` (índice de huellas SHA-256). El registro `mission_audit` guarda quién revisó, cuándo, qué fecha leyó, con qué zona horaria, qué checklist confirmó y sus observaciones. Los conteos siguen en `mission_credits`.

Las huellas detectan reutilización de bytes idénticos y modificación de archivos ya archivados. No detectan con certeza una imagen reexportada, editada o un reloj manipulado. **No hay reconocimiento visual automático ni OCR que decida por el staff.** Ninguna aprobación automática se basa en el nombre, tamaño, resolución o metadatos de la imagen.

Los botones de revisión pendientes se restauran al reiniciar. La entrega procesa hasta veinte reportes pendientes de actualización por ciclo, sin cargar todo el historial. Una doble aprobación no duplica los créditos. No se modifican pagos ni saldos por instalar este módulo.

## Pruebas y alcance de la entrega

Ejecutar `python -m unittest discover -s tests -v` desde la raíz del proyecto. La entrega contiene 19 pruebas nuevas y conserva las 19 del mercado: **38 aprobadas**. Se verifican restricción de rol, falta de fecha, comprobación de todas las imágenes, duplicados, autoaprobación, corrección, desfases UTC, integridad, límites de disco, tipos/modos, rangos y persistencia.

También se comprobó la carga de los nuevos comandos en el proyecto completo. No se ha conectado ni desplegado esta versión en tu servidor: prueba permisos reales, el modal de adjuntos y una aprobación/rechazo en Discord antes de abrirlo a los jugadores.
