from pathlib import Path

import click
from itd import ITDClient, Post

from itdshot.main import edit_html, screenshot


@click.command()
@click.option(
    "-d", "--dark/--no-dark", help="Enable dark theme", default=True, is_flag=True
)
@click.argument("id_or_url")
@click.argument(
    "output", required=False, type=click.Path(dir_okay=False, writable=True)
)
@click.argument("token", envvar="ITD_TOKEN")
def post_screenshot(dark: bool, id_or_url: str, output: str | None, token: str):
    print("init itd client")
    ITDClient(token)

    if id_or_url.startswith("http"):
        id = id_or_url.split("post/")[-1]
    else:
        id = id_or_url
    post = Post(id)
    print(
        f"found post content={post.content[:250].replace('\n', ' ') or 'empty'} attachments={len(post.attachments)}"
    )
    edit_html(post, dark)
    print("screenshot")

    path = Path(output or f"{post.id}.png")

    clipboard = output in ("copy", "c", "clipboard")
    if clipboard:
        path = Path("clipboard.png")
    screenshot(path, clipboard)

    # if clipboard:
    #     path.unlink()
