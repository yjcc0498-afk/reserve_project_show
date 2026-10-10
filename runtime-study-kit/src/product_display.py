"""Human-readable product rows shared by terminal and local UI."""
import unicodedata

def plain(value):
    return ' '.join(str(value if value is not None else '').split())

def price_text(value):
    text=plain(value)
    return f'¥{text}' if text else '暂无价格'

def shorten(value,width=68):
    text=plain(value);parts=[];used=0
    for char in text:
        size=2 if unicodedata.east_asian_width(char) in ('W','F') else 1
        if used+size>width-1:return ''.join(parts)+'…'
        parts.append(char);used+=size
    return text

def print_products(products,stream):
    if not products:return
    print('\n  本次商品',file=stream)
    print('  '+'─'*46,file=stream)
    for index,product in enumerate(products,1):
        title=product.get('title') or product.get('t') or product.get('name') or '未提供名称'
        print(f"  {index:02d}. {shorten(title)}",file=stream)
        print(f"      {price_text(product.get('price',product.get('jp')))}  ·  SKU {plain(product.get('sku'))}",file=stream)
    print(f'  共 {len(products)} 条；完整名称和链接见商品 JSON。\n',file=stream,flush=True)
