# World X — reinicio, aldeas, ruleta y recompensas

Actualización para el bot **Python 3.12 / discord.py** de la entrega de mercado y reportes de misiones. No es un segundo bot y no migra el proyecto a JavaScript.

**Instalar no borra ninguna ficha.** El reinicio solo se ejecuta mediante el comando exclusivo del dueño, su vista previa y una confirmación escrita.

## Instalar en Bot-Hosting

1. Detén el bot desde el panel. No ejecutes simultáneamente la copia de tu PC.
2. Descarga una copia de `data/`, de tus archivos de configuración y del código actual. Conserva las variables de entorno del alojamiento.
3. Abre **Archivos** y sube `WorldX-Recompensas-Actualizacion.zip` a la misma carpeta que contiene `main.py` y `bot.py`.
4. Extrae el ZIP y permite reemplazar los archivos indicados en `MANIFIESTO_RECOMPENSAS.json`. Los archivos deben quedar directamente junto a `main.py`, sin una carpeta adicional. El parche presupone que instalaste la actualización anterior de misiones y mercado.
5. **No borres ni reemplaces `data/` ni `.env`.** Este paquete no incluye bases de datos, fichas de prueba, tokens ni capturas personales.
6. En **Entorno**, comprueba `BOT_OWNER_ID`: debe contener **tu ID de usuario de Discord**, no el del bot ni el de un rol. Conserva `GUILD_ID` y todas las variables anteriores.
7. En **Configuración → Inicio**, usa Python **3.12**, archivo `main.py`. Conserva el comando que instala `requirements.txt` y ejecuta Python. Las dependencias no cambian.
8. Inicia el bot. Se crean las tablas nuevas y se fusionan las opciones Vinland/Hierro. El bot sincroniza los comandos en el servidor al arrancar.
9. En Discord, ejecuta `/mantenimiento aldeas`. Debe mostrar las siete opciones y sus cupos.

Si tu instalación aún no incluye `wx_market/`, `mission_evidence_policy.py` y `mission_review_ui.py`, usa el ZIP **Código-Completo** como código base, conservando tus datos y revisando tus configuraciones personalizadas. No superpongas el catálogo completo sobre cambios manuales sin compararlos primero.

## Configuración inicial de la ruleta

1. Crea un canal, por ejemplo `#ruleta`.
2. Dale al bot **Ver canal, Enviar mensajes, Insertar enlaces y Adjuntar archivos**. Los jugadores necesitan ver el canal y usar comandos de aplicaciones. Los botones no necesitan permiso de administrador.
3. Ejecuta como dueño: `/ruleta publicar canal:#ruleta`.
4. Financia el fondo, por ejemplo: `/ruleta capital importe:10000000`.
   - Sin `aldea_id`, esto es una **emisión explícita de capital**, auditada y exclusiva del dueño.
   - Con `aldea_id`, transfiere dinero existente desde la tesorería de esa aldea; no crea dinero. Usa el ID económico de aldea del bot, no un ID de canal.
5. Comprueba `/ruleta fondo`.
6. Los jugadores usan el botón **Girar · 500.000 Ryo** o `/ruleta girar` en ese canal. Necesitan ficha aprobada y acreditación económica válida.

El canal queda guardado en SQLite; no hace falta añadir un ID nuevo al `.env`. Publicar en otro canal cambia el canal autorizado. Los paneles anteriores rechazan giros fuera del canal vigente.

La animación y el resultado se muestran privadamente al jugador para no saturar el canal. El panel público permanece visible. El GIF gira hasta el sector del premio sorteado; el tamaño de los sectores representa las probabilidades. El resultado económico se guarda **antes** de enviar la animación. Si Discord no entrega el GIF, consulta el historial: no vuelvas a girar para recuperar el premio, porque un nuevo giro es una compra nueva.

### Premios por giro

| Premio | Probabilidad |
| --- | ---: |
| 100.000 Ryo | 40 % |
| 250.000 Ryo | 35 % |
| 500.000 Ryo | 18 % |
| 1.000.000 Ryo | 5 % |
| 2.000.000 Ryo | 1,4 % |
| 1 reroll | 0,5 % |
| 5 rerolls | 0,1 % |
| **Total** | **100 %** |

