# World X — misiones y compras sencillas

Actualización del 15 de noviembre de 2026. Basada en «Proyecto Completo Bot.gz».
No utiliza IA, OCR ni servicios de pago. La evidencia sigue siendo revisada por personas.

## Instalar en Bot-Hosting

1. **Detén el bot.** Descarga un respaldo completo del alojamiento antes de reemplazar archivos. Conserva el código anterior y toda la carpeta `data`, incluyendo SQLite, archivos WAL/SHM y capturas si existen. Nunca copies una base de datos vacía encima de la actual.
2. Descarga `WorldX-Update-Flujo-Sencillo.zip`. Es un **parche**, no un proyecto independiente.
3. Extrae su contenido en la raíz del bot, donde están `main.py` y `bot.py`. Acepta reemplazar únicamente los archivos incluidos. Mantén la carpeta `wx_market` y `tests` con sus rutas; no pongas todos sus archivos sueltos en la raíz.
4. **No borres `data`, `.env`, `config`, assets ni los demás archivos.** Este ZIP no contiene bases de datos, tokens ni capturas de jugadores. No modifica los precios del catálogo ni tus roles de economía.
5. Conserva Python 3.12 y `main.py` como archivo de entrada. La versión recibida usa discord.py 2.7.1 y ya admite adjuntos en formularios; no requiere instalar servicios adicionales.
6. Inicia el bot y revisa la consola. Las nuevas tablas se crean automáticamente en tu SQLite existente. No hay reseteo de cuentas, dinero, misiones, ruleta o inventarios.
7. Configura los canales y publica el panel como se indica debajo. Haz primero una prueba con un jugador y un supervisor diferentes.

La actualización ha sido probada localmente con SQLite y discord.py; no se ha conectado a tu servidor ni se ha desplegado desde aquí. Los permisos reales de los canales se comprueban al configurar/usar el bot.

## Configuración inicial (una sola vez)

### Roles

- `SHINOBI_ROLE_ID`: ID exacto del rol Shinobi; obligatorio para iniciar y reportar misiones.
- `REVIEWER_ROLE_ID`: rol de supervisores de misiones. El propietario del bot y quienes tienen Gestionar servidor también pueden revisar.
- Las aldeas y rangos económicos deben seguir configurados con `/economia`, incluyendo `nivel_permisos`. Se sincronizan automáticamente los roles al verificar/pagar/comprar.
- Ningún supervisor, tampoco el propietario, puede aprobar sus propias misiones, perfil o compra. Necesitas otro supervisor para probar con tu personaje.

### Canales

1. Crea o conserva un **canal de texto privado para autorizaciones**. Deniega Ver canal a @everyone; permite al rol supervisor y al bot. El bot necesita ver, enviar, adjuntar, insertar enlaces y leer historial.
2. Crea un **canal de foro para reportes de misiones**. Ocúltalo a @everyone y permite acceso a Shinobi/staff. Las capturas de misión serán visibles para quienes vean este foro. Puedes denegar a jugadores crear publicaciones para que todas se creen mediante el bot. El bot necesita Ver canal, Enviar mensajes/crear publicaciones, Enviar mensajes en hilos, Adjuntar archivos, Insertar enlaces y Leer historial. Desactiva «exigir etiqueta». No uses un canal multimedia.
3. Ejecuta `/panel configurar autorizaciones:#revisión-autorizaciones foro:#misiones`.
4. Conserva o crea un **canal de texto privado para compras/perfiles**. Permite al bot y al rol revisor; el bot también necesita Adjuntar archivos para conservar las capturas.
5. Ejecuta `/panel compras canal:#revisión-compras rol:@Supervisor`. Guarda esta selección en SQLite y la conserva al reiniciar; prevalece sobre el canal de compras del entorno.
6. En el canal de acceso de los jugadores ejecuta `/panel publicar`.
7. Ejecuta `/panel estado` para comprobar canales, rol Shinobi y economía habilitada. Para tradeos, enseñanza, préstamos y auditoría siguen aplicando los canales de mercado existentes.

No es necesario abrir una sesión como líder cada día. «Comenzar misiones» crea la sesión diaria si no existe. Cada jugador requiere su autorización individual.

## Uso del jugador

### Comenzar misiones

Pulsa **Comenzar misiones**, escribe nivel y puntos CHI NIN TAI HP, y adjunta una o dos capturas completas donde se vean el username Roblox, stats, nivel y fecha/hora del computador o móvil. Si usas dos, ambas deben mostrar fecha y hora. El rango y la aldea se toman de los roles económicos, no de un campo que puedas cambiar.

