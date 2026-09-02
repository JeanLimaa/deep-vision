"""Casamento de intencoes em pt-BR.

A tabela abaixo e, na pratica, a especificacao dos comandos de voz do
dispositivo -- inclusive com acentuacao ausente e erros tipicos de transcricao.
"""

from __future__ import annotations

import pytest

from app.speech.intents import Intent, IntentMatcher, normalize


@pytest.fixture()
def matcher() -> IntentMatcher:
    return IntentMatcher()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("o que tem na minha frente?", Intent.DESCRIBE_SCENE),
        ("descreva o ambiente", Intent.DESCRIBE_SCENE),
        ("o que voce ve", Intent.DESCRIBE_SCENE),
        ("onde esta a cadeira", Intent.FIND_OBJECT),
        ("tem uma pessoa aqui?", Intent.FIND_OBJECT),
        ("procurar mochila", Intent.FIND_OBJECT),
        ("quantas pessoas tem aqui", Intent.COUNT_OBJECT),
        ("o caminho esta livre?", Intent.OBSTACLE_STATUS),
        ("posso andar", Intent.OBSTACLE_STATUS),
        ("pausar", Intent.STOP),
        ("para de falar", Intent.STOP),
        ("iniciar", Intent.START),
        ("aumentar o volume", Intent.VOLUME_UP),
        ("volume mais baixo", Intent.VOLUME_DOWN),
        ("repetir", Intent.REPEAT),
        ("qual o status da bateria", Intent.STATUS),
        ("ajuda", Intent.HELP),
    ],
)
def test_known_commands_are_recognized(matcher, text, expected):
    assert matcher.match(text).intent is expected


def test_accents_and_case_do_not_matter(matcher):
    assert matcher.match("O QUE TEM NA MINHA FRENTE?").intent is Intent.DESCRIBE_SCENE
    assert matcher.match("onde está a cadeira").intent is Intent.FIND_OBJECT


def test_object_slot_is_extracted(matcher):
    assert matcher.match("onde esta a cadeira").slots["label"] == "chair"
    assert matcher.match("quantas pessoas tem aqui").slots["label"] == "person"
    assert matcher.match("procurar celular").slots["label"] == "cell phone"


def test_longer_name_wins_over_substring(matcher):
    # "vaso sanitario" nao pode ser confundido com "vaso".
    assert matcher.match("onde fica o vaso sanitario").slots["label"] == "toilet"


def test_find_without_object_degrades_to_scene_description(matcher):
    assert matcher.match("onde").intent is Intent.DESCRIBE_SCENE


def test_bare_object_name_is_treated_as_search(matcher):
    match = matcher.match("cadeira")
    assert match.intent is Intent.FIND_OBJECT
    assert match.slots["label"] == "chair"


def test_unknown_text_is_reported_as_such(matcher):
    match = matcher.match("qwerty zxcvb")
    assert match.intent is Intent.UNKNOWN
    assert not match.understood


def test_normalization_strips_accents_and_punctuation():
    assert normalize("Onde está a cadeira?!") == "onde esta a cadeira"
