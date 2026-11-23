#!/usr/bin/env python3
"""
比房網（ibigfun）全頁翻頁物件同步腳本
- 支援完整分頁解析（[data-name="cardinfo"] 與 [data-name="page-button-next"]）
- 內建嚴格防覆寫安全鎖：
  1. 若遇 Cloudflare／反爬蟲驗證頁，立即中止且絕不覆寫 listings.json
  2. 若抓取筆數低於 20 筆，絕不覆寫 listings.json
  3. 若抓取筆數低於現有 listings.json 筆數的 50%，絕不覆寫 listings.json
  4. 寫入採暫存檔原子替換，確保 listings.json 不會被寫壞或清空
"""

import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SOURCE_URL = os.environ.get(
    "IBIGFUN_URL",
    "https://www.ibigfun.com/pages/index/0906771994?page=1&sorts=default",
)
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "listings.json"

MIN_ITEMS = 20
MIN_RATIO = 0.5
MAX_PAGES = 50

CHALLENGE_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verify you are human",
    "cloudflare",
    "access denied",
    "ddos-guard",
    "請稍候",
    "驗證您是人類",
    "安全檢查",
)

CHALLENGE_TEXT_MARKERS = (
    "just a moment",
    "attention required! | cloudflare",
    "checking if the site connection is secure",
    "checking your browser before accessing",
    "verify you are human",
    "enable javascript and cookies to continue",
    "cf-challenge",
    "cloudflare ray id",
    "captcha",
)

CHALLENGE_SELECTORS = (
    "#challenge-running",
    "#challenge-form",
    "#challenge-stage",
    "#cf-challenge-running",
    ".cf-browser-verification",
    "iframe[src*='challenges.cloudflare.com']",
    "input[name='cf-turnstile-response']",
    "#turnstile-wrapper",
    ".g-recaptcha",
    ".h-captcha",
)

EXTRACT_JS = r"""
() => {
  const baseUrl = location.href;

  const normImg = (raw) => {
    if (!raw) return '';
    let s = String(raw).replace(/&amp;/gi, '&').trim();
    if (!s || s.startsWith('data:') || /^blob:/i.test(s) || s.startsWith('./') || /_files\//i.test(s)) {
      return '';
    }
    if (s.includes(',')) {
      s = s.split(',')[0].trim().split(/\s+/)[0];
    }
    if (s.startsWith('//')) {
      s = 'https:' + s;
    }
    try {
      const u = new URL(s, baseUrl).href;
      if (!/^https?:\/\//i.test(u) || /\.svg(\?|$)/i.test(u)) return '';
      return u;
    } catch {
      return '';
    }
  };

  const parsePrice = (txt) => {
    if (!txt) return 0;
    const s = String(txt).replace(/\s+/g, '');
    const yi = s.match(/([\d,.]+)億(?:([\d,.]+)萬)?/);
    if (yi) {
      const yiVal = parseFloat(yi[1].replace(/,/g, '')) || 0;
      const wanVal = yi[2] ? parseFloat(yi[2].replace(/,/g, '')) || 0 : 0;
      return Math.round(yiVal * 10000 + wanVal);
    }
    const wan = s.match(/([\d,.]+)萬/);
    return wan ? parseFloat(wan[1].replace(/,/g, '')) || 0 : 0;
  };

  const classify = (url, text) => {
    const all = text + ' ' + url;
    if (/htype=true|[,=]land\b|[,=]factory\b|[,=]industrial\b|土地|廠房|工業|建地|農地|丁建|丙建|倉庫|廠辦/i.test(all)) {
      return '土地廠房';
    }
    if (/[,=]villa\b|[,=]townhouse\b|透天|別墅|農舍/i.test(all)) {
      return '透天';
    }
    return '住宅';
  };

  const getCardImg = (card) => {
    const imgs = card.querySelectorAll('img[data-ga="shop__cardLink__home"], a[href*="/pages/house/"] img, img');
    for (const im of imgs) {
      for (const raw of [
        im.getAttribute('src'),
        im.currentSrc,
        im.getAttribute('data-src'),
        im.getAttribute('data-original'),
        im.getAttribute('srcset'),
      ]) {
        const u = normImg(raw);
        if (u && !/logo|icon|avatar|loading|spinner|placeholder/i.test(u)) {
          return u;
        }
      }
    }
    const hm = (card.outerHTML || '').match(/(?:https?:\/\/www\.ibigfun\.com)?\/Redirect\?l=[^"'\s)<>]+/i);
    return hm ? normImg(hm[0]) : '';
  };

  const out = [];
  for (const card of document.querySelectorAll('[data-name="cardinfo"]')) {
    const titleA =
      card.querySelector('[data-name="cardinfo__content__title"]') ||
      card.querySelector('a[href*="/pages/house/"]');
    if (!titleA) continue;

    const title = (titleA.textContent || titleA.getAttribute('data-ga-param') || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 100);
    if (!title || title.length < 2) continue;

    let url = baseUrl;
    try {
      url = new URL(titleA.getAttribute('href'), baseUrl).href;
    } catch {}

    const img = getCardImg(card);

    const pu = card.querySelector('.price_unit');
    const pTxt = pu && pu.parentElement ? pu.parentElement.textContent : '';
    let price = parsePrice(pTxt);
    if (!price) {
      price = parsePrice((card.innerText || '').replace(/\b0\s*萬/g, ''));
    }

    const pingEl = card.querySelector('.ping_string');
    const pingTxt = (pingEl ? pingEl.textContent : '').replace(/\s+/g, ' ').trim();
    const am = pingTxt.match(/([\d,.]+)\s*坪/);
    const totalPing = am ? parseFloat(am[1].replace(/,/g, '')) : 0;

    const layoutEl = pingEl ? pingEl.previousElementSibling : null;
    const layout = (layoutEl ? layoutEl.textContent : '').replace(/\s+/g, ' ').trim();

    const addrEl = card.querySelector('p[data-ga="shop__card__goMap"]');
    const addr = (addrEl ? addrEl.textContent : '').replace(/\s+/g, ' ').trim();

    out.push({
      category: classify(url, title + ' ' + layout + ' ' + addr),
      title,
      price,
      totalPing,
      url,
      img,
      feature: [addr, layout, pingTxt].filter(Boolean).join('｜').slice(0, 260),
    });
  }
  return out;
}
"""

