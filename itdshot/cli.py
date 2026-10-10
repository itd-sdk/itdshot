import sys
from pathlib import Path

import click
from itd import ITDConfig, Post, init_client
from itd.enums import AttachType

from itdshot.main import DEFAULT_WIDTH, edit_html, screenshot


def has_video(post: Post) -> bool:
    posts = [post] if post.original_post is None else [post, post.original_post]
    return any(a.type == AttachType.VIDEO for p in posts for a in p.attachments)


@click.command()
@click.option(
    "-d", "--dark/--no-dark", help="Enable dark theme", default=True, is_flag=True
)
@click.option(
    "-w", "--width", help="Post width in pixels", default=DEFAULT_WIDTH, show_default=True
)
@click.option(
    "-s", "--scale", help="Device scale factor (2 = retina)", default=1.0, show_default=True
)
@click.option(
    "--static", help="Save PNG even if the post has a video", is_flag=True
)
@click.option(
    "--offline", help="Use bundled site classes instead of fetching the current ones", is_flag=True
)
@click.option("--fps", help="GIF frame rate", default=20, show_default=True)
@click.option(
    "--max-duration", help="Max GIF length in seconds", default=10.0, show_default=True
)
@click.argument("id_or_url")
@click.argument(
    "output", required=False, type=click.Path(dir_okay=False, writable=True)
)
def post_screenshot(
    dark: bool,
    width: int,
    scale: float,
    static: bool,
    offline: bool,
    fps: int,
    max_duration: float,
    id_or_url: str,
    output: str | None,
):
    print("init itd client")
    # отдельная сессия itdshot: при первом запуске itd-sdk спросит почту и пароль
    # (или QR-код) и сохранит ее, дальше вход автоматический. токен из браузера
    # не нужен: refresh-токены одноразовые и общий с браузером сервер отзывает
    client = init_client(
        "itdshot",
        config=ITDConfig(
            captcha_solve=True,  # капча решается в браузере camoufox (itd-sdk[captcha])
            captcha_headless="virtual" if sys.platform == "linux" else False,
            dwell_enabled=False,  # не отправлять статистику просмотров
            post_update_stats=False,
            post_auto_view=False,
        ),
    )

    if id_or_url.startswith("http"):
        id = id_or_url.split("post/")[-1]
    else:
        id = id_or_url
    post = Post(id)
    print(
        f"found post content={post.content[:250].replace('\n', ' ') or 'empty'} attachments={len(post.attachments)}"
    )

    clipboard = output in ("copy", "c", "clipboard")
    extension = ".gif" if has_video(post) and not static else ".png"
    if clipboard:
        path = Path(f"clipboard{extension}")
    else:
        path = Path(output or f"{post.id}{extension}")

    animated = path.suffix.lower() == ".gif"
    videos = edit_html(post, dark, width, animated, offline)
    print("screenshot")
    screenshot(
        path,
        clipboard,
        scale=scale,
        videos=videos if animated else [],
        fps=fps,
        max_duration=max_duration,
    )
