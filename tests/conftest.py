import pytest

from ecommerce.repositories.sqlite import connect, init_db


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()
