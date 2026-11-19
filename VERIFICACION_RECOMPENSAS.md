# Verificación de la entrega

Fecha: 15 de noviembre de 2026.

- 73 pruebas automatizadas: 38 anteriores de mercado/misiones y 35 nuevas de recompensas.
- Pruebas con SQLite temporal, sin token y sin conexión al Discord del usuario.
- Distribución exhaustiva de las 10.000 posiciones del sorteo: 10 para 5 rerolls, 50 para 1 reroll y 9.940 para dinero.
- Cobro completo, saldo previo, reservas de compras, fondo insuficiente y rollback al fallar el abono.
- Idempotencia de interacción, reutilización de reportes, primer premio por fecha en Bogotá, siete días, huecos de actividad y reinicio del servicio.
- Entrega de permiso al inventario y rechazo de artículos sin requisitos verificados.
- Restricción estricta al ID del dueño, frase obligatoria, expiración y vista previa invalidada por cambios.
- Reinicio en base temporal, copia SQLite legible, independencia de otros servidores, liberación de únicos y no reutilización de IDs/revisiones.
- Concurrencia real con dos conexiones SQLite disputando la plaza 50: solo una consigue reservarla.
- Retirada de cupo al borrar ficha y migración idempotente de Vinland a Hierro.
- Limpieza de publicaciones/roles vinculada al usuario incluso tras crear una ficha con otro ID.
- Bot importado y comandos serializados sin errores; 39 comandos/grupos raíz, incluidos los anteriores. Botones de ruleta persistentes.
- Animación de 15 fotogramas, sin bucle infinito, menor de 1 MB. Sector final correspondiente al premio.
- Medición orientativa local importando el bot y renderizando un giro: RSS máxima aproximada 75 MiB. No es un límite garantizado en producción.
- 34 entradas de iconos del catálogo con URL de imagen verificada. Las demás usan resolución desde la wiki, con nombre y enlace como alternativa si la fuente no responde.

Pendiente de validar en tu servidor: permisos y jerarquía real de roles, sincronización visible de comandos, entrega de mensajes/GIF y disponibilidad de imágenes externas. No se ha ejecutado ningún reinicio de tus datos reales.

Ejecutar: `python -m unittest discover -s tests -v`.
