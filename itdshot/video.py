"""Сборка GIF: скриншот поста + кадры видео поверх превью."""

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from shutil import copyfileobj
from subprocess import run
from tempfile import TemporaryDirectory, gettempdir
from urllib.request import Request, urlopen

from imageio_ffmpeg import count_frames_and_secs, get_ffmpeg_exe
from PIL import Image, ImageDraw


@dataclass
class VideoBox:
    """Положение видео на скриншоте (в css-пикселях, относительно #post)."""

    index: int
    x: float
    y: float
    width: float
    height: float
    # видимая часть (карусель обрезает вложения, которые не влезли)
    clip_x: float
    clip_y: float
    clip_width: float
    clip_height: float
    radius: float


def _download(url: str, path: Path):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 itdshot"})
    with urlopen(request, timeout=60) as response, path.open("wb") as file:
        copyfileobj(response, file)


VIDEOS_PATH = Path(gettempdir()) / "itdshot"


def local_video(url: str) -> Path:
    """Скачать видео один раз за запуск (и между запусками, пока жив temp)"""
    path = VIDEOS_PATH / (sha1(url.encode()).hexdigest() + ".mp4")
    if not path.exists():
        print("download video")
        VIDEOS_PATH.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        _download(url, partial)
        partial.replace(path)
    return path


def first_frame(video: Path) -> tuple[str, int, int]:
    """Первый кадр видео как data:url и его размеры (если API не прислал превью)"""
    frame = video.with_suffix(".jpg")
    if not frame.exists():
        run(
            [get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(frame)],
            check=True,
        )
    with Image.open(frame) as image:
        width, height = image.size
    return "data:image/jpeg;base64," + b64encode(frame.read_bytes()).decode(), width, height


def _even(value: float) -> int:
    return max(2, round(value) // 2 * 2)


def _mask(path: Path, width: int, height: int, radius: int):
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=radius, fill=255
    )
    mask.save(path)


def make_gif(
    frame: Path,
    output: Path,
    videos: list[tuple[str, VideoBox]],
    scale: float = 1,
    fps: int = 20,
    max_duration: float = 10,
):
    ffmpeg = get_ffmpeg_exe()

    with TemporaryDirectory(prefix="itdshot-") as tmp:
        tmp_path = Path(tmp)
        inputs: list[str] = []
        filters: list[str] = []
        durations: list[float] = []

        visible = [
            (url, box)
            for url, box in videos
            if box.clip_width >= 1 and box.clip_height >= 1
        ]
        sources = []
        for url, box in visible:
            video = local_video(url)
            sources.append(video)
            durations.append(count_frames_and_secs(str(video))[1])

        duration = min(max(durations, default=0) or max_duration, max_duration)

        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration}", "-i", str(frame)]
        filters.append("[0:v]format=rgba[base0]")

        for number, (url, box) in enumerate(visible):
            width, height = _even(box.width * scale), _even(box.height * scale)
            mask = tmp_path / f"mask{number}.png"
            _mask(mask, width, height, round(box.radius * scale))

            video_input = 1 + number * 2
            inputs += ["-stream_loop", "-1", "-t", f"{duration}", "-i", str(sources[number])]
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration}", "-i", str(mask)]

            crop_x = round((box.clip_x - box.x) * scale)
            crop_y = round((box.clip_y - box.y) * scale)
            crop_w = max(1, min(width - crop_x, round(box.clip_width * scale)))
            crop_h = max(1, min(height - crop_y, round(box.clip_height * scale)))
            filters.append(
                f"[{video_input}:v]fps={fps},"
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},format=rgba[v{number}];"
                f"[{video_input + 1}:v]format=gray[m{number}];"
                f"[v{number}][m{number}]alphamerge,crop={crop_w}:{crop_h}:{crop_x}:{crop_y}[o{number}];"
                f"[base{number}][o{number}]overlay={round(box.clip_x * scale)}:{round(box.clip_y * scale)}"
                f":eof_action=repeat[base{number + 1}]"
            )

        last = f"base{len(visible)}"
        # одна палитра на всю анимацию + перерисовка только изменившейся области
        filters.append(
            f"[{last}]split[a][b];"
            "[a]palettegen=stats_mode=diff:reserve_transparent=1[palette];"
            "[b][palette]paletteuse=dither=sierra2_4a:diff_mode=rectangle:alpha_threshold=128"
        )

        print("render gif")
        run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                *inputs,
                "-filter_complex", ";".join(filters),
                "-t", f"{duration}",
                "-loop", "0",
                str(output),
            ],
            check=True,
        )
