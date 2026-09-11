# Parse Hik RCS map XML -> compact JSON for web canvas (nodes + edges + type legend)
import json, re, glob, os, sys

def parse_map(path, txt=None):
    if txt is None:
        txt = open(path, encoding='utf-8', errors='replace').read()
    name = re.search(r'<MapName>(.*?)</MapName>', txt)
    qr = re.search(r'<MapQRCode>(.*?)</MapQRCode>', txt)
    nodes, edges = {}, []
    # split into PointInfo blocks (NeighbInfo nested inside each)
    for pm in re.finditer(r'<PointInfo>(.*?)</PointInfo>', txt, re.S):
        b = pm.group(1)
        pid = re.search(r'<id>(-?\d+)</id>', b)
        x = re.search(r'<xpos>([\d.eE+-]+)</xpos>', b)
        y = re.search(r'<ypos>([\d.eE+-]+)</ypos>', b)
        v = re.search(r'<value>(-?\d+)</value>', b)
        if not (pid and x and y):
            continue
        i = int(pid.group(1))
        nodes[i] = [round(float(x.group(1)), 3), round(float(y.group(1)), 3),
                    int(v.group(1)) if v else 0]
        nm = re.search(r'<NeighbInfo>(.*?)</NeighbInfo>', b, re.S)
        if nm:
            # each neighbor = <id>..</id> possibly followed by attrs; ids repeat per edge block
            for em in re.finditer(r'<id>(\d+)</id>\s*<distance>([\d.]+)</distance>', nm.group(1)):
                edges.append([i, int(em.group(1)), float(em.group(2))])
    # dedupe undirected edges
    seen = set(); ue = []
    for a, bb, d in edges:
        k = (min(a, bb), max(a, bb))
        if k not in seen and bb in nodes and a != bb:
            seen.add(k); ue.append([a, bb])
    return {'name': name.group(1) if name else '', 'qr': qr.group(1) if qr else '',
            'nodes': [[k, v[0], v[1], v[2]] for k, v in sorted(nodes.items())],
            'edges': ue}

if __name__ == '__main__':
    out_dir = os.path.join(os.path.dirname(__file__), 'maps')
    os.makedirs(out_dir, exist_ok=True)
    legend = {}
    # 用法: python parse_map.py 地图.xml ...（CMS 界面导出的拓扑 XML；不给参数则找当前目录 地图-*.xml）
    for f in (sys.argv[1:] or glob.glob(os.path.join(os.getcwd(), '地图-*.xml'))):
        m = parse_map(f)
        fp = os.path.join(out_dir, m['qr'] + '.json')
        json.dump(m, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
        vals = {}
        for n in m['nodes']:
            vals[n[3]] = vals.get(n[3], 0) + 1
        print(m['qr'], m['name'], 'nodes:', len(m['nodes']), 'edges:', len(m['edges']),
              'types:', dict(sorted(vals.items(), key=lambda kv: -kv[1])[:10]))
        for k in vals:
            legend[k] = legend.get(k, 0) + vals[k]
    print('all type values seen:', json.dumps(dict(sorted(legend.items()))))
