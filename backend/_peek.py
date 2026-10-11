import json
d = json.load(open('_bt2026_data/_intc_base43.json'))
print('type', type(d))
if isinstance(d, dict):
    print('keys', list(d.keys()))
    for kk, vv in d.items():
        if isinstance(vv, list):
            print(kk, 'list len', len(vv), 'sample', vv[0] if vv else None)
        else:
            print(kk, repr(vv)[:120])
else:
    print('list len', len(d), 'sample', d[0] if d else None)
