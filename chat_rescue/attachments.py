"""Private, bounded attachment bundles and account-relative file transactions.

Never opens documents, launches media viewers, transcodes, or uploads payloads.
The adapter accepts only positively matched resources and portable DAT layouts.
"""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

CAPABILITY = 'mac-attachments-v1'
TYPES = {3, 43, (6 << 32) | 49}
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
DAT1, DAT2 = b'\x07\x08V1\x08\x07', b'\x07\x08V2\x08\x07'


def filename(name):
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or len(name.encode('utf-8')) > 240 or any(ord(c) < 32 for c in name)
            or any(c in name for c in '/\\:\x00')):
        raise ValueError('附件文件名不安全')
    return name


def validate_path(relative):
    if not isinstance(relative, str) or str(PurePosixPath(relative)) != relative:
        raise ValueError('附件路径无效')
    p = PurePosixPath(relative)
    if p.is_absolute() or any(x in ('.', '..') for x in p.parts):
        raise ValueError('附件路径越界')
    parts = p.parts
    if (len(parts) == 6 and parts[:2] == ('msg','attach')
            and re.fullmatch('[0-9a-f]{32}', parts[2]) and parts[4] == 'Img'):
        month, name = parts[3], parts[5]
    elif len(parts) == 4 and parts[:2] in (('msg','video'), ('msg','file')):
        month, name = parts[2], parts[3]
    else:
        raise ValueError('附件路径不在允许的微信媒体目录内')
    if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('附件月份无效')
    filename(name)
    return relative


def path_at(root, relative):
    validate_path(relative)
    p = Path(root)
    if p.is_symlink():
        raise ValueError('附件根目录不能是符号链接')
    for part in PurePosixPath(relative).parts:
        p = p / part
        if p.is_symlink():
            raise ValueError('附件文件或父目录不能是符号链接')
    if p.exists() and not p.is_file():
        raise ValueError('附件目的地不是普通文件')
    return p


def file_hash(root, relative):
    from .workflow import digest
    p = path_at(root, relative)
    return digest(p) if p.exists() else None


def check_state(root, expected):
    return {rel:file_hash(root, rel) for rel in expected} == expected


def read_file(root, relative):
    p = path_at(root, relative)
    if not p.is_file() or not 0 < p.stat().st_size <= MAX_FILE:
        raise ValueError('附件缺失、为空或超过单文件256MiB限制')
    from .workflow import digest
    before = digest(p)
    with p.open('rb') as f:
        raw = f.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE or hashlib.sha256(raw).hexdigest() != before or digest(p) != before:
        raise ValueError('读取附件期间文件改变')
    return raw, before