Se cobran **500.000 completos** y luego se entrega el premio. Ganar 100.000 implica un resultado neto de −400.000. La devolución media en dinero es 295.500 Ryo por giro (59,1 % del coste), sin valorar los rerolls. No hay compensación por derrotas ni garantía tras cierta cantidad de giros.

El fondo general usa la cuenta contable `worldx:treasury`. Recibe las tarifas completas y paga los premios. Antes de sortear debe tener al menos **1.500.000 Ryo**, que junto con la tarifa cubren el premio máximo. Si no alcanza, **no sortea ni cobra**. Los premios diarios comparten este fondo. Consulta el saldo y repón capital cuando sea necesario.

## Rerolls reales de nacimiento

- 1 reroll = **3 giros de elementos + 3 de principal + 3 de secundarios**.
- 5 rerolls = cinco reinicios completos; se consumen de uno en uno.
- `/recompensas reroll` abre una confirmación: escribe `REEMPLAZAR`.
- Solo se permite con una asignación terminada, sin congelación ni revisión de ficha pendiente.
- Reemplaza la asignación anterior, mantiene la aldea y permite volver a elegir el orden con `/asignacion iniciar`.
- Los resultados nuevos deben completar nuevamente identidad, capturas y aprobación de ficha. Se invalidan los perfiles verificados del mercado y se cancelan solicitudes de compra/tradeo pendientes de esa persona.
- Se conservan saldo, inventario y obligaciones económicas. Los permisos anteriores no sustituyen los requisitos del nuevo nacimiento.
- Un reroll **no libera los cupos históricos de bloodlines únicos**. Solo el reinicio global los libera. Los cinco premios no permiten cinco portadores de un único.
- Se vuelve a sortear el origen de población conforme a la modalidad activa; no cambia las probabilidades configuradas de esa modalidad.
- El saldo de rerolls aparece en `/recompensas panel` y `/mercado inventario`. Uso e ingresos quedan auditados.

## Recompensas de siete días

El activador es la **primera misión aprobada** del día, no un borrador, una autorización ni un reporte rechazado. Así un envío sin evidencia válida no genera premios. Se incluyen participantes acreditados en un reporte grupal aprobado. Las misiones anteriores a instalar esta actualización no generan premios retroactivos.

- Zona horaria: **America/Bogota**, cambio de día a medianoche local.
- Día 1: 25.000 Ryo.
- Día 2: 40.000 Ryo.
- Día 3: 55.000 Ryo.
- Día 4: 70.000 Ryo.
- Día 5: 85.000 Ryo.
- Día 6: 100.000 Ryo.
- Día 7: **1 reroll completo**.
- En cada ciclo, **un día aleatorio entre el 2 y el 6 sustituye su dinero por un permiso**.
- Son siete días de actividad: faltar un día **no borra el progreso**. Después del día 7 empieza un ciclo nuevo en la siguiente fecha con misión aprobada.
- No es un temporizador móvil de 24 horas: una misión aprobada antes de medianoche y otra después pertenecen a días distintos.
- El día se calcula con la aprobación del staff. Varios reportes antiguos aprobados juntos no permiten reclamar una semana de premios en un solo día.

La entrega es automática, normalmente dentro de 20 segundos. Dinero al saldo, permisos al inventario existente, rerolls a su saldo de uso. Si el fondo no tiene dinero, la recompensa **permanece pendiente** con el mismo resultado y se reintenta; no se vuelve a sortear. Los errores de una cuenta no impiden procesar las de los demás.

Comandos:

| Comando | Uso |
| --- | --- |
| `/recompensas panel` | Calendario personal, progreso y rerolls |
| `/recompensas historial` | Giros, premios reservados y entregados, con páginas |
| `/recompensas premio numero:…` | Estado de un premio diario y logo de su técnica |
| `/recompensas reroll` | Confirmar y consumir un reroll completo |
| `/mercado inventario` | Permisos entregados y saldo de rerolls |
| `/mantenimiento recompensas_logs` | Auditoría general, exclusiva del dueño |

