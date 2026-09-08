"""公文 docx 生成：大字号、宽行距，适配政务报送阅读。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


def _set_cn_font(run, cn_name: str = "仿宋", size_pt: float = 16, bold: bool = False,
                 color: str | None = "000000") -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), cn_name)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _add_para(doc, text, *, cn_name="仿宋", size_pt=16, bold=False,
              align=None, indent=True, line_pt=28, space_before=0, space_after=6,
              style_name=None):
    p = doc.add_paragraph(style=style_name)
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(line_pt)
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if indent:
        pf.first_line_indent = Pt(size_pt * 2)  # 首行缩进两字符
    run = p.add_run(text)
    _set_cn_font(run, cn_name=cn_name, size_pt=size_pt, bold=bold)
    return p


def build_report_docx(items: Iterable[dict], out_path: str | Path,
                      title: str | None = None, date_text: str | None = None) -> Path:
    """items：已按序排好、含 title/zh_title/summary/source_name/published_at/url 的字典列表。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    # A4与宽页边距（适配政务材料打印阅读）
    for section in doc.sections:
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Pt(72)
        section.bottom_margin = Pt(72)
        section.left_margin = Pt(90)
        section.right_margin = Pt(90)

    doc_title = title or "人工智能安全治理信息摘报"
    # 标题：小标宋/黑体放大
    _add_para(doc, doc_title, cn_name="方正小标宋简体", size_pt=22, bold=True,
              align=WD_ALIGN_PARAGRAPH.CENTER, indent=False, line_pt=36, space_after=12,
              style_name="Title")
    date_text = date_text or datetime.now().strftime("%Y年%m月%d日")
    _add_para(doc, date_text, size_pt=16, align=WD_ALIGN_PARAGRAPH.CENTER,
              indent=False, line_pt=28, space_after=18)

    for i, it in enumerate(items, 1):
        _add_para(doc, f"{i}. {it.get('zh_title') or it.get('title', '')}",
                  cn_name="黑体", size_pt=16, bold=False, indent=False,
                  line_pt=28, space_before=6)
        meta = f"{it.get('source_name', '')} | {it.get('published_at', '')}"
        if it.get("url"):
            meta += f" | {it['url']}"
        _add_para(doc, meta, size_pt=12, indent=False, line_pt=22, space_after=4)
        _add_para(doc, it.get("summary", ""), size_pt=16, line_pt=28, space_after=12)

    doc.save(str(out_path))
    return out_path