def aes(data, key, encrypt):
    """Use the OpenSSL provider already linked by the bundled SQLCipher library."""
    from .vendor.sqlcipher_probe import library
    lib = library()
    specs = {
        'EVP_CIPHER_CTX_new': (C.c_void_p, []),
        'EVP_CIPHER_CTX_free': (None, [C.c_void_p]),
        'EVP_aes_128_ecb': (C.c_void_p, []),
        'EVP_CipherInit_ex': (C.c_int, [C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,C.c_int]),
        'EVP_CipherUpdate': (C.c_int, [C.c_void_p,C.c_void_p,C.POINTER(C.c_int),C.c_void_p,C.c_int]),
        'EVP_CipherFinal_ex': (C.c_int, [C.c_void_p,C.c_void_p,C.POINTER(C.c_int)]),
    }
    try:
        for name,(restype,argtypes) in specs.items():
            fn = getattr(lib,name);fn.restype=restype;fn.argtypes=argtypes
    except AttributeError:
        raise ValueError('打包加密库没有图片DAT所需AES接口') from None
    if len(key) != 16 or len(data) > MAX_FILE:
        raise ValueError('DAT加密参数无效')
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise ValueError('无法创建DAT加密上下文')
    try:
        out=C.create_string_buffer(len(data)+32);n=C.c_int();tail=C.c_int()
        if (lib.EVP_CipherInit_ex(ctx,lib.EVP_aes_128_ecb(),None,key,None,int(encrypt)) != 1
                or lib.EVP_CipherUpdate(ctx,out,C.byref(n),data,len(data)) != 1
                or lib.EVP_CipherFinal_ex(ctx,C.byref(out,n.value),C.byref(tail)) != 1):
            raise ValueError('DAT密钥或填充校验失败')
        return out.raw[:n.value+tail.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def image_format(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        pos=8;seen=[]
        while pos+12<=len(raw):
            n=struct.unpack('>I',raw[pos:pos+4])[0]
            if n>len(raw)-pos-12:break
            tag=raw[pos+4:pos+8];body=raw[pos+8:pos+8+n]
            crc=struct.unpack('>I',raw[pos+8+n:pos+12+n])[0]
            if zlib.crc32(tag+body)&0xffffffff!=crc:break
            seen.append(tag);pos+=12+n
            if tag==b'IEND':
                if pos==len(raw) and seen[0]==b'IHDR':return 'png'
                break
    elif raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'):
        return 'jpeg'
    elif raw.startswith((b'GIF87a',b'GIF89a')) and raw.endswith(b';') and len(raw)>13:
        return 'gif'
    elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP' and len(raw)>=20 and struct.unpack('<I',raw[4:8])[0]+8==len(raw):
        return 'webp'
    elif raw.startswith(b'wxgf') and len(raw)>16:
        return 'wxgf'
    elif len(raw)>16 and raw[4:8]==b'ftyp' and raw[8:12] in (b'heic',b'heix',b'hevc',b'hevx',b'mif1',b'avif'):
        return 'heif'
    raise ValueError('图片容器不完整或格式尚未适配')


def dat_parts(raw):
    if len(raw)<15 or raw[:6] not in (DAT1,DAT2):
        raise ValueError('DAT版本尚未适配')
    a,x=struct.unpack('<II',raw[6:14]);n=(a+16-a%16) if a else 0
    if a>MAX_FILE or x>MAX_FILE or 15+n+x>len(raw):
        raise ValueError('DAT长度无效')
    return a,x,n


def decode_dat(raw, key, xor):
    a,x,n=dat_parts(raw)
    head=aes(raw[15:15+n],key,False) if n else b''
    if len(head)!=a:raise ValueError('DAT解密长度不一致')
    tail=raw[15+n:]
    return head+(tail[:-x] if x else tail)+(bytes(b^xor for b in tail[-x:]) if x else b'')


def encode_dat(raw, header, key, xor):
    if not isinstance(header,bytes) or len(header)!=15:raise ValueError('DAT容器描述无效')
    a,x=struct.unpack('<II',header[6:14])
    if len(header)!=15 or header[:6] not in (DAT1,DAT2) or a+x>len(raw):
        raise ValueError('DAT容器描述无效')
    head=aes(raw[:a],key,True) if a else b''
    rest=raw[a:]
    return header+head+(rest[:-x] if x else rest)+(bytes(b^xor for b in rest[-x:]) if x else b'')


class Images:
    def __init__(self, root, owner):
        self.root=Path(root);self.owner=owner;self.dependencies={};self._verified=None;self._v1=None

    def candidates(self):
        app_data=self.root.parent.parent/'app_data'
        codes=set()
        for sub in ('net/kvcomm','ilink/kvcomm'):
            folder=app_data/sub
            if folder.is_symlink():raise ValueError('图片密钥元数据目录不能使用符号链接')
            for p in folder.glob('key_*_*.statistic'):
                match=re.fullmatch(r'key_([0-9]+)_.*\.statistic',p.name)
                if match and not p.is_symlink():codes.add(int(match[1]))
        for code in sorted(codes):
            for own in dict.fromkeys((self.owner,self.root.name)):
                yield hashlib.md5((str(code)+own).encode()).hexdigest()[:16].encode(),code&255

    def decode(self, raw):
        if raw[:6] in (DAT1,DAT2):
            a,x,n=dat_parts(raw)
            candidates=list(self.candidates()) if raw[:6]==DAT2 else [(b'cfcd208495d565ef',v) for v in range(256) if x] or [(b'cfcd208495d565ef',0)]
            matches={}
            for key,xor in candidates:
                try:
                    body=decode_dat(raw,key,xor);kind=image_format(body)
                    if raw[:6]==DAT1 and x and kind in ('wxgf','heif'):
                        continue  # No complete checksum can disambiguate the V1 tail.
                    matches[body]=(key,xor)
                except ValueError:continue
            if len(matches)!=1:raise ValueError('图片DAT缺少唯一可验证密钥或格式未适配')
            body=next(iter(matches))
            return body, {'format':'dat-v1' if raw[:6]==DAT1 else 'dat-v2','header':raw[:15].hex()}
        try:
            image_format(raw);return raw,{'format':'plain'}
        except ValueError:pass
        variants={}
        for magic in (0xff,0x89,0x47,0x52):
            xor=raw[0]^magic if raw else 0
            body=bytes(b^xor for b in raw)
            try:image_format(body);variants[body]=xor
            except ValueError:continue
        if len(variants)!=1:raise ValueError('旧XOR图片格式未适配')
        body=next(iter(variants));return body,{'format':'xor','xor':variants[body]}

    def verified_key(self):
        if self._verified:return self._verified
        keys=list(self.candidates())
        if not keys:raise ValueError('目标账号缺少图片DAT V2密钥元数据')
        count=0
        for p in sorted((self.root/'msg/attach').glob('*/*/Img/*.dat')):
            if count>=32:break
            relative=p.relative_to(self.root).as_posix()
            count+=1
            if p.stat().st_size==0 or p.stat().st_size>MAX_FILE:continue
            raw,h=read_file(self.root,relative)
            if raw[:6]!=DAT2 or dat_parts(raw)[0]==0:continue
            for key,xor in keys:
                try:image_format(decode_dat(raw,key,xor))
                except ValueError:continue
                self.dependencies[relative]=h;self._verified=(key,xor);return self._verified
        raise ValueError('目标账号没有可核验的DAT V2图片样本，不能猜测重加密密钥')

    def verified_v1(self):
        if self._v1:return self._v1
        count=0
        for p in sorted((self.root/'msg/attach').glob('*/*/Img/*.dat')):
            if count>=32:break
            relative=p.relative_to(self.root).as_posix();count+=1
            if p.stat().st_size==0 or p.stat().st_size>MAX_FILE:continue
            raw,h=read_file(self.root,relative)
            if raw[:6]!=DAT1 or dat_parts(raw)[1]==0:continue
            matches={}
            for xor in range(256):
                try:
                    body=decode_dat(raw,b'cfcd208495d565ef',xor);kind=image_format(body)
                    if kind not in ('wxgf','heif'):matches[body]=xor
                except ValueError:continue
            if len(matches)==1:
                self.dependencies[relative]=h
                self._v1=(b'cfcd208495d565ef',next(iter(matches.values())))
                return self._v1
        raise ValueError('目标账号没有可核验的DAT V1尾部样本，不能猜测XOR密钥')

    def encode(self, body, encoding):
        image_format(body)
        kind=encoding.get('format')
        if kind=='plain':return body
        if kind=='xor':
            xor=encoding.get('xor')
            if type(xor) is not int or not 0<=xor<=255:raise ValueError('XOR描述无效')
            return bytes(b^xor for b in body)
        if kind not in ('dat-v1','dat-v2'):raise ValueError('图片加密描述未适配')
        try:header=bytes.fromhex(encoding['header'])
        except (ValueError,TypeError):raise ValueError('DAT头无效') from None
        if len(header)!=15 or header[:6]!=(DAT1 if kind=='dat-v1' else DAT2):raise ValueError('DAT头版本冲突')
        if kind=='dat-v2':key,xor=self.verified_key()
        else:
            key,xor=self.verified_v1() if struct.unpack('<II',header[6:14])[1] else (b'cfcd208495d565ef',0)
        raw=encode_dat(body,header,key,xor)
        if decode_dat(raw,key,xor)!=body:raise ValueError('图片重新加密读回失败')
        return raw


def month(row):
    return datetime.fromtimestamp(row['create_time'],ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')


def destination(kind, peer, mon, name):
    filename(name)
    if kind==3:
        base='msg/attach/'+hashlib.md5(peer.encode()).hexdigest()+'/'+mon+'/Img/'
        if not re.fullmatch(r'[0-9a-fA-F]{32}(?:_[a-zA-Z0-9]+)?\.(?:dat|png|jpe?g|gif|webp|heic|heif)',name):
            raise ValueError('图片缓存文件名未适配')
    else:base=('msg/video/' if kind==43 else 'msg/file/')+mon+'/'
    return validate_path(base+name)


def xml_file(row):
    value=row['message_content']
    raw=value.encode() if isinstance(value,str) else value
    if not isinstance(raw,bytes):raise ValueError('附件消息没有可读取的XML')
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        from compression import zstd
        decoder=zstd.ZstdDecompressor()
        raw=decoder.decompress(raw,max_length=10*1024*1024+1)
        if not decoder.eof or decoder.unused_data:raise ValueError('附件压缩XML过大或帧结构未适配')
    if len(raw)>10*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('附件XML过大或含不允许的实体')
    try:doc=ET.fromstring(raw[raw.index(b'<'):])
    except (ValueError,ET.ParseError):raise ValueError('附件XML结构未适配') from None
    if doc.findtext('.//appmsg/type')!='6':raise ValueError('不是受支持的文件附件')
    name=filename(doc.findtext('.//appmsg/title') or '')
    try:size=int(doc.findtext('.//appattach/totallen') or '0')
    except ValueError:raise ValueError('文件附件长度无效') from None
    md5=(doc.findtext('.//appattach/md5') or doc.findtext('.//md5') or '').lower()
    if not 0<size<=MAX_FILE or not re.fullmatch('[0-9a-f]{32}',md5):
        raise ValueError('文件附件缺少大小或MD5，不能只凭名字猜测')
    return name,size,md5


def resources_for(root, peer, message):
    from .workflow import connect
    row=message['fields'];resources=[]
    with connect(Path(root)/'message/message_resource.db') as c:
        rows=c.execute('SELECT i.*,ch.user_name AS chat_user,s.user_name AS sender_user FROM MessageResourceInfo i LEFT JOIN ChatName2Id ch ON ch.rowid=i.chat_id LEFT JOIN SenderName2Id s ON s.rowid=i.sender_id WHERE i.message_svr_id=?',(row['server_id'],)).fetchall()
        for raw in rows:
            info=dict(raw)
            if info.pop('chat_user')!=peer or info.pop('sender_user')!=message['sender']:
                continue
            if info['message_local_id']!=row['local_id'] or info['message_create_time']!=row['create_time'] or info['message_local_type']!=row['local_type']:
                raise ValueError('附件资源本地关联冲突')
            resources.append({'info':info,'details':[dict(d) for d in c.execute('SELECT * FROM MessageResourceDetail WHERE message_id=? ORDER BY resource_id',(info['message_id'],))]})
    if len(resources)>1:raise ValueError('附件资源多行，不能猜测选择')
    return resources


def locate(root, peer, message, resources):
    row=message['fields'];kind=row['local_type'];mon=month(row)
    if kind not in (3,43):
        name,size,md5=xml_file(row);relative=destination(kind,peer,mon,name)
        p=path_at(root,relative)
        if not p.is_file():return []
        raw,h=read_file(root,relative)
        if len(raw)!=size or hashlib.md5(raw).hexdigest()!=md5:raise ValueError('文件附件内容MD5或大小不符')
        return [(relative,raw,h,None)]
    if len(resources)!=1:raise ValueError('图片/视频缺少唯一会话资源关联')
    resource=resources[0];hashes=re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])',resource['info']['packed_info'] or b'')
    if len(set(hashes))!=1:raise ValueError('图片/视频资源名尚未适配')
    token=hashes[0].decode();folder=Path(root)/str(PurePosixPath(destination(kind,peer,mon,token+('.dat' if kind==3 else '.mp4'))).parent)
    if folder.is_symlink():raise ValueError('附件目录是符号链接')
    found={}
    for detail in resource['details']:
        if detail['data_index']!='0':raise ValueError('附件data_index尚未适配')
        if detail['status']!=1 or not detail['size']:continue
        matches=[]
        for p in sorted(folder.glob(token+'*')):
            if p.is_file() and p.stat().st_size==detail['size']:
                relative=p.relative_to(root).as_posix();destination(kind,peer,mon,p.name)
                raw,h=read_file(root,relative);matches.append((relative,raw,h,detail['type']))
        if len(matches)>1:raise ValueError('附件资源大小匹配多义，不能猜测文件')
        if not matches:return []  # Declared downloaded resources must actually exist.
        found[matches[0][0]]=matches[0]
    return list(found.values())


def primary(kind, files):
    if kind==3:return any(not PurePosixPath(f[0]).stem.endswith('_t') for f in files)
    if kind==43:return any(PurePosixPath(f[0]).suffix.lower()=='.mp4' for f in files)
    return bool(files)


def collect(plain, asset_root, owner, peer, messages, output, allow_missing=False):
    from .workflow import digest
    if Path(output).resolve().is_relative_to(Path(asset_root).resolve()):
        raise ValueError('附件bundle必须位于微信账号目录之外')
    context=Images(asset_root,owner);entries=[];originals={};total=0
    for message in messages:
        row=message['fields'];kind=row['local_type']
        if kind not in TYPES:continue
        if row['status']!=3 or row['upload_status']!=0:raise ValueError('附件消息状态尚未适配')
        resources=resources_for(plain,peer,message)
        files=locate(Path(asset_root),peer,message,resources)
        if not primary(kind,files):
            if not allow_missing:raise ValueError('图片原内容、视频或文件附件未完整下载；不能只恢复缩略图')
            files=[]
        entry={'server_id':row['server_id'],'kind':kind,'month':month(row),'files':[]}
        for relative,raw,h,resource_type in files:
            body,encoding=context.decode(raw) if kind==3 else (raw,{'format':'plain'})
            if kind==43 and relative.endswith('.mp4') and (len(raw)<16 or raw[4:8]!=b'ftyp'):
                raise ValueError('视频容器未适配')
            total+=len(body)
            if total>MAX_TOTAL:raise ValueError('附件bundle超过2GiB限制')
            name='attachments/'+str(sum(len(e['files']) for e in entries)+len(entry['files'])+1).zfill(6)+'.bin'
            p=Path(output)/name;p.parent.mkdir(mode=0o700,exist_ok=True)
            with p.open('xb') as f:f.write(body)
            p.chmod(0o600)
            entry['files'].append({'name':PurePosixPath(relative).name,'resource_type':resource_type,
                'encoding':encoding,'size':len(body),'stored_size':len(raw),'sha256':digest(p),'payload':name})
            originals[relative]=h
        entries.append(entry)
    if not check_state(asset_root,originals):raise ValueError('打包期间源附件改变')
    return entries,originals


def read_entries(bundle,data):
    rows={m['fields']['server_id']:m['fields'] for m in data['messages'] if m['fields']['local_type'] in TYPES}
    found={};used=set();total=0
    for entry in data.get('attachments',[]):
        sid=entry['server_id']
        if sid in found or sid not in rows or entry['kind']!=rows[sid]['local_type'] or entry['month']!=month(rows[sid]):
            raise ValueError('附件清单与消息不一一对应')
        if rows[sid]['status']!=3 or rows[sid]['upload_status']!=0:raise ValueError('附件消息状态尚未适配')
        files=[];names=set()
        for item in entry['files']:
            name=filename(item['name']);destination(entry['kind'],data['peer'],entry['month'],name)
            payload=item['payload'];size=item['size']
            if (not isinstance(payload,str) or not re.fullmatch(r'attachments/[0-9]{6}\.bin',payload)
                    or payload in used or name in names or type(size) is not int or not 0<size<=MAX_FILE
                    or type(item['stored_size']) is not int or not 0<item['stored_size']<=MAX_FILE):
                raise ValueError('附件清单路径、大小或重复条目无效')
            used.add(payload);names.add(name);total+=size
            p=Path(bundle)/payload
            if any(x.is_symlink() for x in (Path(bundle),p.parent,p)) or total>MAX_TOTAL or p.stat().st_size!=size:
                raise ValueError('附件文件大小、符号链接或总体积无效')
            with p.open('rb') as f:body=f.read(MAX_FILE+1)
            if len(body)!=size or hashlib.sha256(body).hexdigest()!=item['sha256']:
                raise ValueError('附件文件哈希校验失败')
            if entry['kind']==3:image_format(body)
            elif item['encoding']!={'format':'plain'}:raise ValueError('视频/文件不允许图片加密描述')
            if entry['kind']==43 and name.endswith('.mp4') and (len(body)<16 or body[4:8]!=b'ftyp'):
                raise ValueError('视频容器未适配')
            if entry['kind'] not in (3,43):
                title,length,md5=xml_file(rows[sid])
                if name!=title or size!=length or hashlib.md5(body).hexdigest()!=md5:
                    raise ValueError('文件附件与消息MD5/大小不符')
            else:
                resources=[r for r in data['resources'] if r['info']['message_svr_id']==sid]
                if len(resources)!=1:raise ValueError('附件清单缺少唯一资源')
                hashes=re.findall(rb'(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])',resources[0]['info']['packed_info'] or b'')
                if len(set(hashes))!=1 or not name.startswith(hashes[0].decode()):raise ValueError('附件缓存文件名与资源令牌不符')
                details=[d for d in resources[0]['details'] if d['type']==item['resource_type'] and d['size']==item['stored_size'] and d['status']==1 and d['data_index']=='0']
                if len(details)!=1:raise ValueError('附件清单尺寸或资源关联不符')
            files.append((item,body))
        if files and not primary(entry['kind'],[(i['name'],) for i,b in files]):raise ValueError('附件清单只有缩略图')
        found[sid]=(entry,files)
    if set(found)!=set(rows):raise ValueError('附件清单与消息不一一对应')
    return found


def prepare(bundle,data,target_plain,target_assets,target_owner,output):
    from .workflow import connect,table_for,digest
    if Path(output).resolve().is_relative_to(Path(target_assets).resolve()):
        raise ValueError('附件候选必须位于微信账号目录之外')
    entries=read_entries(bundle,data);context=Images(target_assets,target_owner)
    watched={};candidates={};counts={'image_added':0,'image_repaired':0,'video_added':0,'video_repaired':0,'file_added':0,'file_repaired':0,'attachments_skipped':0}
    for message in data['messages']:
        row=message['fields'];sid=row['server_id'];kind=row['local_type']
        if sid not in entries:continue
        entry,files=entries[sid];peer=data['owner']
        existing=None
        with connect(Path(target_plain)/'message/message_0.db') as c:
            table=table_for(peer)
            if c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                existing=c.execute('SELECT * FROM "'+table+'" WHERE server_id=?',(sid,)).fetchone()
        if existing and (existing['status']!=3 or existing['upload_status']!=0):raise ValueError('目标附件消息状态尚未适配')
        if not files:
            if not existing:raise ValueError('源附件缺失且目标没有完整对应消息')
            target_message={'sender':message['sender'],'fields':dict(existing)}
            target_resources=resources_for(target_plain,peer,target_message)
            local=locate(Path(target_assets),peer,target_message,target_resources)
            if not primary(kind,local):raise ValueError('双方附件原内容均缺失，不能生成完整计划')
            for rel,raw,h,t in local:
                if kind==3:context.decode(raw)
                watched[rel]=h
            counts['attachments_skipped']+=1;continue
        changed=False
        for item,body in files:
            rel=destination(kind,peer,entry['month'],item['name']);path=path_at(target_assets,rel)
            before=file_hash(target_assets,rel);watched[rel]=before
            if path.exists() and path.stat().st_size:
                raw,h=read_file(target_assets,rel)
                prior=context.decode(raw)[0] if kind==3 else raw
                if prior!=body:raise ValueError('attachment content collision：目标已有不同非空文件')
                continue
            raw=context.encode(body,item['encoding']) if kind==3 else body
            if len(raw)!=item['stored_size']:raise ValueError('附件重加密尺寸与资源关联不一致')
            if kind not in (3,43):
                name,size,md5=xml_file(row)
                if name!=item['name'] or size!=len(raw) or hashlib.md5(raw).hexdigest()!=md5:
                    raise ValueError('文件附件清单与原消息MD5不符')
            p=Path(output)/'attachment-candidates'/rel;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if rel in candidates:
                if digest(p)!=hashlib.sha256(raw).hexdigest():raise ValueError('attachment filename collision：同一路径对应不同内容')
            else:
                with p.open('xb') as f:f.write(raw)
                p.chmod(0o600)
                candidates[rel]=digest(p)
            changed=True
        key='image' if kind==3 else 'video' if kind==43 else 'file'
        if changed:counts[key+('_repaired' if existing else '_added')]+=1
        else:counts['attachments_skipped']+=1
    watched.update(context.dependencies)
    if not check_state(target_assets,watched):raise ValueError('准备期间目标附件改变')
    return {'originals':watched,'candidates':candidates},counts


def resolve_root(plain, explicit=None):
    report=Path(plain)/'export-report.json'
    value=None
    if explicit is None:
        value=json.loads(report.read_text())
        explicit=value.get('attachment_root')
    if not explicit or not Path(explicit).is_absolute():raise ValueError('缺少已绑定的账号附件根目录，请重新以附件模式导出')
    root=Path(explicit).resolve(strict=True)
    if value is not None and root.name!=value.get('account_directory'):
        raise ValueError('附件根目录与导出账号绑定不一致')
    return root


def validate_plan(plan):
    asset=plan.get('assets')
    if not isinstance(asset,dict) or set(asset)!={'originals','candidates'}:
        raise ValueError('附件计划结构无效')
    if not set(asset['candidates'])<=set(asset['originals']):raise ValueError('附件候选缺少原件状态')
    peer=plan.get('report',{}).get('attachment_peer')
    if not isinstance(peer,str) or not peer or '@' in peer or peer==plan.get('target_owner'):
        raise ValueError('附件计划缺少明确的单聊身份')
    for rel in asset['candidates']:
        parts=PurePosixPath(rel).parts
        if parts[:2]==('msg','attach') and (len(parts)!=6 or parts[2]!=hashlib.md5(peer.encode()).hexdigest()):
            raise ValueError('图片候选目录与目标会话不一致')
    for mapping in (asset['originals'],asset['candidates']):
        for rel,h in mapping.items():
            validate_path(rel)
            if h is not None and (not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h)):
                raise ValueError('附件计划哈希无效')
    if any(h is None for h in asset['candidates'].values()):raise ValueError('附件候选哈希缺失')
    return asset


