import json
import os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==================== 配置区 ====================
# 1. 监控的分类列表页（可按需自由增删，备注友好名称）
WATCHED_CATEGORY_PAGES = [
    {
        "name": "官网女款新品 (Women New Arrivals)",
        "url": "https://arcteryx.com/fr/fr/c/womens/new-arrivals",
    },
    {
        "name": "奥莱女款新品 (Outlet Women Just Landed)",
        "url": "https://outlet.arcteryx.com/fr/fr/c/womens/just-landed/wid-39r1kkxj",
    },
    {
        "name": "奥莱男款新品 (Outlet Men Just Landed)",
        "url": "https://outlet.arcteryx.com/fr/fr/c/mens/just-landed/wid-39r1kkxj",
    },
]

# 2. 监控特定单品新颜色的链接
WATCHED_PRODUCT_URLS = [
    "https://arcteryx.com/fr/fr/shop/womens/elec-insulated-jacket-9512"
]

STATE_FILE = "state.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}
# ===============================================


def send_discord_alert(content: str):
  if not WEBHOOK_URL:
    print(f"📢 [本地测试推送]:\n{content}")
    return
  try:
    resp = requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
    print(f"Discord 推送结果: {resp.status_code}")
  except Exception as e:
    print(f"推送失败: {e}")


# ---------------- 任务 1：通用列表页商品提取 ----------------
def fetch_category_products(page_url: str):
  resp = requests.get(
      page_url, headers=HEADERS, impersonate="chrome120", timeout=15
  )
  if resp.status_code != 200:
    print(f"页面抓取失败 HTTP {resp.status_code}: {page_url}")
    return [], 0

  soup = BeautifulSoup(resp.text, "html.parser")
  script = soup.find("script", id="__NEXT_DATA__")
  if not script or not script.string:
    return [], 0

  data = json.loads(script.string)
  page_props = data.get("props", {}).get("pageProps", {})

  # 自动提取当前站点的域名根路径（arcteryx.com 或 outlet.arcteryx.com）
  parsed_uri = urlparse(page_url)
  base_domain = f"{parsed_uri.scheme}://{parsed_uri.netloc}"

  # 1. 递归读取在架总件数
  total_count = 0

  def get_count(node):
    nonlocal total_count
    if (
        isinstance(node, dict)
        and "itemCount" in node
        and isinstance(node["itemCount"], dict)
    ):
      total_count = node["itemCount"].get("count", 0)
      return
    if isinstance(node, dict):
      for v in node.values():
        get_count(v)
    elif isinstance(node, list):
      for it in node:
        get_count(it)

  get_count(page_props)

  # 2. 递归读取商品卡片列表
  raw_products = []

  def get_items(node):
    if (
        isinstance(node, dict)
        and "name" in node
        and ("price" in node or "pricing" in node)
    ):
      if "slug" in node or "model" in node or "modelNumber" in node:
        raw_products.append(node)
        return
    if isinstance(node, dict):
      for v in node.values():
        get_items(v)
    elif isinstance(node, list):
      for it in node:
        get_items(it)

  get_items(page_props)

  parsed, seen = [], set()
  for p in raw_products:
    name = p.get("name") or p.get("title", "")
    slug = p.get("slug") or p.get("id", "")
    pid = str(p.get("id") or p.get("modelNumber") or name)

    price_val = (
        p.get("price")
        or p.get("pricing", {}).get("formattedPrice")
        or "价格见详情"
    )
    price = (
        f"{price_val} €"
        if isinstance(price_val, (int, float))
        else str(price_val)
    )

    # 兼容处理相对路径与绝对路径
    if slug:
      if str(slug).startswith("http"):
        item_url = str(slug)
      elif str(slug).startswith("/"):
        item_url = f"{base_domain}{slug}"
      else:
        item_url = f"{base_domain}/fr/fr/shop/{slug}"
    else:
      item_url = page_url

    if pid not in seen and len(name) > 1:
      seen.add(pid)
      parsed.append({"id": pid, "name": name, "price": price, "url": item_url})

  return parsed, total_count


