# re-verify undocumented endpoints before writing the doc (fresh evidence, not memory)
import os, urllib.request, urllib.parse, http.cookiejar, hashlib, json, time

UA = {'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest'}
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
B = os.environ.get('RCS_WEB', 'http://127.0.0.1:8181')

def P(u, d=None, get=False, full=False):
    body = None if get or d is None else urllib.parse.urlencode(d).encode()
    h = dict(UA)
    if body: h['Content-Type'] = 'application/x-www-form-urlencoded'
    r = op.open(urllib.request.Request(B + u, data=body, headers=h), timeout=30)
    b = r.read()
    if full: return r, b
    return b.decode('utf-8', 'replace')

print('== login (form, sha256 pw) ==')
print(P('/rcms/web/login.login.action'.replace('login.login', 'login/login'),
      {'ecsUserName': os.environ['RCS_USER'], 'ecsPassword': hashlib.sha256(os.environ['RCS_PWD'].encode()).hexdigest(), 'pwdSafeLevelLogin': '0'})[:60],
      [c.name for c in cj])

print('== wrong password ==')
try:
    print(P('/rcms/web/login/login.action', {'ecsUserName': os.environ['RCS_USER'], 'ecsPassword': 'deadbeef', 'pwdSafeLevelLogin': '0'})[:200])
except Exception as e: print('ERR', e)
# re-login
P('/rcms/web/login/login.action', {'ecsUserName': os.environ['RCS_USER'], 'ecsPassword': hashlib.sha256(os.environ['RCS_PWD'].encode()).hexdigest(), 'pwdSafeLevelLogin': '0'})

print('== findByElcMapCode missing param ==')
print(P('/rcms/web/elcMap/findByElcMapCode.action', {})[:150])
print('== findByElcMapCode BB ==')
d = json.loads(P('/rcms/web/elcMap/findByElcMapCode.action', {'elcMapCode': 'BB'}))
print({k: (str(v)[:40] + ('...' if len(str(v)) > 40 else '')) for k, v in d.items()})

print('== findElcMapListByOrgCode 1001 ==')
d = json.loads(P('/rcms/web/elcMap/findElcMapListByOrgCode.action', {'orgCode': '1001'}))
print('top keys:', sorted(d.keys()), 'rowCount:', d.get('rowCount'))
print('row0 keys:', sorted(d['rows'][0].keys()))
print('row0:', {k: v for k, v in d['rows'][0].items() if k != 'bufferDir'})

print('== getMenuListForSeleModule GET ==')
d = json.loads(P('/rcms/web/getMenuListForSeleModule.action', get=True))
print('code:', d['code'], 'top modules:', [x['name'] for x in d['data']])

print('== getSubMenuList POST parentMenuCode ==')
d = json.loads(P('/rcms/web/getSubMenuList.action', {'parentMenuCode': 'cms_1010'}))
print('keys:', sorted(d.keys()), '->', [(x['name'], x.get('url')) for x in d['code']][:6])

print('== mapData/export full ==')
cid = str(int(time.time() * 1000))
r, b = P('/rcms/web/mapData/export.action',
         {'mapCode': 'BB', 'mapDataCode': '', 'dataTyp': '', 'dataName': '', 'podCode': '',
          'areaCode': '', 'stgSecCode': '', 'exportCookieId': cid}, full=True)
hdr = dict(r.headers)
print(r.status, hdr.get('Content-Type'), '|', hdr.get('Content-Disposition'), '| len', len(b),
      '| set-cookie', str(hdr.get('Set-Cookie'))[:40])
r, b = P('/rcms/web/mapData/export.action', {'mapCode': 'BB', 'exportCookieId': str(int(time.time() * 1000) + 1)}, full=True)
print('minimal params ->', r.status, len(b), dict(r.headers).get('Content-Disposition'))

print('== queryAgvStatus extra fields ==')
req = urllib.request.Request(os.environ.get('RCS_DPS', 'http://127.0.0.1:8083') + '/rcms-dps/rest/queryAgvStatus',
                             data=json.dumps({'reqCode': 'v1', 'mapShortName': 'BB'}).encode(),
                             headers={'Content-Type': 'text/plain'})
j = json.loads(urllib.request.urlopen(req, timeout=10).read())
a = j['data'][0] if j['data'] else {}
print('fields:', sorted(a.keys()))

print('== logout ==')
try:
    print(P('/rcms/web/logout.action', get=True)[:80])
    r, b = P('/rcms/web/elcMap/findByElcMapCode.action', {'elcMapCode': 'BB'}, full=True)
    print('after logout, map fetch ->', len(b), b[:60])
except Exception as e:
    print('ERR', e)