def preflight(plan,directory):
    from .workflow import digest
    asset=validate_plan(plan);root=Path(plan['target_root']).parent
    if not check_state(root,asset['originals']):raise ValueError('附件在预览后改变，拒绝应用')
    for rel,h in asset['candidates'].items():
        if digest(path_at(Path(directory)/'attachment-candidates',rel))!=h:
            raise ValueError('附件候选改变，拒绝应用')
    return asset


def merge_resource(c, resource, peer, sender, local_id):
    """Preserve existing IDs/other caches; repair only matched download entries."""
    from .workflow import _insert
    info=resource['info'];sid=info['message_svr_id']
    rows=c.execute('SELECT r.* FROM MessageResourceInfo r JOIN ChatName2Id ch ON ch.rowid=r.chat_id WHERE ch.user_name=? AND r.message_svr_id=?',(peer,sid)).fetchall()
    if not rows:return None
    changes=0
    if len(rows)!=1:raise ValueError('attachment resource identity collision')
    old=dict(rows[0]);expected=dict(info)
    expected.update(message_id=old['message_id'],chat_id=old['chat_id'],sender_id=old['sender_id'],message_local_id=local_id)
    actor=c.execute('SELECT user_name FROM SenderName2Id WHERE rowid=?',(old['sender_id'],)).fetchone()
    if old!=expected or not actor or actor[0]!=sender:raise ValueError('attachment resource metadata collision')
    target={}
    for d in c.execute('SELECT * FROM MessageResourceDetail WHERE message_id=?',(old['message_id'],)):
        d=dict(d);key=(d['type'],d['data_index'])
        if key in target:raise ValueError('attachment resource detail collision')
        target[key]=d
    seen=set()
    for source in resource['details']:
        key=(source['type'],source['data_index'])
        if key in seen:raise ValueError('attachment resource detail collision')
        seen.add(key);prior=target.get(key)
        if prior:
            if prior['packed_info']!=source['packed_info']:raise ValueError('attachment resource detail metadata collision')
            if source['status']==1 and source['size']>0 and (prior['status']!=1 or prior['size']!=source['size']):
                if prior['status']==1 and prior['size'] not in (0,source['size']):raise ValueError('attachment resource size collision')
                c.execute('UPDATE MessageResourceDetail SET size=?,status=1 WHERE resource_id=?',(source['size'],prior['resource_id']));changes+=1
        else:
            d=dict(source);d.pop('resource_id');d['message_id']=old['message_id'];_insert(c,'MessageResourceDetail',d);changes+=1
    return changes


