#!/usr/bin/python3
# -*- encoding: utf-8 -*-
#
# esepro の単体テスト（標準ライブラリ unittest のみ）。
#   python3 -m unittest               # 全テスト実行
#   python3 -m unittest -v            # 詳細表示
#
# esepro.py は import 時に副作用を持たない（起動は __main__ ガードの内側）ため、
# ここから Proxy / Message などを直接 import してテストできる。

import io, socket, contextlib, unittest

import esepro
from esepro import AddrSpec, NameAddr, Header, Message, Proxy


def sip(lines, body=''):
  """ヘッダ行のリストから CRLF 区切りの SIP メッセージ文字列を組み立てる。"""
  return '\r\n'.join(lines) + '\r\n\r\n' + body


# ---------------------------------------------------------------------------
# パース／シリアライズ（ソケット不要）
# ---------------------------------------------------------------------------

class TestAddrSpec(unittest.TestCase):

  def test_full_uri(self):
    a = AddrSpec('sip:alice@example.com:5060')
    self.assertEqual(a.scheme, 'sip')
    self.assertEqual(a.userinfo, 'alice')
    self.assertEqual(a.host, 'example.com')
    self.assertEqual(a.port, '5060')

  def test_no_port(self):
    a = AddrSpec('sip:bob@example.com')
    self.assertEqual(a.userinfo, 'bob')
    self.assertEqual(a.host, 'example.com')
    self.assertIsNone(a.port)

  def test_non_sip_scheme(self):
    a = AddrSpec('tel:+81312345678')
    self.assertEqual(a.scheme, 'tel')
    self.assertIsNone(a.host)
    self.assertIsNone(a.port)


class TestNameAddr(unittest.TestCase):

  def test_display_name_and_params(self):
    n = NameAddr('"Alice" <sip:alice@example.com>;tag=1')
    self.assertIn('Alice', n.display_name)
    self.assertEqual(n.addr_spec, 'sip:alice@example.com')
    self.assertEqual(n.userinfo, 'alice')
    self.assertEqual(n.host, 'example.com')
    self.assertEqual(n.prms, ';tag=1')

  def test_bare_uri(self):
    n = NameAddr('sip:bob@host')
    self.assertEqual(n.addr_spec, 'sip:bob@host')
    self.assertEqual(n.host, 'host')


class TestHeader(unittest.TestCase):

  def test_str_joins_values(self):
    self.assertEqual(str(Header('Via', ['a', 'b'])), 'Via: a, b\r\n')


class TestMessage(unittest.TestCase):

  def _invite(self):
    return sip([
      'INVITE sip:alice@test.example SIP/2.0',
      'Via: SIP/2.0/UDP 1.2.3.4:5060;branch=z9hG4bKx',
      'From: <sip:bob@test.example>;tag=1',
      'To: <sip:alice@test.example>',
      'Call-ID: abc',
      'CSeq: 1 INVITE',
      'Content-Length: 0',
    ])

  def test_request_line(self):
    m = Message(self._invite())
    self.assertEqual(m.method, 'INVITE')
    self.assertEqual(m.requri, 'sip:alice@test.example')
    self.assertIsNone(m.stcode)

  def test_status_line(self):
    m = Message('SIP/2.0 200 OK\r\n\r\n')
    self.assertIsNone(m.method)
    self.assertEqual(m.stcode, '200')
    self.assertEqual(m.reason, 'OK')

  def test_search_and_rsearch(self):
    m = Message(self._invite())
    self.assertEqual(m.search('via', 'v'), 0)        # 正式名で先頭一致
    self.assertEqual(m.search('cseq'), 4)            # Via,From,To,Call-ID,CSeq
    self.assertIsNone(m.search('contact', 'm'))      # 無いヘッダは None
    m.hdrs.append(Header('Via', ['SIP/2.0/UDP 9.9.9.9']))
    self.assertEqual(m.search('via', 'v'), 0)        # search は先頭
    self.assertEqual(m.rsearch('via', 'v'), len(m.hdrs) - 1)  # rsearch は末尾

  def test_header_folding_unfolded(self):
    raw = ('INVITE sip:x SIP/2.0\r\n'
           'Subject: line one\r\n one continued\r\n'
           '\r\n')
    m = Message(raw)
    pos = m.search('subject')
    self.assertIn('one continued', m.hdrs[pos].vals[0])

  def test_gen_resp_copies_dialog_headers(self):
    m = Message(self._invite())
    resp = m.gen_resp('100', 'Trying')
    self.assertIsNone(resp.method)
    self.assertEqual(resp.stcode, '100')
    for name in ('via', 'from', 'to', 'call-id', 'cseq'):
      self.assertIsNotNone(resp.search(name), name + ' should be copied')
    self.assertEqual(resp.hdrs[resp.search('content-length')].vals, ['0'])

  def test_gen_resp_adds_contact(self):
    m = Message(self._invite())
    resp = m.gen_resp('200', 'OK', ['sip:alice@10.0.0.9'])
    self.assertEqual(resp.hdrs[resp.search('contact')].vals, ['sip:alice@10.0.0.9'])

  def test_roundtrip_preserves_request(self):
    m = Message(self._invite())
    m2 = Message(str(m))
    self.assertEqual(m2.method, m.method)
    self.assertEqual(m2.requri, m.requri)
    self.assertEqual(len(m2.hdrs), len(m.hdrs))


