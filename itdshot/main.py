from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sys
from subprocess import run
from urllib.parse import quote, urlparse

from itd import Post
from itd.enums import AttachType
from itd.models.span import Span
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from playwright.sync_api import sync_playwright

from itdshot import site
from itdshot.video import VideoBox, first_frame, local_video, make_gif

base_path = Path(__file__).parent
templates_path = base_path / "templates"
out_path = base_path.parent / "out.html"

# шаблонизатор Jinja2: откуда брать шаблоны и экранирование HTML (текст поста не станет разметкой)
env = Environment(loader=FileSystemLoader(templates_path), autoescape=select_autoescape())

SITE_URL = site.SITE_URL
# страница открывается как будто с сайта: шрифты и иконки там подключаются
# относительными путями, а шрифты с другого origin (file://) браузер не грузит
RENDER_URL = SITE_URL + "__itdshot__"
# шрифты и иконки сайта кешируются, чтобы не качать их при каждом запуске
CACHE_PATH = Path.home() / ".cache" / "itdshot"

# размеры с сайта: ширина ленты и ограничения одиночного вложения
DEFAULT_WIDTH = 650
SINGLE_MAX_WIDTH = 650
SINGLE_MAX_HEIGHT = 500
PIN_SIZES = {"xs": 12, "sm": 14, "md": 16, "lg": 22}

MONTHS = [
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.",
]


@dataclass
class Media:
    src: str
    width: int | None
    height: int | None
    video_url: str | None = None
    video_index: int | None = None


@dataclass
class Extras:
    """Тетрадь, корректор и красная ручка поста, подготовленные для шаблона (как их показывает сайт)."""

    notebook: str | None = None  # grid / ruled
    marks: list[dict] = field(default_factory=list)  # замазанный текст
    corrections: list[dict] = field(default_factory=list)  # правки красной ручкой
    corrector_actors: list[dict] = field(default_factory=list)
    red_pen_actors: list[dict] = field(default_factory=list)
    has_tools: bool = False  # пост рендерится через слой инструментов (как на сайте)


def is_url(value: str | None) -> bool:
    return bool(value) and value.startswith(("http://", "https://", "/"))


def count(value: int) -> str:
    for divider, suffix in ((1_000_000, "M"), (1_000, "K")):
        if value >= divider:
            number = value / divider
            return f"{number:g}{suffix}" if number.is_integer() else f"{number:.1f}{suffix}"
    return str(value)


def short_date(date: datetime) -> str:
    date = date.astimezone()
    return f"{date.day} {MONTHS[date.month - 1]}"


def relative_time(date: datetime) -> str:
    seconds = int((datetime.now(timezone.utc) - date).total_seconds())
    if seconds < 60:
        return "сейчас"
    if seconds < 3600:
        return f"{seconds // 60} мин."
    if seconds < 86400:
        return f"{seconds // 3600} ч."
    if seconds < 604800:
        return f"{seconds // 86400} дн."
    if seconds < 2419200:
        return f"{seconds // 604800} нед."
    return short_date(date)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def single_width(width: int, height: int) -> int:
    if width / height > SINGLE_MAX_WIDTH / SINGLE_MAX_HEIGHT:
        return round(min(width, SINGLE_MAX_WIDTH))
    return round(min(height, SINGLE_MAX_HEIGHT) * width / height)


def media_of(post: Post, videos: list[Media]) -> list[Media]:
    media = []
    for attach in post.attachments:
        if attach.type == AttachType.VIDEO:
            src, width, height = attach.thumbnail_url, attach.width, attach.height
            if not src or not width or not height:
                # бывает, что API не присылает превью или размеры видео: берем первый кадр
                try:
                    src, width, height = first_frame(local_video(attach.url))
                except Exception as e:
                    print(f"warning: can't get the video frame ({e})")
                    src = src or ""
            item = Media(src, width, height, video_url=attach.url, video_index=len(videos))
            videos.append(item)
            media.append(item)
        elif attach.type in (AttachType.IMAGE, AttachType.MEDIA):
            media.append(Media(attach.url, attach.width, attach.height))
    return media


