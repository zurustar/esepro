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

  # (uri, scheme, userinfo, host, port)
  CASES = [
    ('sip:alice@example.com',              'sip', 'alice',        'example.com',    None),
    ('sip:alice@example.com:5060',         'sip', 'alice',        'example.com',    '5060'),
    ('sip:example.com',                    'sip', None,           'example.com',    None),
    ('sip:example.com:5060',               'sip', None,           'example.com',    '5060'),
    ('sip:alice:secret@example.com',       'sip', 'alice:secret', 'example.com',    None),
    ('sip:a@192.168.0.1:5060',             'sip', 'a',            '192.168.0.1',    '5060'),
    ('sip:1234@gw.example.com;user=phone', 'sip', '1234',         'gw.example.com', None),
    ('SIP:alice@example.com',              'SIP', 'alice',        'example.com',    None),  # スキーム大小無視
    ('sip:@example.com',                   'sip', '',             'example.com',    None),  # 空 userinfo
  ]

  def test_core_fields(self):
    for uri, scheme, user, host, port in self.CASES:
      with self.subTest(uri=uri):
        a = AddrSpec(uri)
        self.assertEqual(a.scheme, scheme)
        self.assertEqual(a.userinfo, user)
        self.assertEqual(a.host, host)
        self.assertEqual(a.port, port)

  def test_uri_params_separated_from_host(self):
    a = AddrSpec('sip:alice@example.com:5060;transport=tcp;lr')
    self.assertEqual(a.host, 'example.com')
    self.assertEqual(a.port, '5060')
    self.assertEqual(a.uri_prms, ';transport=tcp;lr')
    self.assertEqual(a.headers, '')

  def test_uri_headers_split_on_question_mark(self):
    a = AddrSpec('sip:alice@example.com?subject=hi')
    self.assertEqual(a.host, 'example.com')
    self.assertEqual(a.uri_prms, '')
    self.assertEqual(a.headers, '?subject=hi')

  def test_uri_params_and_headers_together(self):
    a = AddrSpec('sip:alice@example.com;maddr=1.2.3.4?subject=hi')
    self.assertEqual(a.uri_prms, ';maddr=1.2.3.4')
    self.assertEqual(a.headers, '?subject=hi')