# ---------------- 任务 2：单品颜色监控提取 ----------------
def fetch_product_colors(url: str):
  resp = requests.get(
      url, headers=HEADERS, impersonate="chrome120", timeout=15
  )
  if resp.status_code != 200:
    return "未知商品", set()

  soup = BeautifulSoup(resp.text, "html.parser")
  script = soup.find("script", id="__NEXT_DATA__")
  if not script or not script.string:
    return "未知商品", set()

  data = json.loads(script.string)
  page_props = data.get("props", {}).get("pageProps", {})

  title = (
      soup.find("title").string.split("|")[0].strip()
      if soup.find("title")
      else "始祖鸟单品"
  )
  colors = set()

  def find_colors(node):
    if isinstance(node, dict):
      for k in ["colourName", "colorName"]:
        if k in node and isinstance(node[k], str) and node[k].strip():
          colors.add(node[k].strip())

      if "colourways" in node and isinstance(node["colourways"], list):
        for cw in node["colourways"]:
          if isinstance(cw, dict):
            c = cw.get("colourName") or cw.get("name")
            if c:
              colors.add(str(c).strip())

      for val in node.values():
        find_colors(val)
    elif isinstance(node, list):
      for item in node:
        find_colors(item)

  find_colors(page_props)
  return title, colors


# ---------------- 主执行流程 ----------------
def main():
  state = {}
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    except Exception:
      state = {}

  categories_state = state.get("categories", {})
  product_colors_cache = state.get("product_colors", {})

  # --- 1. 批量巡检所有分类列表页 ---
  for cat in WATCHED_CATEGORY_PAGES:
    cat_name = cat["name"]
    cat_url = cat["url"]

    current_items, total_count = fetch_category_products(cat_url)
    if not current_items:
      continue

    current_ids = {p["id"] for p in current_items}
    saved_cat_info = categories_state.get(cat_url, {})
    cached_ids = set(saved_cat_info.get("known_ids", []))

    if not cached_ids:
      print(
          f"[{cat_name}] 首次初始化：记录 {len(current_ids)} 件，在架总数:"
          f" {total_count}"
      )
    else:
      new_ids = current_ids - cached_ids
      if new_ids:
        new_items = [p for p in current_items if p["id"] in new_ids]
        lines = [
            f"- **{it['name']}** ({it['price']})\n  {it['url']}"
            for it in new_items
        ]
        alert_text = (
            f"🚨 **【{cat_name}】发现 {len(new_items)} 件新品/补货！** 🚨\n"
            + "\n".join(lines)
        )
        print(f"🚨 [{cat_name}] 发现上新:\n{alert_text}")
        send_discord_alert(alert_text)
      else:
        print(f"[{cat_name}] 扫描正常，无新品（在架总数: {total_count}）。")

    categories_state[cat_url] = {
        "name": cat_name,
        "known_ids": list(current_ids),
        "total_count": total_count,
    }

  state["categories"] = categories_state

  # --- 2. 巡检单品颜色更新 ---
  for url in WATCHED_PRODUCT_URLS:
    title, current_colors = fetch_product_colors(url)
    if not current_colors:
      continue

    cached_colors = set(product_colors_cache.get(url, []))

    if not cached_colors:
      print(f"[单品监控] 首次记录 {title} 颜色: {current_colors}")
      product_colors_cache[url] = list(current_colors)
    else:
      new_colors = current_colors - cached_colors
      if new_colors:
        alert_msg = (
            f"🎨 **【始祖鸟单品新配色上线】** 🎨\n"
            f"**款式**: {title}\n"
            f"**新加配色**: {', '.join(new_colors)}\n"
            f"🔗 {url}"
        )
        print(f"🚨 发现新配色: {new_colors}")
        send_discord_alert(alert_msg)
        product_colors_cache[url] = list(current_colors)
      else:
        print(f"[单品监控] {title} 配色无变动。")

  state["product_colors"] = product_colors_cache

  # 写入持久化存储
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
  main()
