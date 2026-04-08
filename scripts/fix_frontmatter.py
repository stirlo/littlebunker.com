#!/usr/bin/env python3
"""
fix_frontmatter.py — One-time repair of broken Jekyll frontmatter in littlebunker.com
Fixes: escaped quotes, liquid tags, unbalanced quotes in YAML string fields.
Run from repo root: python3 scripts/fix_frontmatter.py
"""
import glob, re, yaml
from pathlib import Path

POSTS_DIR = Path('/Users/DT/Documents/repo/littlebunker.com/_posts')

def sanitize_yaml_string(value):
    if not isinstance(value, str):
        return value
    value = value.replace('\\"', '\u201c').replace("\\'", "\u2019")
    value = value.replace('\\n', ' ').replace('\\t', ' ')
    value = value.replace('{{', '{ {').replace('}}', '} }')
    value = value.replace('{%', '{ %').replace('%}', '% }')
    value = re.sub(r'\s+', ' ', value).strip()
    return value

def fix_post(path):
    content = path.read_text(encoding='utf-8')
    if not content.startswith('---'):
        return False
    parts = content.split('---', 2)
    if len(parts) < 3:
        return False
    fm_raw, body = parts[1], parts[2]
    if not ('\\"' in fm_raw or "\\'" in fm_raw or '{{' in fm_raw or '{%' in fm_raw):
        return False
    # Raw repair before yaml parse
    fm_raw_clean = fm_raw.replace('\\"', '\u201c').replace('\\n', ' ')
    try:
        fm = yaml.safe_load(fm_raw_clean) or {}
    except yaml.YAMLError:
        print(f'  SKIP (unparseable): {path.name}')
        return False
    for key in ('title', 'description', 'excerpt', 'summary'):
        if key in fm:
            fm[key] = sanitize_yaml_string(str(fm[key]))
    new_content = f"---\n{yaml.dump(fm, default_flow_style=False, allow_unicode=True)}---\n{body}"
    path.write_text(new_content, encoding='utf-8')
    return True

fixed = 0
for p in sorted(POSTS_DIR.glob('*.md')):
    if fix_post(p):
        print(f'  Fixed: {p.name}')
        fixed += 1
print(f'\nDone — fixed {fixed} posts.')