Espera la aceptación del supervisor. Puedes consultar **Mis reportes** y recibir un mensaje privado si Discord lo permite. No se interpreta automáticamente ninguna imagen.

La autorización dura hasta **Terminar misiones** o el cambio de día en Colombia (UTC−5). El cierre diario es lógico: aunque ningún usuario pulse nada a medianoche, una autorización del día anterior no permite nuevas misiones. «Terminar» es definitivo para esa autorización; una nueva sesión diaria necesita otra autorización.

### Reportar una misión

Pulsa **Reportar misión**, selecciona mundo, objetivo y compañeros opcionales. Para misiones con enemigo, selecciona su rango más fuerte; el supervisor contrasta la clasificación. Cada compañero necesita autorización.

Pulsa **Adjuntar capturas y enviar**, indica inicio y fin en hora de Colombia (el supervisor contrasta la hora local de tu dispositivo) y adjunta de una a cuatro imágenes completas. El bot crea una publicación en el foro y mantiene el estado actualizado. En la revisión privada del supervisor aparecen también las pruebas de las autorizaciones de los participantes.

- Permanencia/protección: temporizador **a 3 segundos o menos**. Esa imagen se acepta como evidencia suficiente por regla del servidor, sin exigir otra del resultado final.
- Ejecución: enemigo abatido/K/O en el suelo **y jugador realizando la ejecución**. No basta estar junto a un K/O.
- Destrucción: cargamento a punto de destruirse y progreso/vida visibles.
- Captura: objetivo capturado; entrega si el encargo la exige.
- Otros objetivos: misión y progreso/resultado identificables.
- Todas: pantalla completa sin recortes, username reconocible y fecha/hora reales del dispositivo visibles.

Las capturas idénticas se detectan por hash; esto no detecta todas las imágenes manipuladas ni demuestra autenticidad. El staff decide si cumple.

**Mis reportes** permite abrir borradores, corregirlos o ver decisiones sin escribir IDs. Para reportar después de cerrar sesión, abre la autorización aprobada en Mis reportes y pulsa **Reportar misión de esta sesión**; los horarios deben quedar dentro del intervalo autorizado.

### Comprar

Pulsa **Tienda**, filtra y elige un artículo. Verás requisitos cumplidos/faltantes, precio final, dinero disponible y reservas.

Si falta perfil, pulsa **Completar / actualizar mi perfil**: elige raza, completa nivel, stats y niveles de tus bloodlines, adjunta pruebas y espera verificación. El formulario reutiliza elementos y bloodlines de la ficha aprobada. Los niveles BL vienen como `paper=150, otro=300`; modifica solo números de linajes que realmente tienes. Los IDs de desbloqueos son opcionales y los revisa el staff cuando un artículo los exige. **No tienes que crear ni subir un JSON.**

Las capturas iniciales autorizadas de misiones aportan niveles/stats; raza, niveles de bloodline y desbloqueos que no figuran en esa autorización necesitan revisión propia. El perfil conserva la vigencia de 30 días configurada anteriormente. Un cambio de ficha después de verificar el formulario obliga a revisar de nuevo.

Para comprar, pulsa **Adjuntar rango y solicitar compra**. Incluye captura de tu rango Shinobi en Discord y pruebas adicionales si son necesarias. El bot contrasta los roles actuales. Si el dinero no alcanza o hay un requisito obligatorio incumplido, bloquea la solicitud.

Al solicitar: reserva el dinero. Al aprobar: cobra y entrega al inventario. Al rechazar, cancelar o vencer a las 72 horas: libera la reserva. Una corrección conserva la reserva y el vencimiento original. Consulta **Mis compras** para añadir evidencia, cancelar o ver resultados.

El ×3 solo aplica a requisitos marcados `x3` en tu catálogo; no elimina los requisitos `hard`. Se conservan tus precios y permisos actuales.

## Uso del supervisor

