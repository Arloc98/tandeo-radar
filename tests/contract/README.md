# Pruebas de contrato

Pruebas de aceptación escritas **antes** que el código que verifican. Convierten un criterio
en prosa ("el fallo del modelo debe ser visible") en algo que una máquina comprueba.

La regla que siguen: **probar la superficie observable, no el ayudante interno.** Lo que
llega a la API, lo que devuelve `extract()`, lo que responde `/refresh`, el código de salida
de un script. Así la implementación queda libre de cambiar de forma sin romper la prueba.

Esa regla salió de un fallo real: un contrato que solo ejercitaba `parse_items` quedó en
verde mientras `extract()` seguía devolviendo `[]` y `/refresh` respondía lo mismo ante una
llamada rota que ante un día sin cortes.

Ninguna toca la red: los clientes de Tavily y Nebius se sustituyen por dobles.