class TestNameAddr(unittest.TestCase):

  def test_quoted_display_and_brackets(self):
    n = NameAddr('"Alice" <sip:alice@example.com>;tag=1')
    self.assertIn('Alice', n.display_name)
    self.assertEqual(n.addr_spec, 'sip:alice@example.com')
    self.assertEqual(n.userinfo, 'alice')
    self.assertEqual(n.host, 'example.com')
    self.assertEqual(n.prms, ';tag=1')

  def test_brackets_no_display(self):
    n = NameAddr('<sip:alice@example.com>')
    self.assertEqual(n.display_name, '')
    self.assertEqual(n.addr_spec, 'sip:alice@example.com')
    self.assertEqual(n.prms, '')

  def test_bare_addr_spec(self):
    n = NameAddr('sip:bob@host')
    self.assertEqual(n.addr_spec, 'sip:bob@host')
    self.assertEqual(n.host, 'host')

  def test_bare_addr_spec_with_header_param(self):
    n = NameAddr('sip:alice@example.com;tag=1')
    self.assertEqual(n.addr_spec, 'sip:alice@example.com')
    self.assertEqual(n.host, 'example.com')
    self.assertEqual(n.prms, ';tag=1')

  def test_quoted_display_without_space(self):
    n = NameAddr('"Alice"<sip:a@b>')
    self.assertEqual(n.display_name, '"Alice"')
    self.assertEqual(n.addr_spec, 'sip:a@b')
    self.assertEqual(n.host, 'b')

  def test_empty_quoted_display(self):
    n = NameAddr('"" <sip:alice@host>')
    self.assertIn('""', n.display_name)
    self.assertEqual(n.addr_spec, 'sip:alice@host')

  def test_semicolon_inside_quoted_display(self):
    # ';' が引用符の内側にあれば表示名として保持される
    n = NameAddr('"Weird;Name" <sip:x@h>')
    self.assertIn('Weird;Name', n.display_name)
    self.assertEqual(n.addr_spec, 'sip:x@h')
    self.assertEqual(n.host, 'h')

  def test_uri_param_in_brackets_vs_header_param_outside(self):
    # 角括弧の中は URI パラメータ、外はヘッダパラメータとして分離される
    n = NameAddr('<sip:alice@example.com;transport=tcp>;tag=1')
    self.assertEqual(n.addr_spec, 'sip:alice@example.com;transport=tcp')
    self.assertEqual(n.uri_prms, ';transport=tcp')
    self.assertEqual(n.host, 'example.com')
    self.assertEqual(n.prms, ';tag=1')

  def test_port_in_brackets(self):
    n = NameAddr('<sip:alice@example.com:5060>')
    self.assertEqual(n.host, 'example.com')
    self.assertEqual(n.port, '5060')

  def test_leading_whitespace_before_bracket(self):
    n = NameAddr('  <sip:a@b>  ;tag=2')
    self.assertEqual(n.addr_spec, 'sip:a@b')
    self.assertEqual(n.host, 'b')
    self.assertIn(';tag=2', n.prms)

  def test_bare_addr_spec_with_port_and_param(self):
    n = NameAddr('sip:alice@host:5060;tag=1')
    self.assertEqual(n.addr_spec, 'sip:alice@host:5060')
    self.assertEqual(n.host, 'host')
    self.assertEqual(n.port, '5060')
    self.assertEqual(n.prms, ';tag=1')

  def test_unquoted_display_name(self):
    # 引用符なし表示名（1トークン・空白なし・複数トークンすべて）
    for s, disp in [
        ('Alice <sip:alice@example.com>', 'Alice'),
        ('Alice<sip:a@b>',                'Alice'),
        ('1001 <sip:1001@gw>',            '1001'),
        ('Bob Smith <sip:bob@h>',         'Bob Smith'),
    ]:
      with self.subTest(s=s):
        n = NameAddr(s)
        self.assertEqual(n.display_name, disp)
        self.assertEqual(n.scheme, 'sip')
        self.assertIsNotNone(n.host)

  def test_quoted_display_containing_angle_brackets(self):
    # 引用符の内側の '<' '>' は表示名の一部であり、addr-spec の境界にしない
    n = NameAddr('"<weird>" <sip:alice@host>')
    self.assertEqual(n.display_name, '"<weird>"')
    self.assertEqual(n.addr_spec, 'sip:alice@host')
    self.assertEqual(n.host, 'host')

  def test_param_attribution_brackets_vs_bare(self):
    # 角括弧の有無で ; パラメータの帰属が変わる（RFC3261 §19.1.1）
    inside = NameAddr('<sip:alice@host;transport=tcp>;tag=1')
    self.assertEqual(inside.uri_prms, ';transport=tcp')  # 角括弧内は URI パラメータ
    self.assertEqual(inside.prms, ';tag=1')              # 角括弧外はヘッダパラメータ
    bare = NameAddr('sip:alice@host;transport=tcp')
    self.assertEqual(bare.uri_prms, '')                  # 角括弧なしは URI パラメータを持たず
    self.assertEqual(bare.prms, ';transport=tcp')        # 全てヘッダパラメータ


class TestParsingLimitations(unittest.TestCase):
  """簡略パーサに残る既知の制約（DESIGN.md GAP-5）。現状の記録と回帰検知のため。"""

  def test_unsupported_scheme_no_host_but_safe(self):
    # sips/tel は host を解決しない（TLS 等は非対応）が、userinfo=None で安全
    for uri in ('sips:bob@example.com', 'tel:+81312345678'):
      with self.subTest(uri=uri):
        a = AddrSpec(uri)
        self.assertIsNone(a.host)
        self.assertIsNone(a.userinfo)

  def test_sips_inside_name_addr_not_resolved(self):
    # name-addr 内の sips: も（クラッシュせず）host=None になる
    n = NameAddr('"Bob Smith" <sips:bob@example.com>;tag=xyz')
    self.assertEqual(n.addr_spec, 'sips:bob@example.com')
    self.assertIsNone(n.host)

  def test_ipv6_host_is_truncated(self):
    # IPv6 参照は最初の ':' で切れる（IPv6 非対応・未修正）
    a = AddrSpec('sip:alice@[2001:db8::1]:5060')
    self.assertEqual(a.userinfo, 'alice')
    self.assertEqual(a.host, '[2001')
    self.assertIsNone(a.port)


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

  def test_register_with_unquoted_display_name_in_to(self):
    # To に引用符なし表示名があっても登録できる（パーサ修正 A の実利）
    raw = sip([
      'REGISTER sip:test.example SIP/2.0',
      'Via: SIP/2.0/UDP 9.9.9.9:5060;branch=z9hG4bKreg',
      'From: Alice <sip:alice@test.example>;tag=1',
      'To: Alice <sip:alice@test.example>',
      'Call-ID: c', 'CSeq: 1 REGISTER',
      'Contact: sip:alice@10.0.0.9:5070',
      'Content-Length: 0',
    ])
    self.feed(raw)
    self.assertEqual(self.px.location_service['alice'], 'sip:alice@10.0.0.9:5070')
    self.assertEqual(len(self.sent), 1)
    self.assertTrue(self.sent[0][0].startswith('SIP/2.0 200 OK'))


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
