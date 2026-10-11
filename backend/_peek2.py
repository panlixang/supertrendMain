import json
d = json.load(open('_bt2026_data/INTC_USDT_SWAP.json'))
print('type', type(d))
if isinstance(d, dict):
    print('keys', list(d.keys()))
    for kk, vv in d.items():
        if isinstance(vv, list):
            print(kk, 'len', len(vv), 'sample', vv[0] if vv else None)
        else:
            print(kk, repr(vv)[:120])
elif isinstance(d, list):
    print('list len', len(d))
    print('sample0', d[0])
    print('sample-1', d[-1])
