# control-de-horas-lap

Datos públicos que descarga la app Control de Horas LAP:

- `data/laboral.json`: SMI, tabla salarial del convenio y avisos oficiales.
- `data/convenio_frutas_granada.json`: texto del convenio, por artículos.
- `data/noticias.json`: noticias de respaldo, por si la app no puede leerlas de X.
- `POLITICA_PRIVACIDAD.md`: política de privacidad (enlazada desde Google Play).
- `foreground-service-demo.mp4`: vídeo del aviso por movimiento (declaración de Google Play).

## Robot laboral

`.github/workflows/labor-robot.yml` lanza `tools/labor_robot.py` de lunes a sábado. Revisa el BOE (SMI)
y todos los boletines del BOP de Granada desde la última revisión. Si sale la tabla salarial o el texto
del convenio de manipulado y envasado de frutas, hortalizas y patata, los comprueba y los publica, y abre
un aviso (issue) con lo que ha hecho. Lo que no sabe leer no lo publica: lo deja en el aviso para revisarlo.
