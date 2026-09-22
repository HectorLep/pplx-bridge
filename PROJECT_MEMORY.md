# PROJECT MEMORY

Memoria acumulada de auditorias Perplexity (generado por tools/run_pplx_audit.py).
Cada corrida registra hallazgos por categoria y actualiza BEST_SCORE.

## BEST_KNOWN_STATE

BEST_SCORE: 91  (actualizado 2026-09-22T04:13:59+00:00)

## SECURITY

- [2026-09-22] Cobertura de seguridad/límites de entrada. No hay saneamiento contra entradas extremadamente largas en /autocomplete o /contains a nivel de API (solo se limita limit, no la longitud del string de consulta), aunque para un proyecto académico esto es un riesgo bajo.

## REGRESSIONS

- [2026-09-22] Normalización lingüística correcta. El truco del centinela \x00 para proteger la ñ durante la descomposición NFD antes de eliminar diacríticos es una solución elegante a un bug común en motores léxicos en español (evita que "niño" colapse a "nino"). Está bien testeado (test_normalize_tildes, test_search_enie, test_sub_anagrams_enie_y_tildes).
- [2026-09-22] DictionaryService atómico. El patrón de construir Trie, NGramIndexer y AnagramSolver nuevos en variables locales y solo publicarlos bajo RLock al final es correcto: evita que lectores concurrentes vean estado a medio construir, y si load_word_list lanza FileNotFoundError, el estado previo queda intacto (verificado en test_load_dictionary_ok_and_missing). El test de concurrencia con lectores + escr
- [2026-09-22] Test de concurrencia superficial. test_service_concurrent_reads_no_crash solo verifica ausencia de excepciones, no invariantes de consistencia (p. ej., que un lector nunca vea word_count de una estructura y trie de otra a mitad de swap). Dado que el diseño de snapshot bajo lock lo garantiza estructuralmente, es aceptable, pero un assert explícito reforzaría la prueba.

## ARCHITECTURE

- [2026-09-22] Tras revisar los 8 archivos completos (trie.py, indexer.py, anagram.py, server.py y sus 4 suites de test), la refactorización cumple con creces los objetivos declarados. El código demuestra madurez de ingeniería en varios frentes:
- [2026-09-22] Tipado y estilo. Anotaciones consistentes (dict[str, set[int]], list[str] | None, -> None), __slots__ en TrieNode/RadixNode/NGramIndexer para eficiencia de memoria, y from __future__ import annotations en todos los módulos. Los docstrings documentan complejidad algorítmica (O(m), O(p+k), O(1) promedio) de forma rigurosa, no decorativa.
- [2026-09-22] En conjunto, el nivel de rigor (manejo de tildes/ñ, atomicidad thread-safe, complejidad documentada y verificada con tests de latencia p95, ausencia de falsos positivos/negativos) sitúa este código por encima del estándar típico de un proyecto de curso. Los puntos descontados son de trazabilidad (archivos de soporte no visibles) y pulido cosmético, no defectos funcionales.

## OBSERVATIONS

- [2026-09-22] OBSERVACIONES
- [2026-09-22] Fortalezas técnicas
- [2026-09-22] Vector de frecuencias en anagramas. La firma canónica ("".join(sorted(key))) combinada con el índice _sig_counters (Counter cacheado) para el fallback acotado por longitud es una solución sólida: resuelve anagramas exactos en O(1) y sub-anagramas en O(C) donde C depende de la consulta, no del vocabulario, con un límite documentado (_MAX_SUBSETS) para evitar explosión combinatoria.
- [2026-09-22] Índice N-gramas. La estrategia de ordenar posting lists por tamaño antes de intersectar, verificar candidatos contra la clave normalizada (evitando falsos positivos), y el fallback lineal para consultas sub-N-grama, está bien pensada y validada con un test de recall/precisión exacto contra oráculo de escaneo lineal (test_contains_no_false_negatives), que es la prueba más rigurosa de todo el set.
- [2026-09-22] Observaciones y riesgos menores
- [2026-09-22] Dependencias no auditables. Los tests referencian fixtures (trie, solver, indexer, words) que deben vivir en un conftest.py no incluido en esta entrega. No puedo verificar que esas fixtures carguen datos representativos ni que las 73 pruebas realmente pasen sin ver ese archivo y loader.py.
- [2026-09-22] Recursión en Trie.delete. La función interna _rec es recursiva sobre la longitud de la palabra; para el vocabulario español no es un riesgo real, pero no es tan defensivo como el resto del código (que prefiere DFS iterativo en autocomplete).
- [2026-09-22] _estimate_subsets con corte anticipado. El valor retornado tras el break no es el producto real sino un valor parcial superior al umbral; funciona correctamente porque solo se usa para la comparación > _MAX_SUBSETS, pero si en el futuro se reutiliza para telemetría o logging, el valor sería engañoso. Vale la pena un comentario adicional o renombrar a algo como _exceeds_max_subsets.

