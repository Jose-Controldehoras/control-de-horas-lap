# Robot de noticias

La app lee `data/noticias.json` desde GitHub para mostrar comunicados y enlaces de interes.

El robot se ejecuta con GitHub Actions dos veces al dia. Lee las cuatro publicaciones originales mas recientes de `@UGT_LAPALMA` y solo modifica el feed cuando detecta un cambio real.

La app debe mostrar siempre que es informacion recopilada de fuentes publicas y que no es una aplicacion oficial de Granada La Palma ni de ningun sindicato.

## Fuentes configuradas

El robot tiene dos niveles:

1. Fuentes públicas legibles sin permisos:
   - UGT Granada, desde su web oficial.
   - CCOO Granada, desde su web oficial.
   - GranadaDigital, etiqueta pública de CCOO Granada mediante RSS.

2. Fuentes de vigilancia o API:
   - UGT Granada La Palma en Instagram/Facebook.
   - CCOO Granada en Instagram/Facebook.

Las fuentes de Instagram/Facebook quedan en modo vigilancia si no hay API oficial disponible. No se copia contenido bloqueado ni se publica una noticia solo porque la página exista.

Si no aparece nada exacto de Granada La Palma, el robot mantiene noticias sindicales de Granada relacionadas con trabajadores, convenio, huelgas, paros, plantilla o condiciones laborales. Así la sección no queda vacía, pero la app muestra siempre la fuente original.

## X sin API de pago

El workflow activo lee el perfil publico `https://x.com/UGT_LAPALMA` sin iniciar sesion y sin utilizar la API de pago. El script `tools/x_news_robot.py`:

- Obtiene exclusivamente las cuatro publicaciones originales mas recientes; no incluye respuestas ni reposts.
- Conserva el identificador, el texto visible, la fecha, el enlace original y la primera imagen publica de `pbs.twimg.com`.
- Comprueba que cada imagen disponible responde y es realmente una imagen.
- Usa Tesseract con espanol e ingles para leer el texto de los carteles. Esto permite generar un titulo y un resumen incluso cuando la publicacion solo contiene una imagen.
- Mantiene siempre la atribucion `UGT Granada La Palma · @UGT_LAPALMA` y el enlace a X.
- Compara los cuatro elementos completos con `data/noticias.json`. Si no cambian, no crea ningun commit.
- Si X cambia su pagina, devuelve menos de cuatro publicaciones o alguna imagen no es valida, termina con error antes de escribir y conserva el feed anterior.

No se necesita ningun secreto de GitHub ni token de X. Las imagenes no se vuelven a alojar: se conserva su URL publica oficial.

## Meta API

El robot esta preparado para usar la Graph API oficial de Meta si se configuran estos secretos en GitHub Actions:

- `META_ACCESS_TOKEN`
- `UGT_INSTAGRAM_USER_ID`
- `UGT_FACEBOOK_PAGE_ID`
- `CCOO_INSTAGRAM_USER_ID`
- `CCOO_FACEBOOK_PAGE_ID`

El token debe tener permisos validos para leer las fuentes configuradas. Si faltan permisos o Meta bloquea la lectura, el robot sigue funcionando con las fuentes públicas y deja el aviso controlado en `data/news_robot_status.json`.

## Funcionamiento

- GitHub Actions ejecuta `.github/workflows/news-robot.yml` a las 08:17 y 20:17, hora de Madrid, y tambien permite lanzarlo manualmente.
- El script activo es `tools/x_news_robot.py`; el robot general anterior se conserva como respaldo, pero no se ejecuta automaticamente.
- Antes de consultar X se ejecuta `tools/test_x_news_robot.py`.
- El workflow instala Tesseract OCR en el runner, valida `data/noticias.json` y solo confirma ese archivo cuando cambia.
- El historial de Git conserva todas las versiones anteriores del JSON para poder restaurarlas.
