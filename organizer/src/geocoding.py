import os
import json
import logging
import reverse_geocoder as rg
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger("geocoding")

# Имя файла переопределений пользователя
OVERRIDES_FILE = "geo_overrides.json"

# Встроенный маппинг кодов стран на украинский язык
BUILTIN_COUNTRIES = {
    "UA": "Україна",
    "US": "США",
    "GB": "Велика Британія",
    "DE": "Німеччина",
    "FR": "Франція",
    "IT": "Італія",
    "ES": "Іспанія",
    "PL": "Польща",
    "TR": "Туреччина",
    "EG": "Єгипет",
    "GR": "Греція",
    "RU": "Росія",
    "BY": "Білорусь",
    "MD": "Молдова",
    "RO": "Румунія",
    "HU": "Угорщина",
    "SK": "Словаччина",
    "GE": "Грузія",
    "AM": "Вірменія",
    "AZ": "Азербайджан",
    "IL": "Ізраїль",
    "CN": "Китай",
    "JP": "Японія",
    "TH": "Таїланд",
    "AE": "ОАЕ",
    "CY": "Кіпр",
    "BG": "Болгарія",
    "CZ": "Чехія",
    "AT": "Австрія",
    "CH": "Швейцарія",
    "NL": "Нідерланди",
    "BE": "Бельгія",
    "SE": "Швеція",
    "NO": "Норвегія",
    "FI": "Фінляндія",
    "DK": "Данія",
    "HR": "Хорватія",
    "ME": "Чорногорія",
    "AL": "Албанія",
    "PT": "Португалія",
    "CA": "Канада",
    "MX": "Мексика",
    "BR": "Бразилія",
    "IN": "Індія",
    "ID": "Індонезія",
    "VN": "В'єтнам",
    "LK": "Шрі-Ланка",
    "MV": "Мальдіви",
}

# Встроенный маппинг частых городов на украинский язык
BUILTIN_CITIES = {
    "Kyiv": "Київ",
    "Kyiv City": "Київ",
    "Kiev": "Київ",
    "Lviv": "Львів",
    "Odesa": "Одеса",
    "Kharkiv": "Харків",
    "Dnipro": "Дніпро",
    "Zaporizhzhia": "Запоріжжя",
    "Donetsk": "Донецьк",
    "Luhansk": "Луганськ",
    "Mykolaiv": "Миколаїв",
    "Kherson": "Херсон",
    "Chernihiv": "Чернігів",
    "Poltava": "Полтава",
    "Cherkasy": "Черкаси",
    "Sumy": "Sumy",
    "Zhytomyr": "Житомир",
    "Vinnytsia": "Вінниця",
    "Khmelnytskyi": "Хмельницький",
    "Chernivtsi": "Чернівці",
    "Rivne": "Рівне",
    "Ivano-Frankivsk": "Івано-Франківськ",
    "Ternopil": "Тернопіль",
    "Lutsk": "Луцьк",
    "Uzhhorod": "Ужгород",
    "Yalta": "Ялта",
    "Sevastopol": "Севастополь",
    "Simferopol": "Сімферополь",
    "Kertch": "Керч",
    "Feodosiya": "Феодосія",
    "Yevpatoriya": "Євпаторія",
    "Sudak": "Судак",
    "Alushta": "Алушта",
    "Mariupol": "Маріуполь",
    "Kryvyi Rih": "Кривий Ріг",
    "Kremenchuk": "Кременчук",
    "Kamianske": "Кам'янське",
    "Kropyvnytskyi": "Кропивницький",
    "Mukachevo": "Мукачево",
    "Truskavets": "Трускавець",
    "Kamianets-Podilskyi": "Кам'янець-Подільський",
    "London": "Лондон",
    "Paris": "Париж",
    "Rome": "Рим",
    "Berlin": "Берлін",
    "Madrid": "Мадрид",
    "Vienna": "Відень",
    "Warsaw": "Варшава",
    "Krakow": "Краків",
    "Budapest": "Будапешт",
    "Prague": "Прага",
    "New York": "Нью-Йорк",
    "Tokyo": "Токіо",
    "Beijing": "Пекін",
    "Istanbul": "Стамбул",
    "Antalya": "Анталія",
    "Athens": "Афіни",
    "Cairo": "Каїр",
    "Dubai": "Дубай"
}