PAGE_SIG_JS = r"""
() => {
  const els = document.querySelectorAll(
    '[data-name="cardinfo"] [data-name="cardinfo__content__title"], [data-name="cardinfo"] a[href*="/pages/house/"]'
  );
  return [...els].slice(0, 3).map((a) => a.getAttribute('href') || '').join('|');
}
"""

IS_LOADING_JS = r"""
() => {
  const el = document.querySelector('.loading__wrap');
  if (!el) return false;
  const st = getComputedStyle(el);
  return (
    st.display !== 'none' &&
    st.visibility !== 'hidden' &&
    parseFloat(st.opacity || '1') > 0.05 &&
    el.offsetParent !== null
  );
}
"""

SCROLL_HYDRATE_JS = r"""
async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const h = document.body ? document.body.scrollHeight : 1800;
  for (let y = 0; y <= h; y += 900) {
    window.scrollTo(0, y);
    await sleep(50);
  }
  window.scrollTo(0, 0);
  await sleep(120);
}
"""


def write_step_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def emit_warning(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}")


def load_existing_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except Exception as e:
        print(f"⚠️ 讀取現有 {path.name} 失敗：{e}")
        return 0


def detect_cloudflare_challenge(page) -> str | None:
    """檢查當前頁面是否為 Cloudflare／反爬蟲驗證頁，若是則回傳偵測原因。"""
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""

    title_lower = title.lower()
    for marker in CHALLENGE_TITLE_MARKERS:
        if marker in title_lower:
            return f"頁面標題包含驗證關鍵字（title='{title}'）"

    for selector in CHALLENGE_SELECTORS:
        try:
            if page.query_selector(selector):
                return f"頁面出現驗證元件（selector='{selector}'）"
        except Exception:
            continue

    try:
        has_cards = bool(page.query_selector('[data-name="cardinfo"]'))
        body_text = (page.inner_text("body") or "").strip()
    except Exception:
        has_cards = False
        body_text = ""

    if not has_cards:
        body_lower = body_text.lower()
        for marker in CHALLENGE_TEXT_MARKERS:
            if marker in body_lower:
                return f"頁面內容包含驗證關鍵字（'{marker}'）"
        if "ray id" in body_lower and "cloudflare" in body_lower:
            return "頁面出現 Cloudflare Ray ID 阻擋頁"

    return None


def wait_for_page_change(page, before_sig: str) -> tuple[bool, str | None]:
    """等待翻頁後卡片內容更新，若中途遭遇驗證頁立即回傳阻擋原因。"""
    for _ in range(36):
        page.wait_for_timeout(250)
        blocked = detect_cloudflare_challenge(page)
        if blocked:
            return False, blocked
        try:
            now_sig = page.evaluate(PAGE_SIG_JS)
            loading = page.evaluate(IS_LOADING_JS)
        except Exception:
            continue
        if now_sig and now_sig != before_sig and not loading:
            page.wait_for_timeout(300)
            return True, None
    page.wait_for_timeout(300)
    return False, None


