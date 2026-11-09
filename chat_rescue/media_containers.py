"""Explicit container checks, not codec decoding or WeChat playback evidence."""
import struct

VIDEO_SUFFIXES = ('.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.3gp', '.3g2')


class UnsupportedMediaFormat(ValueError):
    """Unknown container; distinct from malformed data or missing evidence."""


class MediaEvidenceError(ValueError):
    """Media could not be verified; no bytes may be restored as playable."""
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason=reason


def tiff_check(raw):
    if len(raw)<8 or raw[:4] not in (b'II*\0',b'MM\0*'):
        raise ValueError('TIFF 头截断或布局未适配')
    endian='<' if raw[:2]==b'II' else '>'
    offset=struct.unpack_from(endian+'I',raw,4)[0];seen=set()
    while offset:
        if offset in seen or len(seen)>=128 or offset<8 or offset+2>len(raw):
            raise ValueError('TIFF IFD 越界或循环')
        seen.add(offset);count=struct.unpack_from(endian+'H',raw,offset)[0]
        end=offset+2+12*count
        if not count or count>4096 or end+4>len(raw):raise ValueError('TIFF IFD 截断')
        tags={}
        widths={1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8}
        for pos in range(offset+2,end,12):
            tag,kind,n,value=struct.unpack_from(endian+'HHII',raw,pos)
            if tag in tags or kind not in widths or n<1:raise UnsupportedMediaFormat('TIFF 字段布局尚未适配')
            size=widths[kind]*n
            start=pos+8 if size<=4 else value
            if start<0 or start+size>len(raw):raise ValueError('TIFF 字段载荷截断')
            if tag in (256,257,273,279,324,325):
                if kind not in (3,4) or n>65536:raise UnsupportedMediaFormat('TIFF 图像字段尚未适配')
                tags[tag]=struct.unpack_from(endian+str(n)+('H' if kind==3 else 'I'),raw,start)
            else:tags[tag]=None
        if not tags.get(256) or not tags.get(257) or not tags[256][0] or not tags[257][0]:
            raise ValueError('TIFF 缺少图像尺寸')
        positions,sizes=(tags.get(273),tags.get(279)) if 273 in tags else (tags.get(324),tags.get(325))
        if not positions or not sizes or len(positions)!=len(sizes):raise UnsupportedMediaFormat('TIFF 像素位置尚未适配')
        if any(p<8 or n<1 or p+n>len(raw) for p,n in zip(positions,sizes)):
            raise ValueError('TIFF 像素载荷截断')
        offset=struct.unpack_from(endian+'I',raw,end)[0]
    if not seen:raise ValueError('TIFF 没有图像目录')


def jxl_check(raw):
    signature=b'\0\0\0\x0cJXL \x0d\x0a\x87\x0a'
    if not raw.startswith(signature):raise ValueError('JPEG XL 容器头无效')
    pos, codestream = 12, False
    while pos+8<=len(raw):
        size,tag=struct.unpack_from('>I4s',raw,pos);header=8
        if size==1:
            if pos+16>len(raw):raise ValueError('JPEG XL 扩展头截断')
            size,header=struct.unpack_from('>Q',raw,pos+8)[0],16
        elif size==0:size=len(raw)-pos
        if size<header or pos+size>len(raw):raise ValueError('JPEG XL 容器块截断')
        if tag==b'jxlc' and size>header:codestream=True
        pos+=size
    if pos!=len(raw) or not codestream:raise ValueError('JPEG XL 缺少完整码流块')


def riff_chunks(raw, kind):
    if (len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != kind
            or struct.unpack_from('<I', raw, 4)[0] != len(raw) - 8):
        raise ValueError('RIFF 容器长度或类型不符')
    pos, chunks = 12, []
    while pos + 8 <= len(raw):
        tag, size = raw[pos:pos+4], struct.unpack_from('<I', raw, pos+4)[0]
        end = pos + 8 + size
        if end + (size & 1) > len(raw):
            raise ValueError('RIFF 块截断')
        chunks.append((tag, raw[pos+8:end]))
        pos = end + (size & 1)
    if pos != len(raw):
        raise ValueError('RIFF 尾部截断')
    return chunks


