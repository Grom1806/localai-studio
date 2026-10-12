"""Russian text normalization for TTS (runs before every Russian synthesis).

Expands numbers, ordinals, dates, times, currencies, percentages, units and abbreviations into spoken words,
applies user pronunciation overrides (config/ru_pronunciation.json) and tidies punctuation for natural pauses.
Names and Latin-script brand names are left untouched unless an override exists.
"""
import json
import re
from pathlib import Path

from num2words import num2words

OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "config" / "ru_pronunciation.json"
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
ABBR = {
    r"т\.\s?е\.": "то есть", r"т\.\s?к\.": "так как", r"т\.\s?д\.": "так далее", r"т\.\s?п\.": "тому подобное",
    r"и\s?т\.\s?д\.": "и так далее", r"и\s?т\.\s?п\.": "и тому подобное", r"и\s?др\.": "и другие",
    r"т\.\s?н\.": "так называемый", r"напр\.": "например", r"см\.": "смотрите", r"др\.": "другие",
    r"млрд\.?": "миллиардов", r"млн\.?": "миллионов", r"тыс\.": "тысяч", r"руб\.": "рублей", r"коп\.": "копеек",
    r"долл\.": "долларов", r"ул\.": "улица", r"пр-т": "проспект", r"д\.(?=\s*\d)": "дом", r"кв\.(?=\s*\d)": "квартира",
    r"г\.(?=\s*[А-ЯЁ])": "город", r"гг\.": "годы", r"вв\.": "века", r"в\.(?=\s)": "век", r"с\.(?=\s*\d)": "страница",
    r"№": "номер", r"проф\.": "профессор", r"акад\.": "академик", r"им\.": "имени", r"ок\.": "около",
    r"мин\.": "минут", r"сек\.": "секунд", r"ч\.(?=\s)": "часов",
}
UNITS = {  # unit: (1, 2-4, 5+)
    "км": ("километр", "километра", "километров"), "м": ("метр", "метра", "метров"),
    "см": ("сантиметр", "сантиметра", "сантиметров"), "мм": ("миллиметр", "миллиметра", "миллиметров"),
    "кг": ("килограмм", "килограмма", "килограммов"), "г": ("грамм", "грамма", "граммов"),
    "л": ("литр", "литра", "литров"), "мл": ("миллилитр", "миллилитра", "миллилитров"),
    "ч": ("час", "часа", "часов"), "мин": ("минута", "минуты", "минут"), "сек": ("секунда", "секунды", "секунд"),
    "%": ("процент", "процента", "процентов"), "°C": ("градус Цельсия", "градуса Цельсия", "градусов Цельсия"),
    "₽": ("рубль", "рубля", "рублей"), "руб": ("рубль", "рубля", "рублей"), "р": ("рубль", "рубля", "рублей"),
    "$": ("доллар", "доллара", "долларов"), "€": ("евро", "евро", "евро"), "£": ("фунт", "фунта", "фунтов"),
    "млн": ("миллион", "миллиона", "миллионов"), "млрд": ("миллиард", "миллиарда", "миллиардов"),
    "тыс": ("тысяча", "тысячи", "тысяч"),
}
FEMININE = {"минута", "секунда", "тысяча"}


def plural(n: int, forms) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def cardinal(n: int, feminine=False) -> str:
    w = num2words(n, lang="ru")
    w = re.sub(r"^(одна тысяча|один миллион|один миллиард)", lambda m: m[1].split()[1], w)   # "тысяча пятьсот"
    if feminine:
        w = re.sub(r"(^|\s)один$", r"\1одна", w)
        w = re.sub(r"(^|\s)два$", r"\1две", w)
    return w


def _ordinal_form(n: int, form: str) -> str:
    """form: nom_m (пятый), nom_n (пятое), gen (пятого), dat (пятому), prep (пятом)."""
    w = num2words(n, lang="ru", to="ordinal")
    words = w.split()
    last = words[-1]
    if form == "nom_n":
        last = re.sub(r"(ый|ой)$", "ое", last)
        last = re.sub(r"ий$", "ье" if last.endswith("тий") else "ее", last)
    elif form == "dat":
        last = re.sub(r"(ый|ой)$", "ому", last)
        last = re.sub(r"тий$", "тьему", last)
        last = re.sub(r"ий$", "ему", last)
    elif form == "prep":
        last = re.sub(r"(ый|ой)$", "ом", last)
        last = re.sub(r"тий$", "тьем", last)
        last = re.sub(r"ий$", "ем", last)
    elif form == "gen":
        last = re.sub(r"(ый|ой)$", "ого", last)
        last = re.sub(r"тий$", "тьего", last)
        last = re.sub(r"ий$", "его", last)
    words[-1] = last
    return " ".join(words)


def say_date(d: int, m: int, y: int | None) -> str:
    out = f"{_ordinal_form(d, 'nom_n')} {MONTHS_GEN[m - 1]}"
    if y:
        out += f" {_ordinal_form(y, 'gen')} года"
    return out


