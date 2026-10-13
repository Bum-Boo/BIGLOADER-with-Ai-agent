from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ig_post_controller.database import Database


class DatabaseConnectionLifecycleTests(unittest.TestCase):
    def test_existing_database_checks_release_every_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.db"
            Database(path)
            connections: list[sqlite3.Connection] = []
            connect = sqlite3.connect

            def track_connection(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connections.append(connection)
                return connection

            try:
                with mock.patch("ig_post_controller.database.sqlite3.connect", side_effect=track_connection):
                    Database(path)
                self.assertEqual(len(connections), 2)
                for connection in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
                # On Windows, an open connection also prevents rename/removal.
                path.rename(path.with_name("moved.db"))
            finally:
                for connection in connections:
                    connection.close()


if __name__ == "__main__":
    unittest.main()
