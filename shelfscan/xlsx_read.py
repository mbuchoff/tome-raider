import sys, zipfile, re, json, xml.etree.ElementTree as ET
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
def col_idx(ref):
    letters=re.match(r'[A-Z]+',ref).group(); n=0
    for ch in letters: n=n*26+ord(ch)-64
    return n-1
def read(path):
    z=zipfile.ZipFile(path)
    ss=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',NS):
            ss.append(''.join(t.text or '' for t in si.iter('{%s}t'%NS['m'])))
    wb=ET.fromstring(z.read('xl/workbook.xml'))
    rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rmap={r.get('Id'):r.get('Target') for r in rels}
    out={}
    for sh in wb.find('m:sheets',NS):
        tgt=rmap[sh.get('{%s}id'%NS['r'])].lstrip('/')
        tgt=tgt if tgt.startswith('xl/') else 'xl/'+tgt
        rows=[]
        for row in ET.fromstring(z.read(tgt)).iter('{%s}row'%NS['m']):
            vals={}
            for c in row.findall('m:c',NS):
                v=c.find('m:v',NS); t=c.get('t')
                if t=='inlineStr':
                    val=''.join(x.text or '' for x in c.iter('{%s}t'%NS['m']))
                elif v is None: continue
                elif t=='s': val=ss[int(v.text)]
                else: val=v.text
                vals[col_idx(c.get('r'))]=val
            if vals:
                rows.append([vals.get(i) for i in range(max(vals)+1)])
        out[sh.get('name')]=rows
    return out
if __name__=='__main__':
    for p in sys.argv[1:]:
        print('=====',p.split('/')[-1])
        for name,rows in read(p).items():
            print(f'--- sheet {name!r}: {len(rows)} rows')
            for r in rows[:6]: print('   ',r[:20])
