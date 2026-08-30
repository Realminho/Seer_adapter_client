"""Task 3.2 — credentials policy: custom min_length / forbidden_tokens."""
import unittest
import tempfile
import os

from web.credentials import load_credentials


class TestCredPolicy(unittest.TestCase):
    def _write(self, body):
        fd, p = tempfile.mkstemp(suffix=".toml")
        os.close(fd)
        open(p, "w").write(body)
        return p

    def test_custom_min_length_enforced(self):
        p = self._write('username="u"\npassword="short1"\n')
        with self.assertRaises(ValueError):
            load_credentials(p, min_length=20)

    def test_custom_min_length_accepts_long_enough(self):
        p = self._write('username="u"\npassword="this-is-twenty-chars!"\n')
        cred = load_credentials(p, min_length=20)
        assert cred.username == "u"
        assert cred.password == "this-is-twenty-chars!"

    def test_default_min_length_still_12(self):
        """Default min_length=12 must stay; an 11-char password must be rejected."""
        p = self._write('username="u"\npassword="onlyeleven"\n')
        with self.assertRaises(ValueError):
            load_credentials(p)

    def test_custom_forbidden_tokens(self):
        """Injecting a custom token list must block passwords containing that token."""
        p = self._write('username="u"\npassword="mysecret-pass-1234"\n')
        with self.assertRaises(ValueError):
            load_credentials(p, forbidden_tokens=("mysecret",))

    def test_custom_forbidden_tokens_empty_allows_anything(self):
        """Empty forbidden_tokens list allows any string that meets min_length."""
        p = self._write('username="u"\npassword="password-password-1"\n')
        # default forbidden_tokens would block "password"; empty list must allow it.
        cred = load_credentials(p, forbidden_tokens=())
        assert cred.password == "password-password-1"
