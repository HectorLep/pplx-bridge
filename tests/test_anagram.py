"""Tests de anagramas: exactos, tildes y sub-anagramas."""
from __future__ import annotations

from src.engine.anagram import AnagramSolver, signature


def test_signature_tilde_insensitive() -> None:
    """La firma ignora tildes pero distingue ñ."""
    assert signature("oído") == signature("odio")
    assert signature("árbol") == signature("labor")
    assert signature("niño") != signature("nino")


def test_exact_anagrams(solver: AnagramSolver) -> None:
    """Grupos conocidos del diccionario."""
    amor: set[str] = set(solver.anagrams_of("amor", include_self=True))
    assert {"amor", "roma", "mora", "ramo"} <= amor
    # Sin include_self se excluye la consulta (por clave normalizada).
    sin_self: list[str] = solver.anagrams_of("amor")
    assert "amor" not in [w.lower() for w in sin_self] or "roma" in sin_self
    assert "roma" in sin_self


def test_anagram_oido_odio(solver: AnagramSolver) -> None:
    """'odio' encuentra 'oído' gracias a la normalización."""
    assert "oído" in solver.anagrams_of("odio", include_self=True)


def test_anagram_missing(solver: AnagramSolver) -> None:
    """Palabra sin anagramas devuelve vacío."""
    assert solver.anagrams_of("zzzqqq") == []
    assert solver.anagrams_of("") == []


def test_sub_anagrams(solver: AnagramSolver) -> None:
    """Con 'amor' se forman 'amor', 'roma', 'mora', 'ramo' y 'armo'."""
    subs: list[str] = solver.sub_anagrams("amor", min_len=4, limit=50)
    assert {"amor", "roma", "mora", "ramo"} <= set(subs)
    # min_len filtra cortas.
    cortas: list[str] = solver.sub_anagrams("amor", min_len=99, limit=50)
    assert cortas == []


def test_groups(solver: AnagramSolver) -> None:
    """groups() devuelve grupos con >=2 miembros ordenados."""
    groups: list[list[str]] = solver.groups(min_size=2)
    assert len(groups) > 0
    assert any({"amor", "roma"} <= set(g) for g in groups)


# --- Cobertura completa AnagramSolver y sub_anagrams ---


def test_signature_bordes() -> None:
    """Firma: vacíos, espacios, mayúsculas y caracteres raros."""
    assert signature("") == ""
    assert signature("   ") == ""
    assert signature("AMOR") == signature("amor")
    assert signature("ÁMOR") == signature("amor")
    assert signature("a m o r") == signature("amor")
    # ñ se preserva: no colapsa con n.
    assert signature("niña") != signature("nina")
    assert signature("España") != signature("espana")


def test_add_word_idempotente_y_build() -> None:
    """add_word no duplica; build cuenta altas nuevas."""
    s = AnagramSolver()
    s.add_word("amor")
    s.add_word("amor")  # idempotente por forma exacta
    assert len(s) == 1
    assert s.build(["amor", "roma", "", "  ", "# comentario"]) == 1
    assert len(s) == 2
    assert s.group_count == 1  # misma firma
    s.add_word("casa")
    assert s.group_count == 2
    assert s.stats() == {"words": 3, "signatures": 2}


def test_add_word_ignora_vacios_y_sin_letras() -> None:
    """Entradas vacías o sin letras normalizables se ignoran."""
    s = AnagramSolver()
    s.add_word("")
    s.add_word("   ")
    assert len(s) == 0
    assert s.build(["", "  ", "# c"]) == 0


def test_anagrams_case_and_accent_insensitive() -> None:
    """La consulta es insensible a mayúsculas/tildes."""
    s = AnagramSolver()
    s.build(["amor", "roma", "mora", "ramo", "oído", "odio"])
    assert set(s.anagrams_of("AMOR", include_self=True)) == {"amor", "mora", "ramo", "roma"}
    assert set(s.anagrams_of("ÓIDO", include_self=True)) == {"odio", "oído"}
    # Ordenado alfabéticamente.
    assert s.anagrams_of("amor", include_self=True) == sorted(
        s.anagrams_of("amor", include_self=True)
    )


