# Tandeo Radar · complemento del pitch

*Respaldo de la presentación: qué se midió, cómo se construyó y qué salió mal. La problemática y
la solución están en el [README](../README.md). Cada cifra de este documento salió de una corrida
real o de un registro del repositorio; ninguna está redondeada.*

**Demo:** [tandeo-radar.vercel.app](https://tandeo-radar.vercel.app)

---

## 1. Los números

### 1.1 Precisión del extractor

Sobre `data/eval/`: 6 avisos reales de prensa, 10 eventos etiquetados y verificados a mano.
Nivel `fast` (`nvidia/Nemotron-3_5-Lightning`), **3 corridas**.

| Campo | Acierto | Por corrida |
|---|---|---|
| **Alcaldía** | **30/30 · 1.00** | 10, 10, 10 |
| **Colonias** | **188/189 · 0.99** | 63, 62, 63 |
| Tipo de corte | 28/30 · 0.93 | 10, 9, 9 |
| **Día de restablecimiento** | **15/15 · 1.00** | sobre las 5 etiquetas con `end` |
| Filas descartadas por malformación | **0** | en ninguna corrida |

**La frase para el pitch:** el extractor identifica *qué pasa y dónde* con precisión
prácticamente total, y acierta el **día** de restablecimiento siempre que el aviso lo declara.

### 1.2 La debilidad, dicha antes de que la pregunten

**El modelo inventa una fecha de inicio en 21 de 30 casos (70%)** donde el aviso no la declara.
Es la debilidad real y conviene nombrarla primero: decirla da más credibilidad que esconderla.

### 1.3 Dos cifras que el reporte crudo distorsiona

`scripts/eval_extractor.py` imprime `start ≈ 0.10` y `end ≈ 0.30`. **Ninguna de las dos debe
citarse sin contexto**, porque engañan en direcciones opuestas:

- **`start` no mide precisión, mide abstención.** Las 10 etiquetas tienen `start` nulo, porque la
  convención de etiquetado prohíbe inferir un inicio que el aviso no declara. El número no dice
  que el modelo se equivoque: dice que no se abstiene.
- **`end` se hunde por una discrepancia de convención, no de comprensión.** A nivel de día el
  acierto es 15/15; con la tolerancia de ±60 minutos baja a 9/15. Toda la diferencia es que el
  modelo devuelve `00:00:00` donde la etiqueta usa `23:59:59` para un día sin hora explícita.

### 1.4 Del proceso

| | |
|---|---|
| Líneas de aplicación | 912 |
| Líneas de prueba | **2,285** (2.5× el código) |
| Pruebas automáticas | 136, todas en verde |
| Commits | 61 en el repositorio de trabajo, 12 en el público |
| Tareas despachadas a un agente constructor | 7, con 5 modelos distintos |
| Tokens de entrada de los despachos documentados | ~8.2 millones, 94–96% servido desde caché |
| Duración de un despacho | de 86 s a 25 min según el modelo |
| Corrida del pipeline en producción | 5.6 s |

---

## 2. Cómo se construyó

### 2.1 Dos agentes con papeles distintos

El proyecto no se escribió a mano ni se le pidió a un modelo "hazme la app". Se separó en dos
papeles con autoridad distinta:

| | **Orquestador** | **Constructor** |
|---|---|---|
| Quién | Claude Code (Opus) | Command Code, CLI headless |
| Qué hace | Divide el trabajo, escribe la especificación **y las pruebas de aceptación**, revisa el diff, decide | Implementa una sola especificación, prueba, reporta |
| Qué **no** hace | Escribir el código de la feature | Tocar git, las especificaciones o las reglas |

La regla que da sentido a todo: **el orquestador escribe la prueba antes de despachar la tarea.**
El constructor recibe un contrato ejecutable, no una descripción en prosa. "Cumple los criterios"
deja de depender de que alguien lea con cuidado.

Cada tarea va a su propia rama, con un modelo elegido según el riesgo: barato para lo mecánico,
caro para lo que puede romper el producto en silencio.

| Tarea | Riesgo | Modelo constructor | Veredicto |
|---|---|---|---|
| T-001 · niveles de modelo con IDs verificados | Medio | DeepSeek V4 Flash | Aceptar con un hallazgo |
| T-002 · extracción estructurada, fallos ruidosos | Alto | Kimi K2.7 | **Rechazar** |
| T-003 · set de evaluación y métrica | Bajo | MiMo V2.6 Flash | Aceptar |
| T-004 · el reintento escala de nivel | Alto | Kimi K2.7 | Aceptar |
| T-005 · extracción determinista | Alto | Nemotron 3 Ultra | Aceptar con un criterio incumplido |
| T-006 · el collector no entrega titulares | Alto | GLM-5.3 | Aceptar |
| T-007 · frescura y reintento | Alto | Kimi K2.7 | Aceptar |

### 2.2 Herramientas

**Para construir el producto**

- **Nebius Token Factory** — API compatible con OpenAI. Dos niveles: `Nemotron-3_5-Lightning`
  para cada aviso, `nemotron-3-super-120b-a12b` para los casos ambiguos.
- **Tavily** — búsqueda de avisos en prensa y fuentes oficiales.
- **OpenStreetMap Nominatim** — geocodificación de colonias.
- **FastAPI + Leaflet**, sin framework de frontend. **Vercel** para el despliegue.

**Para construir con agentes**

- **Claude Code** como orquestador, con automatización de Chrome para verificar la interfaz en el
  navegador real en lugar de suponer que funciona.
- **Command Code** como constructor headless, con *hooks* propios: `PreToolUse` rechaza
  escrituras a archivos protegidos, `Stop` corre la compuerta antes de dejarlo terminar y le
  devuelve el error hasta tres veces.
- **`scripts/gate.py`** — compuerta mecánica que repite *todo* lo que el constructor afirma:
  alcance, hashes de archivos protegidos, dependencias, secretos, suite completa, contrato de la
  tarea y humo del servidor. El reporte del constructor nunca se toma como evidencia.
- **Skill `web-design-guidelines` de Vercel** para auditar la interfaz, y un `DESIGN.md` del
  sistema Geist como referencia visual.

---

## 3. Lo que salió mal

Esta es la sección que sostiene a las demás. Ordenada por capa.

### 3.1 El modelo miente en silencio, que es el peor modo de fallo

**El problema.** Para un servicio que avisa cortes de agua, "no hay cortes" y "la llamada se
rompió" no pueden verse igual. Y se veían igual: el modelo devolvía una lista vacía válida,
`parse_items` se tragaba la excepción y la app reportaba cero eventos con cero errores.

**Peor:** Lightning a veces deja `reasoning_content` vacío y vuelca su razonamiento en `content`,
así que el JSON no parsea. Es un fallo de formato disfrazado de día tranquilo.

**Solución.** El extractor marca el *vacío sospechoso*: si el texto tiene señales de interrupción
—una alcaldía nombrada, la palabra suspensión— y el modelo no devuelve nada, eso es un error
explícito. `/refresh` lo reporta y la interfaz lo muestra.

**Resuelto.**

### 3.2 `temperature=0` no garantiza determinismo

**El problema.** Con temperatura 0.1 el extractor devolvía cero eventos para el mismo aviso entre
el 25% y el 83% de las veces. Bajarla a 0 lo redujo, **no lo eliminó**:

| tanda sobre el aviso 006 | vacíos |
|---|---|
| la que se usó para escribir la spec | 0 de 6 |
| siguiente | 1 de 6 |
| siguiente | 1 de 5 |
| siguiente | **4 de 8** |

Medido una sola vez, la conclusión habría sido exactamente la contraria.

**Solución.** El vacío sospechoso se reintenta **escalando al nivel de razonamiento**, no
repitiendo con el modelo que acaba de fallar. Medido: de 4 de 8 vacíos a 2 de 8.

**Resuelto a medias**, y así se declara. Es mitigación estadística, no una garantía.

### 3.3 Diagnosticamos mal una causa, y hubo que retractarse

**El problema.** Se afirmó —en el repositorio y en varios mensajes de commit— que las consultas
con acento hacían que Tavily devolviera resultados sin contenido. **Era falso.** Se concluyó de
un A/B de cuatro muestras que capturó una racha.

Al volver a medir: la misma consulta con acento devolvió contenido completo en **45 de 45**
resultados.

**La causa real** era degradación intermitente de Tavily, que responde `200 OK` con `raw_content`
en `null`, y un collector que aceptaba el recorte como si fuera el aviso.

**Solución.** Se corrigió por escrito, y la lección subió a las reglas del proyecto: medir una
tasa sobre muchas corridas en lugar de leer causalidad en cuatro muestras. El mismo error se
había cometido antes con la temperatura.

**Resuelto**, y es el hallazgo del que más se aprendió.

### 3.4 Nuestro propio contrato dejó pasar el fallo que existía para atrapar

**El problema.** La prueba de aceptación de T-002 ejercitaba `parse_items` directamente y nunca
`extract()`. El constructor la satisfizo mientras `extract()` seguía devolviendo `[]` y
`/refresh` seguía respondiendo lo mismo ante una llamada rota que ante un día sin cortes. La
compuerta pasó entera. El fallo se entregó igual.

**Solución.** Regla dura: **probar la superficie observable, no el ayudante interno.** Lo que
llega a la API, lo que devuelve `extract()`, lo que responde `/refresh`, el código de salida de
un script.

**Resuelto**, y es la razón de que haya 2,285 líneas de prueba contra 912 de aplicación.

### 3.5 La métrica de precisión iba a mentir en el pitch

**El problema.** Antes de normalizar nombres, una comparación literal puso la precisión de
alcaldía en **0.00** porque el aviso decía `Benito Juarez` y la etiqueta `Benito Juárez`. Y el
reporte crudo de fechas engaña en las dos direcciones (ver §1.3).

**Solución.** Normalizar contra la lista oficial de las 16 alcaldías, y documentar la métrica
**con sus distorsiones** en lugar de publicar el número redondo.

**Resuelto.**

### 3.6 El buscador no ancla geografía

**El problema.** Una corrida en vivo devolvía avisos de **Nuevo León, Yucatán y Chiapas** —uno de
ellos un corte de *luz*—, y el extractor los convertía en eventos. Un evento en San Pedro Garza
García dentro de una app de la Ciudad de México es un defecto, no ruido.

Añadir `language="es"` eliminó el ruido en inglés, pero **no bastó**: el español cubre todo el
país. Medido en dos corridas, seguían saliendo 2 de 8 resultados fuera de la ciudad.

**Solución.** La pertenencia la decide el código, no el buscador: un resultado que no nombre la
ciudad, su organismo de agua o alguna de las 16 alcaldías se descarta y se cuenta. Vuelto a
medir: **2 resultados fuera → 0**, en ambas corridas.

**Resuelto.**

### 3.7 Tres bugs que solo aparecen al ejecutar, no al leer

- **El mapa se veía en blanco con una marca de agua.** Los basemaps gratuitos de CARTO ahora
  exigen llave: respondían `200 OK` con un *tile* de 2 KB que era solo el aviso. Se detectó
  midiendo el tamaño de la respuesta —**2,049 B contra 9,988 B** de OpenStreetMap—, no leyendo el
  código. Se cambió a OSM y el monocromo salió de un filtro CSS, que además dio el modo oscuro
  gratis.
- **La geocodificación habría reventado en el primer uso en producción.** `geo.py` hacía
  `mkdir()` como primera sentencia, y en el sistema de archivos de solo lectura de Vercel eso
  falla *antes* de geocodificar nada. La caché era una dependencia dura disfrazada de
  optimización. Ahora vive en memoria y el disco es persistencia oportunista.
- **El límite de una corrida por sesión no limitaba nada.** El botón decía "Corrida ya ejecutada"
  y seguía funcionando: una línea posterior lo rehabilitaba. Se detectó **probando en producción
  con el navegador**, no revisando el diff.

**Resueltos los tres.** El patrón es el mismo: ninguno era visible en el código.

---

## 4. Lo que sigue roto

Declararlo es parte del pitch, no una concesión.

- **El pipeline en vivo rinde poco hoy.** Una corrida devuelve 1 o 2 eventos, y a menudo con
  colonias no geocodificables como `"TODAS las colonias de la alcaldía"`. El snapshot de la demo
  viene del set de evaluación —avisos reales, etiquetados a mano— y la interfaz lo declara.
- **Cada corrida descarta 6 avisos reales.** Los 6 resultados degradados de cada búsqueda son
  publicaciones de Facebook e Instagram, varias de ellas avisos legítimos de la ciudad —incluido
  uno del propio organismo operador. Tavily los devuelve sin contenido y el filtro los rechaza,
  con razón: un titular no es un aviso. **Sabemos exactamente dónde están los siguientes seis
  avisos por corrida y por qué aún no podemos leerlos.**
- **No hay persistencia.** El estado vive en memoria, lo que bloquea histórico, deduplicación y
  detección de cambios — que es lo que desbloquearía predecir patrones de tandeo.
- **El modelo inventa fechas de inicio**, y una vez devolvió un fin anterior al inicio con años
  inventados. Falta una validación que rechace rangos incoherentes.
- **Sin autenticación en la demo pública.** El gasto se acota con un techo de créditos leído del
  medidor de Tavily antes de gastar; el límite por sesión es fricción honesta, no seguridad, y
  así está comentado en el código.

---

## 5. Lo que nos llevamos del método

1. **Escribir la prueba antes de despachar** convierte la revisión en una comprobación, no en una
   opinión. Pero una prueba débil pasa igual: el contrato de T-002 se cumplió mientras el fallo
   se entregaba.
2. **No creerle al reporte del agente.** La compuerta repite la suite completa desde cero. En
   T-001 todo pasó mientras el guardián central del script no tenía ni una prueba de regresión.
3. **Una sola medición no dice nada.** Costó dos veces: con la temperatura y con los acentos.
4. **Probar en el navegador y en producción.** Tres de los bugs de este proyecto eran invisibles
   en el código y evidentes al primer clic.
5. **Publicar la debilidad medida.** "Inventa la fecha de inicio en el 70% de los avisos que no
   la declaran" es más útil, y más creíble, que un porcentaje redondo sin contexto.

---

## Anexo · cómo reproducir las mediciones

```bash
# Precisión del extractor sobre el set etiquetado
python scripts/eval_extractor.py --tier fast
python scripts/eval_extractor.py --tier reasoning

# Suite completa
pytest -q

# Verificar que los IDs de modelo siguen vivos en Token Factory
python scripts/list_models.py
```

**Límites de la medición, declarados:** seis avisos y tres corridas son pocos; orientan, no
publican un porcentaje redondo. El set es prensa, no boletines oficiales, porque Tavily no
devuelve los avisos directos del organismo operador con contenido. Las reglas de etiquetado y la
procedencia del set están en [`data/eval/README.md`](../data/eval/README.md).
