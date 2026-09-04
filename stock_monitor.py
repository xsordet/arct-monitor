import json
import os
import random
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==================== 尺码监控目标 ====================
# 注意：URL 已去掉 ?colour= 参数，更容易命中边缘缓存
WATCHED_TARGETS = [
    {
        "url": "https://arcteryx.com/fr/fr/shop/womens/gamma-mx-hoody-9456",
        "target_color": "Arctic Silk",
        "target_size": "XS",
        "alias": "Gamma MX Hoody (女款)",
    }
]

STATE_FILE = "state_stock.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 补齐完整 Client Hints 标头，降低风控拦截率
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": (
        '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def send_discord_alert(content: str):
  if not WEBHOOK_URL:
    print(f"📢 [本地测试未配置 Webhook]:\n{content}")
    return
  try:
    resp = requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
    print(f"Discord 推送状态: {resp.status_code}")
  except Exception as e:
    print(f"推送失败: {e}")


def check_target_inventory(target: dict):
  url = target["url"]
  target_color = target["target_color"].strip().lower()
  target_size = target["target_size"].strip().upper()

  # 遇到 429 自动退避重试机制
  resp = None
  for attempt in range(2):
    resp = requests.get(
        url,
        headers=HEADERS,
        impersonate="chrome120",
        timeout=15,
        allow_redirects=True,
    )
    if resp.status_code == 200:
      break
    if resp.status_code == 429 and attempt == 0:
      wait_sec = random.uniform(2.5, 4.5)
      print(f"[{target['alias']}] 遭遇 429 频控，等待 {wait_sec:.1f}s 后重试...")
      time.sleep(wait_sec)

  if not resp or resp.status_code != 200:
    print(
        f"[{target['alias']}] 页面请求失败: HTTP"
        f" {resp.status_code if resp else 'No Response'}"
    )
    return None

  soup = BeautifulSoup(resp.text, "html.parser")
  script = soup.find("script", id="__NEXT_DATA__")
  if not script or not script.string:
    print(f"[{target['alias']}] 未找到 __NEXT_DATA__")
    return None

  try:
    data = json.loads(script.string)
    page_props = data.get("props", {}).get("pageProps", {})
    raw_product = page_props.get("product")
    if not raw_product:
      print(f"[{target['alias']}] 页面未包含 product 属性")
      return None

    # 解包嵌套字符串
    product_data = (
        json.loads(raw_product) if isinstance(raw_product, str) else raw_product
    )
    variants = product_data.get("variants", [])

    matched_variant = None
    for v in variants:
      c_name = (
          v.get("colourName") or v.get("colorName") or v.get("colour") or ""
      )
      s_name = (
          v.get("size")
          or v.get("sizeDescription")
          or v.get("analyticsSize")
          or ""
      )

      if (
          c_name.strip().lower() == target_color
          and s_name.strip().upper() == target_size
      ):
        matched_variant = v
        break

    if not matched_variant:
      print(
          f"[{target['alias']}] 未找到变体: {target['target_color']} /"
          f" {target['target_size']}"
      )
      return None

    stock_status = matched_variant.get("stockStatus", "OutOfStock")
    is_in_stock = stock_status in ["InStock", "LowStock"]

    return {
        "is_in_stock": is_in_stock,
        "stock_status": stock_status,
        "product_name": product_data.get("analyticsName")
        or target.get("alias"),
    }
  except Exception as e:
    print(f"[{target['alias']}] 解析错误: {e}")
    return None


def main():
  state = {}
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    except Exception:
      state = {}

  for target in WATCHED_TARGETS:
    target_key = (
        f"{target['alias']}_{target['target_color']}_{target['target_size']}"
    )
    result = check_target_inventory(target)
    if not result:
      continue

    is_in_stock = result["is_in_stock"]
    status_text = result["stock_status"]
    prod_name = result["product_name"]

    prev_in_stock = state.get(target_key, {}).get("in_stock", False)

    print(
        f"[{prod_name}] {target['target_color']} - 尺码 {target['target_size']}:"
        f" {status_text} (有货: {is_in_stock})"
    )

    # 首次或补货时触发推送
    if is_in_stock and not prev_in_stock:
      alert_msg = (
          f"🚨 **【始祖鸟尺码补货提醒！】** 🚨\n"
          f"**款式**: {prod_name}\n"
          f"**颜色**: {target['target_color']}\n"
          f"**尺码**: **{target['target_size']}** 补货上线！\n"
          f"**状态**: `{status_text}`\n"
          f"🔗 {target['url']}"
      )
      print(f"🔥 触发 Discord 推送:\n{alert_msg}")
      send_discord_alert(alert_msg)

    state[target_key] = {"in_stock": is_in_stock, "status": status_text}

  with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
  main()