def load_overrides() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Загружает переопределения географических названий пользователя."""
    if not os.path.exists(OVERRIDES_FILE):
        # Создаем пустой шаблон для удобства пользователя
        default_template = {
            "countries": {
                "Example_CC": "Приклад_Країни"
            },
            "cities": {
                "Example_City": "Приклад_Міста"
            }
        }
        try:
            with open(OVERRIDES_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_template, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Не удалось создать geo_overrides.json: {e}")
        return {}, {}
        
    try:
        with open(OVERRIDES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("countries", {}), data.get("cities", {})
    except Exception as e:
        logger.error(f"Ошибка чтения geo_overrides.json: {e}")
        return {}, {}

def transliterate_en_to_uk(text: str) -> str:
    """Правила транслитерации английских названий на украинский язык."""
    # Словарь замен сложных буквосочетаний
    rules = [
        # 4-буквенные
        ("shch", "щ"), ("Shch", "Щ"),
        # 3-буквенные
        ("sch", "щ"), ("Sch", "Щ"),
        # 2-буквенные
        ("sh", "ш"), ("Sh", "Ш"),
        ("ch", "ч"), ("Ch", "Ч"),
        ("zh", "ж"), ("Zh", "Ж"),
        ("kh", "х"), ("Kh", "Х"),
        ("ts", "ц"), ("Ts", "Ц"),
        ("ph", "ф"), ("Ph", "Ф"),
        ("th", "т"), ("Th", "Т"),
        ("ya", "я"), ("Ya", "Я"),
        ("yu", "ю"), ("Yu", "Ю"),
        ("ye", "є"), ("Ye", "Є"),
        ("yi", "ї"), ("Yi", "Ї"),
        ("ia", "іа"), ("ie", "іе"),
        ("iu", "іу"), ("io", "іо"),
        ("iy", "ій"), ("oy", "ой"),
        ("ay", "ай"), ("uy", "уй"),
        # Одиночные согласные и гласные
        ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), 
        ("f", "ф"), ("g", "г"), ("h", "г"), ("i", "і"), ("j", "й"), 
        ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), 
        ("p", "п"), ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), 
        ("u", "у"), ("v", "в"), ("w", "в"), ("x", "кс"), ("y", "и"), 
        ("z", "з"),
        ("A", "А"), ("B", "Б"), ("C", "К"), ("D", "Д"), ("E", "Е"), 
        ("F", "Ф"), ("G", "Г"), ("H", "Г"), ("I", "І"), ("J", "Й"), 
        ("K", "К"), ("L", "Л"), ("M", "М"), ("N", "Н"), ("O", "О"), 
        ("P", "П"), ("Q", "К"), ("R", "Р"), ("S", "С"), ("T", "Т"), 
        ("U", "У"), ("V", "В"), ("W", "В"), ("X", "Кс"), ("Y", "И"), 
        ("Z", "З"),
    ]
    
    translit = text
    for eng, ukr in rules:
        translit = translit.replace(eng, ukr)
        
    return translit

def geocode(latitude: Optional[float], longitude: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    """
    Принимает координаты, возвращает кортеж (Страна, Город) на украинском языке.
    Использует reverse_geocoder для оффлайн поиска.
    """
    if latitude is None or longitude is None:
        return None, None
        
    try:
        # reverse_geocoder принимает список координат и возвращает список словарей
        # Поиск ближайшей точки
        results = rg.search((latitude, longitude))
        if not results:
            return None, None
            
        res = results[0]
        country_code = res.get("cc", "")
        city_name_en = res.get("name", "")
        
        # Загружаем переопределения пользователя
        user_countries, user_cities = load_overrides()
        
        # 1. Определяем страну
        country_name = None
        if country_code in user_countries:
            country_name = user_countries[country_code]
        elif country_code in BUILTIN_COUNTRIES:
            country_name = BUILTIN_COUNTRIES[country_code]
        else:
            # Если нет перевода, транслитерируем двухсимвольный код или оставляем как есть
            country_name = country_code
            
        # 2. Определяем город
        city_name = None
        if city_name_en in user_cities:
            city_name = user_cities[city_name_en]
        elif city_name_en in BUILTIN_CITIES:
            city_name = BUILTIN_CITIES[city_name_en]
        else:
            # Транслитерируем название
            city_name = transliterate_en_to_uk(city_name_en)
            
        logger.info(f"Геокодинг: ({latitude}, {longitude}) -> {country_name}, {city_name} (исх: {country_code}, {city_name_en})")
        return country_name, city_name
        
    except Exception as e:
        logger.error(f"Ошибка офлайн-геокодинга для координат ({latitude}, {longitude}): {e}")
        return None, None