def say_amount(num: str, unit: str) -> str:
    unit_key = unit.strip().rstrip(".")
    forms = UNITS.get(unit_key) or UNITS.get(unit_key.lower())
    num = num.replace(" ", "").replace(" ", "")
    if "," in num or "." in num:
        whole, frac = re.split(r"[.,]", num, 1)
        spoken = f"{cardinal(int(whole), True)} целых {cardinal(int(frac), True)} " + \
                 {1: "десятых", 2: "сотых", 3: "тысячных"}.get(len(frac), "")
        return f"{spoken} {forms[1] if forms else unit}".strip()
    n = int(num)
    fem = bool(forms) and forms[0].split()[0] in FEMININE
    return f"{cardinal(n, fem)} {plural(n, forms) if forms else unit}"


def load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def normalize(text: str, overrides: dict | None = None) -> str:
    t = " " + text.strip() + " "
    ov = load_overrides() if overrides is None else overrides
    for word, say in sorted(ov.items(), key=lambda kv: -len(kv[0])):          # user pronunciations first
        t = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", say, t, flags=re.IGNORECASE)
    t = re.sub(r"(\d+)\s?(ч|мин|сек)\.(?=\s|$|,)", lambda m: say_amount(m[1], m[2]), t)   # 1 ч. 45 мин.
    for pat, rep in ABBR.items():
        t = re.sub(rf"(?<![\w]){pat}", rep, t)
    # dates: 25.09.2026 / 25.09 / 25 сентября 2026 г.
    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b",
               lambda m: say_date(int(m[1]), int(m[2]), int(m[3])) if 1 <= int(m[2]) <= 12 else m[0], t)
    case_after = {"к": "dat", "ко": "dat", "до": "gen", "с": "gen", "со": "gen", "после": "gen", "от": "gen",
                  "около": "gen", "для": "gen", "по": "dat"}
    t = re.sub(r"(?:\b(к|ко|до|с|со|после|от|около|по)\s+)?\b(\d{1,2})\s+(" + "|".join(MONTHS_GEN) +
               r")(?:\s+(\d{4})\s*(?:г\.|года|год)?)?",
               lambda m: (f"{m[1]} " if m[1] else "") +
                         f"{_ordinal_form(int(m[2]), case_after.get((m[1] or '').lower(), 'nom_n'))} {m[3]}" +
                         (f" {_ordinal_form(int(m[4]), 'gen')} года" if m[4] else ""), t)
    t = re.sub(r"\bв\s+(\d{4})\s*(?:г\.|году)", lambda m: f"в {_ordinal_form(int(m[1]), 'prep')} году", t)
    t = re.sub(r"\b(\d{4})\s*(?:г\.|год)(?!\w)", lambda m: f"{_ordinal_form(int(m[1]), 'nom_m')} год", t)
    # time 14:30
    t = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
               lambda m: f"{cardinal(int(m[1]))} {plural(int(m[1]), ('час', 'часа', 'часов'))}" +
                         ("" if m[2] == "00" else f" {cardinal(int(m[2]), True)} "
                          f"{plural(int(m[2]), ('минута', 'минуты', 'минут'))}"), t)
    # currencies before or after the number: $5, 5 $, 1 500 ₽, 20 руб.
    t = re.sub(r"([$€£₽])\s?(\d[\d\s ]*(?:[.,]\d+)?)", lambda m: say_amount(m[2].strip(), m[1]), t)
    t = re.sub(r"(\d[\d ]*(?:\s\d{3})*(?:[.,]\d+)?)\s?(₽|\$|€|£|руб\.?|р\.(?=\s)|%|°C|км|кг|мм|см|мл|млн|млрд|тыс\.?)(?!\w)",
               lambda m: say_amount(m[1], m[2]), t)
    # ordinals written as 5-й, 21-го, 3-е
    t = re.sub(r"\b(\d+)-(?:й|ый|ой|ий)\b", lambda m: _ordinal_form(int(m[1]), "nom_m"), t)
    t = re.sub(r"\b(\d+)-(?:го|ого)\b", lambda m: _ordinal_form(int(m[1]), "gen"), t)
    t = re.sub(r"\b(\d+)-(?:е|ое)\b", lambda m: _ordinal_form(int(m[1]), "nom_n"), t)
    # remaining numbers: decimals, grouped thousands, plain integers, ranges 5-10
    t = re.sub(r"\b(\d+)[.,](\d+)\b", lambda m: f"{cardinal(int(m[1]), True)} целых {cardinal(int(m[2]), True)} " +
               {1: "десятых", 2: "сотых", 3: "тысячных"}.get(len(m[2]), ""), t)
    t = re.sub(r"\b(\d{1,3}(?:[\s ]\d{3})+)\b", lambda m: cardinal(int(re.sub(r"\s", "", m[1]))), t)
    t = re.sub(r"\b(\d+)\s?[-–]\s?(\d+)\b", lambda m: f"от {cardinal(int(m[1]))} до {cardinal(int(m[2]))}", t)
    t = re.sub(r"\b\d+\b", lambda m: cardinal(int(m[0])), t)
    # punctuation → pauses the TTS engines understand
    t = t.replace("…", "...").replace(" — ", ", — ").replace(" - ", ", ")
    t = re.sub(r"[«»“”„\"]", "", t)
    t = re.sub(r"\(([^)]*[.!?…])\)", r" \1", t)          # (Шучу.) -> separate sentence
    t = re.sub(r"\(([^)]*)\)", r", \1,", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,.!?;:])(?=[^\s\d.,!?;:])", r"\1 ", t)
    t = re.sub(r"([.!?])\s*,", r"\1", t)
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t and t[-1] not in ".!?…":
        t += "."
    return t