class Transaction:
    def __init__(self,plan,directory):
        self.plan=plan;self.directory=Path(directory);self.root=Path(plan['target_root']).parent
        self.asset=preflight(plan,directory);self.stages={};self.replaced=[];self.created=[]

    def stage(self):
        import shutil
        from .workflow import digest
        from .operations import sync_directory
        for rel,h in self.asset['originals'].items():
            if h is not None:
                dest=path_at(self.directory/'original-assets',rel);dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                shutil.copy2(path_at(self.root,rel),dest);dest.chmod(0o600)
                with dest.open('rb') as f:os.fsync(f.fileno())
                sync_directory(dest.parent)
                if digest(dest)!=h:raise ValueError('附件原件备份改变')
        for rel,h in self.asset['candidates'].items():
            live=path_at(self.root,rel);missing=[];parent=live.parent
            while not parent.exists():missing.append(parent);parent=parent.parent
            for folder in reversed(missing):
                folder.mkdir(mode=0o700);self.created.append(folder);sync_directory(folder.parent)
            live=path_at(self.root,rel)
            stage=live.parent/('.chat-rescue-stage-'+hashlib.sha256(rel.encode()).hexdigest()[:24])
            with stage.open('xb') as f, path_at(self.directory/'attachment-candidates',rel).open('rb') as src:
                self.stages[rel]=stage;shutil.copyfileobj(src,f);f.flush();os.fsync(f.fileno())
            stage.chmod(0o600)
            if digest(stage)!=h:raise ValueError('附件暂存哈希不符')
        self.check()

    def check(self):
        if not check_state(self.root,self.asset['originals']):raise ValueError('附件在应用前改变')

    def apply(self):
        from .operations import journal,sync_directory
        for rel,stage in self.stages.items():
            self.check_one(rel)
            journal(self.directory,{'state':'replace_intent','attachment':rel})
            self.replaced.append(rel);os.replace(stage,path_at(self.root,rel))
            sync_directory(path_at(self.root,rel).parent)
            journal(self.directory,{'state':'replace_done','attachment':rel})

    def check_one(self,rel):
        if file_hash(self.root,rel)!=self.asset['originals'][rel]:raise ValueError('附件在写入前改变')

    def rollback(self):
        import shutil
        from .operations import sync_directory
        errors=[]
        for rel in reversed(self.replaced):
            try:
                live=path_at(self.root,rel);backup=path_at(self.directory/'original-assets',rel)
                if self.asset['originals'][rel] is None:live.unlink(missing_ok=True)
                else:
                    temp=live.parent/('.chat-rescue-repair-'+hashlib.sha256(rel.encode()).hexdigest()[:24])
                    shutil.copy2(backup,temp);os.replace(temp,live)
                sync_directory(live.parent)
            except (OSError,ValueError):errors.append('account/'+rel)
        return errors

    def cleanup(self):
        for stage in self.stages.values():stage.unlink(missing_ok=True)
        for folder in reversed(self.created):
            try:folder.rmdir()
            except OSError:pass


