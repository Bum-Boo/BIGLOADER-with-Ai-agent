"""Rejected download bytes must not consume a previously saved installer."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest import mock

from ig_post_controller.services.update_service import UpdateService


class UpdateDownloadPreservationTests(unittest.TestCase):
    def setUp(self):
        self.service = UpdateService("0.1.0", "https://example.test/update.json")

    def test_bad_hash_preserves_existing_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "setup.exe"
            destination.write_bytes(b"previous verified installer")
            with mock.patch.object(self.service, "_download_url_bytes", return_value=b"bad"):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    self.service.download_installer("https://example.test/setup", destination, "00")
            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_bytes(), b"previous verified installer")

    def test_bad_hash_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "updates" / "setup.exe"
            with mock.patch.object(self.service, "_download_url_bytes", return_value=b"bad"):
                with self.assertRaises(ValueError):
                    self.service.download_installer("https://example.test/setup", destination, "00")
            self.assertFalse(destination.parent.exists())

    def test_download_error_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "updates" / "setup.exe"
            with mock.patch.object(self.service, "_download_url_bytes", side_effect=OSError("transport")):
                with self.assertRaises(OSError):
                    self.service.download_installer("https://example.test/setup", destination)
            self.assertFalse(destination.parent.exists())

    def test_download_error_preserves_existing_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "setup.exe"
            destination.write_bytes(b"prior")
            with mock.patch.object(self.service, "_download_url_bytes", side_effect=OSError("transport")):
                with self.assertRaises(OSError):
                    self.service.download_installer("https://example.test/setup", destination)
            self.assertEqual(destination.read_bytes(), b"prior")

    def test_matching_hash_still_replaces_installer_case_insensitively(self):
        content = b"verified new installer"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "setup.exe"
            destination.write_bytes(b"prior")
            with mock.patch.object(self.service, "_download_url_bytes", return_value=content):
                result = self.service.download_installer(
                    "https://example.test/setup", destination, hashlib.sha256(content).hexdigest()
                )
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), content)

    def test_optional_hash_behavior_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "updates" / "setup.exe"
            with mock.patch.object(self.service, "_download_url_bytes", return_value=b"downloaded"):
                self.assertEqual(self.service.download_installer("https://example.test/setup", destination), destination)
            self.assertEqual(destination.read_bytes(), b"downloaded")


if __name__ == "__main__":
    unittest.main()
