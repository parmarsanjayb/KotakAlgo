import os
import pytest

# Force SQLite test database for all tests to ensure isolation and prevent locks
os.environ["DATABASE_URL_LOCAL"] = "sqlite+aiosqlite:///test_db.db"

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Initializes a clean SQLite test database schema before running any tests."""
    import asyncio
    from database.connection import init_db
    asyncio.run(init_db())