# ---------------------------------------------------------------------------
# Proxy の振る舞い
# ---------------------------------------------------------------------------

class ProxyTestCase(unittest.TestCase):
  """送出を捕捉する Proxy を用意する基底クラス。実ネットワークには出さない。"""

  DOMAIN = 'test.example'
  IP = '127.0.0.1'

  def setUp(self):
    # 空きポートを取得してから Proxy を bind する
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind((self.IP, 0))
    port = probe.getsockname()[1]
    probe.close()
    self.px = Proxy(self.DOMAIN, self.IP, port)
    self.addCleanup(self.px.sock.close)
    # send を差し替えて (buf, host, port) を記録（実送信しない）
    self.sent = []
    self.px.send = lambda buf, host, port: self.sent.append((buf, host, port))

  def feed(self, raw, addr=('9.9.9.9', 5060)):
    """handle() を通す（受信ループ本体）。診断 print は捨てる。"""
    with contextlib.redirect_stdout(io.StringIO()):
      self.px.handle(raw.encode('utf-8'), addr)


class TestComp(ProxyTestCase):

  def test_matches_domain(self):
    self.assertTrue(self.px.comp(AddrSpec('sip:x@test.example'),
                                 self.DOMAIN, self.IP, 5060))

  def test_matches_ip_and_default_port(self):
    self.assertTrue(self.px.comp(AddrSpec('sip:x@127.0.0.1'),
                                 self.DOMAIN, self.IP, 5060))

  def test_rejects_wrong_port(self):
    self.assertFalse(self.px.comp(AddrSpec('sip:x@127.0.0.1:9999'),
                                  self.DOMAIN, self.IP, 5060))

  def test_rejects_other_host(self):
    self.assertFalse(self.px.comp(AddrSpec('sip:x@elsewhere.net'),
                                  self.DOMAIN, self.IP, 5060))


class TestRegister(ProxyTestCase):

  def test_stores_contact_and_returns_200(self):
    raw = sip([
      'REGISTER sip:test.example SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKreg',
      'From: <sip:alice@test.example>;tag=1',
      'To: <sip:alice@test.example>',
      'Call-ID: c',
      'CSeq: 1 REGISTER',
      'Contact: sip:alice@10.0.0.9:5070',
      'Content-Length: 0',
    ])
    self.feed(raw)
    # Location Service に登録された
    self.assertEqual(self.px.location_service['alice'], 'sip:alice@10.0.0.9:5070')
    # 200 OK が、received で書き換えられた送信元へ返る
    self.assertEqual(len(self.sent), 1)
    buf, host, port = self.sent[0]
    self.assertTrue(buf.startswith('SIP/2.0 200 OK'))
    self.assertEqual(host, '9.9.9.9')


