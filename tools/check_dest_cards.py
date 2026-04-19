"""Extract dest-card info from index.html to map base64 cards to destination files."""
import re

lines = open('index.html', encoding='utf-8', errors='ignore').readlines()
for i, line in enumerate(lines[97:107], 98):
    if 'home-dest-card' not in line:
        continue
    # Strip the base64 blob to see readable content
    clean = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", 'BASE64', line)
    print(f'Line {i}: {clean[:400]}')
    print()
