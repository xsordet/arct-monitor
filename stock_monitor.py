import json
import os
import subprocess
import sys
from bs4 import BeautifulSoup

# 强制终端实时输出，防止 GitHub Actions 日志被缓冲吞掉
sys.stdout.reconfigure(line_buffering=True)

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
  # -s: 静音模式; -L: 自动跟随 301/302 重定向
  cmd = [
      "curl",
      "-sL",
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
  if not html_text:
    print(f"[{target['alias']}] 页面获取为空")
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

    product_data = (
        json.loads(raw_product) if isinstance(raw_product, str) else raw_product
    )
    variants = product_data.get("variants", [])

    if not variants:
      print(f"[{target['alias']}] variants 变体数组为空")
      return None

    matched_variant = None
    extracted_summary = []

    for v in variants:
      # 宽容度颜色提取
      c_name = ""
      if isinstance(v.get("colour"), dict):
        c_name = (
            v["colour"].get("name")
            or v["colour"].get("label")
            or v["colour"].get("description")
            or ""
        )
      elif isinstance(v.get("colour"), str):
        c_name = v["colour"]

      if not c_name:
        for k in [
            "colourName",
            "colorName",
            "colourDescription",
            "colorDescription",
            "colourId",
        ]:
          if v.get(k):
            c_name = str(v[k])
            break

      # 宽容度尺码提取
      s_name = ""
      if isinstance(v.get("size"), dict):
        s_name = v["size"].get("name") or v["size"].get("label") or ""
      elif isinstance(v.get("size"), str):
        s_name = v["size"]

      if not s_name:
        for k in ["sizeName", "sizeDescription", "analyticsSize", "sizeLabel"]:
          if v.get(k):
            s_name = str(v[k])
            break

      extracted_summary.append(f"{c_name}/{s_name}")

      c_lower = c_name.strip().lower()
      s_upper = s_name.strip().upper()

      # 支持颜色模糊包含与尺码完全匹配
      if (
          target_color in c_lower or c_lower in target_color
      ) and s_upper == target_size:
        matched_variant = v
        break

    if not matched_variant:
      print(
          f"[{target['alias']}] 未找到变体: {target['target_color']} /"
          f" {target['target_size']}"
      )
      print(f"👉 该商品变体字段名: {list(variants[0].keys())}")
      print(f"👉 实际提取到的规格列表: {extracted_summary[:10]}")
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
  print("🚀 启动库存监控任务...")
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
        f"✅ [{prod_name}] {target['target_color']} - 尺码"
        f" {target['target_size']}: {status_text} (有货: {is_in_stock})"
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
  print("🏁 监控检查完成。")


if __name__ == "__main__":
  main()
