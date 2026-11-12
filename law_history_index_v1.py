"""Build law_history_sources_v1/index.json for the in-app annex comparison.

Lists installation-decree versions (소방법 시행령 and its successors, not the
construction-business decree) with effective date, master id and source path.
Run after law_history_sync_* adds new versions:  python3 law_history_index_v1.py
"""
import json
from pathlib import Path

ROOT = Path('law_history_sources_v1')


def main():
    rows = []
    for path in sorted((ROOT / 'versions').glob('*.json')):
        item = json.loads(path.read_text(encoding='utf-8'))
        name = item.get('name', '')
        if '시행령' not in name or '공사업' in name:
            continue
        rows.append({
            'date': item['effectiveDate'],
            'master': item['master'],
            'name': name,
            'source': item['source'],
        })
    rows.sort(key=lambda r: (r['date'], int(r['master'])))
    out = {'schemaVersion': 1, 'kind': 'installation-decree', 'versions': rows}
    (ROOT / 'index.json').write_text(
        json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'{len(rows)} versions -> {ROOT / "index.json"}')


if __name__ == '__main__':
    main()