- Usa `/panel pendientes` o abre el mensaje de revisión.
- Pulsa **Ver todas las pruebas**. Se muestran la misión y las autorizaciones de cada participante, incluidas sus capturas de stats/nivel/username.
- En autorizaciones: **Aceptar autorización** → escribe CONFIRMO solo tras comprobar todos los puntos.
- En misiones: **Aprobar y pagar** muestra tesorería, intervalo y reparto sugerido. Después abre el formulario, escribe CONFIRMO e importe (sin puntos ni comas).
- Alternativa por comando: `/panel pagar reporte:` tiene autocompletado de pendientes. Abre la misma revisión; no entrega dinero sin confirmación.
- **Pedir corrección** conserva el reporte; el jugador lo reenvía desde Mis reportes. Una revisión antigua no sirve para aprobar una versión corregida.
- **Rechazar** guarda el motivo y no mueve dinero.

La tesorería pagadora es la aldea económica del autor. El staff configurado puede aprobar y pagar desde ella; no es necesario que esté el dueño o el Kage. Se mantiene el reparto proporcional de squad de tu versión anterior (incluye sus bonificaciones por rango), no se multiplica el pago por el número de miembros. El bot muestra la distribución sugerida. Cada participante obtiene una misión completada.

Aprobación, contador, recibo y movimiento de dinero se confirman en **una transacción SQLite**. Si la tesorería no alcanza o falla el pago, no se aprueba ni se suma la misión. Dos pulsaciones/revisores no generan doble pago. Los reportes nuevos no admiten la aprobación aislada con `/misiones aprobar`.

En compras el staff mantiene Aprobar/Rechazar y ahora **Pedir corrección**. La aprobación relee saldo, reservas, catálogo y requisitos. En perfiles, revisa la solicitud del formulario y aprueba o solicita corrección. No se admite autorrevisión.

## Recompensas y contadores

| Modalidad | Pago total |
|---|---:|
| Normal azul | 500 Ryo |
| Normal verde | 1.000 Ryo |
| Normal roja | 750 Ryo |
| World X/Open World D | intervalo económico configurado; referencia 5.000–9.999 |
| C | referencia 10.000–24.999 |
| B | referencia 25.000–49.999 |
| A | referencia 50.000–99.999 |
| S | referencia 100.000–500.000 |

Los intervalos World X se leen de tu economía existente. El mínimo se sugiere en la revisión; el staff elige dentro del intervalo. Los encargos normales tienen precio fijo y no permiten aumentar el importe.

Mis reportes distingue colores normales y rangos World X. Los contadores heredados por rango mantienen la clasificación histórica D de los encargos normales para no reescribir tus estadísticas previas; la nueva vista los separa por tipo. Las recompensas diarias existentes se activan por la primera aprobación, que en el nuevo flujo sucede junto con el pago. No se entregan por abrir sesión o enviar un reporte pendiente.

## Prueba rápida después de instalar

1. Jugador con ficha aprobada + rol Shinobi + rol de aldea + rango económico.
2. Abrir panel, solicitar autorización con capturas. Otro supervisor la acepta.
3. Reportar una misión normal azul, revisar todas las pruebas y pagar 500. Verificar saldo y contador.
4. Volver a pulsar el pago: debe rechazar la repetición.
5. Probar un reporte cuya tesorería no tenga saldo: debe permanecer pendiente y no sumar.
6. Abrir Tienda, solicitar un artículo y comprobar que la reserva reduce el saldo disponible. Otro supervisor lo aprueba; verificar inventario y cobro único.
7. Probar corrección y rechazo; comprobar liberación de reservas donde corresponda.
8. Reiniciar con una solicitud pendiente. El panel y los botones persistentes de revisión deben seguir funcionando. Los formularios y listas temporales se abren de nuevo desde el panel.

Si Discord bloquea mensajes privados, el resultado sigue disponible en Mis reportes/Mis compras. El envío de mensajes se reintenta; una incidencia de Discord no vuelve a ejecutar el pago.

## Datos y reversión

El parche incorpora tablas auxiliares idempotentes para perfiles pendientes, vínculos a ficha y notificaciones. Usa las tablas de economía/misiones originales. No modifica el sistema de registro/asignación.

Para volver atrás, detén el bot y restaura el código anterior. Si ya hubo movimientos con la actualización, no restaures a ciegas una base antigua porque perderías esos movimientos: conserva el respaldo actual y revisa los pendientes. Los reportes nuevos llevan `simple_flow` y requieren esta versión para su flujo de aprobación.

El ZIP incluye un manifiesto de hashes y las pruebas nuevas; los tests necesitan el proyecto completo instalado y se ejecutan con `python -m unittest discover -s tests -v`. No ejecutes el bot de producción con una segunda instancia sobre la misma base.
