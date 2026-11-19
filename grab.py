import json, re
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

URL = "https://www.ibigfun.com/pages/index/0906771994"
items = []

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900}
        )
        page = context.new_page()
        page.set_default_timeout(25000)

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print("Goto warning:", e)

        page.wait_for_timeout(8000)
        print("Page title:", page.title())

        for _ in range(4):
            try:
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(800)
            except Exception:
                break

        links = page.query_selector_all("a[href]")
        print("Total links:", len(links))

        seen = set()
        for a in links:
            try:
                href = urljoin(URL, a.get_attribute("href") or "")
                if not href or href == URL or href in seen:
                    continue

                text = (a.inner_text() or "").strip()
                parent_text = (a.evaluate("el => (el.closest('div, li, article, section') || el).innerText") or "").strip()
                combined = parent_text if len(parent_text) > len(text) else text
                lines = [l.strip() for l in combined.splitlines() if l.strip()]
                if not lines:
                    continue

                title = lines[0]
                if any(w in title.lower() for w in ["just a moment", "captcha", "cloudflare", "checking your browser"]):
                    continue

                img = a.query_selector("img") or a.evaluate_handle(
                    "el => (el.closest('div, li, article, section') || el).querySelector('img')"
                ).as_element()
                src = urljoin(URL, img.get_attribute("src") or img.get_attribute("data-src") or "") if img else ""
                if src.startswith("data:"):
                    src = ""

                price_m = re.search(r"([\d,]+)\s*萬", combined)
                ping_m = re.search(r"([\d.]+)\s*坪", combined)
                if not price_m and not ping_m and "/pages/" not in href and "/house" not in href and "/item" not in href:
                    continue

                cat = (
                    "土地廠房"
                    if any(k in combined for k in ["地", "廠", "建地", "農地"])
                    else ("透天" if any(k in combined for k in ["透天", "別墅"]) else "住宅")
                )

                seen.add(href)
                items.append({
                    "id": len(items) + 1,
                    "category": cat,
                    "title": title[:60],
                    "price": float(price_m.group(1).replace(",", "")) if price_m else 0,
                    "totalPing": float(ping_m.group(1)) if ping_m else 0,
                    "url": href,
                    "img": src,
                    "feature": " ・ ".join(lines[1:4])[:140]
                })
            except Exception:
                continue

        browser.close()
except Exception as e:
    print("Playwright error:", e)

with open("listings.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)

print(f"Saved listings.json with {len(items)} items.")