def normalize_number(val):
    if isinstance(val, (int, float)):
        if float(val).is_integer():
            return int(val)
        return val
    return 0


def scrape_all_pages() -> tuple[list[dict], str | None, int]:
    """
    抓取比房網全部分頁物件。
    回傳：(items, blocked_reason, pages_scraped)
    """
    seen: dict[str, dict] = {}
    blocked_reason: str | None = None
    pages_scraped = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="zh-TW",
        )
        page = context.new_page()
        page.set_default_timeout(25000)

        try:
            print(f"正在開啟比房網：{SOURCE_URL}")
            response = page.goto(SOURCE_URL, wait_until="domcontentloaded", timeout=45000)
            status = response.status if response else None
            print(f"HTTP 狀態：{status}｜頁面標題：{page.title()!r}")

            if status in (403, 429, 503):
                blocked = detect_cloudflare_challenge(page)
                blocked_reason = blocked or f"HTTP {status} 拒絕存取／驗證頁"
                print(f"🛡️ {blocked_reason}")
                return [], blocked_reason, 0

            blocked = detect_cloudflare_challenge(page)
            if blocked:
                print(f"🛡️ {blocked}")
                return [], blocked, 0

            try:
                page.wait_for_selector('[data-name="cardinfo"]', timeout=25000)
            except PlaywrightTimeoutError:
                blocked = detect_cloudflare_challenge(page)
                blocked_reason = (
                    blocked
                    or f"等待 [data-name='cardinfo'] 逾時（頁面標題：{page.title()!r}）"
                )
                print(f"🛡️ {blocked_reason}")
                return [], blocked_reason, 0

            # 確保從第 1 頁開始抓取
            p1_btn = page.query_selector('[data-name="page-button-1"]')
            if p1_btn:
                p1_cls = p1_btn.get_attribute("class") or ""
                if "tw-bg-main" not in p1_cls:
                    print("偵測目前非第 1 頁，切換回第 1 頁...")
                    b0 = page.evaluate(PAGE_SIG_JS)
                    p1_btn.click()
                    _, blocked = wait_for_page_change(page, b0)
                    if blocked:
                        print(f"🛡️ {blocked}")
                        return [], blocked, 0

            total_el = page.query_selector('[data-name="searchTotalCount"]')
            if total_el:
                total_txt = (total_el.inner_text() or "").strip()
                print(f"頁面顯示總件數：{total_txt}")

            for pg in range(1, MAX_PAGES + 1):
                blocked = detect_cloudflare_challenge(page)
                if blocked:
                    print(f"🛡️ 第 {pg} 頁偵測到驗證阻擋：{blocked}")
                    return [], blocked, pages_scraped

                page.evaluate(SCROLL_HYDRATE_JS)
                cards = page.evaluate(EXTRACT_JS)
                pages_scraped = pg

                for it in cards:
                    item = {
                        "category": it.get("category") or "住宅",
                        "title": str(it.get("title") or "").strip(),
                        "price": normalize_number(it.get("price", 0)),
                        "totalPing": normalize_number(it.get("totalPing", 0)),
                        "url": str(it.get("url") or "").strip(),
                        "img": str(it.get("img") or "").strip(),
                        "feature": str(it.get("feature") or "").strip(),
                    }
                    key = (
                        item["url"]
                        if "/house/" in item["url"]
                        else f"{item['title']}|{item['price']}|{item['totalPing']}"
                    )
                    if key not in seen:
                        seen[key] = {"id": len(seen) + 1, **item}
                    elif not seen[key]["img"] and item["img"]:
                        seen[key]["img"] = item["img"]

                with_img = sum(1 for v in seen.values() if v.get("img"))
                print(
                    f"第 {pg} 頁完成｜本頁 {len(cards)} 件｜累計 {len(seen)} 件（含照片 {with_img} 件）"
                )

                next_btn = page.query_selector('[data-name="page-button-next"]')
                if not next_btn:
                    print(f"找不到下一頁按鈕，結束翻頁（共 {pg} 頁）。")
                    break

                next_cls = next_btn.get_attribute("class") or ""
                next_disabled = (
                    next_btn.get_attribute("disabled") is not None
                    or next_btn.get_attribute("aria-disabled") == "true"
                    or "tw-cursor-not-allowed" in next_cls
                    or "tw-opacity-20" in next_cls
                )
                if next_disabled:
                    print(f"已達最後一頁（第 {pg} 頁），結束翻頁。")
                    break

                before_sig = page.evaluate(PAGE_SIG_JS)
                next_btn.scroll_into_view_if_needed()
                next_btn.click()

                changed, blocked = wait_for_page_change(page, before_sig)
                if blocked:
                    print(f"🛡️ 翻至第 {pg + 1} 頁時偵測到驗證阻擋：{blocked}")
                    return [], blocked, pages_scraped

                if not changed:
                    num_btn = page.query_selector(f'[data-name="page-button-{pg + 1}"]')
                    if num_btn:
                        num_btn.scroll_into_view_if_needed()
                        num_btn.click()
                        changed, blocked = wait_for_page_change(page, before_sig)
                        if blocked:
                            print(f"🛡️ 翻至第 {pg + 1} 頁時偵測到驗證阻擋：{blocked}")
                            return [], blocked, pages_scraped

                if not changed:
                    print(f"第 {pg + 1} 頁內容未變更，停止翻頁。")
                    break
        finally:
            browser.close()

    return list(seen.values()), blocked_reason, pages_scraped


