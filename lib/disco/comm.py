import httplib, os, time, struct
from cStringIO import StringIO

from disco.error import CommError
from disco.settings import DiscoSettings
from disco.util import iterify, urlresolve, urlsplit

BUFFER_SIZE = int(1024**2)
CHUNK_SIZE = int(10 * 1024**2)

settings = DiscoSettings()
nocurl = 'nocurl' in settings['DISCO_FLAGS'].lower().split()

try:
    import pycurl
except ImportError:
    nocurl = True

if nocurl:
    import httplib as commlib
else:
    from disco import comm_pycurl as commlib

# get rid of this for python2.6+
try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        import cjson
        class Dummy(object):
            pass
        json = Dummy()
        json.loads = cjson.decode
        json.dumps = cjson.encode

def range_header(offset):
    def httprange(start='', end=''):
        return '%s-%s' % (start, end)
    if offset:
        return {'Range': 'bytes=%s' % httprange(*tuple(iterify(offset)))}
    return {}

def request(method, url, data=None, headers={}, sleep=0):
    scheme, netloc, path = urlsplit(urlresolve(url))

    try:
        conn = commlib.HTTPConnection(str(netloc))
        conn.request(method, '/%s' % path, body=data, headers=headers)
        response = conn.getresponse()
    except (httplib.HTTPException, httplib.socket.error), e:
        raise CommError("Request failed: %s" % e, url)

    if response.status == httplib.SERVICE_UNAVAILABLE:
        if sleep == 9:
            raise CommError("Service unavailable", url)
        time.sleep(2**sleep)
        return request(method, url, data=data, headers=headers, sleep=sleep + 1)
    elif not str(response.status).startswith('2'):
        raise CommError(response.read(), url, response.status)
    return response

def download(url, method='GET', data=None, offset=()):
    return request(method if data is None else 'POST',
                   url,
                   data=data,
                   headers=range_header(offset)).read()

def upload(urls, sources, **kwargs):
    urls, sources = iterify(urls), iterify(sources)
    if nocurl:
        return [request('PUT', url, data=source.makefile().read()).read()
                for url, source in zip(urls, sources)]
    return list(commlib.upload(urls, sources, **kwargs))

def open_local(path, url):
    fd = open(path, 'r', BUFFER_SIZE)
    size = os.stat(path).st_size
    return fd, size, 'file://%s' % path

def open_remote(url):
    conn = Connection(urlresolve(url))
    return conn, len(conn), conn.url

class Connection(object):
    def __init__(self, url):
        self.url = url
        self.buf = None
        self.offset = 0
        self.orig_offset = 0
        self.eof = False
        self.headers = {}
        self.read(1)
        self.i = 0

    def __iter__(self):
        pass

    def __len__(self):
        if 'content-range' in self.headers:
            return int(self.headers['content-range'].split('/')[1])
        return int(self.headers.get('content-length', 0))

    def close(self):
        pass

    def read(self, size=-1):
        buf = StringIO()
        while size:
            bytes = self._read_chunk(size if size > 0 else CHUNK_SIZE)
            if not bytes:
                break
            size -= len(bytes)
            buf.write(bytes)
        return buf.getvalue()

    def _read_chunk(self, n):
        if self.buf is None or self.i >= len(self.buf):
            if self.eof:
                return ''
            self.i = 0
            if len(self):
                end = min(len(self), self.offset + CHUNK_SIZE) - 1
            else:
                end = self.offset + CHUNK_SIZE - 1
            response = request('GET',
                               self.url,
                               headers=range_header((self.offset, end)))
            self.buf = response.read()
            self.headers = dict(response.getheaders())
            self.orig_offset = self.offset
            self.offset += len(self.buf)
            if len(self) and self.offset >= len(self):
                self.eof = True
            elif self.buf == '':
                self.eof = True
        ret = self.buf[self.i:self.i + n]
        self.i += len(ret)
        return ret

    def tell(self):
        return self.orig_offset + self.i

    def seek(self, pos, mode=0):
        if mode == 0:
            self.offset = pos
        elif mode == 1:
            self.offset = self.tell() + pos
        else:
            self.offset = len(self) - pos
        self.eof = False
        self.buf = None
        self.orig_offset = self.offset
        self.i = 0
