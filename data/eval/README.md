# Set de evaluación del extractor

Aquí vive el set real de avisos para medir la precisión de `app/extractor.py`. Cada
aviso es un **par** de archivos con el mismo nombre:

| archivo | qué contiene |
|---|---|
| `NNN.txt` | el texto completo del aviso (SACMEX o alcaldía), en UTF-8 |
| `NNN.json` | los eventos del aviso, etiquetados a mano |

`NNN` es un número de tres dígitos: `001.txt` + `001.json`, `002.txt` + `002.json`, etc.
La meta son de 10 a 15 avisos. Un `.json` sin su `.txt` (o al revés) se reporta como
error y no entra a la métrica.

## El formato de `NNN.json`

Una **lista** de objetos, uno por cada interrupción que el aviso anuncie:

```json
[
  {
    "alcaldia": "Benito Juárez",
    "colonias": ["Del Valle", "Narvarte"],
    "start": "2026-09-25T08:00:00-06:00",
    "end": "2026-09-26T08:00:00-06:00",
    "kind": "suspension"
  }
]
```

- `alcaldia` (obligatoria): el nombre completo, con acentos.
- `colonias`: solo las que diga el aviso; nunca inventar colonias.
- `start` / `end`: ISO-8601 con offset `-06:00`, o `null` si el aviso no lo indica.
- `kind`: `suspension`, `tandeo` o `baja_presion`.
- Opcionales: `reason` (texto libre) y `confidence` (0 a 1).
- **No** pongan `source_url`: el script lo toma del `.txt`.

Regla de oro: etiqueten lo que dice el aviso, no lo que deduzcan. Un `null` honesto
vale más que una fecha inventada; `start` y `end` se comparan con tolerancia de
±60 minutos, así que una fecha adivinada casi siempre resta.

## Cómo correr la evaluación

```bash
python scripts/eval_extractor.py --tier fast
python scripts/eval_extractor.py --tier reasoning   # mismo set, otro nivel de modelo
```

Salida: una tabla markdown por archivo y un total, con precisión y recall de `alcaldia`,
`colonias` (traslape de conjuntos), `start`, `end` (±60 min) y `kind`, más las filas
descartadas por el extractor (`discarded rows`) por archivo y en total. El reporte
completo se guarda en `runs/eval-<timestamp>.json`.

- **precisión**: de lo que predijo el extractor, cuánto estaba bien.
- **recall**: de lo que decía la etiqueta, cuánto encontró el extractor.

`n/a` significa que no hubo nada que evaluar en ese campo (por ejemplo, cero
predicciones); no es lo mismo que `0.00`. Y un recall en cero con muchas filas
descartadas es un diagnóstico distinto: el modelo devolvió basura, no se equivocó de
colonia.

Si un `NNN.json` viene mal formado, el script lo reporta, lo salta y sigue con los
demás. Si `data/eval/` no tiene ningún `.txt`, sale con código 1.

---

## Procedencia de este set (24 de septiembre de 2026)

**Cómo se armó.** Los seis avisos se recuperaron con Tavily y se limpiaron para dejar el cuerpo
del texto. La primera línea de cada `.txt` conserva el medio y el titular; la URL y la
fecha de publicación quedaron en el registro local de la corrida, fuera del repo.

**Son notas de prensa, no boletines oficiales.** Se intentó recuperar avisos directos de SACMEX,
Segiagua y las alcaldías, y Tavily no los devuelve con contenido: casi todos llegan vacíos o son
páginas de trámites. Es una limitación real del pipeline, no una decisión de conveniencia.

**El etiquetado fue asistido y luego corregido a mano.** Claude hizo un primer pase leyendo cada
aviso, y la revisión humana es la que manda. Esto se declara aquí porque cambia lo que el número
puede afirmar: no es verdad humana independiente, es pre-etiquetado validado. Cada
desacuerdo entre etiqueta y modelo se revisó uno por uno.

Ya cobró su primera pieza: en `011` el pre-etiquetado omitió cuatro colonias que el modelo sí
había extraído correctamente, porque el pase inicial leyó extractos truncados en vez del texto
completo. El modelo tenía razón y la etiqueta estaba mal.

## Convención de fechas, para que la tolerancia de ±60 min signifique algo

- Si el aviso **no dice** cuándo empieza la afectación: `start` es `null`. No se infiere.
- Si da el día en que se restablece **sin hora**: `end` es ese día a las `23:59:59-06:00`.
- Si da hora explícita, se usa esa hora. Ejemplo real: "concluirán el 26 de junio... a partir de
  las 17:00" → `2026-06-26T17:00:00-06:00`.
- El año sale de la **fecha de publicación** del aviso, no del año en curso.

Sin esta convención la métrica de fechas mide desacuerdo de criterio y no precisión.

## Nombres de alcaldía

Se etiqueta el nombre oficial completo: `La Magdalena Contreras`, `Gustavo A. Madero`. Los avisos
suelen escribir `Magdalena Contreras` o `GAM`, y la comparación actual es literal, así que eso
cuenta como error sin serlo. Es deuda conocida de la métrica.
