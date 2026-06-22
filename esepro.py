#!/usr/bin/python3
# -*- encoding: utf-8 -*-

import socket, re, hashlib, sys

class AddrSpec:

  def __init__(self, buf):
    m = re.match(r'([^:]+):', buf)
    self.scheme, buf = m.group(1), buf[m.end():]
    if self.scheme.lower() == 'sip':           # スキームは大小無視
      m = re.match(r'(([^@]*)@)?([^?;]+)', buf) # userinfo は空でも可('@'をhostに混ぜない)
      self.userinfo, hostport = m.group(2), m.group(3)
      buf = buf[m.end():]
      m = re.match(r'([^:]+)(:(\d+))?', hostport)
      self.host, self.port = m.group(1), m.group(3)
    else:
      self.userinfo = self.host = self.port = None
    m = re.match(r'[^?]*', buf)
    self.uri_prms, self.headers = m.group(0), buf[m.end():]


class NameAddr(AddrSpec):

  def __init__(self, buf):
    # name-addr (表示名 + <addr-spec>) を優先。表示名は quoted-string を先に
    # 試し(内側の '<' '>' を巻き込まない)、無ければ '<' の手前までを引用符なし
    # 表示名として許容する。
    m = re.match(r'\s*("[^"]*"|[^<]*?)\s*<([^>]+)>', buf)
    if m != None:
      self.display_name, self.addr_spec = m.group(1), m.group(2)
      buf = buf[m.end():]
    else:
      self.display_name = ''
      m = re.match(r'[^;]+', buf)
      self.addr_spec, buf = m.group(0), buf[m.end():]
    self.prms = buf
    AddrSpec.__init__(self, self.addr_spec)


class Header:

  def __init__(self, name, vals):
    self.name, self.vals = name, vals

  def __str__(self):
    return self.name + ': ' + ', '.join(self.vals) + "\r\n"


class Message:

  def __init__(self, buf):
    buf = re.sub(r'^((\r\n)|(\r)|(\n))*', "", buf)
    m = re.search(r'((\r\n\r\n)|(\r\r)|(\n\n))', buf)
    self.body = buf[m.end():]
    buf = re.sub(r'\n[ \t]+',' ', re.sub(r'\r\n?', "\n", buf[:m.start()]))
    ary = buf.split("\n")
    m = re.match(r'(([A-Z]+) ([^ ]+) )?SIP\/2\.0( (\d+) ([^\n]+))?', ary[0])
    self.method, self.requri, self.stcode, self.reason = \
      m.group(2), m.group(3), m.group(5), m.group(6)
    self.hdrs = []
    for buf in ary[1:]:
      name, buf = re.split(r'\s*:\s*', buf, 1)
      self.hdrs.append(Header(name, re.split(r'\s*,\s*', buf)))

  def __str__(self):
    if self.method != None:
      s = self.method + ' ' + self.requri + " SIP/2.0\r\n"
    else:
      s = 'SIP/2.0 ' + self.stcode + " " + self.reason + "\r\n"
    for hdr in self.hdrs:
      s += str(hdr)
    return s + "\r\n" + self.body

  def search(self, name1, name2 = ''):
    for i, h in enumerate(self.hdrs):
      if name1.lower() == h.name.lower() or name2.lower() == h.name.lower():
        return i
    return None

  def rsearch(self, name1, name2 = ''):
    pos = None
    for i, h in enumerate(self.hdrs):
      if name1.lower() == h.name.lower() or name2.lower() == h.name.lower():
        pos = i
    return pos

  def gen_resp(self, stcode, reason, contacts = []):
    hs=["call-id","i","from","f","to","t","via","v","cseq","record-route"]
    resp = Message("SIP/2.0 " + stcode + " " + reason + "\r\n\r\n")
    for h in self.hdrs:
      if h.name.lower() in hs:
        resp.hdrs.append(h)
    if contacts != []:
      resp.hdrs.append(Header("Contact", contacts))
    resp.hdrs.append(Header("Content-Length", ["0"]))
    return resp


