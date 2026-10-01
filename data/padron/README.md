# Colonias de la Ciudad de México con distribución de agua por tandeo, 2019

Capa oficial de las 2,398 colonias de la CDMX, con su polígono y una bandera que dice si en
2019 recibían agua por tandeo. Es el padrón que sustituye la geocodificación por red.

## Procedencia

- **Portal:** [datos.cdmx.gob.mx](https://datos.cdmx.gob.mx/dataset/colonias-con-distribucion-de-agua-por-tandeo-2019),
  dataset `colonias-con-distribucion-de-agua-por-tandeo-2019` (id `cba78780-cdf0-464c-a9c2-3078c0fdf7f1`)
- **Licencia: CC-BY-4.0-ESP.** Obliga a dar atribución; está declarada en el README del repo.
- **Descargado:** 30 de septiembre de 2026, vía la API CKAN del portal (`/api/3/action/package_show`),
  nunca por una URL escrita a mano.

| archivo | bytes | verificación |
|---|---|---|
| `tandeo2019.zip` | 1,435,391 | tamaño igual al declarado por el portal; **el portal no publica hash para este recurso**. sha256 `fadf89193a0e0538b174c29f20a6857efcb28a79916621ea98edd285dc9e0fdd` |
| `diccionario.csv` | 2,294 | **md5 `930e66a2beda502b025dc10facce97b9`, igual al que publica el portal** |

El ZIP se versiona aquí a propósito: así los artefactos derivados se pueden regenerar y
auditar sin depender de que el portal siga en pie.

## Lo que hay que saber antes de parsearlo

Medido archivo en mano, no leído de la documentación:

- **2,399 registros, uno es basura**: el índice 2398 (Tláhuac) viene con `NOMBRE` vacío y una
  geometría de cero anillos. Hay que descartarlo. Quedan **2,398** colonias.
- **Proyección WGS84 lat/lon** (`.prj` dice `GCS_WGS_1984`), así que **no hay que reproyectar**:
  las coordenadas sirven tal cual para GeoJSON y Leaflet. 131,322 vértices, todos los polígonos
  de un solo anillo.
- El `.dbf` es **UTF-8**, pero **43 nombres traen la `Ñ` destruida** como la secuencia `ï¿½`
  (U+00EF U+00BF U+00BD), que es un carácter de reemplazo re-codificado dos veces. Siempre fue
  `Ñ` y se repara sin ambigüedad: `NIÑO JESUS`, `TAXQUEÑA`, `PEÑON DE LOS BAÑOS`, `EL ERMITAÑO`.
- **`NOMBRE` no tiene ninguna otra tilde**: está en ASCII mayúsculas. `DELEGACIO` sí las trae.
  Cualquier comparación va sin acentos y sin distinguir mayúsculas.
- **151 nombres tienen entre 2 y 4 polígonos** (319 filas). La bandera de tandeo **nunca** se
  contradice entre ellos, así que un nombre puede apuntar a varios polígonos sin ambigüedad.
- El diccionario `diccionario.csv` viene en **latin-1**, no en UTF-8.
- El ZIP trae entradas `__MACOSX/` (212 B cada una, metadatos de macOS). Se ignoran.

## Cobertura

273 de 2,398 colonias en tandeo. Seis alcaldías con cero (Azcapotzalco, Miguel Hidalgo,
Venustiano Carranza, Benito Juárez, Iztacalco, Cuauhtémoc); La Magdalena Contreras con 30 de
sus 51.

**Hueco real:** la capa cubre colonias, no pueblos originarios. Faltan San Andrés Mixquic,
San Juan Ixtayopan y San Pedro Tláhuac, que sí aparecen en avisos reales.

## Validación cruzada

El centroide de `EL MIRADOR / IZTAPALAPA` calculado desde este archivo da
`-99.10336, 19.3378`. La API de SACMEX reporta `19.337802, -99.103364` para esa colonia.
Coinciden a cuatro decimales por dos caminos independientes.
