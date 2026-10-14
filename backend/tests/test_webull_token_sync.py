import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import webull_broker as wb


class _Doc:
    def __init__(self, data):
        self.data = data
        self.exists = data is not None

    def to_dict(self):
        return self.data


class _Ref:
    def __init__(self, store):
        self.store = store

    def get(self, **_kw):
        return _Doc(self.store.get("doc"))

    def set(self, payload, **_kw):
        self.store["doc"] = dict(payload)
        self.store["writes"] = self.store.get("writes", 0) + 1


class _Db:
    def __init__(self, store):
        self.store = store

    def collection(self, _name):
        return self

    def document(self, _name):
        return _Ref(self.store)


class TokenDirTests(unittest.TestCase):
    def test_sdk_token_dir_matches_sync_path_and_legacy_token_is_carried_over(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            os.makedirs(os.path.join(cwd, "conf"))
            with open(os.path.join(cwd, "conf", "token.txt"), "w") as handle:
                handle.write("legacy-sdk-token")
            previous_cwd = os.getcwd()
            os.chdir(cwd)
            try:
                with patch.dict(os.environ, {"BOTBULLS_TOKEN_DIR": home}):
                    directory = wb._prepare_sdk_token_dir()
                    self.assertEqual(directory, os.path.abspath(home))
                    with open(wb._token_path()) as handle:
                        self.assertEqual(handle.read(), "legacy-sdk-token")
                    self.assertEqual(oct(os.stat(wb._token_path()).st_mode & 0o777), "0o600")
                    # A newer token already in place is never replaced by the legacy copy.
                    wb._write_token_securely("newer-token")
                    wb._prepare_sdk_token_dir()
                    with open(wb._token_path()) as handle:
                        self.assertEqual(handle.read(), "newer-token")
            finally:
                os.chdir(previous_cwd)

    def test_apply_sdk_token_dir_calls_the_sdk(self):
        seen = []

        class Client:
            def set_token_dir(self, path):
                seen.append(path)

        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"BOTBULLS_TOKEN_DIR": home}):
            wb._apply_sdk_token_dir(Client())
        self.assertEqual(seen, [os.path.abspath(home)])


class ForeignKeyTokenTests(unittest.TestCase):
    def setUp(self):
        self.ours = Fernet.generate_key().decode()
        self.other = Fernet.generate_key().decode()
        wb._foreign_token_warned = False
        wb._last_token_sync_digest = None
        wb._token_sync_retry_at = 0.0

    def run_save(self, store, env):
        with tempfile.TemporaryDirectory() as home:
            environment = {"VAULT_SECRET_KEY": self.ours, "BOTBULLS_TOKEN_DIR": home, **env}
            with patch.dict(os.environ, environment), patch.object(wb, "get_firestore_db", return_value=_Db(store)):
                wb._write_token_securely("fresh-local-token")
                class _Inline:
                    def __init__(self, target, daemon=True):
                        self.target = target

                    def start(self):
                        self.target()

                with patch.object(wb.threading, "Thread", _Inline):
                    wb.sync_token_to_firestore()

    def test_does_not_overwrite_a_token_encrypted_with_another_key(self):
        foreign = Fernet(self.other.encode()).encrypt(b"prod-token").decode()
        store = {"doc": {"token_content": foreign}}
        self.run_save(store, {"ENV": "development"})
        self.assertEqual(store["doc"]["token_content"], foreign)
        self.assertNotIn("writes", store)

    def test_production_or_explicit_override_may_replace_it(self):
        foreign = Fernet(self.other.encode()).encrypt(b"prod-token").decode()
        for env in ({"ENV": "production"}, {"ENV": "development", "BOTBULLS_WEBULL_TOKEN_OVERWRITE": "1"}):
            wb._last_token_sync_digest = None
            store = {"doc": {"token_content": foreign}}
            self.run_save(store, env)
            self.assertEqual(store.get("writes"), 1, env)
            with patch.dict(os.environ, {"VAULT_SECRET_KEY": self.ours}):
                self.assertEqual(wb._decrypt_with_vault_rotation(store["doc"]["token_content"])[0], b"fresh-local-token")

    def test_saves_when_the_document_is_missing_or_ours(self):
        for doc in (None, {"token_content": Fernet(self.ours.encode()).encrypt(b"old").decode()}):
            wb._last_token_sync_digest = None
            store = {"doc": doc} if doc else {}
            self.run_save(store, {"ENV": "development"})
            self.assertEqual(store.get("writes"), 1)

    def test_restore_warns_once_with_the_written_date_and_does_not_raise(self):
        foreign = Fernet(self.other.encode()).encrypt(b"prod-token").decode()
        store = {"doc": {"token_content": foreign}}
        printed = []
        with patch.dict(os.environ, {"VAULT_SECRET_KEY": self.ours}), \
                patch.object(wb, "get_firestore_db", return_value=_Db(store)), \
                patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            wb.sync_token_from_firestore()
            wb.sync_token_from_firestore()
        warnings = [line for line in printed if "different vault key" in line]
        self.assertEqual(len(warnings), 1)
        self.assertRegex(warnings[0], r"written \d{4}-\d\d-\d\d")
        self.assertNotIn(foreign, " ".join(printed))


if __name__ == "__main__":
    unittest.main()
