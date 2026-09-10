CREATE TABLE IF NOT EXISTS categories (
  id SERIAL PRIMARY KEY, name VARCHAR(64) UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY, sku VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(200) NOT NULL,
  price NUMERIC(10,2) NOT NULL, category_id INT NOT NULL REFERENCES categories(id));
CREATE INDEX IF NOT EXISTS ix_products_sku ON products(sku);

INSERT INTO categories (name) VALUES ('telescopes'),('optics'),('mounts'),('books')
  ON CONFLICT DO NOTHING;

-- 500 rows: enough that the N+1 endpoint is visibly slower than /products
INSERT INTO products (sku, name, price, category_id)
SELECT 'SKU-'||LPAD(i::text,5,'0'), 'Product '||i,
       ROUND((random()*900+10)::numeric,2), (i % 4) + 1
FROM generate_series(1,500) AS s(i)
ON CONFLICT DO NOTHING;
