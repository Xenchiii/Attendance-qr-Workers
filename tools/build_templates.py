import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "templates")
TARGET = os.path.join(ROOT, "src", "templates_bundle.py")

names = sorted(name for name in os.listdir(SOURCE) if name.endswith(".html"))
lines = ["TEMPLATES = {"]
for name in names:
    with open(os.path.join(SOURCE, name), "r", encoding="utf-8") as handle:
        lines.append("    %r: %r," % (name, handle.read()))
lines.append("}")
with open(TARGET, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
print("bundled %d templates into src/templates_bundle.py" % len(names))