class TestRequestRouting(ProxyTestCase):

  def _register_alice(self):
    self.feed(sip([
      'REGISTER sip:test.example SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKreg',
      'From: <sip:alice@test.example>;tag=1',
      'To: <sip:alice@test.example>',
      'Call-ID: c', 'CSeq: 1 REGISTER',
      'Contact: sip:alice@10.0.0.9:5070',
      'Content-Length: 0',
    ]))
    self.sent.clear()

  def test_forwards_to_registered_contact(self):
    self._register_alice()
    self.feed(sip([
      'INVITE sip:alice@test.example SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKinv',
      'From: <sip:bob@test.example>;tag=2',
      'To: <sip:alice@test.example>',
      'Call-ID: c2', 'CSeq: 1 INVITE',
      'Max-Forwards: 70',
      'Content-Length: 0',
    ]))
    self.assertEqual(len(self.sent), 1)
    buf, host, port = self.sent[0]
    self.assertEqual(host, '10.0.0.9')       # 登録 Contact の宛先へ
    self.assertEqual(port, '5070')
    self.assertIn('INVITE sip:alice@10.0.0.9:5070 SIP/2.0', buf)
    self.assertIn('Max-Forwards: 69', buf)   # デクリメントされた
    self.assertIn('Record-Route:', buf)      # RR が付与された

  def test_404_for_unregistered_user(self):
    self.feed(sip([
      'INVITE sip:nobody@test.example SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKnf',
      'From: <sip:bob@test.example>;tag=1',
      'To: <sip:nobody@test.example>',
      'Call-ID: c', 'CSeq: 1 INVITE',
      'Content-Length: 0',
    ]))
    self.assertEqual(len(self.sent), 1)
    self.assertTrue(self.sent[0][0].startswith('SIP/2.0 404'))

  def test_420_for_proxy_require(self):
    self.feed(sip([
      'INVITE sip:bob@elsewhere.net SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKpr',
      'From: <sip:alice@test.example>;tag=1',
      'To: <sip:bob@elsewhere.net>',
      'Call-ID: c', 'CSeq: 1 INVITE',
      'Proxy-Require: someext',
      'Content-Length: 0',
    ]))
    self.assertEqual(len(self.sent), 1)
    self.assertTrue(self.sent[0][0].startswith('SIP/2.0 420'))

  def test_483_when_max_forwards_zero(self):
    self.feed(sip([
      'INVITE sip:bob@elsewhere.net SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKtmh',
      'From: <sip:alice@test.example>;tag=1',
      'To: <sip:bob@elsewhere.net>',
      'Call-ID: c', 'CSeq: 1 INVITE',
      'Max-Forwards: 0',
      'Content-Length: 0',
    ]))
    self.assertEqual(len(self.sent), 1)
    self.assertTrue(self.sent[0][0].startswith('SIP/2.0 483'))


class TestResponseRouting(ProxyTestCase):

  def test_strips_top_via_and_routes_by_received(self):
    raw = sip([
      'SIP/2.0 200 OK',
      'Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtop',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKcli;received=8.8.8.8',
      'From: <sip:bob@test.example>;tag=1',
      'To: <sip:alice@test.example>;tag=2',
      'Call-ID: c', 'CSeq: 1 INVITE',
      'Content-Length: 0',
    ])
    self.feed(raw)
    self.assertEqual(len(self.sent), 1)
    buf, host, port = self.sent[0]
    self.assertEqual(host, '8.8.8.8')        # received= が優先される
    self.assertEqual(port, '5060')
    self.assertNotIn('z9hG4bKtop', buf)      # 先頭 Via は除去された


class TestRobustness(ProxyTestCase):
  """handle() は不正入力で例外を上げる（受信ループの try/except が捕捉する根拠）。"""

  def test_non_utf8_raises(self):
    with contextlib.redirect_stdout(io.StringIO()):
      with self.assertRaises(UnicodeDecodeError):
        self.px.handle(b'\xff\xfe garbage', ('9.9.9.9', 5060))

  def test_garbage_text_raises(self):
    with contextlib.redirect_stdout(io.StringIO()):
      with self.assertRaises(Exception):
        self.px.handle(b'not a sip message\r\n\r\n', ('9.9.9.9', 5060))


if __name__ == '__main__':
  unittest.main()
