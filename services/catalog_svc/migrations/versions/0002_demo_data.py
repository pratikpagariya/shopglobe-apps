"""demo data: 4 categories, 500 products

Lab data only -- the same rows seed.sql loads on the legacy stack. Real
product data would arrive through an admin API or an import job, never a
schema migration. ON CONFLICT DO NOTHING makes re-running it harmless.

500 rows is deliberate: enough that /products-n1 is visibly slower than
/products under load.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO categories (name) VALUES "
        "('telescopes'), ('optics'), ('mounts'), ('books') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO products (sku, name, price, category_id) "
        "SELECT 'SKU-' || LPAD(i::text, 5, '0'), 'Product ' || i, "
        "       ROUND((random() * 900 + 10)::numeric, 2), (i % 4) + 1 "
        "FROM generate_series(1, 500) AS s(i) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM products WHERE sku LIKE 'SKU-%'")
    op.execute("DELETE FROM categories WHERE name IN ('telescopes', 'optics', 'mounts', 'books')")
