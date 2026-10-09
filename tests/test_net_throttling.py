import contextlib
import io
import unittest
from unittest.mock import Mock

from tes3x.net import proto as p
from tes3x.net.server import RemoteAdmin, Server, SourceBuckets


class SourceLimitTests(unittest.TestCase):
    def test_ipv6_rotation_and_mapped_ipv4_share_budgets(self):
        limits = SourceBuckets(*p.PASSWORD_RATE)
        bucket = limits.get('2001:db8:1:2::1', 100.)
        for i in range(5):
            self.assertTrue(limits.get(f'2001:db8:1:2::{i + 1}', 100.).take(100.))
        self.assertIs(limits.get('2001:0db8:0001:0002:ffff::1%eth0', 100.), bucket)
        self.assertFalse(bucket.take(100.))
        self.assertTrue(limits.get('2001:db8:1:3::1', 100.).take(100.))
        self.assertIs(limits.get('::ffff:192.0.2.1', 100.), limits.get('192.0.2.1', 100.))

    def test_full_table_preserves_spent_budgets_and_refuses_new_sources(self):
        limits = SourceBuckets(*p.PASSWORD_RATE)
        for i in range(p.HANDSHAKES_PENDING):
            bucket = limits.get(f'2001:db8:{i:x}::1', 100.)
            for _ in range(5):
                self.assertTrue(bucket.take(100.))
        for i in range(p.HANDSHAKES_PENDING, p.HANDSHAKES_PENDING + 100):
            self.assertIsNone(limits.get(f'2001:db8:{i:x}::1', 100.))
        self.assertEqual(len(limits.buckets), p.HANDSHAKES_PENDING)
        self.assertFalse(limits.get('2001:db8::1', 100.).available(100.))
        self.assertIsNone(limits.get('192.0.2.1', 160.))  # one recovered try is not a fresh burst
        self.assertTrue(limits.get('2001:db8::1', 160.).take(160.))
        self.assertIsNotNone(limits.get('192.0.2.1', 400.))
        self.assertEqual(len(limits.buckets), p.HANDSHAKES_PENDING)

    def test_only_fully_recovered_entries_are_replaced(self):
        limits = SourceBuckets(5, 1., limit=2)
        spent = limits.get('192.0.2.1', 0.)
        for _ in range(5):
            spent.take(0.)
        limits.get('192.0.2.2', 0.).take(0.)
        self.assertIsNone(limits.get('192.0.2.3', 0.))
        self.assertIsNotNone(limits.get('192.0.2.3', 1.))
        self.assertIs(limits.get('192.0.2.1', 1.), spent)
        self.assertEqual(set(limits.buckets), {'192.0.2.1', '192.0.2.3'})


class PasswordLimitTests(unittest.TestCase):
    def setUp(self):
        self.server = object.__new__(Server)
        self.server.password = b'secret'
        self.server.password_buckets = SourceBuckets(*p.PASSWORD_RATE, limit=2)
        self.server.bans = {kind: set() for kind in p.BAN_KINDS}
        self.server.admitted, self.server.admitted_path = set(), None
        self.server.refuse = Mock()
        self.server.handshake_client = Mock()
        self.server.manager_character = Mock(return_value='')
        self.server.build_server = None
        self.server.send = Mock()

    def hello(self, host, key, password, now=100.):
        packet = bytes(p.T3MP.size) + p.HELLO_BODY.pack(bytes(6), 0, 0, p.MANAGER,
                                                      *[0.] * 6, bytes(32)) + password
        with contextlib.redirect_stdout(io.StringIO()):
            self.server.hello(packet, (host, 1000), (key, (bytes(32), bytes(32))),
                              p.MANAGER_VERSION, 1, 0, 0, '', now)

    def test_join_limit_survives_rotation_and_capacity_pressure(self):
        key = b'new-key'
        for i in range(5):
            self.hello(f'2001:db8:1::{i}', key, b'wrong')
        self.hello('2001:db8:1::99', key, b'secret')
        self.assertNotIn(key, self.server.admitted)
        self.hello('192.0.2.1', key, b'wrong')
        for _ in range(10):
            self.hello('192.0.2.2', key, b'wrong')
        self.hello('2001:db8:1::100', key, b'secret')
        self.server.send.assert_not_called()
        self.assertEqual(len(self.server.password_buckets.buckets), 2)
        self.hello('2001:db8:1::100', key, b'secret', 160.)
        self.assertIn(key, self.server.admitted)
        self.server.send.assert_called_once()

    def test_previously_admitted_keys_bypass_limits(self):
        self.server.admitted.add(b'known')
        for i in range(2):
            bucket = self.server.password_buckets.get(f'192.0.2.{i}', 100.)
            for _ in range(5):
                bucket.take(100.)
        self.hello('192.0.2.99', b'known', b'')
        self.server.send.assert_called_once()
        self.server.refuse.assert_not_called()

    def test_remote_admin_rotation_and_full_table_cannot_refresh_failures(self):
        try:
            p.crypto()
        except SystemExit:
            self.skipTest('needs cryptography')
        admin = RemoteAdmin(b'admin secret', None, lambda: 100.)
        admin.failures = SourceBuckets(*p.PASSWORD_RATE, limit=2)
        run = Mock(return_value='ok')

        def command(host, secret):
            addr = (host, 1000)
            hello = p.REMOTE_HEAD.pack(p.REMOTE_MAGIC, p.REMOTE_VERSION, p.REMOTE_HELLO)
            nonce = admin.handle(hello, addr, run)[p.REMOTE_HEAD.size:]
            head = p.REMOTE_HEAD.pack(p.REMOTE_MAGIC, p.REMOTE_VERSION, p.REMOTE_COMMAND) + nonce
            return admin.handle(head + p.seal(p.remote_key(secret, nonce), 0, head, b'list'),
                                addr, run)

        for i in range(5):
            self.assertIn(b'unauthorized', command(f'2001:db8:1::{i}', b'wrong'))
        self.assertIn(b'slow down', command('2001:db8:1::99', admin.secret))
        command('192.0.2.1', b'wrong')
        self.assertIn(b'slow down', command('192.0.2.2', admin.secret))
        self.assertIn(b'slow down', command('2001:db8:1::100', admin.secret))
        run.assert_not_called()
        self.assertEqual(len(admin.failures.buckets), 2)
