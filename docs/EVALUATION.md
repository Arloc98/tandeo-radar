# Precisión del extractor

Medido el 25 de septiembre de 2026 sobre `data/eval/`: 6 avisos reales de prensa, 10 eventos
etiquetados y verificados a mano. Tier `fast` (`nvidia/Nemotron-3_5-Lightning`), **3 corridas**.

```bash
python scripts/eval_extractor.py --tier fast
```

## Resultados

| Campo | Acierto | Por corrida |
|---|---|---|
| Alcaldía | **30/30 · 1.00** | 10, 10, 10 |
| Colonias | **188/189 · 0.99** | 63, 62, 63 |
| Tipo de corte | **28/30 · 0.93** | 10, 9, 9 |
| Fecha de fin, día calendario | **15/15 · 1.00** | sobre las 5 etiquetas que traen `end` |
| Filas descartadas | **0** | nada malformado en ninguna corrida |

El extractor identifica **qué pasa y dónde** con precisión prácticamente total, y acierta el
**día** de restablecimiento siempre que el aviso lo declara.

## Las dos cifras que el reporte crudo distorsiona

`eval_extractor.py` reporta `start ≈ 0.10` y `end ≈ 0.30`. Ninguna de las dos significa lo que
aparenta, y conviene no citarlas sin este contexto:

- **`start` no mide precisión, mide abstención.** Las 10 etiquetas tienen `start` nulo, porque
  la convención de `data/eval/README.md` prohíbe inferir un inicio que el aviso no declara. Lo
  que el campo mide es si el modelo se abstiene igual — y no lo hace: **inventa una fecha de
  inicio en 21 de 30 casos (70%)**. Esa es la debilidad real del extractor.
- **`end` se hunde por una discrepancia de convención, no de comprensión.** A nivel de día el
  acierto es 15/15; dentro de la tolerancia de ±60 minutos baja a 9/15. La diferencia completa
  es que el modelo devuelve `00:00:00` donde la etiqueta usa `23:59:59` para un día sin hora
  explícita.

## Hallazgos de método

**Las corridas no son idénticas pese a `temperature=0`.** `kind` dio 1.00, 0.90 y 0.90 en tres
corridas consecutivas. Una sola corrida habría reportado un 100% falso. Cualquier cifra de este
archivo que se cite en una presentación debe venir de varias corridas.

**La escalada de nivel funciona en vivo.** El aviso `006` disparó un "vacío sospechoso" —el
modelo devolvió lista vacía sobre un texto con señales de interrupción—, se reintentó en el tier
de razonamiento y devolvió sus 4 eventos.

**Error real del modelo, no de etiqueta:** en el aviso `003` devolvió `start=2024-12-01` y
`end=2024-05-15`, un fin anterior al inicio y con años inventados. Falta una validación que
rechace rangos incoherentes.

## Límites de esta medición

Seis avisos y tres corridas son pocos: sirven para orientar, no para publicar un porcentaje
redondo. El set es prensa, no boletines oficiales, porque Tavily no devuelve los avisos directos
de SACMEX con contenido. Y la comparación de alcaldías es literal, así que `Magdalena Contreras`
frente a `La Magdalena Contreras` contaría como error si el modelo no usara el nombre oficial.
