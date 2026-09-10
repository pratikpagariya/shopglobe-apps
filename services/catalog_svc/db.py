"""catalog-svc data layer. Pool sizing here is the thing that takes RDS down."""
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

from libs.common import Settings


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    category: Mapped[Category] = relationship(lazy="raise")


def make_engine(s: Settings):
    """pool_size + max_overflow is PER PROCESS. With 4 uvicorn workers and 10
    replicas that is (5 + 2) * 4 * 10 = 280 connections against an RDS
    max_connections of ~100 -> 'FATAL: too many connections'. Either shrink the
    pool, or put PgBouncer / RDS Proxy in front."""
    return create_async_engine(
        s.database_url, pool_size=5, max_overflow=2,
        pool_pre_ping=True,   # survives an RDS Multi-AZ failover
        pool_recycle=1800,    # beat any idle-connection reaper in the middle
    )


def make_sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


__all__ = ["Base", "Category", "Product", "make_engine", "make_sessionmaker",
           "select", "selectinload"]
