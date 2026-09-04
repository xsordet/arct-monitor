import json
import os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==================== 配置区 ====================
# 1. 监控的新品分类列表（发现新商品卡片时报警）
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

# 2. 监控特定款式的颜色上新（从所属分类页提取，无视反爬 429）
# 后续想加其他衣服，照此格式添加即可
WATCHED_PRODUCTS = [
    {
        "name": "Veste isolante Elec Femme",
        "slug": "womens/elec-insulated-jacket-9512",
        "category_url": "https://arcteryx.com/fr/fr/c/womens/insulated-jackets",
        "detail_url": (
            "https://arcteryx.com/fr/fr/shop/womens/elec-insulated-jacket-9512"
        ),
    }
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
    print(f"📢 [测试模式 - 未配置 Webhook]:\n{content}")
    return
  try:
    resp = requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
    print(f"Discord 推送结果: {resp.status_code}")
  except Exception as e:
    print(f"推送失败: {e}")


def get_page_cards_and_count(page_url: str):
  """通用函数：拉取分类页，返回 (卡片列表, 标称总数)"""
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

  # 1. 递归提取总数
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

  # 2. 递归提取所有商品卡片
  raw_cards = []

  def get_cards(node):
    if isinstance(node, dict):
      if "name" in node and ("slug" in node or "model" in node or "id" in node):
        raw_cards.append(node)
        return
      for v in node.values():
        get_cards(v)
    elif isinstance(node, list):
      for it in node:
        get_cards(it)

  get_cards(page_props)
  return raw_cards, total_count


def extract_colors_from_card(card: dict) -> set[str]:
  """从特定商品的 JSON 卡片中提取全部颜色选项"""
  colors = set()

  def scan_color_nodes(node):
    if isinstance(node, dict):
      for k in ["colourName", "colorName", "name", "label"]:
        val = node.get(k)
        if isinstance(val, str):
          val = val.strip()
          # 过滤系统默认词汇，只保留真实颜色名
          if (
              val
              and 1 < len(val) < 30
              and val not in ["Arc'teryx", "Femme", "Homme", "Jacket"]
          ):
            colors.add(val)
      for v in node.values():
        scan_color_nodes(v)
    elif isinstance(node, list):
      for item in node:
        scan_color_nodes(item)

  # 优先扫描颜色变体专用字段
  for field in ["colourOptions", "colourVariants", "selectedColour"]:
    if field in card and card[field]:
      scan_color_nodes(card[field])

  # 兜底：如果上述字段为空，递归全卡片
  if not colors:
    scan_color_nodes(card)

  return colors


def main():
  state = {}
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    except Exception:
      state = {}

  categories_state = state.get("categories", {})
  product_colors_state = state.get("product_colors", {})

  # ==================== 任务 1：监控分类列表新品 ====================
  for cat in WATCHED_CATEGORY_PAGES:
    cat_name = cat["name"]
    cat_url = cat["url"]

    parsed_uri = urlparse(cat_url)
    base_domain = f"{parsed_uri.scheme}://{parsed_uri.netloc}"

    raw_cards, total_count = get_page_cards_and_count(cat_url)
    if not raw_cards:
      continue

    # 规范化去重
    current_items = []
    seen_ids = set()
    for card in raw_cards:
      name = card.get("name") or card.get("title", "")
      slug = card.get("slug") or card.get("id", "")
      pid = str(card.get("id") or card.get("modelNumber") or slug or name)
      price_val = (
          card.get("price")
          or card.get("pricing", {}).get("formattedPrice")
          or "见详情"
      )
      price = (
          f"{price_val} €"
          if isinstance(price_val, (int, float))
          else str(price_val)
      )

      if slug:
        if str(slug).startswith("http"):
          item_url = str(slug)
        elif str(slug).startswith("/"):
          item_url = f"{base_domain}{slug}"
        else:
          item_url = f"{base_domain}/fr/fr/shop/{slug}"
      else:
        item_url = cat_url

      if pid not in seen_ids and len(name) > 1:
        seen_ids.add(pid)
        current_items.append({
            "id": pid,
            "name": name,
            "price": price,
            "url": item_url,
        })

    cached_info = categories_state.get(cat_url, {})
    cached_ids = set(cached_info.get("known_ids", []))

    if not cached_ids:
      print(
          f"[{cat_name}] 初始化基准：{len(current_items)} 件，总计:"
          f" {total_count}"
      )
    else:
      new_ids = seen_ids - cached_ids
      if new_ids:
        new_items = [p for p in current_items if p["id"] in new_ids]
        lines = [
            f"- **{it['name']}** ({it['price']})\n  {it['url']}"
            for it in new_items
        ]
        alert_msg = (
            f"🚨 **【{cat_name}】发现 {len(new_items)} 件新品上线！** 🚨\n"
            + "\n".join(lines)
        )
        print(f"🚨 触发推送:\n{alert_msg}")
        send_discord_alert(alert_msg)
      else:
        print(f"[{cat_name}] 正常（在架 {total_count} 件，无新增）。")

    categories_state[cat_url] = {
        "name": cat_name,
        "known_ids": list(seen_ids),
        "total_count": total_count,
    }

  # ==================== 任务 2：监控目标款式的颜色更新 ====================
  for prod in WATCHED_PRODUCTS:
    prod_name = prod["name"]
    prod_slug = prod["slug"]
    cat_url = prod["category_url"]
    detail_url = prod["detail_url"]

    raw_cards, _ = get_page_cards_and_count(cat_url)
    target_card = None
    for card in raw_cards:
      if prod_slug in str(card.get("slug", "")):
        target_card = card
        break

    if not target_card:
      print(f"[款式颜色] 未在分类页中找到 {prod_name}")
      continue

    current_colors = extract_colors_from_card(target_card)
    cached_colors = set(product_colors_state.get(prod_slug, []))

    if not cached_colors:
      print(
          f"[款式颜色] 首次记录 {prod_name} 现有配色:"
          f" {list(current_colors)}"
      )
      product_colors_state[prod_slug] = list(current_colors)
    else:
      new_colors = current_colors - cached_colors
      if new_colors:
        alert_msg = (
            f"🎨 **【始祖鸟单品新配色上线！】** 🎨\n"
            f"**款式**: {prod_name}\n"
            f"**新增配色**: {', '.join(new_colors)}\n"
            f"**当前所有颜色**: {', '.join(current_colors)}\n"
            f"🔗 {detail_url}"
        )
        print(f"🚨 发现新配色上线:\n{alert_msg}")
        send_discord_alert(alert_msg)
        product_colors_state[prod_slug] = list(current_colors)
      else:
        print(
            f"[款式颜色] {prod_name} 配色无变化（当前: {list(current_colors)}）。"
        )

  # 写入持久化
  state["categories"] = categories_state
  state["product_colors"] = product_colors_state
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
  main()
