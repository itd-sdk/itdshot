from copy import copy
from pathlib import Path
from subprocess import run

from arrow import get
from bs4 import BeautifulSoup, Tag
from itd import Post
from itd.file import PostAttach
from playwright.sync_api import sync_playwright

base_path = Path(__file__).parent


def edit_html(post: Post, dark: bool = True):
    soup = BeautifulSoup(
        (base_path / "templates" / "post.html").read_text(), "html.parser"
    )

    if dark:
        body = soup.body
        assert body
        body["data-theme"] = "dark"

    def find(query: str, element: Tag | None = None, by: str = "id") -> Tag:
        if by == "tag":
            element = (element or soup).find(query)
        else:
            element = (element or soup).find(lambda e: e.get(by) == query)
        assert element
        return element

    find("avatar").string = post.author.avatar
    find("display-name").string = post.author.display_name

    pin = find("pin")
    if post.author.pin is not None:
        pin["src"] = post.author.pin.url or ""
    else:
        pin.extract()

    find("created-at").string = get(post.created_at).humanize(locale="ru-RU")
    find("content").string = post.content

    attachments = find("attachments")
    if post.attachments:
        attachments["data-count"] = str(len(post.attachments))

        if len(post.attachments) == 1:
            post_attachment = post.attachments[0]
            attachment = find("attachment-template")
            attachment["style"] = (
                f"aspect-ratio: {post_attachment.width} / {post_attachment.height}"
            )
            del attachment["id"]
            find("img", attachment, by="tag")["src"] = post_attachment.url
            attachments.insert(0, attachment)
        else:
            container = attachments.new_tag(
                "div",
                attrs={"class": "r94T j1ge", "data-count": str(len(post.attachments))}
            )
            attachments.insert(0, container)
            template_container = find("attachment-template")
            template = find("img", template_container, by="tag")

            for attachment in post.attachments[::-1]:
                img = copy(template)
                img["src"] = attachment.url
                container.insert(0, img)

            template_container.extract()
    else:
        attachments.extract()

    if post.original_post is not None:
        orig = post.original_post
        find("orig-avatar").string = orig.author.avatar
        find("orig-display-name").string = orig.author.display_name

        pin = find("orig-pin")
        if orig.author.pin is not None:
            pin["src"] = orig.author.pin.url or ""
        else:
            pin.extract()

        find("orig-content").string = orig.content
        find("orig-created-at").string = get(orig.created_at).humanize(locale="ru-RU")

        attachments = find("orig-attachments")
        if orig.attachments:
            attachments["data-count"] = str(len(orig.attachments))

            if len(orig.attachments) == 1:
                post_attachment = orig.attachments[0]
                attachment = find("orig-attachment-template")
                attachment["style"] = (
                    f"aspect-ratio: {post_attachment.width} / {post_attachment.height}"
                )
                del attachment["id"]
                find("img", attachment, by="tag")["src"] = post_attachment.url
                attachments.insert(0, attachment)
            else:
                container = attachments.new_tag(
                    "div",
                    attrs={"class": "r94T", "data-count": str(len(orig.attachments))}
                )
                attachments.insert(0, container)
                template_container = find("orig-attachment-template")
                template = find("img", template_container, by="tag")

                for attachment in orig.attachments[::-1]:
                    img = copy(template)
                    img["src"] = attachment.url
                    container.insert(0, img)

                template_container.extract()
        else:
            attachments.extract()

        find("orig-likes-count").string = str(orig.likes_count)
        find("orig-comments-count").string = str(orig.comments_count)
        find("orig-reposts-count").string = str(orig.reposts_count)
        if orig.dominant:
            find("orig-dominant-value").string = orig.dominant
        else:
            find("orig-dominant").extract()
        find("orig-views-count").string = str(orig.views_count)
    else:
        find("orig").extract()

    find("likes-count").string = str(post.likes_count)
    find("comments-count").string = str(post.comments_count)
    find("reposts-count").string = str(post.reposts_count)
    if post.dominant:
        find("dominant-value").string = post.dominant
    else:
        find("dominant").extract()
    find("views-count").string = str(post.views_count)

    (base_path.parent / "out.html").write_text(str(soup.contents[0]))


def screenshot(path: Path, clipboard: bool = False):
    with sync_playwright() as p:
        browser = p.firefox.launch()
        page = browser.new_page()
        page.goto((base_path.parent / "out.html").as_uri())
        page.locator("#post").screenshot(path=path)

        if clipboard:
            run(
                ["xclip", "-i", "-selection", "clipboard", "-t", "text/uri-list"],
                input=path.absolute().as_uri().encode("utf-8"),
                check=True
            )
            print("copied")
        else:
            print(f"saved to {path}")
        browser.close()
