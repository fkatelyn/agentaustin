"""Build the Agent Austin presentation (dark theme, 16:9).

Run:
    uv run python docs/presentation/build_ppt.py
    # or
    python3 docs/presentation/build_ppt.py

Outputs docs/presentation/agent-austin.pptx.
"""
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = Path(__file__).parent / "agent-austin.pptx"

BG = RGBColor(0x1A, 0x1A, 0x2E)
CARD = RGBColor(0x16, 0x21, 0x3E)
ACCENT_DEEP = RGBColor(0x0F, 0x34, 0x60)
CYAN = RGBColor(0x00, 0xD2, 0xFF)
PURPLE = RGBColor(0x7B, 0x2F, 0xF7)
CORAL = RGBColor(0xFF, 0x6B, 0x6B)
YELLOW = RGBColor(0xFF, 0xD9, 0x3D)
GREEN = RGBColor(0x6B, 0xCB, 0x77)
TEXT = RGBColor(0xE0, 0xE0, 0xE0)
MUTED = RGBColor(0x99, 0x99, 0xAA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def new_prs() -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    return prs


def paint_background(slide, color=BG):
    bg = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5)
    )
    bg.line.fill.background()
    bg.fill.solid()
    bg.fill.fore_color.rgb = color
    bg.shadow.inherit = False
    slide.shapes._spTree.remove(bg._element)
    slide.shapes._spTree.insert(2, bg._element)
    return bg


def add_text(
    slide,
    text,
    left,
    top,
    width,
    height,
    *,
    size=18,
    bold=False,
    color=TEXT,
    align=PP_ALIGN.LEFT,
    font="Calibri",
):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run()
        r.text = line
        r.font.name = font
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
    return tb


def add_card(slide, left, top, width, height, *, color=CARD, radius=True):
    shape = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    card = slide.shapes.add_shape(shape, left, top, width, height)
    card.line.fill.background()
    card.fill.solid()
    card.fill.fore_color.rgb = color
    card.shadow.inherit = False
    if radius and hasattr(card, "adjustments"):
        try:
            card.adjustments[0] = 0.08
        except Exception:
            pass
    return card


def add_accent_bar(slide, top, color=CYAN, left=Inches(0.6), width=Inches(0.12), height=Inches(0.5)):
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    bar.line.fill.background()
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    return bar


def add_footer(slide, page_num, total):
    add_text(
        slide,
        "Agent Austin",
        Inches(0.6),
        Inches(7.05),
        Inches(4),
        Inches(0.3),
        size=10,
        color=MUTED,
    )
    add_text(
        slide,
        f"{page_num} / {total}",
        Inches(12.0),
        Inches(7.05),
        Inches(0.8),
        Inches(0.3),
        size=10,
        color=MUTED,
        align=PP_ALIGN.RIGHT,
    )


# ------- slide builders -------

def slide_title(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    paint_background(s)
    # Accent gradient band
    band = s.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, Inches(3.1), Inches(13.333), Inches(0.05)
    )
    band.line.fill.background()
    band.fill.solid()
    band.fill.fore_color.rgb = CYAN
    add_text(
        s,
        "Agent Austin",
        Inches(0.6),
        Inches(2.3),
        Inches(12),
        Inches(1.2),
        size=64,
        bold=True,
        color=WHITE,
    )
    add_text(
        s,
        "An AI data-science agent for Austin 311 service requests",
        Inches(0.6),
        Inches(3.3),
        Inches(12),
        Inches(0.6),
        size=24,
        color=CYAN,
    )
    add_text(
        s,
        "Architecture · Agent Skills · Visualization · Use Cases",
        Inches(0.6),
        Inches(4.1),
        Inches(12),
        Inches(0.5),
        size=18,
        color=MUTED,
    )
    return s


