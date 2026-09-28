import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from webull_broker import _decrypt_with_vault_rotation


class WebullVaultRotationTests(unittest.TestCase):
    def test_active_key_decrypts_without_plaintext_fallback(self):
        active = Fernet.generate_key().decode()
        encrypted = Fernet(active.encode()).encrypt(b"token-value").decode()
        with patch.dict(os.environ, {"VAULT_SECRET_KEY": active}, clear=False):
            plaintext, source = _decrypt_with_vault_rotation(encrypted)

        self.assertEqual(plaintext, b"token-value")
        self.assertEqual(source, "active")

    def test_previous_key_decrypts_rotated_token(self):
        active = Fernet.generate_key().decode()
        previous = Fernet.generate_key().decode()
        encrypted = Fernet(previous.encode()).encrypt(b"rotated-token").decode()
        with patch.dict(
            os.environ,
            {
                "VAULT_SECRET_KEY": active,
                "VAULT_SECRET_KEY_PREVIOUS": previous,
            },
            clear=False,
        ):
            plaintext, source = _decrypt_with_vault_rotation(encrypted)

        self.assertEqual(plaintext, b"rotated-token")
        self.assertEqual(source, "previous")

    def test_invalid_ciphertext_is_rejected(self):
        active = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"VAULT_SECRET_KEY": active}, clear=False):
            with self.assertRaises(ValueError):
                _decrypt_with_vault_rotation("not-a-token")


if __name__ == "__main__":
    unittest.main()
