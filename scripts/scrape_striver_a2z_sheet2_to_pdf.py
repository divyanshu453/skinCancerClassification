#!/usr/bin/env python3
import argparse
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class CodeBlock:
    language: Optional[str]
    code: str


@dataclass
class ArticleCodes:
    title: str
    url: str
    codes: List[CodeBlock]


def http_get(url: str, session: requests.Session, max_retries: int = 3, timeout_sec: int = 20) -> Optional[requests.Response]:
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=timeout_sec)
            if resp.status_code == 200:
                return resp
            # Retry on 5xx or 429
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt, 10))
            else:
                return None
        except requests.RequestException:
            time.sleep(min(2 ** attempt, 10))
    return None


def extract_language_from_classes(tag: Tag) -> Optional[str]:
    classes = tag.get("class", [])
    for cls in classes:
        # e.g., language-cpp, lang-cpp, language-python, brush: cpp
        if cls.startswith("language-"):
            return cls.replace("language-", "").strip()
        if cls.startswith("lang-"):
            return cls.replace("lang-", "").strip()
        if cls.startswith("brush:"):
            return cls.replace("brush:", "").strip()
    return None


def clean_code_text(text: str) -> str:
    if text is None:
        return ""
    # Replace \r\n variations, strip trailing spaces on lines
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    # Collapse excessive blank lines
    cleaned: List[str] = []
    blank_streak = 0
    for ln in lines:
        if ln.strip() == "":
            blank_streak += 1
            if blank_streak <= 2:
                cleaned.append(ln)
        else:
            blank_streak = 0
            cleaned.append(ln)
    return "\n".join(cleaned).strip("\n")