def sync_preflight(manifest, output):
    if manifest.get('version')!=3:return
    if set(manifest.get('original_assets',{}))!={'A','B'}:raise ValueError('双向附件快照缺失')
    for slot,account in manifest['accounts'].items():
        expected=manifest['original_assets'][slot]
        for rel in expected:validate_path(rel)
        if not check_state(Path(account['root']).parent,expected):raise ValueError('账号'+slot+'附件在预览后改变；双方均未应用')
        if manifest['directions'][slot]['plan']:
            directory=Path(output)/(slot+'-plan')
            plan=json.loads((directory/'plan.json').read_text())
            preflight(plan,directory)
            if any(expected.get(rel,'missing')!=h for rel,h in plan['assets']['originals'].items()):
                raise ValueError('单向附件与双向快照不一致')


class UndoTransaction:
    def __init__(self,output,manifest,result,backup):
        self.output=Path(output);self.manifest=manifest;self.result=result;self.backup=Path(backup)
        self.tasks=[];self.stages={};self.changed=[]
        for slot,account in manifest['accounts'].items():
            expected=manifest['original_assets'][slot];after=result['accounts'][slot].get('assets_after')
            if not isinstance(after,dict) or set(after)!=set(expected):raise ValueError('撤回附件回执不完整')
            root=Path(account['root']).parent
            for rel in after:validate_path(rel)
            if not check_state(root,after):raise ValueError('附件已产生新变化，拒绝撤回以保护文件')
            if result['accounts'][slot]['state']!='applied':continue
            directory=self.output/(slot+'-plan');plan=json.loads((directory/'plan.json').read_text())
            asset=validate_plan(plan)
            if plan['target_root']!=account['root'] or plan['target_owner']!=account['owner']:
                raise ValueError('撤回附件账号绑定不一致')
            for rel in asset['candidates']:
                if asset['originals'][rel]!=expected.get(rel,'missing'):raise ValueError('撤回附件原件状态不一致')
                h=asset['originals'][rel]
                if h is not None and file_hash(directory/'original-assets',rel)!=h:
                    raise ValueError('撤回附件原件改变')
                self.tasks.append((slot,root,rel,h,directory))

    def stage(self):
        import shutil
        from .workflow import digest
        for slot,root,rel,h,directory in self.tasks:
            live=path_at(root,rel);backup=path_at(self.backup/slot/'_assets',rel)
            backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copy2(live,backup);backup.chmod(0o600)
            with backup.open('rb') as f:os.fsync(f.fileno())
            from .operations import sync_directory
            sync_directory(backup.parent)
            if digest(backup)!=self.result['accounts'][slot]['assets_after'][rel]:raise ValueError('撤回前附件备份改变')
            if h is not None:
                stage=live.parent/('.chat-rescue-undo-'+hashlib.sha256(rel.encode()).hexdigest()[:24])
                with stage.open('xb') as f, path_at(directory/'original-assets',rel).open('rb') as src:
                    self.stages[(slot,rel)]=stage;shutil.copyfileobj(src,f);f.flush();os.fsync(f.fileno())
                stage.chmod(0o600)
                if digest(stage)!=h:raise ValueError('撤回附件暂存校验失败')

    def check(self):
        for slot,account in self.manifest['accounts'].items():
            if not check_state(Path(account['root']).parent,self.result['accounts'][slot]['assets_after']):
                raise ValueError('撤回前附件发生变化')

    def apply(self):
        from .operations import journal,sync_directory
        for slot,root,rel,h,directory in self.tasks:
            live=path_at(root,rel);journal(self.backup,{'state':'undo_intent','slot':slot,'attachment':rel})
            self.changed.append((slot,root,rel))
            if h is None:live.unlink(missing_ok=True)
            else:os.replace(self.stages[(slot,rel)],live)
            sync_directory(live.parent);journal(self.backup,{'state':'undo_done','slot':slot,'attachment':rel})

    def rollback(self):
        import shutil
        from .operations import sync_directory
        errors=[]
        for slot,root,rel in reversed(self.changed):
            try:
                live=path_at(root,rel);stage=live.parent/('.chat-rescue-undo-repair-'+hashlib.sha256(rel.encode()).hexdigest()[:24])
                shutil.copy2(path_at(self.backup/slot/'_assets',rel),stage);os.replace(stage,live);sync_directory(live.parent)
            except (OSError,ValueError):errors.append(slot+'/account/'+rel)
        return errors

    def cleanup(self):
        for stage in self.stages.values():stage.unlink(missing_ok=True)