def audio_format(raw):
    for header in (b'\x02#!SILK_V3', b'#!SILK_V3'):
        if raw.startswith(header) and len(raw) > len(header):
            return 'silk'
    if raw.startswith(b'RIFF'):
        chunks = riff_chunks(raw, b'WAVE')
        formats = [data for tag, data in chunks if tag == b'fmt ']
        payloads = [data for tag, data in chunks if tag == b'data']
        if len(formats) != 1 or len(payloads) != 1 or len(formats[0]) not in (16, 18):
            raise ValueError('WAV 缺少唯一格式或音频块')
        codec, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', formats[0])
        if codec != 1 or channels not in (1,2) or bits not in (8,16,24,32):
            raise UnsupportedMediaFormat('WAV 编码或声道/位深布局尚未适配')
        if (not 1 <= rate <= 192000 or align != channels * bits // 8
                or byte_rate != rate * align or not payloads[0] or len(payloads[0]) % align
                or (len(formats[0]) == 18 and formats[0][16:] != b'\0\0')):
            raise ValueError('WAV 仅适配明确的完整 PCM 帧')
        return 'wav'
    if raw.startswith(b'#!AMR\n'):
        # RFC 4867 storage format, AMR-NB speech / SID frames only.
        bits = (95, 103, 118, 134, 148, 159, 204, 244, 39)
        pos, frames = 6, 0
        while pos < len(raw):
            header = raw[pos]
            mode = (header >> 3) & 15
            if header & 0x83 or mode >= len(bits):
                raise ValueError('AMR 帧头或模式未适配')
            size = (bits[mode] + 7) // 8
            end = pos + 1 + size
            if end > len(raw) or (bits[mode] % 8 and raw[end-1] & ((1 << (8-bits[mode] % 8))-1)):
                raise ValueError('AMR 帧截断或填充位错误')
            pos, frames = end, frames + 1
        if frames:
            return 'amr'
    raise UnsupportedMediaFormat('语音容器未适配：支持 SILK v3、PCM WAV、AMR-NB；不转码')


def video_format(raw):
    if raw.startswith(b'\x1a\x45\xdf\xa3'):
        def elements(start, end):
            values=[]
            while start < end:
                first=raw[start]
                if not first: raise ValueError('EBML 元素头无效')
                width=8-first.bit_length()+1
                if width>4 or start+width>=end: raise ValueError('EBML 元素编号截断')
                tag=int.from_bytes(raw[start:start+width], 'big');start+=width
                first=raw[start]
                if not first: raise ValueError('EBML 长度头无效')
                width=8-first.bit_length()+1
                if start+width>end: raise ValueError('EBML 长度截断')
                size=int.from_bytes(raw[start:start+width], 'big') & ((1 << (7*width))-1)
                start+=width
                # Only a Segment may extend to the end with unknown size.
                if size == (1 << (7*width))-1:
                    if tag != 0x18538067: raise UnsupportedMediaFormat('EBML 未知长度元素尚未适配')
                    size=end-start
                if start+size>end: raise ValueError('EBML 元素截断')
                values.append((tag,start,start+size));start+=size
            return values
        top=elements(0,len(raw))
        if len(top)!=2 or top[0][0]!=0x1a45dfa3 or top[1][0]!=0x18538067:
            raise UnsupportedMediaFormat('EBML 顶层布局尚未适配')
        header=elements(top[0][1],top[0][2])
        types=[raw[a:b] for tag,a,b in header if tag==0x4282]
        if types not in ([b'matroska'],[b'webm']): raise UnsupportedMediaFormat('EBML DocType 尚未适配')
        children=elements(top[1][1],top[1][2])
        if (sum(tag==0x1654ae6b and b>a for tag,a,b in children)!=1
                or not any(tag==0x1f43b675 and b>a for tag,a,b in children)):
            raise ValueError('Matroska/WebM 缺少轨道或媒体簇')
        return 'webm' if types==[b'webm'] else 'mkv'
    if raw.startswith(b'RIFF'):
        chunks = riff_chunks(raw, b'AVI ')
        lists = [data[:4] for tag, data in chunks if tag == b'LIST' and len(data) > 4]
        if lists.count(b'hdrl') == 1 and lists.count(b'movi') == 1:
            return 'avi'
        raise ValueError('AVI 缺少唯一头部或媒体列表')
    if len(raw)<8 or raw[4:8]!=b'ftyp':
        raise UnsupportedMediaFormat('视频容器尚未适配')
    pos, boxes = 0, []
    while pos + 8 <= len(raw):
        size, tag = struct.unpack_from('>I4s', raw, pos)
        header = 8
        if size == 1:
            if pos + 16 > len(raw): raise ValueError('视频扩展块头截断')
            size, header = struct.unpack_from('>Q', raw, pos+8)[0], 16
        elif size == 0:
            size = len(raw) - pos
        if size < header or pos + size > len(raw):
            raise ValueError('视频容器块长度无效')
        boxes.append((tag, raw[pos+header:pos+size]))
        pos += size
    if pos != len(raw) or not boxes or boxes[0][0] != b'ftyp':
        raise ValueError('视频容器未适配或截断')
    ftyp = boxes[0][1]
    if len(ftyp) < 8 or len(ftyp) % 4 or sum(tag == b'ftyp' for tag, _ in boxes) != 1:
        raise ValueError('视频品牌块无效')
    brands = {ftyp[:4], *(ftyp[i:i+4] for i in range(8, len(ftyp), 4))}
    if not any(tag == b'mdat' and data for tag, data in boxes):
        raise ValueError('视频缺少媒体数据块')
    if b'qt  ' in brands: return 'mov'
    if brands.intersection((b'3gp4',b'3gp5',b'3gp6',b'3gp7',b'3gp8')): return '3gp'
    if brands.intersection((b'3g2a',b'3g2b',b'3g2c')): return '3g2'
    if brands.intersection((b'isom', b'iso2', b'iso5', b'iso6', b'mp41', b'mp42', b'avc1', b'M4V ')):
        return 'mp4'
    raise UnsupportedMediaFormat('视频容器品牌未适配')
