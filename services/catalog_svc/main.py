"""catalog-svc: FastAPI + SQLAlchemy + RDS Postgres."""

from fastapi import HTTPException
from sqlalchemy import text

from libs.common import Settings
from libs.common.app import make_app, run
from services.catalog_svc.db import Product, make_engine, make_sessionmaker, select, selectinload

S = Settings(service_name="catalog-svc")
engine = None
Session = None


async def _startup() -> None:
    global engine, Session
    engine = make_engine(S)
    Session = make_sessionmaker(engine)


async def _shutdown() -> None:
    if engine:
        await engine.dispose()


async def _check_db() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


app = make_app(S, {"postgres": _check_db}, _startup, _shutdown)


@app.get("/products")
async def list_products(limit: int = 20, offset: int = 0) -> dict:
    limit = min(limit, 100)  # never let a client ask for the whole table
    async with Session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Product)
                    .options(selectinload(Product.category))
                    .order_by(Product.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "price": float(p.price),
                "category": p.category.name,
            }
            for p in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/products/{pid}")
async def get_product(pid: int) -> dict:
    async with Session() as sess:
        p = (
            await sess.execute(
                select(Product).options(selectinload(Product.category)).where(Product.id == pid)
            )
        ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "product not found")
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "price": float(p.price),
        "category": p.category.name,
    }


@app.get("/products-n1")
async def list_products_n1(limit: int = 20) -> dict:
    """DELIBERATELY BROKEN. One query for the list, then one more per row.
    Day 2: find this in the p95 graph under k6 load, confirm it with
    log_min_duration_statement on RDS, then fix it by using /products.
    The fix is the `selectinload` above -- eager-load the relationship."""
    async with Session() as sess:
        ids = (
            (await sess.execute(select(Product.id).order_by(Product.id).limit(min(limit, 100))))
            .scalars()
            .all()
        )
        out = []
        for pid in ids:  # N+1: this loop issues one round-trip per product
            p = (
                await sess.execute(
                    select(Product).options(selectinload(Product.category)).where(Product.id == pid)
                )
            ).scalar_one()
            out.append({"id": p.id, "name": p.name, "category": p.category.name})
    return {"items": out, "queries_issued": len(ids) + 1}


if __name__ == "__main__":
    run(app, S)
