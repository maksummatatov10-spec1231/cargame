import struct, zlib
def _read_prop(d,p):
    t=chr(d[p]); p+=1
    if t=='Y': v=struct.unpack('<h',d[p:p+2])[0]; p+=2
    elif t=='C': v=d[p]; p+=1
    elif t=='I': v=struct.unpack('<i',d[p:p+4])[0]; p+=4
    elif t=='F': v=struct.unpack('<f',d[p:p+4])[0]; p+=4
    elif t=='D': v=struct.unpack('<d',d[p:p+8])[0]; p+=8
    elif t=='L': v=struct.unpack('<q',d[p:p+8])[0]; p+=8
    elif t in 'fdlbi':
        cnt,enc,cl=struct.unpack('<III',d[p:p+12]); p+=12
        raw=d[p:p+cl]; p+=cl
        if enc==1: raw=zlib.decompress(raw)
        fmt={'f':'f','d':'d','l':'q','i':'i','b':'b'}[t]
        v=struct.unpack('<%d%s'%(cnt,fmt),raw)
    elif t in 'SRr':
        l=struct.unpack('<I',d[p:p+4])[0]; p+=4
        v=d[p:p+l]; p+=l
        if t=='S': v=v.decode('utf8','ignore')
    else: raise Exception('type '+t)
    return v,p
def _read_node(d,p,ver=7500):
    # FBX 7.5 (version 7500) widened the node header from three 32-bit values
    # to three 64-bit ones. Older files, including the very common 7400, still
    # use the 32-bit layout, so the version has to drive the unpack.
    hdr = 25 if ver >= 7500 else 13
    if p + hdr > len(d):
        return None, len(d)
    if ver >= 7500:
        end,nprop,plen=struct.unpack('<QQQ',d[p:p+24]); p+=24
    else:
        end,nprop,plen=struct.unpack('<III',d[p:p+12]); p+=12
    nl=d[p]; p+=1
    # A null record (all zeroes) terminates a list of siblings.
    if end == 0 and nprop == 0 and plen == 0 and nl == 0:
        return None, p
    if end > len(d) or p + nl > len(d):
        return None, len(d)
    name=d[p:p+nl].decode('utf8','ignore'); p+=nl
    if end==0: return None,p
    props=[]
    for _ in range(nprop):
        v,p=_read_prop(d,p); props.append(v)
    kids=[]
    while p<end:
        # The version has to be threaded through: children use the same header
        # width as their parent, and forgetting it silently reads 64-bit
        # headers out of a 7400 file, which makes every node look childless.
        c,p2=_read_node(d,p,ver)
        p=p2
        if c is None: break
        kids.append(c)
    return (name,props,kids),end
def parse(path):
    d=open(path,'rb').read()
    ver=struct.unpack('<I',d[23:27])[0]
    pos=27; nodes=[]
    # The footer is padding, not a node; stop before running into it.
    while pos < len(d):
        n,pos=_read_node(d,pos,ver)
        if n is None: break
        nodes.append(n)
    return nodes


def version(path):
    with open(path,'rb') as fh:
        return struct.unpack('<I',fh.read(27)[23:27])[0]
def props70(node):
    out={}
    for c in node[2]:
        if c[0]=='Properties70':
            for p in c[2]:
                out[p[1][0]]=p[1][4:]
    return out
