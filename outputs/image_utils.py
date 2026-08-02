"""
image_utils.py
---------------
图片处理工具：把用户上传/粘贴的任意尺寸截图统一压成同样大小的正方形缩略图，
供截图板块的图片墙展示。

为什么用「等比缩放 + 居中留白」而不是「居中裁剪」：
投递截图绝大多数是竖长图（手机截屏）或横长图（网页截屏），居中裁剪成正方形
会把公司名、岗位名这些关键信息切掉，用户就没法靠缩略图辨认哪张是哪张了。
留白方案保证整张图都看得见，同时所有卡片尺寸完全一致，排出来是整齐的方阵。
"""

import hashlib
import io

from PIL import Image, ImageOps

# 缩略图边长（像素）。渲染时会按列宽再等比缩放，这里只决定清晰度上限。
THUMB_SIZE = 320

# 留白底色：与 .streamlit/config.toml 里 secondaryBackgroundColor 一致，
# 暗色主题下留白区域看起来就是卡片背景，不会出现突兀的白边。
THUMB_BG = (26, 26, 25)


def to_png_bytes(img: Image.Image) -> bytes:
    """PIL Image -> PNG 字节流（粘贴组件返回的是 PIL 对象，需要转成字节存起来）。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def image_signature(image_bytes: bytes) -> str:
    """图片内容指纹，用作图片墙里的唯一 id：同一张图重复上传/粘贴不会出现两次。"""
    return hashlib.md5(image_bytes).hexdigest()


def make_square_thumbnail(image_bytes: bytes, size: int = THUMB_SIZE) -> bytes:
    """把任意尺寸的图片压成 size x size 的正方形 PNG 缩略图。

    - 先按 EXIF 方向信息摆正（手机截图/照片常带旋转标记，不处理会显示成躺着的）
    - 等比缩放到能放进 size x size 的最大尺寸
    - 居中贴到 size x size 的纯色画布上，四周留白

    图片解不开（损坏/不是图片）时抛 OSError/ValueError，由调用方兜住。
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        # ImageOps.contain 只缩不裁，长边贴合 size，短边按比例
        fitted = ImageOps.contain(img, (size, size), method=Image.LANCZOS)

        canvas = Image.new("RGB", (size, size), THUMB_BG)
        offset = ((size - fitted.width) // 2, (size - fitted.height) // 2)
        canvas.paste(fitted, offset)

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()


__all__ = ["THUMB_SIZE", "to_png_bytes", "image_signature", "make_square_thumbnail"]
