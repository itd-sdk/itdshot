"""Классы и стили сайта ИТД.

Сайт собран через vite + CSS modules, поэтому хешированные имена классов меняются
с каждой сборкой. Ключи модулей (`postInner`, `originalPostMedia`, ...) - это имена
из исходников сайта, они стабильны. Поэтому при запуске качается актуальный бандл,
из него по ключам находятся нужные модули, и результат кешируется по имени бандла:
сеть нужна один раз после каждого обновления сайта.

Если сайт недоступен или в бандле что-то поменялось слишком сильно, используются
встроенные templates/classes.json и templates/site.css.
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SITE_URL = "https://xn--d1ah4a.com/"
CACHE_PATH = Path.home() / ".cache" / "itdshot" / "site"
TEMPLATES_PATH = Path(__file__).parent / "templates"

# имя модуля в шаблоне -> ключи, по которым модуль однозначно узнаётся
MODULES = {
    "post": {"postInner", "originalPostMedia", "textWrapper"},
    "header": {"headerMain", "authorLink", "moreDropdown"},
    "actions": {"actionsLeft", "capturedEmoji", "views"},
    "media": {"mediaWrapper", "singleVideo", "isFeed"},
    "user_name": {"userName", "pinBadge", "nukstaGlow"},
    "avatar": {"avatar", "emoji", "onlineDot"},
    "text": {"spoiler", "mention", "hashtag", "monospace"},
    "dropdown": {"dropdownWrapper", "trigger", "menuItem"},
    "tool": {"signature", "stamp", "signatureText", "actor"},
    "red_pen": {"textLayer", "overlay", "strike", "handwriting"},
}

# картинки из подписей корректора и красной ручки
ASSETS = {
    "corrector_stamp": r"/assets/icon-37-[\w-]+\.svg",
    "red_pen_icon": r"/assets/icon2-44-[\w-]+\.svg",
}

# формы пятен корректора: массив пар svg-путей перед генерацией data:url
TEXTURES = r'=(\[\["M[^=]*?\]\]),\w+=\w+\.map\(\(\[\w+,\w+\]\)=>\{const \w+=`<svg'

IDENT = r"[A-Za-z_$][\w$]*"


class SiteError(Exception):
    pass


def parse_modules(js: str) -> list[dict[str, str]]:
    """Найти в JS объекты CSS-модулей вида `{post:"OAX3",postInner:qJiW,...}`"""
    variables = dict(re.findall(rf'(?<![\w$.])({IDENT})="([\w-]+)"', js))
    modules = []
    for match in re.finditer(rf"\{{((?:{IDENT}:(?:{IDENT}|\"[\w-]+\"),?)+)\}}", js):
        module = {}
        for pair in match.group(1).rstrip(",").split(","):
            key, value = pair.split(":", 1)
            value = value.strip('"') if value.startswith('"') else variables.get(value)
            if value is None:
                break
            module[key] = value
        else:
            modules.append(module)
    return modules


def find_modules(js: str, found: dict | None = None) -> dict[str, dict[str, str]]:
    found = dict(found or {})
    for module in parse_modules(js):
        for name, keys in MODULES.items():
            if keys <= module.keys() and name not in found:
                found[name] = module
    return found


def extract(js: str, css: str) -> dict:
    """Собрать classes.json из JS и CSS сайта. Бросает SiteError, если чего-то не хватает"""
    found = find_modules(js)
    missing = MODULES.keys() - found.keys()
    if missing:
        raise SiteError(f"не найдены модули: {', '.join(sorted(missing))}")

    for name, module in found.items():
        if not any(cls in css for cls in module.values()):
            raise SiteError(f"классов модуля {name} нет в CSS")

    assets = {}
    for name, pattern in ASSETS.items():
        match = re.search(pattern, js)
        if not match:
            raise SiteError(f"не найдена картинка {name}")
        assets[name] = match.group(0)

    textures = re.search(TEXTURES, js)
    if not textures:
        raise SiteError("не найдены текстуры корректора")

    found["assets"] = assets
    found["corrector_textures"] = json.loads(textures.group(1))
    return found


def _get(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 itdshot"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _chunks(js: str) -> list[str]:
    """Ленивые чанки из `__vite__mapDeps`: модули могут переехать туда при обновлении"""
    deps = re.search(r"m\.f=\[(.*?)\]", js)
    if not deps:
        return []
    return re.findall(r'"(assets/[^"]+)"', deps.group(1))


def fetch() -> tuple[dict, str]:
    """Скачать актуальные классы и CSS с сайта (с кешем по имени бандла)"""
    index = _get(SITE_URL)
    js_match = re.search(r'src="(/assets/index-[\w-]+\.js)"', index)
    css_match = re.search(r'href="(/assets/index-[\w-]+\.css)"', index)
    if not js_match or not css_match:
        raise SiteError("не найден бандл на главной странице")

    bundle = Path(js_match.group(1)).stem
    cache = CACHE_PATH / f"{bundle}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data["classes"], data["css"]

    print(f"site updated ({bundle}), extracting classes")
    js = _get(urljoin(SITE_URL, js_match.group(1)))
    css = _get(urljoin(SITE_URL, css_match.group(1)))

    found = find_modules(js)
    if MODULES.keys() - found.keys():
        # чего-то нет в основном бандле - ищем по ленивым чанкам
        chunks = _chunks(js)
        for chunk in (chunk for chunk in chunks if chunk.endswith(".js")):
            if not MODULES.keys() - found.keys():
                break
            chunk_js = _get(urljoin(SITE_URL, chunk))
            js += "\n" + chunk_js
            found = find_modules(chunk_js, found)
        # стили чанков лежат отдельно, какой к какому - неизвестно, поэтому берем все
        for chunk in (chunk for chunk in chunks if chunk.endswith(".css")):
            css += "\n" + _get(urljoin(SITE_URL, chunk))

    classes = extract(js, css)
    CACHE_PATH.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"classes": classes, "css": css}, ensure_ascii=False), encoding="utf-8")
    return classes, css


def bundled() -> tuple[dict, str]:
    """Встроенные классы и CSS (на момент последнего обновления itdshot)"""
    return (
        json.loads((TEMPLATES_PATH / "classes.json").read_text(encoding="utf-8")),
        (TEMPLATES_PATH / "site.css").read_text(encoding="utf-8"),
    )


def load(offline: bool = False) -> tuple[dict, str]:
    if not offline:
        try:
            return fetch()
        except Exception as e:  # сеть, формат бандла - в любом случае есть встроенные
            print(f"warning: can't get classes from the site ({e}), using bundled")
    return bundled()
