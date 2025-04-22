# extract_clean_or_fragment.py

import argparse
import re
import requests
from bs4 import BeautifulSoup, Comment

# ——— UTILS ———
def fetch_page(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": "DemoBot/1.0"})
    resp.raise_for_status()
    return resp.text

def clean_html(soup: BeautifulSoup) -> BeautifulSoup:
    # Remove script, style, noscript, iframe
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    # Remove common ad/analytics containers
    pattern = re.compile(r"(ad-|ads-|advert|cookie|analytics)", re.I)
    for tag in soup.find_all(
        lambda t: (t.get("id") and pattern.search(t["id"]))
                  or (t.get("class") and any(pattern.search(c) for c in t["class"]))
    ):
        tag.decompose()
    return soup

def find_product_fragment(soup: BeautifulSoup) -> BeautifulSoup:
    # 1) <main>
    main = soup.find("main")
    if main:
        return main
    # 2) elements with “product” in id/class
    prod = soup.find(
        lambda tag: tag.name in ["div", "section", "article"] and (
            (tag.get("id") and "product" in tag["id"].lower())
            or (tag.get("class") and any("product" in c.lower() for c in tag["class"]))
        )
    )
    if prod:
        return prod
    # 3) fallback to <body>
    return soup.body or soup

def save_html(soup: BeautifulSoup, path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(soup.prettify())
    print(f"[+] Saved to {path}")

# ——— MAIN ———
def main():
    p = argparse.ArgumentParser(
        description="Fetch a page and either clean it or extract the product fragment."
    )
    p.add_argument("--url",      required=True, help="URL of the product page")
    p.add_argument("--output",   default="output.html", help="Where to save HTML")
    p.add_argument(
        "--mode",
        choices=["clean", "fragment"],
        default="fragment",
        help="‘clean’: full page minus irrelevant; ‘fragment’: just the product section"
    )
    args = p.parse_args()

    html = fetch_page(args.url)
    soup = BeautifulSoup(html, "html.parser")
    soup = clean_html(soup)

    if args.mode == "fragment":
        soup = find_product_fragment(soup)

    save_html(soup, args.output)

if __name__ == "__main__":
    main()