def _optional_field(item, name: str):
    # поле, которого может не быть в этой версии itd-sdk (getattr с default тут не подходит:
    # модели itd-sdk на неизвестное поле бросают не AttributeError)
    fields = {key for cls in type(item).__mro__ for key in getattr(cls, "__annotations__", {})}
    return getattr(item, name) if name in fields else None


def avatar_of(user) -> str:
    """Аватар, как его показывает сайт: ссылка на картинку или эмодзи"""
    # itd-sdk 2.10+: avatar - эмодзи клана, а поле avatar из API - possible_url_avatar
    return _optional_field(user, "possible_url_avatar") or user.avatar


def _alive(item) -> bool:
    """Корректор и ручка временные: сайт показывает их только до endsAt"""
    ends_at = _optional_field(item, "ends_at")  # пока itd-sdk не хранит endsAt, считаем живыми
    return ends_at is None or ends_at > datetime.now(timezone.utc)


def _rank(item) -> tuple[int, str]:
    # как на сайте: на одном фрагменте остаётся более поздняя правка, при равенстве - с большим id
    micros = _optional_field(item, "created_at_micros")
    if micros is None:
        micros = round(item.created_at.timestamp() * 1_000_000) if item.created_at else 0
    return micros, str(item.id)


def _actors(items) -> list[dict]:
    actors = {}
    for item in items:
        actors.setdefault(item.actor.id, {"username": item.actor.username, "displayName": item.actor.display_name})
    return list(actors.values())


def extras_of(post: Post | None) -> Extras:
    if post is None:
        return Extras()

    corrector = post.corrector if post.is_loaded("corrector") else None
    red_pen = post.red_pen if post.is_loaded("red_pen") else None

    marks = [mark for mark in corrector.correctors if _alive(mark)] if corrector else []
    claims = [claim for claim in red_pen.claims if _alive(claim)] if red_pen else []
    corrections = red_pen.red_pens if claims else []  # правки пропадают вместе с заявками

    latest: dict[tuple[int, int], tuple] = {}
    for item in [*marks, *corrections]:
        key = (item.start, item.end)
        if key not in latest or _rank(item) > latest[key]:
            latest[key] = _rank(item)

    def keep(item) -> bool:
        return latest[(item.start, item.end)] == _rank(item)

    return Extras(
        notebook=post.notebook.value if post.notebook else None,
        marks=[{"id": str(m.id), "start": m.start, "end": m.end} for m in marks if keep(m)],
        corrections=[
            {"id": str(c.id), "start": c.start, "end": c.end, "replacement": c.replacement}
            for c in corrections
            if keep(c)
        ],
        corrector_actors=_actors(marks),
        red_pen_actors=_actors(claims) if corrections else [],
        has_tools=corrector is not None or red_pen is not None,
    )


def corrector_textures(paths: list[list[str]]) -> list[str]:
    textures = []
    for outline, lines in paths:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="28" viewBox="0 0 120 28" preserveAspectRatio="none">'
            f'<path d="{outline}" fill="#fffef7" stroke="#cecbbc" stroke-width=".6" vector-effect="non-scaling-stroke"/>'
            f'<path d="{lines}" fill="none" stroke="#e6e2d4" stroke-width=".55"/></svg>'
        )
        textures.append("data:image/svg+xml," + quote(svg, safe="-_.!~*'()"))
    return textures


def _fnv1a(value: str) -> int:
    result = 2166136261
    for unit in value.encode("utf-16-le")[::2]:
        result = ((result ^ unit) * 16777619) & 0xFFFFFFFF
    return result


def texture_variants(marks: list[dict], count: int) -> dict[str, int]:
    """Как на сайте: соседние замазки получают разные формы пятна."""
    variants: dict[str, int] = {}
    used: set[int] = set()
    previous = -1
    for mark in marks:
        mark_id = str(mark.get("id"))
        if mark_id in variants:
            continue
        if len(used) == count:
            used.clear()
        variant = _fnv1a(mark_id) % count
        while variant in used or (not used and variant == previous):
            variant = (variant + 1) % count
        variants[mark_id] = variant
        used.add(variant)
        previous = variant
    return variants


