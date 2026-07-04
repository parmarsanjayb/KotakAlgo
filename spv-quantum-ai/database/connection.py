from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from core.config import settings
from core.logging import get_logger

logger = get_logger("database")

# Retrieve database URL from configuration manager
DATABASE_URL = settings.get_database_url()

def get_engine(url: str):
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            echo=False,
            future=True
        )
    else:
        return create_async_engine(
            url,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800
        )

# Setup asynchronous SQLAlchemy Engine
engine = get_engine(DATABASE_URL)

# Async session factory
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base class for all model classes
class Base(DeclarativeBase):
    pass

async def init_db() -> None:
    """Creates database tables defined in models if they do not exist."""
    global engine
    logger.info("Initializing database schemas...")
    try:
        async with engine.begin() as conn:
            # Create all tables using metadata schema
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schemas initialized successfully.")
    except Exception as e:
        if "postgresql" in DATABASE_URL:
            sqlite_url = "sqlite+aiosqlite:///local_db.db"
            logger.warning(f"Failed to connect to PostgreSQL ({e}). Falling back to SQLite: {sqlite_url}")
            engine = get_engine(sqlite_url)
            async_session.configure(bind=engine)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                logger.info("Database schemas initialized successfully using SQLite fallback.")
                return
            except Exception as ex:
                logger.exception("Failed to initialize SQLite fallback database", error=str(ex))
                raise ex
        else:
            logger.exception("Failed to initialize database tables", error=str(e))
            raise e

async def get_db_session():
    """
    Asynchronous generator context manager yielding database sessions.
    Used for FastAPI endpoints or direct programmatic database work.
    """
    async with async_session() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            raise e
        finally:
            await session.close()
