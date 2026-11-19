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
        except Exception as nav_err:
            print("Goto warning:", nav_err)

        # 給頁面時間跑驗證與動態渲染，不死等 networkidle
        page.wait_for_timeout(8000)

        for _ in range(4):
            try:
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(800)
            except Exception:
                break

        seen = set()
        for a in page.query_selector_all("a[href]"):
            try:
                href = urljoin(URL, a.get_attribute("href") or "")
                if not href or href == URL or "ibigfun.com" not in href or href in seen:
                    continue

                img = a.query_selector("img") or a.evaluate_handle(
                    "el => (el.closest('div, li, article') || el).querySelector('img')"
                ).as_element()
                src = urljoin(URL, img.get_attribute("src") or img.get_attribute("data-src") or "") if img else ""
                if not src or src.startswith("data:"):
                    continue

                text = a.evaluate("el => (el.closest('div, li, article') || el).innerText") or ""
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                if not lines:
                    continue

                title = lines[0]
                if any(w in title.lower() for w in ["just a moment", "captcha", "cloudflare", "checking your browser"]):
                    continue

                price_m = re.search(r"([\d,]+)\s*萬", text)
                ping_m = re.search(r"([\d.]+)\s*坪", text)
                cat = (
                    "土地廠房"
                    if any(k in title for k in ["地", "廠", "建地", "農地"])
                    else ("透天" if any(k in title for k in ["透天", "別墅"]) else "住宅")
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

# 確保檔案必定存在，避免後續 git add 找不到檔案而 exit code 1
with open("listings.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)

print(f"Done. Total items: {len(items)}")