def slide_agenda(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    paint_background(s)
    add_accent_bar(s, Inches(0.6), CYAN)
    add_text(
        s,
        "Agenda",
        Inches(0.85),
        Inches(0.55),
        Inches(10),
        Inches(0.7),
        size=36,
        bold=True,
        color=WHITE,
    )
    items = [
        ("1", "What is Agent Austin?", "The product and the data it sits on"),
        ("2", "Architecture", "How the pieces fit together"),
        ("3", "Agent Skills", "What the agent knows how to do"),
        ("4", "Visualization", "Plotly charts, dashboards, and reports"),
        ("5", "Use Cases", "Potholes, trash, code compliance, and more"),
        ("6", "Roadmap", "Where this is headed"),
    ]
    top = Inches(1.6)
    for i, (n, title, sub) in enumerate(items):
        y = top + Inches(i * 0.85)
        # number badge
        badge = s.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(0.85), y, Inches(0.55), Inches(0.55)
        )
        badge.line.fill.background()
        badge.fill.solid()
        badge.fill.fore_color.rgb = CYAN if i % 2 == 0 else PURPLE
        tf = badge.text_frame
        tf.margin_left = Emu(0)
        tf.margin_right = Emu(0)
        tf.margin_top = Emu(0)
        tf.margin_bottom = Emu(0)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = n
        r.font.bold = True
        r.font.size = Pt(20)
        r.font.color.rgb = BG
        # title + subtitle
        add_text(s, title, Inches(1.65), y + Inches(0.02), Inches(10), Inches(0.45), size=22, bold=True, color=WHITE)
        add_text(s, sub, Inches(1.65), y + Inches(0.45), Inches(10), Inches(0.4), size=14, color=MUTED)
    return s


def slide_overview(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    paint_background(s)
    add_accent_bar(s, Inches(0.6), CYAN)
    add_text(s, "What is Agent Austin?", Inches(0.85), Inches(0.55), Inches(12), Inches(0.7), size=36, bold=True, color=WHITE)
    add_text(
        s,
        "A chat-first data-science agent for the City of Austin's 311 service-request dataset.\nAsk in plain English — it downloads, analyzes, visualizes, and writes reports for you.",
        Inches(0.85),
        Inches(1.35),
        Inches(12),
        Inches(1.1),
        size=18,
        color=TEXT,
    )

    # Three stat cards
    stats = [
        ("~2.4M", "311 requests\n2014 → present", CYAN),
        ("Daily", "Delta-merged on\neach deploy", PURPLE),
        ("6", "Skills the agent\nknows how to run", CORAL),
    ]
    card_w = Inches(3.8)
    card_h = Inches(2.1)
    gap = Inches(0.25)
    total_w = card_w * 3 + gap * 2
    start_x = (Inches(13.333) - total_w) / 2
    for i, (val, label, color) in enumerate(stats):
        x = start_x + (card_w + gap) * i
        add_card(s, x, Inches(2.75), card_w, card_h)
        add_text(s, val, x, Inches(2.95), card_w, Inches(1.1), size=54, bold=True, color=color, align=PP_ALIGN.CENTER)
        add_text(s, label, x, Inches(4.0), card_w, Inches(0.8), size=15, color=TEXT, align=PP_ALIGN.CENTER)

    # Data source line
    add_text(
        s,
        "Data: City of Austin Open Data · Socrata dataset xwdj-i9he · no API key required",
        Inches(0.85),
        Inches(5.3),
        Inches(12),
        Inches(0.5),
        size=14,
        color=MUTED,
    )
    return s


def build():
    prs = new_prs()
    slides = [
        slide_title,
        slide_agenda,
        slide_overview,
    ]
    for fn in slides:
        fn(prs)
    # footers (skip title)
    total = len(prs.slides)
    for i, s in enumerate(prs.slides, start=1):
        if i == 1:
            continue
        add_footer(s, i, total)
    prs.save(OUT)
    print(f"wrote {OUT} ({total} slides)")


if __name__ == "__main__":
    build()