def render_text(
    text: str,
    spans: list[Span],
    classes: dict[str, str],
    marks: list[dict] = [],
    textures: list[str] = [],
) -> Markup:
    """Аналог компонента текста поста на сайте. Смещения в UTF-16."""
    encoded = text.encode("utf-16-le")
    length = len(encoded) // 2
    variants = texture_variants(marks, len(textures)) if marks else {}

    def substring(start: int, end: int) -> str:
        return encoded[start * 2 : end * 2].decode("utf-16-le", errors="replace")

    def with_marks(start: int, end: int) -> Markup:
        hits = [m for m in marks if m["start"] < end and m["end"] > start]
        if not hits:
            return escape(substring(start, end))
        cuts = {start, end}
        for mark in hits:
            cuts.add(max(start, mark["start"]))
            cuts.add(min(end, mark["end"]))
        cuts = sorted(cuts)
        result = Markup("")
        for left, right in zip(cuts, cuts[1:]):
            piece = substring(left, right)
            mark = next((m for m in hits if m["start"] <= left < m["end"]), None)
            if mark is None:
                result += escape(piece)
                continue
            variant = variants[str(mark.get("id"))]
            result += Markup(
                '<span class="%s" data-corrector-mark style="background-image: url(&quot;%s&quot;)">'
                '<span aria-hidden="true">%s</span></span>'
            ) % (classes["corrector"], Markup(textures[variant]), piece)
        return result

    if not spans:
        return Markup('<span class="">%s</span>') % with_marks(0, length)

    events = []
    for index, span in enumerate(spans):
        events.append((span.offset, 0, index))
        events.append((span.offset + span.length, -1, index))
    events.sort()

    parts = []
    active: dict[int, Span] = {}
    position = 0

    def push(end: int):
        if end <= position:
            return
        chunk = with_marks(position, end)
        styles = {span.type.value for span in active.values()}

        if "bold" in styles:
            chunk = Markup("<strong>%s</strong>") % chunk
        if "italic" in styles:
            chunk = Markup("<em>%s</em>") % chunk
        if "underline" in styles:
            chunk = Markup('<span class="%s">%s</span>') % (classes["underline"], chunk)
        if "strike" in styles:
            chunk = Markup("<s>%s</s>") % chunk
        if "monospace" in styles:
            chunk = Markup('<code class="%s">%s</code>') % (classes["monospace"], chunk)
        if "quote" in styles:
            chunk = Markup('<span class="%s">%s</span>') % (classes["quote"], chunk)
        if "spoiler" in styles:
            chunk = Markup('<span class="%s">%s</span>') % (classes["spoiler"], chunk)
        for kind in ("link", "mention", "hashtag"):
            if kind in styles:
                chunk = Markup('<a class="%s">%s</a>') % (classes[kind], chunk)
        parts.append(Markup("<span>%s</span>") % chunk)

    for offset, kind, index in events:
        push(offset)
        position = max(position, offset)
        if kind == 0:
            active[index] = spans[index]
        else:
            active.pop(index, None)
    push(length)

    return Markup('<span class="">%s</span>') % Markup("").join(parts)


def edit_html(
    post: Post,
    dark: bool = True,
    width: int = DEFAULT_WIDTH,
    animated: bool = False,
    offline: bool = False,
) -> list[Media]:
    """Собрать out.html.

    Возвращает видео из поста (по порядку их индексов в разметке).
    offline - не обращаться к сайту за актуальными классами, взять встроенные.
    """
    classes, site_css = site.load(offline)
    textures = corrector_textures(classes["corrector_textures"])

    videos: list[Media] = []
    post_media = media_of(post, videos)
    orig = post.original_post
    orig_media = media_of(orig, videos) if orig is not None else []

    html = env.get_template("post.html").render(
        # классы сайта и функции форматирования, которые вызываются из шаблона
        classes=classes,
        pin_sizes=PIN_SIZES,
        is_url=is_url,
        avatar_of=avatar_of,
        count=count,
        short_date=short_date,
        relative_time=relative_time,
        plural=plural,
        single_width=single_width,
        render_text=lambda text, spans, marks=[]: render_text(
            text, spans, classes["text"], marks, textures
        ),
        # сам пост
        post=post,
        post_media=post_media,
        post_extra=extras_of(post),
        orig_media=orig_media,
        orig_extra=extras_of(orig),
        dark=dark,
        width=width,
        animated=animated,
        site_css=Markup(site_css),
    )
    out_path.write_text(html, encoding="utf-8")
    return videos