### Permisos del premio semanal

Se usa **el catálogo importado del HTML que ya tiene el bot**, sin inventar precios o requisitos nuevos. Categorías elegibles: `subjutsu`, `taijutsu`, `sub_modes`. Incluye técnicas ocultas, médicas, invocaciones, clones y modos de subjutsu cuando estén habilitados. Excluye jutsus elementales, habilidades/modos de bloodline, razas y artículos prohibidos/desactivados.

Pesos de rango: **D 45, C 30, B 18, A 6, S 1**. Primero se elige un rango entre los que tengan candidatos compatibles; después una técnica de ese rango uniformemente. Si faltan rangos elegibles, sus pesos no se fuerzan: se normalizan los disponibles.

Se exige perfil de mercado vigente, rango, nivel, stats y todos los requisitos verificados, incluidos los tags `unlock:...` ya usados por tu mercado. Un premio no concede un bijū, un linaje ni una autorización de staff inexistente. Los permisos únicos ya poseídos se excluyen.

**Si no hay candidatos compatibles, el premio es 100.000 Ryo.** Esto evita entregar algo prohibido o inutilizable. Para habilitar candidatos reales, el staff debe mantener actualizado `/mercado perfil`. Si un permiso ya reservado deja de ser válido antes de entregarse, queda pendiente con el motivo visible hasta corregir el perfil o las condiciones; no se cambia silenciosamente por otra técnica.

Una misión revocada antes de pagar cancela el premio pendiente. Una revocación posterior a la entrega requiere revisión del dueño: no se intenta deshacer automáticamente un reroll que podría haberse consumido. Esa misión nunca vuelve a conceder un premio diario a la misma persona.

## Aldeas y cupos

| ID estable | Selección visible |
| --- | --- |
| `konoha` | Ember / Konohagakure |
| `dunes` | Dunes / Sunagakure |
| `nimbus` | Nimbus / Kumogakure |
| `haze` | Haze / Kirigakure |
| `obelisk` | Obelisk / Iwagakure |
| `tempest` | Tempest / Amegakure |
| `iron` | Vinland / Tetsu no Kuni (País del Hierro) |

**50 usuarios por aldea**, incluidos nacimientos en curso desde que eligen origen. Vinland deja de ser una opción duplicada: los datos anteriores de `vinland` se normalizan a `iron`. Tetsu no Kuni es la opción que se conserva. Tempest/Amegakure se deja como equivalencia de rol editable de World X, no como una afirmación de canon oficial de Shindo.

No se expulsa automáticamente a ocupantes si una aldea ya tenía más de 50 al instalar: se bloquean nuevas plazas hasta quedar por debajo del límite. Borrar una ficha libera su plaza. Un reroll conserva su plaza. Dos jugadores intentando tomar el último cupo no pueden superar el máximo.

Cambios de límite o etiquetas: edita `config/rewards.json` y reinicia. No cambies los IDs internos. El cambio no modifica pesos ni probabilidades de los bloodlines. Las aldeas y sus roles económicos existentes no se recrean automáticamente.

## Borrar todas las fichas — solamente tú

1. Comprueba que `BOT_OWNER_ID` sea tu ID.
2. Ejecuta `/mantenimiento reiniciar_fichas`.
3. Revisa los conteos y el servidor que muestra.
4. Pulsa **Confirmar borrado global**.
5. Escribe exactamente `BORRAR ID_DEL_SERVIDOR`, sustituyendo el ID por el mostrado.

La confirmación dura cinco minutos y caduca si cambian fichas/asignaciones. Otro administrador no puede usarla, aunque tenga el permiso Administrador. El bot respalda primero SQLite en `respaldos/antes-reset-…sqlite3`; si falla la copia, no procede al borrado.

Se borran las fichas activas y restantes registros de cuenta del servidor, asignaciones, resultados antiguos de nacimiento, historial de cupos únicos, tickets de origen y reservas de aldea. Se cancelan solicitudes pendientes de mercado, se invalidan perfiles verificados, se congelan reportes abiertos y se cierran sesiones de mundo. Roles de registro y publicaciones se retiran mediante la cola existente; fallos de permisos quedan registrados para reintentar.