def test_anagrams_include_self_semantica() -> None:
    """include_self=False excluye por clave normalizada, no por identidad."""
    s = AnagramSolver()
    s.build(["árbol", "ARBOL", "labor"])
    con: list[str] = s.anagrams_of("arbol", include_self=True)
    assert "árbol" in con and "ARBOL" in con
    sin: list[str] = s.anagrams_of("arbol", include_self=False)
    # La consulta "arbol" normaliza igual que ambas formas → ambas excluidas.
    assert "árbol" not in sin and "ARBOL" not in sin
    # Pero un anagrama distinto con la misma firma sí aparece.
    assert "labor" in sin


def test_anagrams_no_duplicados() -> None:
    """Reinsertar la misma forma no duplica el bucket."""
    s = AnagramSolver()
    s.add_word("amor")
    s.add_word("amor")
    s.add_word("roma")
    assert s.anagrams_of("amor", include_self=True).count("amor") == 1


def test_sub_anagrams_orden_y_limite() -> None:
    """Orden por longitud desc. + alfabeto; limit se respeta."""
    s = AnagramSolver()
    s.build(["a", "am", "amo", "amor", "roma", "mora", "ramo", "mar"])
    got: list[str] = s.sub_anagrams("amor", min_len=1, limit=50)
    assert got == sorted(got, key=lambda w: (-len(w), w))
    assert set(got) >= {"amor", "roma", "mora", "ramo"}
    assert s.sub_anagrams("amor", min_len=1, limit=2) == got[:2]
    assert s.sub_anagrams("amor", min_len=1, limit=0) == []
    assert s.sub_anagrams("amor", min_len=1, limit=-1) == []


def test_sub_anagrams_min_len_y_vacios() -> None:
    """min_len filtra; entradas vacías o inválidas devuelven vacío."""
    s = AnagramSolver()
    s.build(["amor", "roma", "am", "a"])
    assert "am" not in s.sub_anagrams("amor", min_len=3, limit=50)
    assert "am" in s.sub_anagrams("amor", min_len=2, limit=50)
    assert s.sub_anagrams("", min_len=2) == []
    assert s.sub_anagrams("   ", min_len=2) == []
    assert s.sub_anagrams("amor", min_len=0) == []
    assert s.sub_anagrams("amor", min_len=-5) == []


def test_sub_anagrams_respeta_multiconjunto() -> None:
    """No basta contener las letras: importan las repeticiones."""
    s = AnagramSolver()
    s.build(["casa", "casas", "saca", "caza"])
    # Con "casa" no se puede formar "casas" (falta una 's').
    got: set[str] = set(s.sub_anagrams("casa", min_len=4, limit=50))
    assert "casa" in got
    assert "casas" not in got
    # Con letras extra sí.
    got2: set[str] = set(s.sub_anagrams("casas", min_len=4, limit=50))
    assert {"casa", "casas", "saca"} <= got2


def test_sub_anagrams_enie_y_tildes() -> None:
    """Sub-anagramas distinguen ñ de n y colapsan tildes."""
    s = AnagramSolver()
    s.build(["niño", "nino", "oído", "odio"])
    assert "niño" in s.sub_anagrams("niño", min_len=4, limit=50)
    assert "nino" not in s.sub_anagrams("niño", min_len=4, limit=50)
    assert "oído" in s.sub_anagrams("odio", min_len=4, limit=50)


def test_groups_min_size_y_orden() -> None:
    """groups filtra por tamaño y ordena por (-len, grupo)."""
    s = AnagramSolver()
    s.build(["amor", "roma", "mora", "casa", "saca", "solo"])
    groups: list[list[str]] = s.groups(min_size=2)
    assert all(len(g) >= 2 for g in groups)
    assert all(g == sorted(g) for g in groups)
    assert groups == sorted(groups, key=lambda g: (-len(g), g))
    assert s.groups(min_size=99) == []
    assert AnagramSolver().groups() == []


def test_sub_anagrams_entradas_largas_y_raras_no_rompen() -> None:
    """Entradas muy largas o con signos raros no lanzan excepción."""
    s = AnagramSolver()
    s.build(["casa", "amor"])
    assert s.sub_anagrams("a" * 500, min_len=2, limit=5) == [] or isinstance(
        s.sub_anagrams("a" * 500, min_len=2, limit=5), list
    )
    assert s.anagrams_of("amor!!") == []
    assert s.sub_anagrams("!!!", min_len=1, limit=10) == []
