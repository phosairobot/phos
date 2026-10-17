import pytest

from robot.voice.normalization import SpeechTextNormalizer


@pytest.mark.parametrize(("source", "expected"), [
    ("24", "ventiquattro"),
    ("42", "quarantadue"),
    ("100", "cento"),
    ("101", "centouno"),
    ("1000", "mille"),
    ("24 °C", "ventiquattro gradi Celsius"),
    ("24°C", "ventiquattro gradi Celsius"),
    ("50%", "cinquanta per cento"),
    ("-18,5", "meno diciotto virgola cinque"),
])
def test_italian_numbers_and_common_units_are_normalized(source, expected):
    assert SpeechTextNormalizer().normalize(source, "it") == expected


@pytest.mark.parametrize("source", [
    "1.5.0", "192.168.1.10", "https://example.test/24", "file123.wav", "0x24af", "GPT-4",
])
@pytest.mark.parametrize("language", ["it", "en"])
def test_identifiers_are_preserved(source, language):
    assert SpeechTextNormalizer().normalize(source, language) == source


def test_unsupported_language_is_unchanged():
    assert SpeechTextNormalizer().normalize("24 °C", "fr") == "24 °C"


@pytest.mark.parametrize(("source", "expected"), [
    ("24", "twenty-four"),
    ("42", "forty-two"),
    ("100", "one hundred"),
    ("101", "one hundred one"),
    ("1000", "one thousand"),
    ("18.5", "eighteen point five"),
    ("24 °C", "twenty-four degrees Celsius"),
    ("50%", "fifty percent"),
    ("-3", "minus three"),
])
def test_english_numbers_and_units_are_normalized(source, expected):
    assert SpeechTextNormalizer().normalize(source, "en") == expected


@pytest.mark.parametrize("language", ["it-IT", "it_IT"])
def test_italian_locale_aliases(language):
    assert SpeechTextNormalizer().normalize("24", language) == "ventiquattro"


@pytest.mark.parametrize("language", ["en-US", "en-GB"])
def test_english_locale_aliases(language):
    assert SpeechTextNormalizer().normalize("24", language) == "twenty-four"
