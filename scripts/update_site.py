"""Обновить встроенные стили и классы ИТД (запасной вариант, если сайт недоступен).

Использование:
    python scripts/update_site.py                      # скачать с сайта
    python scripts/update_site.py index-XXXX.js index-XXXX.css   # из файлов

Обычно это не нужно: itdshot сам берёт актуальные классы с сайта при запуске
(см. itdshot/site.py). Встроенные файлы используются, только если сайт недоступен.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from itdshot.site import TEMPLATES_PATH, extract, fetch  # noqa: E402


def main(args: list[str]):
    if len(args) == 2:
        js, css = (Path(arg).read_text(encoding="utf-8") for arg in args)
        classes = extract(js, css)
    elif not args:
        classes, css = fetch()
    else:
        sys.exit(__doc__)

    (TEMPLATES_PATH / "classes.json").write_text(
        json.dumps(classes, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (TEMPLATES_PATH / "site.css").write_text(css, encoding="utf-8")
    print(f"saved {len(classes)} modules and {len(css)} bytes of css")


if __name__ == "__main__":
    main(sys.argv[1:])
