import json
import os
import subprocess
from bs4 import BeautifulSoup

# ==================== 尺码监控目标 ====================
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


def send_discord_alert(content: str):
  if not WEBHOOK_URL:
    print(f"📢 [未配置 Webhook]:\n{content}")
    return
  try:
    import urllib.request

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
      print(f"Discord 推送状态: {resp.status}")
  except Exception as e:
    print(f"推送失败: {e}")


def fetch_html_via_native_curl(url: str) -> str:
  """直接调用 Linux 系统的原生 curl，完全避开 TLS 指纹反噬"""
  cmd = [
      "curl",
      "-s",
      "-A",
      (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/126.0.0.0 Safari/537.36"
      ),
      url,
  ]
  res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
  return res.stdout


def check_target_inventory(target: dict):
  url = target["url"]
  target_color = target["target_color"].strip().lower()
  target_size = target["target_size"].strip().upper()

  html_text = fetch_html_via_native_curl(url)
  if not html_text or "Gamma MX Hoody" not in html_text:
    print(f"[{target['alias']}] 页面获取失败或未匹配到正文")
    return None

  soup = BeautifulSoup(html_text, "html.parser")
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

    # 解开嵌套的 product 字符串
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