VIDEO_BOXES_JS = """
() => {
    const post = document.querySelector('#post').getBoundingClientRect();
    return [...document.querySelectorAll('[data-itdshot-video]')].map(el => {
        const r = el.getBoundingClientRect();
        let clip = {l: r.left, t: r.top, r: r.right, b: r.bottom};
        for (let a = el.parentElement; a; a = a.parentElement) {
            const s = getComputedStyle(a);
            if (s.overflowX === 'visible' && s.overflowY === 'visible') continue;
            const q = a.getBoundingClientRect();
            clip = {
                l: Math.max(clip.l, q.left), t: Math.max(clip.t, q.top),
                r: Math.min(clip.r, q.right), b: Math.min(clip.b, q.bottom),
            };
        }
        const radius = parseFloat(getComputedStyle(el).borderTopLeftRadius)
            || parseFloat(getComputedStyle(el.parentElement).borderTopLeftRadius) || 0;
        return {
            index: Number(el.dataset.itdshotVideo),
            x: r.left - post.left, y: r.top - post.top, width: r.width, height: r.height,
            clip_x: clip.l - post.left, clip_y: clip.t - post.top,
            clip_width: clip.r - clip.l, clip_height: clip.b - clip.t,
            radius,
        };
    });
}
"""


def copy_file(path: Path):
    """Скопировать файл в буфер обмена (вставляется как файл в мессенджеры)"""
    if sys.platform == "win32":
        literal = str(path.absolute()).replace("'", "''")
        run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -LiteralPath '{literal}'"],
            check=True,
        )
    else:
        run(
            ["xclip", "-i", "-selection", "clipboard", "-t", "text/uri-list"],
            input=path.absolute().as_uri().encode("utf-8"),
            check=True
        )


def _cached(route):
    url = urlparse(route.request.url)
    file = CACHE_PATH / url.path.strip("/").replace("/", "_")
    if not file.exists():
        response = route.fetch(timeout=60_000)
        if not response.ok:
            route.fulfill(response=response)
            return
        CACHE_PATH.mkdir(parents=True, exist_ok=True)
        file.write_bytes(response.body())
    route.fulfill(path=file)


def screenshot(
    path: Path,
    clipboard: bool = False,
    scale: float = 1,
    videos: list[Media] = [],
    fps: int = 20,
    max_duration: float = 10,
):
    """Снять пост. Если path — .gif и в посте есть видео, получится анимация."""
    with sync_playwright() as p:
        # chromium: firefox в playwright не умеет скриншоты с прозрачным фоном (omit_background)
        browser = p.chromium.launch()
        # ширина больше брейкпоинта сайта (1174px), чтобы применились десктопные стили
        page = browser.new_page(
            viewport={"width": 1280, "height": 720}, device_scale_factor=scale
        )
        html = out_path.read_text(encoding="utf-8")
        page.route(
            RENDER_URL,
            lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"),
        )
        page.route(SITE_URL + "fonts/**", _cached)
        page.route(SITE_URL + "assets/**", _cached)
        page.goto(RENDER_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("window.itdshotReady === true")

        post = page.locator("#post")
        if path.suffix.lower() == ".gif" and videos:
            boxes = [VideoBox(**box) for box in page.evaluate(VIDEO_BOXES_JS)]
            frame = path.with_suffix(".frame.png")
            post.screenshot(path=frame, omit_background=True)
            browser.close()
            try:
                make_gif(
                    frame,
                    path,
                    [(videos[box.index].video_url, box) for box in boxes],
                    scale=scale,
                    fps=fps,
                    max_duration=max_duration,
                )
            finally:
                frame.unlink(missing_ok=True)
        else:
            post.screenshot(path=path, omit_background=True)
            browser.close()

    if clipboard:
        copy_file(path)
        print("copied")
    else:
        print(f"saved to {path}")
