# Robot de noticias

La app lee `data/noticias.json` desde GitHub para mostrar comunicados y enlaces de interes.

El robot se ejecuta con GitHub Actions tres veces al dia. Si una fuente falla, queda apuntado en `data/news_robot_status.json`, pero no borra las noticias validas ni publica contenido dudoso.

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

## Meta API

El robot esta preparado para usar la Graph API oficial de Meta si se configuran estos secretos en GitHub Actions:

- `META_ACCESS_TOKEN`
- `UGT_INSTAGRAM_USER_ID`
- `UGT_FACEBOOK_PAGE_ID`
- `CCOO_INSTAGRAM_USER_ID`
- `CCOO_FACEBOOK_PAGE_ID`

El token debe tener permisos validos para leer las fuentes configuradas. Si faltan permisos o Meta bloquea la lectura, el robot sigue funcionando con las fuentes públicas y deja el aviso controlado en `data/news_robot_status.json`.

## Funcionamiento

- GitHub Actions ejecuta `.github/workflows/news-robot.yml` tres veces al día y también permite lanzarlo manualmente.
- El script `tools/news_robot.py` descarga fuentes, filtra contenido y actualiza `data/noticias.json`.
- Si una fuente falla, no borra las noticias buenas anteriores.
- Antes de guardar, el workflow valida que los JSON generados sean correctos.