class Proxy:

  def __init__(self, domain, ip, port):
    self.location_service = {}
    self.domain, self.ip, self.port = domain, ip, port
    self.via = "SIP/2.0/UDP " + self.ip + ":" + str(self.port) + ";branch="
    self.rr = "<sip:" + self.ip + ":" + str(self.port) + ";lr>"
    self.sr = "<sip:" + self.ip + ":" + str(self.port) + ">"
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.bind((self.ip, self.port))

  def load_routes(self, path):
    # 静的ルートを Location Service に取り込む（REGISTER を送らない装置向け）。
    # 形式: 1行 "AOR Contact"。'#' で始まる行と空行は無視する。
    # 後で同じ AOR の REGISTER が来れば上書きされる（動的登録が優先）。
    with open(path) as f:
      for line in f:
        line = line.strip()
        if line == '' or line.startswith('#'):
          continue
        aor, contact = re.split(r'\s+', line, 1)
        self.location_service[aor] = contact

  def start(self):
    while True:
      buf, addr = self.sock.recvfrom(0xffff)
      # 不正なパケット1発でプロセスを止めないよう、処理は個別に保護する
      try:
        self.handle(buf, addr)
      except Exception as e:
        print("!" * 72)
        print("error handling packet from " + addr[0] + " " + str(addr[1]))
        print("-" * 8)
        print(repr(e))

  def handle(self, buf, addr):
    tmp = buf.decode('utf-8')
    print("<" * 72)
    print("received from " + addr[0] + " " + str(addr[1]))
    print("-" * 8)
    print(tmp)
    msg = Message(tmp)
    if msg.method != None:
      viapos = msg.search("via", "v")
      msg.hdrs[viapos].vals[0] += ";received=" + addr[0]
      m = re.search(";\s*rport", msg.hdrs[viapos].vals[0])
      if m != None:
        tmpvia = msg.hdrs[viapos].vals[0]
        tmpvia = tmpvia[:m.end()] + "=" + str(addr[1]) + tmpvia[m.end():]
        msg.hdrs[viapos].vals[0] = tmpvia
      branch = msg.hdrs[viapos].vals[0] + " "
      branch += msg.hdrs[msg.search("call-id", "i")].vals[0] + " "
      cseq = msg.hdrs[msg.search("cseq", "")].vals[0]
      m = re.match(r'(\d+)\s+(\S+)', msg.hdrs[msg.search("cseq")].vals[0])
      cseq_num, cseq_method = m.group(1), m.group(2)
      if cseq_method == "ACK":
        cseq_method = "INVITE"
      branch += cseq_num + " " + cseq_method
      branch = 'z9hG4bK' + hashlib.md5(branch.encode('utf-8')).hexdigest()
      msg.hdrs[viapos].vals.insert(0, self.via + branch)
      self.handle_request(msg)
    else:
      self.handle_response(msg)

  def handle_request(self, msg):
    # (1) Proxy-Requireがあったらエラー
    pos = msg.search("proxy-require")
    if pos != None:
      if msg.method != "ACK":
        unsupported = msg.hdrs[pos]
        resp = msg.gen_resp("420", "Bad Extension")
        resp.hdrs.append(unsupported)
        self.handle_response(resp)
      return
    # (2) Request-URIが自身を指しているかを判定する
    requri = AddrSpec(msg.requri)
    if self.comp(requri, self.domain, self.ip, self.port):
      if msg.method == "REGISTER":
        return self.handle_register(msg)
      if requri.userinfo in self.location_service:
        msg.requri = self.location_service[requri.userinfo]
      else:
        if msg.method != "ACK":
          self.handle_response(msg.gen_resp("404", "Not Found"))
        return
      requri = AddrSpec(msg.requri)
    # (3) Max-Forwardsの確認
    pos = msg.search("max-forwards")
    if pos == None:
      msg.hdrs.append(Header("Max-Forwards", ["70"]))
    elif msg.hdrs[pos].vals[0] == "0":
      if msg.method != "ACK":
        resp = msg.gen_resp("483", "Too Many Hops")
        self.handle_response(resp)
      return
    else:
      msg.hdrs[pos].vals[0] = str(int(msg.hdrs[pos].vals[0]) -1)
    # (4) Record-Routeヘッダを処理
    pos = msg.rsearch("record-route")
    if pos == None:
      msg.hdrs.append(Header("Record-Route", [self.rr]))
    else:
      msg.hdrs[pos].vals.append(self.rr)
    # (5) Routeヘッダの先頭が自分だったら削除する
    pos = msg.search("route")
    if pos != None:
      route = NameAddr(msg.hdrs[pos].vals[0])
      if self.comp(route, self.domain, self.ip, self.port):
        del msg.hdrs[pos].vals[0]
        if msg.hdrs[pos].vals == []:
          del msg.hdrs[pos]
    # (6) 送信先判定、Routeヘッダ値、なければRequest-URIで判定
    pos = msg.search("route")
    if pos != None:
      target = NameAddr(msg.hdrs[pos].vals[0])
    else:
      target = requri
    self.send(str(msg), target.host, target.port)

  def handle_register(self, msg):
    addr = NameAddr(msg.hdrs[msg.search("to", "t")].vals[0])
    contact = msg.hdrs[msg.search("contact", "m")].vals[0]
    self.location_service[addr.userinfo] = contact
    resp = msg.gen_resp("200", "OK", [contact])
    resp.hdrs.append(Header("Service-Route", [self.sr]))
    self.handle_response(resp)

  def handle_response(self, msg):
    # (1) 先頭のViaを削除
    pos = msg.search("via", "v")
    del msg.hdrs[pos].vals[0]
    if msg.hdrs[pos].vals == []:
      del msg.hdrs[pos]
      pos = msg.search("via", "v")
    # (2) 中継先判定
    m = re.match(r'SIP\s*\/\s*2\.0\s*\/\s*UDP\s+([^\s;:]+)(\s*:\s*(\d+))?',
      msg.hdrs[pos].vals[0])
    desthost, destport = m.group(1), m.group(3)
    prms = msg.hdrs[pos].vals[0][m.end():]
    m = re.search(r';\s*received\s*=\s*([^\s;]+)', prms)
    if m != None:
      desthost = m.group(1)
    m = re.search(r';\s*rport\s*=\s*(\d+)', prms)
    if m != None:
      destport = m.group(1)
    # (3) 送信
    self.send(str(msg), desthost, destport)

  def comp(self, requri, domain, ip, port):
    if requri.host == domain:
      return True
    if requri.host == ip:
      requri_port = requri.port
      if requri_port == None:
        requri_port = 5060
      elif requri_port == "":
        requri_port = 5060
      if str(requri_port) == str(port):
        return True
    return False

  def send(self, buf, host, port):
    if port == None:
      port = 5060
    elif port == "":
      port = 5060
    else:
      port = int(port)
    if self.ip == host and self.port == port:
      return
    self.sock.sendto(buf.encode('utf-8'), 0, (host, port))
    print('>' * 72)
    print('send to ' + host + ' ' + str(port))
    print('-' * 8)
    print(buf)

if __name__ == '__main__':
  px = Proxy(sys.argv[1], sys.argv[2], int(sys.argv[3]))
  if len(sys.argv) > 4:
    px.load_routes(sys.argv[4])
  print("ok")
  px.start()