**No es un borrado de la economía:** conserva saldos, deudas, inventarios, pagos, premios, legados económicos y sus auditorías. También conserva las configuraciones del servidor. Las capturas originales en disco y respaldos se conservan para recuperación; se eliminan sus referencias en las fichas. Esto deja **cero fichas y cero asignaciones**, sin destruir el libro contable. La vista previa muestra expresamente este alcance antes de confirmar.

No se reutilizan IDs de fichas ni revisiones de asignación del ciclo anterior; botones antiguos no pueden aprobar ni modificar fichas nuevas. En caso de restauración, detén el proceso y restaura el respaldo **completo**, conservando las capturas. Los roles y mensajes ya eliminados de Discord requieren republicar/reasignar según corresponda.

## Archivos, datos y consumo

- `wx_rewards/domain.py`: transacciones, premios, cursor de misiones, rerolls y reinicio.
- `wx_rewards/villages.py`: etiquetas, alias y cupos atómicos.
- `wx_rewards/ui.py`: comandos, menús, confirmaciones y botones persistentes.
- `wx_rewards/visuals.py`: panel PNG y animación GIF; renderizado serializado en un hilo.
- `wx_rewards/icons.py`: resolución y caché de URL de imagen de la wiki.
- `wx_rewards/schema.sql`: tablas e índices nuevos; no reemplaza el esquema económico.
- `config/rewards.json`: premios, cupos, horario, pesos y reglas semanales editables.
- `config/reward_icons.json`: URLs verificadas por ID de técnica; se pueden completar sin tocar código.
- `tests/test_rewards.py`: pruebas nuevas, junto con las pruebas anteriores de mercado y misiones.

Las imágenes de técnicas usan URLs externas; el bot no descarga sus píxeles. Ryo y el logo de la wiki ocupan unas decenas de KB locales para poder dibujar los paneles sin depender de una petición de red. El GIF de ejemplo ocupa menos de 1 MB y no se guarda en disco en cada giro. No se cargan los historiales completos para paginar. Las transacciones de saldo y las reservas de compra del mercado siguen siendo las mismas.

El respaldo previo a cada reinicio es deliberadamente permanente: descárgalo y gestiona su retención para no agotar el disco. Un servidor SQLite debe ejecutar **una sola instancia** del bot en este alojamiento; no copies una base abierta ignorando sus archivos WAL.

### Imágenes y atribución

Ryo y logos de técnicas: [Shindo Life Wiki](https://shindo-life-rell.fandom.com/). El encabezado usa el logo de esa wiki como referencia visual y no afirma afiliación oficial. `config/reward_icons.json` contiene los enlaces de imagen que pudieron verificarse al preparar esta versión. Para técnicas sin enlace verificado, el bot consulta la imagen principal de su página en la wiki y guarda solo la URL durante siete días. Si la wiki bloquea la consulta, muestra nombre y enlace fuente; **el premio se entrega igualmente**. No se sustituye por un logo inventado.

### Verificación antes de abrir al público

1. Comprueba acceso de dueño y denegación desde otra cuenta administradora.
2. Verifica `/mantenimiento aldeas` y que haya una sola opción Hierro/Vinland.
3. Revisa fondo, tarifa de 500.000 y premios en `/ruleta publicar`.
4. Haz un giro con una cuenta de prueba acreditada; confirma saldo, fondo e historial.
5. Aprueba una misión válida; consulta el panel. Aprobar otra el mismo día no debe dar otro premio diario.
6. Rechaza un reporte: no debe producir recompensa.
7. Reinicia el proceso y prueba los botones del panel publicado.
8. Usa el reinicio global solo cuando hayas revisado la vista previa y quieras borrar las fichas.

Pruebas locales: `python -m unittest discover -s tests -v`. Se comprobó también la construcción y serialización de comandos sin conectar un token. La entrega no ejecuta cambios en tu Discord ni garantiza disponibilidad continua del alojamiento o de la wiki; debes completar la prueba en el canal real.