def check_safety_lock(
    items: list[dict],
    existing_count: int,
    blocked_reason: str | None,
) -> tuple[bool, str]:
    """嚴格防覆寫安全鎖檢查。"""
    if blocked_reason:
        return False, f"遭遇驗證／阻擋機制：{blocked_reason}"

    new_count = len(items)
    if new_count < MIN_ITEMS:
        return False, f"抓取筆數 {new_count} 件低於最低安全門檻 {MIN_ITEMS} 件"

    if existing_count > 0:
        min_allowed = existing_count * MIN_RATIO
        if new_count < min_allowed:
            return (
                False,
                f"抓取筆數 {new_count} 件低於現有 listings.json（{existing_count} 件）的 50%（門檻 {min_allowed:.1f} 件）",
            )

    for item in items:
        combined = f"{item.get('title', '')} {item.get('feature', '')}".lower()
        for marker in CHALLENGE_TEXT_MARKERS:
            if marker in combined:
                return False, f"抓取資料包含疑似驗證頁文字（'{marker}'）"

    return True, "通過所有安全鎖驗證"


def atomic_write_listings(path: Path, items: list[dict]) -> None:
    tmp_path = path.with_suffix(".json.tmp")
    content = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(content, encoding="utf-8")

    # 寫入後重新解析驗證，確保無截斷或格式損毀
    verified = json.loads(tmp_path.read_text(encoding="utf-8"))
    if not isinstance(verified, list) or len(verified) != len(items):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("暫存檔驗證失敗，取消覆寫 listings.json")

    os.replace(tmp_path, path)


def main() -> int:
    existing_count = load_existing_count(OUTPUT_FILE)
    print(f"現有 listings.json 筆數：{existing_count} 件")

    try:
        items, blocked_reason, pages_scraped = scrape_all_pages()
    except Exception as e:
        msg = f"執行過程發生未預期錯誤（{type(e).__name__}: {e}），已啟動防覆寫安全鎖，保留現有 listings.json（{existing_count} 件）。"
        print(f"🛡️ {msg}")
        emit_warning(msg)
        write_step_summary([
            "### 🛡️ 比房網同步：安全鎖啟動（未覆寫）",
            f"- 原因：`{type(e).__name__}: {e}`",
            f"- 現有 `listings.json` 保留：**{existing_count} 件**",
        ])
        return 0

    safe, reason = check_safety_lock(items, existing_count, blocked_reason)
    if not safe:
        msg = f"安全鎖啟動（{reason}），絕不覆寫 listings.json（保留現有 {existing_count} 件）。"
        print(f"🛡️ {msg}")
        emit_warning(msg)
        write_step_summary([
            "### 🛡️ 比房網同步：安全鎖啟動（未覆寫）",
            f"- 原因：{reason}",
            f"- 本次抓取頁數：{pages_scraped} 頁｜抓取筆數：{len(items)} 件",
            f"- 現有 `listings.json` 保留：**{existing_count} 件**",
        ])
        return 0

    atomic_write_listings(OUTPUT_FILE, items)
    with_img = sum(1 for v in items if v.get("img"))
    print(
        f"✅ 已安全更新 listings.json：共 {len(items)} 件（原有 {existing_count} 件｜共 {pages_scraped} 頁｜含照片 {with_img} 件）"
    )
    write_step_summary([
        "### ✅ 比房網同步成功",
        f"- 抓取頁數：**{pages_scraped} 頁**",
        f"- 最新筆數：**{len(items)} 件**（原有 {existing_count} 件｜含照片 {with_img} 件）",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
