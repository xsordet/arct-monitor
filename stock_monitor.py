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
    extracted_summary = []  # 记录所有解析到的变体，供排错用

    for v in variants:
      # 1. 宽容度更高的颜色提取
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

      # 2. 宽容度更高的尺码提取
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

      # 判断是否匹配（支持颜色全名、或者包含关系、或者 colourId 匹配）
      c_lower = c_name.strip().lower()
      s_upper = s_name.strip().upper()

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
      print(f"👉 该商品第 1 个变体的所有键名: {list(variants[0].keys())}")
      print(f"👉 该商品第 1 个变体完整内容: {variants[0]}")
      print(f"👉 脚本实际提取出的所有规格列表 (前 10 个): {extracted_summary[:10]}")
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
