import json
import os
from bs4 import BeautifulSoup
from curl_cffi import requests

URL = "https://arcteryx.com/fr/fr/c/womens/new-arrivals"
STATE_FILE = "state.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def send_discord_alert(items: list[dict]):
  if not WEBHOOK_URL:
    print("未配置 DISCORD_WEBHOOK_URL，跳过推送。")
    return

  lines = [f"- **{it['name']}** ({it['price']})\n  {it['url']}" for it in items]
  payload = {
      "content": (
          f"🚨 **始祖鸟官网发现 {len(items)} 件新品上架！** 🚨\n"
          + "\n".join(lines)
      )
  }
  try:
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    print(f"Discord 推送结果: {resp.status_code}")
  except Exception as e:
    print(f"推送失败: {e}")


def fetch_latest_products():
  resp = requests.get(
      URL, headers=HEADERS, impersonate="chrome120", timeout=15
  )
  if resp.status_code != 200:
    print(f"页面抓取失败，HTTP 状态: {resp.status_code}")
    return [], 0

  soup = BeautifulSoup(resp.text, "html.parser")
  script = soup.find("script", id="__NEXT_DATA__")
  if not script or not script.string:
    return [], 0

  data = json.loads(script.string)
  page_props = data.get("props", {}).get("pageProps", {})

  # 1. 递归读取总件数
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

  # 2. 递归读取首屏商品列表
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

  parsed = []
  seen = set()
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

    if pid not in seen and len(name) > 1:
      seen.add(pid)
      parsed.append({
          "id": pid,
          "name": name,
          "price": price,
          "url": (
              f"https://arcteryx.com/fr/fr/shop/{slug}"
              if slug and not str(slug).startswith("http")
              else str(slug)
          ),
      })

  return parsed, total_count


def main():
  # 读取持久化历史数据
  last_state = {}
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        last_state = json.load(f)
    except Exception:
      last_state = {}

  known_ids = set(last_state.get("known_ids", []))

  current_items, total_count = fetch_latest_products()
  if not current_items:
    return

  current_ids = {p["id"] for p in current_items}

  # 初次运行建立基准库
  if not known_ids:
    print(f"首次初始化：记录当前 {len(current_ids)} 件商品，官方总数: {total_count}")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
      json.dump(
          {"known_ids": list(current_ids), "total_count": total_count},
          f,
          indent=2,
          ensure_ascii=False,
      )
    return

  # 对比差集（识别新增商品）
  new_ids = current_ids - known_ids
  if new_ids:
    new_items = [p for p in current_items if p["id"] in new_ids]
    print(f"检测到 {len(new_items)} 款新上架！")
    send_discord_alert(new_items)
  else:
    print(f"扫描完毕，无新品上架（官方标称在架: {total_count} 件）。")

  # 写入最新状态
  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(
        {"known_ids": list(current_ids), "total_count": total_count},
        f,
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
  main()