def extract_code_blocks_from_article(html: str) -> List[CodeBlock]:
    soup = BeautifulSoup(html, "lxml")

    # Common WordPress code containers: pre > code, div.wp-block-code > pre, div.code-block, figure.wp-block-code
    code_blocks: List[CodeBlock] = []

    # Strategy 1: pre > code
    for code in soup.select("pre code"):
        language = extract_language_from_classes(code) or extract_language_from_classes(code.parent)
        code_text = clean_code_text(code.get_text("\n"))
        if code_text:
            code_blocks.append(CodeBlock(language=language, code=code_text))

    # Strategy 2: standalone pre tags with code
    for pre in soup.select("pre"):
        # If this pre already captured via pre>code, skip
        if pre.find("code") is not None:
            continue
        language = extract_language_from_classes(pre)
        code_text = clean_code_text(pre.get_text("\n"))
        if code_text:
            code_blocks.append(CodeBlock(language=language, code=code_text))

    # Strategy 3: blocks that might contain code text
    for div in soup.select("div.wp-block-code, figure.wp-block-code, div.code-block"):
        language = extract_language_from_classes(div)
        code_text = clean_code_text(div.get_text("\n"))
        if code_text:
            code_blocks.append(CodeBlock(language=language, code=code_text))

    # Deduplicate identical blocks
    unique: List[CodeBlock] = []
    seen = set()
    for blk in code_blocks:
        key = (blk.language or "", blk.code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(blk)

    return unique


def choose_language_blocks(blocks: List[CodeBlock], preferred_languages: List[str]) -> List[CodeBlock]:
    if not blocks:
        return []
    # If any block languages present in preferences, filter to those; else return all
    normalized = [
        CodeBlock(language=(b.language or "").lower().strip(), code=b.code) for b in blocks
    ]
    present_langs = {b.language for b in normalized if b.language}
    for pref in preferred_languages:
        if pref in present_langs:
            return [b for b in normalized if b.language == pref]
    return normalized


def collect_article_links_from_sheet(html: str, base_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    main = soup
    # On TakeUForward, content is often within entry-content
    entry = soup.select_one(".entry-content, article, main") or soup
    anchors = entry.select("a[href]")
    items: List[Tuple[str, str]] = []
    for a in anchors:
        href = a["href"]
        text = a.get_text(strip=True)
        if not href or not text:
            continue
        # Build absolute URL for relative links
        href_abs = urljoin(base_url, href)
        # filter to takeuforward articles
        if "takeuforward.org" not in href_abs:
            continue
        # Skip non-article internal pages like category, tag, courses landing
        if any(x in href_abs for x in ["/category/", "/tag/", "/courses/", "#", "/about", "/contact"]):
            continue
        items.append((text, href_abs))

    # Deduplicate by URL while keeping first title
    seen_url = set()
    deduped: List[Tuple[str, str]] = []
    for title, href in items:
        if href in seen_url:
            continue
        seen_url.add(href)
        deduped.append((title, href))
    return deduped


def build_pdf(articles: List[ArticleCodes], output_pdf_path: str, metadata_title: str) -> None:
    # Build a reasonably formatted PDF using reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        PageBreak,
        Preformatted,
        KeepTogether,
    )

    doc = SimpleDocTemplate(
        output_pdf_path,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=metadata_title,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="Title",
        parent=styles["Title"],
        textColor=colors.HexColor("#1b4b91"),
        fontSize=22,
        leading=26,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        name="Heading1",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        spaceAfter=6,
        textColor=colors.HexColor("#222222"),
    )
    h2 = ParagraphStyle(
        name="Heading2",
        parent=styles["Heading2"],
        fontSize=12,
        leading=16,
        spaceAfter=6,
        textColor=colors.HexColor("#333333"),
    )
    body = ParagraphStyle(
        name="Body",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
    )
    code_style = ParagraphStyle(
        name="Code",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8.5,
        leading=10.5,
    )

    story: List = []

    story.append(Paragraph(metadata_title, title_style))
    story.append(Paragraph("Unofficial code collection scraped from the provided sheet links.", body))
    story.append(Spacer(1, 12))

    for idx, art in enumerate(articles, 1):
        story.append(Paragraph(f"{idx}. {art.title}", h1))
        story.append(Paragraph(f"Source: <u>{art.url}</u>", h2))
        story.append(Spacer(1, 6))
        if not art.codes:
            story.append(Paragraph("No code blocks detected on the page.", body))
            story.append(Spacer(1, 6))
            continue
        for cidx, code in enumerate(art.codes, 1):
            lang_label = f"Language: {code.language}" if code.language else "Language: unknown"
            story.append(Paragraph(lang_label, h2))
            story.append(Preformatted(code.code, code_style))
            story.append(Spacer(1, 8))
        story.append(Spacer(1, 12))
        story.append(PageBreak())

    doc.build(story)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Striver A2Z Sheet (Sheet 2) codes and build a PDF.")
    parser.add_argument(
        "--url",
        default="https://takeuforward.org/strivers-a2z-dsa-course/strivers-a2z-dsa-course-sheet-2/",
        help="URL of the Striver A2Z sheet page",
    )
    parser.add_argument(
        "--prefer",
        default="cpp,python,java",
        help="Comma-separated language preferences (lowercase), we pick the first available",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit on number of article links to process (0 for all)",
    )
    parser.add_argument(
        "--out",
        default="/workspace/output/striver_a2z_sheet2_codes.pdf",
        help="Output PDF path",
    )
    args = parser.parse_args()

    preferred_languages = [x.strip().lower() for x in args.prefer.split(",") if x.strip()]

    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

    # Try static fetch first
    sheet_resp = http_get(args.url, session)
    html_text = sheet_resp.text if sheet_resp else ""

    # If static HTML appears to be a React shell, fall back to Playwright-rendered content
    def is_react_shell(html: str) -> bool:
        if not html:
            return True
        return 'id="root"' in html and 'static/js/main' in html and '<a ' not in html

    if is_react_shell(html_text):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
                page = context.new_page()
                page.goto(args.url, wait_until="networkidle")
                # allow client-side rendering to finish
                page.wait_for_timeout(2500)
                html_text = page.content()
                browser.close()
        except Exception as e:
            print(f"Playwright failed, proceeding with static HTML. Error: {e}", file=sys.stderr)

    if not html_text:
        print(f"Failed to load page HTML: {args.url}", file=sys.stderr)
        return 2

    links = collect_article_links_from_sheet(html_text, args.url)
    if args.limit and args.limit > 0:
        links = links[: args.limit]

    articles: List[ArticleCodes] = []
    for title, href in tqdm(links, desc="Collecting articles"):
        resp = http_get(href, session)
        if not resp:
            articles.append(ArticleCodes(title=title, url=href, codes=[]))
            continue
        blocks = extract_code_blocks_from_article(resp.text)
        chosen = choose_language_blocks(blocks, preferred_languages)
        articles.append(ArticleCodes(title=title, url=href, codes=chosen))
        time.sleep(0.5)  # be polite

    meta_title = "Striver A2Z DSA Course - Sheet 2 Codes (Unofficial)"
    build_pdf(articles, args.out, meta_title)
    print(f"PDF written to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

