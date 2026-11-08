#!/usr/bin/env python3
"""Build rx65n-web-writer.html: index.html with app.js inlined, one file that
works from disk (file://) with no network.  Run after changing either."""
import os
d = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(d, "index.html"), encoding="utf-8").read()
js = open(os.path.join(d, "app.js"), encoding="utf-8").read()
tag = '<script src="app.js"></script>'
assert html.count(tag) == 1 and "</script" not in js
out = html.replace(tag, "<script>\n" + js + "</script>")
open(os.path.join(d, "rx65n-web-writer.html"), "w", encoding="utf-8").write(out)
