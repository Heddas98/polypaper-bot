"""
PolyPaper Bot - Banner Image Generator
Creates Polyscout-style blue gradient banner images for Telegram.
"""

import io

from PIL import Image, ImageDraw, ImageFont

# ── Colors matching Polyscout ──
BG_COLOR = (59, 100, 246)  # Polyscout blue
BG_DARK = (30, 60, 200)
TEXT_COLOR = (255, 255, 255)
ACCENT_COLOR = (255, 140, 0)  # Orange dot
GREEN_DOT = (0, 200, 80)


def create_banner(
    title: str,
    subtitle: str = "",
    width: int = 600,
    height: int = 200,
) -> io.BytesIO:
    """Create a Polyscout-style blue banner image."""
    img = Image.new("RGB", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Gradient effect (subtle)
    for y in range(height):
        factor = y / height
        r = int(BG_COLOR[0] * (1 - factor * 0.3) + BG_DARK[0] * factor * 0.3)
        g = int(BG_COLOR[1] * (1 - factor * 0.3) + BG_DARK[1] * factor * 0.3)
        b = int(BG_COLOR[2] * (1 - factor * 0.3) + BG_DARK[2] * factor * 0.3)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Watermark text (repeated faded title)
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_watermark = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28
        )
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except OSError:
        font_large = ImageFont.load_default()
        font_watermark = font_large
        font_sub = font_large

    # Draw faded watermark copies
    watermark_color = (BG_COLOR[0] + 15, BG_COLOR[1] + 15, min(255, BG_COLOR[2] + 15))
    for i, offset_y in enumerate([50, 80, 110, 140]):
        draw.text((30, offset_y), title, fill=watermark_color, font=font_watermark)

    # Main title
    draw.text((30, 60), title, fill=TEXT_COLOR, font=font_large)

    # Logo dots (top-left, like Polyscout)
    draw.ellipse([25, 18, 39, 32], fill=GREEN_DOT)
    draw.ellipse([42, 18, 56, 32], fill=ACCENT_COLOR)
    draw.ellipse([25, 35, 39, 49], fill=ACCENT_COLOR)
    draw.ellipse([42, 35, 56, 49], fill=GREEN_DOT)

    # Bottom-right logo dots
    bx, by = width - 60, height - 50
    draw.ellipse([bx, by, bx + 14, by + 14], fill=GREEN_DOT)
    draw.ellipse([bx + 17, by, bx + 31, by + 14], fill=ACCENT_COLOR)
    draw.ellipse([bx, by + 17, bx + 14, by + 31], fill=ACCENT_COLOR)
    draw.ellipse([bx + 17, by + 17, bx + 31, by + 31], fill=GREEN_DOT)

    # Subtitle
    if subtitle:
        draw.text((30, height - 40), subtitle, fill=(*TEXT_COLOR, 180), font=font_sub)

    # Export
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# Pre-built banners for each section
def banner_dashboard() -> io.BytesIO:
    return create_banner("Dashboard")


def banner_strategies() -> io.BytesIO:
    return create_banner("Strategies")


def banner_wallets() -> io.BytesIO:
    return create_banner("Wallet")


def banner_stats() -> io.BytesIO:
    return create_banner("Trading Stats")


def banner_settings() -> io.BytesIO:
    return create_banner("Settings")


def banner_referrals() -> io.BytesIO:
    return create_banner("Referrals")
