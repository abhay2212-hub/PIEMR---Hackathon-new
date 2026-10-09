"""Builds the folder to upload to Netlify.

    python make_netlify_site.py https://abc-def.trycloudflare.com

Creates netlify_site/ :
    index.html    = landing page
    public.html   = public page (commuters, phones)
    control.html  = control room (operator)
Drag the netlify_site folder onto https://app.netlify.com/drop

The pages get their live data from your laptop through the tunnel address you give here.
(No address? The pages also accept it in the link:  https://yoursite.netlify.app/?api=https://abc-def.trycloudflare.com)
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "netlify_site")
api = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(os.path.join(OUT, "img"))
web = os.path.join(HERE, "web")
landing = os.path.join(web, "index.html")
with open(landing, encoding="utf-8") as f:
    index_html = f.read().replace('<html lang="en">', '<html lang="en" data-static-site="true">', 1)
with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
    f.write(index_html)
shutil.copy(os.path.join(web, "public.html"), os.path.join(OUT, "public.html"))
shutil.copy(os.path.join(web, "operator.html"), os.path.join(OUT, "control.html"))
for f in ("common.css", "common.js"):
    shutil.copy(os.path.join(web, f), OUT)
for f in ("school.png", "office.png", "hospital.png"):
    if os.path.exists(os.path.join(HERE, f)):
        shutil.copy(os.path.join(HERE, f), os.path.join(OUT, "img", f))
media = os.path.join(web, "media")
if os.path.isdir(media):
    shutil.copytree(media, os.path.join(OUT, "media"))
with open(os.path.join(OUT, "config.js"), "w") as f:
    f.write(f'window.API_BASE = "{api}";   // laptop tunnel address\n')
with open(os.path.join(OUT, "_headers"), "w") as f:
    f.write("/*\n  Cache-Control: no-cache\n")

print(f"Ready: {OUT}")
print("Live data from:", api or "(not set - add ?api=<tunnel address> to the link)")
print("Upload: drag the netlify_site folder onto https://app.netlify.com/drop")
