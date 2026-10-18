"""Provider-neutral speech rendering; logical response text is never mutated."""
from __future__ import annotations

import re


class _CardinalSpeechNormalizer:
    """Small dependency-free language strategy for bounded ordinary values."""

    _number = re.compile(r"(?<![\w.])(-?\d+(?:[,.]\d+)?)(?![\w.])")
    _celsius = re.compile(r"(?<![\w.])(-?\d+(?:[,.]\d+)?)\s*°\s*C\b", re.IGNORECASE)
    _percent = re.compile(r"(?<![\w.])(-?\d+(?:[,.]\d+)?)\s*%")
    name = "base"
    decimal_word = minus_word = celsius_unit = percent_unit = ""

    def normalize(self, text: str) -> str:
        text = self._celsius.sub(lambda match: f"{self._number_words(match.group(1))} {self.celsius_unit}", text)
        text = self._percent.sub(lambda match: f"{self._number_words(match.group(1))} {self.percent_unit}", text)
        return self._number.sub(lambda match: self._number_words(match.group(1)), text)

    def _number_words(self, token: str) -> str:
        negative = token.startswith("-")
        if negative:
            token = token[1:]
        if "." in token or "," in token:
            whole, fractional = re.split(r"[,.]", token, maxsplit=1)
            result = f"{self._integer(int(whole))} {self.decimal_word} " + " ".join(
                self._integer(int(digit)) for digit in fractional
            )
        else:
            value = int(token)
            if not 0 <= value <= 9999:
                return ("-" if negative else "") + token
            result = self._integer(value)
        return f"{self.minus_word} {result}" if negative else result

    def _integer(self, value: int) -> str:  # pragma: no cover - strategy contract
        raise NotImplementedError


class ItalianSpeechNormalizer(_CardinalSpeechNormalizer):
    name, decimal_word, minus_word = "italian", "virgola", "meno"
    celsius_unit, percent_unit = "gradi Celsius", "per cento"

    def _integer(self, value: int) -> str:
        units = ("zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove",
                 "dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici",
                 "diciassette", "diciotto", "diciannove")
        tens = ("", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta")
        if value < 20:
            return units[value]
        if value < 100:
            ten, remainder = divmod(value, 10)
            prefix = tens[ten][:-1] if remainder in (1, 8) else tens[ten]
            return prefix + ("" if not remainder else units[remainder])
        if value < 1000:
            hundreds, remainder = divmod(value, 100)
            prefix = "cento" if hundreds == 1 else units[hundreds] + "cento"
            return prefix + ("" if not remainder else self._integer(remainder))
        thousands, remainder = divmod(value, 1000)
        prefix = "mille" if thousands == 1 else self._integer(thousands) + "mila"
        return prefix + ("" if not remainder else self._integer(remainder))


class EnglishSpeechNormalizer(_CardinalSpeechNormalizer):
    name, decimal_word, minus_word = "english", "point", "minus"
    celsius_unit, percent_unit = "degrees Celsius", "percent"

    def _integer(self, value: int) -> str:
        units = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen")
        tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
        if value < 20:
            return units[value]
        if value < 100:
            ten, remainder = divmod(value, 10)
            return tens[ten] if not remainder else f"{tens[ten]}-{units[remainder]}"
        if value < 1000:
            hundreds, remainder = divmod(value, 100)
            prefix = f"{units[hundreds]} hundred"
            return prefix if not remainder else f"{prefix} {self._integer(remainder)}"
        thousands, remainder = divmod(value, 1000)
        prefix = f"{self._integer(thousands)} thousand"
        return prefix if not remainder else f"{prefix} {self._integer(remainder)}"


class SpeechTextNormalizer:
    """Dispatch a protected speech-only rendering to a language strategy."""

    _unsafe_tokens = re.compile(
        r"https?://\S+|\b0x[0-9a-fA-F]+\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|"
        r"\b\d+(?:\.\d+){2,}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
        r"\b[\w-]*\d[\w.-]*\.[A-Za-z][\w.-]*\b|\b[A-Za-z_][\w-]*\d[\w-]*\b"
    )
    _normalizers = {"it": ItalianSpeechNormalizer(), "en": EnglishSpeechNormalizer()}

    @staticmethod
    def base_language(language: str | None) -> str | None:
        if not isinstance(language, str) or not language.strip():
            return None
        return language.strip().lower().replace("_", "-").split("-", 1)[0]

    def normalizer_name(self, language: str | None) -> str:
        normalizer = self._normalizers.get(self.base_language(language))
        return normalizer.name if normalizer is not None else "passthrough"

    def normalize(self, text: str, language: str | None) -> str:
        normalizer = self._normalizers.get(self.base_language(language))
        if not isinstance(text, str) or normalizer is None:
            return text
        protected: list[str] = []

        def protect(match: re.Match) -> str:
            protected.append(match.group(0))
            return chr(0xE000 + len(protected) - 1)

        result = normalizer.normalize(self._unsafe_tokens.sub(protect, text))
        for index, original in enumerate(protected):
            result = result.replace(chr(0xE000 + index), original)
        return result
