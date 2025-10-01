#!/usr/bin/env python3
import sys
import requests
from bs4 import BeautifulSoup


def main(url: str) -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })
    resp = s.get(url, timeout=30)
    print("status:", resp.status_code)
    html = resp.text
    open("/workspace/output/sheet2.html", "w", encoding="utf-8").write(html)
    soup = BeautifulSoup(html, "lxml")
    entry = soup.select_one(".entry-content, article, main") or soup
    anchors = entry.select("a[href]")
    print("anchors in entry:", len(anchors))
    takeu = [a for a in anchors if "takeuforward.org" in a.get("href", "")]
    print("anchors to takeuforward.org:", len(takeu))
    print("first 20 hrefs:")
    for a in anchors[:20]:
        print("-", a.get_text(strip=True)[:80], a.get("href"))
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://takeuforward.org/strivers-a2z-dsa-course/strivers-a2z-dsa-course-sheet-2/"
    raise SystemExit(main(url))

